"""
news_analyzer.py
================
De News Engine van de screener.

Wat deze module doet, in gewone taal:
  1. Voor de best scorende bedrijven (top NEWS_TOP_N, zie config.py) wordt
     recent nieuws opgehaald via Yahoo Finance (gratis, geen key nodig),
     met Finviz-headlines als reserve/aanvulling. Parallel met threads,
     zodat de screener er nauwelijks door vertraagt. Artikelen die niet
     echt over het bedrijf gaan (sector-listicles, nieuws over peers)
     worden eruit gefilterd vóór ze naar de LLM gaan.
  2. Het nieuws gaat per bedrijf naar de LLM (Gemini gratis, of Claude)
     met een strikte JSON-opdracht: een sentimentscore van -1 tot 1,
     maximaal 10 kernpunten, 3 risico's en 3 kansen.
  3. Het sentiment wordt gemengd met de fundamentele totaalscore tot een
     gecombineerde score (gewicht: NEWS_WEIGHT).
  4. Alles wordt 6 uur gecached (cache/_news_TICKER.json) zodat een
     herhaalrun geen API-kosten of wachttijd oplevert.

Robuustheid:
  - Geen nieuws gevonden -> bedrijf wordt overgeslagen, geen fout.
  - Yahoo of de LLM faalt -> melding, de rest van de run gaat gewoon door.
  - Geen API-key -> de hele nieuws-stap wordt netjes overgeslagen.
  - Kapotte JSON van de LLM -> één herstelpoging, daarna overslaan.

LET OP: dit is GEEN financieel advies. Nieuwssentiment is een momentopname
en de LLM kan zich vergissen; gebruik het als startpunt voor eigen onderzoek.
"""

import json
import os
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
import ai_ranking  # hergebruik van de API-key detectie (provider_available)

_CACHE_PREFIX = "_news_"
_CACHE_VERSION = 2  # v2: relevantiefilter + Finviz-bron toegevoegd

# Actief Gemini-model; kan tijdens een run terugvallen op AI_GEMINI_MODEL
_gemini_model = config.NEWS_AI_MODEL


# ---------------------------------------------------------------------------
# CACHE (zelfde patroon als data_fetcher / finviz_fetcher)
# ---------------------------------------------------------------------------
def _cache_path(ticker):
    return os.path.join(config.CACHE_DIR, f"{_CACHE_PREFIX}{ticker}.json")


def _load_cache(ticker):
    path = _cache_path(ticker)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("_v") != _CACHE_VERSION:
            return None
        fetched_at = datetime.fromisoformat(cached["_fetched_at"])
        if datetime.now() - fetched_at < timedelta(hours=config.NEWS_CACHE_TTL_HOURS):
            return cached["data"]
    except Exception:
        pass
    return None


def _save_cache(ticker, data):
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    try:
        with open(_cache_path(ticker), "w", encoding="utf-8") as f:
            json.dump({"_v": _CACHE_VERSION,
                       "_fetched_at": datetime.now().isoformat(),
                       "data": data}, f, ensure_ascii=False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# NIEUWS OPHALEN (Yahoo via yfinance; RSS als reserve)
# ---------------------------------------------------------------------------
def _parse_date(raw):
    """ISO-string of RSS-datum -> datetime (UTC), of None."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        pass
    try:  # RSS: 'Tue, 10 Jun 2026 14:25:28 +0000'
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(str(raw))
    except Exception:
        return None


def _too_old(dt):
    if dt is None:
        return False  # onbekende datum: niet wegfilteren
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt) > timedelta(days=config.NEWS_MAX_AGE_DAYS)


def _fetch_yfinance_news(ticker):
    """Artikelen via yfinance get_news(). Nieuwe structuur: item['content']."""
    import yfinance as yf
    tk = yf.Ticker(ticker)
    try:
        items = tk.get_news(count=20) or []
    except TypeError:  # oudere yfinance kent count= niet
        items = tk.news or []
    articles = []
    for item in items:
        c = item.get("content") or item  # oude versies: velden op top-level
        title = (c.get("title") or "").strip()
        if not title:
            continue
        pub = _parse_date(c.get("pubDate") or c.get("displayTime"))
        if _too_old(pub):
            continue
        provider = c.get("provider") or {}
        articles.append({
            "title": title,
            "summary": (c.get("summary") or c.get("description") or "").strip()[:500],
            "date": pub.strftime("%Y-%m-%d") if pub else "?",
            "source": (provider.get("displayName") if isinstance(provider, dict)
                       else str(provider or "?")),
        })
    return articles


def _fetch_finviz_news(ticker):
    """Reserve/aanvulling: Finviz-headlines (incl. persberichten, geen
    samenvattingen). Yahoo RSS is bewust NIET de fallback: die geeft
    zonder cookies tegenwoordig 404/429."""
    from finvizfinance.quote import finvizfinance
    df = finvizfinance(ticker).ticker_news()
    articles = []
    for _, row in df.head(40).iterrows():
        try:  # één kapotte rij (bv. NaT-datum) mag de rest niet meeslepen
            title = str(row.get("Title") or "").strip()
            if not title:
                continue
            pub = None
            try:
                ts = row.get("Date")
                if ts == ts:  # filtert NaT/NaN
                    pub = ts.to_pydatetime()
            except Exception:
                pass
            if _too_old(pub):
                continue
            articles.append({
                "title": title,
                "summary": "",
                "date": pub.strftime("%Y-%m-%d") if pub else "?",
                "source": str(row.get("Source") or "Finviz"),
            })
        except Exception:
            continue
    return articles


# Woorden die we negeren bij het matchen van de bedrijfsnaam
_NAME_STOPWORDS = {"inc", "corp", "corporation", "company", "co", "ltd",
                   "plc", "group", "holdings", "incorporated", "the"}

# Te generieke eerste naamwoorden om alléén op te matchen ("American ..."
# zou anders vrijwel elk artikel matchen)
_GENERIC_NAME_WORDS = {"american", "first", "global", "united", "national",
                       "standard", "general", "international", "new",
                       "north", "south", "west", "east", "pacific",
                       "atlantic", "central", "community", "home", "city"}


def _relevance_filter(articles, ticker, name):
    """
    Houd alleen artikelen die echt over dit bedrijf gaan: de ticker
    (woordgrens, evt. met NYSE:/NASDAQ:-prefix) of de kern van de
    bedrijfsnaam moet in de titel of samenvatting staan. Yahoo mengt
    namelijk sector-listicles en nieuws over peers door de feed
    (35-55% van de artikelen is niet ticker-specifiek).
    """
    # Ticker-match is hoofdlettergevoelig: tickers die gewone woorden zijn
    # (CARE, ALL, FOR) zouden anders elk artikel matchen. In nieuwsteksten
    # staan tickers vrijwel altijd in kapitalen. '-' en '.' zijn
    # uitwisselbaar (BRK-B vs BRK.B).
    ticker_pattern = re.escape(ticker).replace(r"\-", "[-.]")
    ticker_re = re.compile(rf"\b(?:NYSE:|NASDAQ:|AMEX:)?{ticker_pattern}\b")

    # Bedrijfsnaam-match: bij voorkeur de eerste TWEE betekenisvolle
    # woorden als frase (precies), anders één woord met woordgrenzen —
    # maar nooit een te generiek los woord als "American" of "First".
    name_words = [w for w in re.findall(r"[A-Za-z]{3,}", name or "")
                  if w.lower() not in _NAME_STOPWORDS]
    name_re = None
    if len(name_words) >= 2:
        name_re = re.compile(
            rf"\b{re.escape(name_words[0])}\s+{re.escape(name_words[1])}\b",
            re.IGNORECASE)
    elif name_words and name_words[0].lower() not in _GENERIC_NAME_WORDS:
        name_re = re.compile(rf"\b{re.escape(name_words[0])}\b", re.IGNORECASE)

    relevant = []
    for a in articles:
        text = f"{a['title']} {a.get('summary', '')}"
        if ticker_re.search(text) or (name_re and name_re.search(text)):
            relevant.append(a)
    return relevant


def fetch_news(ticker, name=None):
    """
    Recent, relevant nieuws voor één ticker: yfinance eerst; als dat
    (na filtering) te dun is, aangevuld met Finviz-headlines.
    Dubbele titels eruit; max NEWS_MAX_ARTICLES artikelen.

    Geeft (artikelen, bron_ok) terug. bron_ok=False betekent dat BEIDE
    bronnen faalden (netwerkstoring): dat is geen "geen nieuws" en mag
    dus niet 6 uur in de negatieve cache belanden. Gooit nooit een fout.
    """
    articles, source_ok = [], False
    try:
        articles = _relevance_filter(_fetch_yfinance_news(ticker), ticker, name)
        source_ok = True
    except Exception:
        pass
    if len(articles) < max(3, config.NEWS_MIN_ARTICLES + 1):
        try:
            extra = _relevance_filter(_fetch_finviz_news(ticker), ticker, name)
            articles.extend(extra)
            source_ok = True
        except Exception:
            pass

    seen, unique = set(), []
    for a in articles:
        key = a["title"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(a)
    return unique[: config.NEWS_MAX_ARTICLES], source_ok


# ---------------------------------------------------------------------------
# LLM-ANALYSE (strikte JSON-output)
# ---------------------------------------------------------------------------
_ANALYSIS_SYSTEM = (
    "Je bent een nuchtere financiële nieuwsanalist. Je krijgt recente "
    "nieuwsberichten over één Amerikaans small-cap bedrijf. Analyseer "
    "UITSLUITEND wat er in de berichten staat; verzin niets.\n"
    "Let op: sommige berichten zijn lijstjes-artikelen die veel bedrijven "
    "tegelijk noemen ('3 stocks to watch'); weeg alleen de informatie die "
    "echt over DIT bedrijf gaat.\n\n"
    "Antwoord met UITSLUITEND geldige JSON, zonder tekst eromheen, met "
    "exact deze sleutels:\n"
    "{\n"
    '  "sentiment": <getal van -1.0 (zeer negatief) tot 1.0 (zeer positief), '
    "0 = neutraal of te weinig informatie>,\n"
    '  "kernpunten": [<maximaal 10 korte zinnen in het Nederlands>],\n'
    '  "risicos": [<exact 3 belangrijkste risico\'s uit het nieuws, of minder '
    "als het nieuws daar geen basis voor geeft>],\n"
    '  "kansen": [<exact 3 belangrijkste kansen uit het nieuws, of minder als '
    "het nieuws daar geen basis voor geeft>]\n"
    "}"
)


def _news_prompt(ticker, name, articles):
    lines = [f"Bedrijf: {name or ticker} (ticker: {ticker})",
             f"Aantal berichten: {len(articles)}", ""]
    for i, a in enumerate(articles, 1):
        lines.append(f"[{i}] {a['date']} | {a['source']}")
        lines.append(f"    Titel: {a['title']}")
        if a.get("summary"):
            lines.append(f"    Samenvatting: {a['summary']}")
    return "\n".join(lines)


def _extract_json(text):
    """Pak het eerste JSON-object uit de tekst (LLM's plakken soms ``` eromheen)."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("geen JSON-object gevonden")
    return json.loads(text[start:end + 1])


def _validate_analysis(raw):
    """Dwing het afgesproken formaat af; clamp/knip waar nodig."""
    try:
        sentiment = float(raw.get("sentiment", 0))
        if sentiment != sentiment:  # NaN (json.loads accepteert 'NaN'!)
            sentiment = 0.0
        sentiment = max(-1.0, min(1.0, sentiment))
    except (TypeError, ValueError):
        sentiment = 0.0

    def _str_list(key, max_len):
        items = raw.get(key) or []
        if not isinstance(items, list):
            return []
        return [str(x).strip() for x in items if str(x).strip()][:max_len]

    return {
        "sentiment": round(sentiment, 2),
        "kernpunten": _str_list("kernpunten", 10),
        "risicos": _str_list("risicos", 3),
        "kansen": _str_list("kansen", 3),
    }


def _call_gemini(prompt):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.environ.get("GOOGLE_GEMINI_API_KEY"))
    # Denkbudget op 0: anders gaan de output-tokens op aan "denken" en wordt
    # de JSON afgekapt (zelfde fix als in ai_ranking.py).
    try:
        gen_config = types.GenerateContentConfig(
            system_instruction=_ANALYSIS_SYSTEM,
            max_output_tokens=config.NEWS_AI_MAX_TOKENS,
            temperature=0.2,
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
    except Exception:
        gen_config = types.GenerateContentConfig(
            system_instruction=_ANALYSIS_SYSTEM,
            max_output_tokens=config.NEWS_AI_MAX_TOKENS,
            temperature=0.2,
            response_mime_type="application/json",
        )
    # Probeer het nieuws-model (Flash-Lite, ruimere gratis limiet); val
    # terug op het ranking-model als dat ooit hernoemd/uitgefaseerd wordt.
    # _gemini_model onthoudt de keuze zodat niet elke call eerst faalt.
    global _gemini_model
    try:
        resp = client.models.generate_content(
            model=_gemini_model, contents=prompt, config=gen_config)
    except Exception as exc:
        if (_gemini_model != config.AI_GEMINI_MODEL
                and ("not found" in str(exc).lower() or "404" in str(exc))):
            print(f"[news] Model {_gemini_model} onbekend; "
                  f"terugvallen op {config.AI_GEMINI_MODEL}.")
            _gemini_model = config.AI_GEMINI_MODEL
            resp = client.models.generate_content(
                model=_gemini_model, contents=prompt, config=gen_config)
        else:
            raise
    return (resp.text or "").strip()


def _call_claude(prompt):
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    msg = client.messages.create(
        model=config.NEWS_AI_MODEL_CLAUDE,
        max_tokens=config.NEWS_AI_MAX_TOKENS,
        system=_ANALYSIS_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return "\n".join(b.text for b in msg.content
                     if getattr(b, "type", None) == "text").strip()


def _retry_wait(msg, attempt):
    """
    Hoe lang wachten voor een nieuwe poging? Google stuurt bij een 429
    vaak zelf een 'retryDelay' mee — die respecteren we (plus marge).
    Anders een oplopende wachttijd: 15s, 30s, 45s.
    """
    m = re.search(r"retry(?:Delay|_delay)['\"]?\s*[:=]\s*['\"]?(\d+)", msg)
    if m:
        return min(90, int(m.group(1)) + 2)
    return 15 * (attempt + 1)


def analyze_articles(ticker, name, articles, provider):
    """
    Eén bedrijf door de LLM halen. Geeft een gevalideerde analyse-dict
    terug, of None bij een blijvende fout. Retry bij rate limits (429),
    time-outs en kapotte JSON (één herstelpoging).
    """
    prompt = _news_prompt(ticker, name, articles)
    call = _call_gemini if provider == "gemini" else _call_claude

    for attempt in range(config.NEWS_AI_MAX_RETRIES + 1):
        try:
            text = call(prompt)
            if not text:
                raise ValueError("leeg antwoord")
            return _validate_analysis(_extract_json(text))
        except Exception as exc:
            msg = str(exc)
            low = msg.lower()
            # Tijdelijke fouten én kapotte JSON (de "herstelpoging" uit de
            # docstring) zijn een retry waard; de rest niet.
            retryable = ("429" in msg or "rate limit" in low
                         or "rate_limit" in low or "resource_exhausted" in low
                         or "overloaded" in low or "leeg antwoord" in msg
                         or "timed out" in low or "timeout" in low
                         or "unavailable" in low or "503" in msg
                         or isinstance(exc, (json.JSONDecodeError, ValueError)))
            if retryable and attempt < config.NEWS_AI_MAX_RETRIES:
                wait = _retry_wait(msg, attempt)
                print(f"[news] {ticker}: probleem ({msg[:50]}); {wait}s wachten...")
                time.sleep(wait)
                continue
            print(f"[news] {ticker}: analyse mislukt ({msg[:70]}).")
            return None


# ---------------------------------------------------------------------------
# HOOFD: NIEUWS VOOR DE TOP OPHALEN, ANALYSEREN EN INMENGEN
# ---------------------------------------------------------------------------
def run_news_analysis(scored, enabled=True, use_cache=True):
    """
    Verrijkt de gescoorde lijst (in-place) met nieuwsvelden voor de top
    NEWS_TOP_N bedrijven:
      news_sentiment   : -1..1
      news_kernpunten  : lijst (max 10)
      news_risicos     : lijst (max 3)
      news_kansen      : lijst (max 3)
      news_count       : aantal geanalyseerde artikelen
      news_score       : sentiment omgerekend naar 0-100
      total_combined   : (1-w)*fundamenteel + w*nieuws  (w = NEWS_WEIGHT)
    Bedrijven zonder nieuws of buiten de top houden total_combined = None.
    Crasht nooit: elke fout degradeert naar 'geen nieuwsanalyse'.
    """
    if not enabled:
        print("[news] Nieuws-analyse uitgeschakeld (--no-news of config).")
        return scored

    provider = ai_ranking.provider_available()
    if not provider:
        print("[news] Geen API-key gevonden -> nieuws-analyse overgeslagen.")
        print("[news] Zet GOOGLE_GEMINI_API_KEY in je .env om dit te gebruiken.")
        return scored

    top = [r for r in scored if r.get("total") is not None][: config.NEWS_TOP_N]
    if not top:
        return scored

    # Stap 1: nieuws ophalen (parallel) of uit de cache
    print(f"[news] Nieuws ophalen voor de top {len(top)} bedrijven...")
    news_map = {}      # ticker -> {"articles": [...], "analysis": {...}|None}
    to_analyze = []    # tickers die nog door de LLM moeten
    lock = threading.Lock()

    def _fetch_worker(r):
        ticker = r["ticker"]
        cached = _load_cache(ticker) if use_cache else None
        if cached is not None:
            with lock:
                news_map[ticker] = cached
            return
        articles, source_ok = fetch_news(ticker, r.get("name"))
        entry = {"articles": articles, "analysis": None}
        with lock:
            news_map[ticker] = entry
            if len(articles) >= config.NEWS_MIN_ARTICLES:
                to_analyze.append(ticker)
        if source_ok and len(articles) < config.NEWS_MIN_ARTICLES:
            # "Geen/te weinig nieuws" cachen we ook (negatieve cache),
            # anders wordt zo'n stil bedrijf elke run opnieuw gefetcht.
            # Maar alléén als minstens één bron echt antwoordde: een
            # netwerkstoring mag niet 6 uur als "geen nieuws" blijven staan.
            _save_cache(ticker, entry)
        # Korte jitter-pauze houdt Yahoo te vriend bij parallelle fetches
        time.sleep(random.uniform(0.25, 0.5))

    with ThreadPoolExecutor(max_workers=config.NEWS_FETCH_WORKERS) as pool:
        futures = {pool.submit(_fetch_worker, r): r.get("ticker", "?")
                   for r in top}
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as exc:
                # Een gefaalde worker mag de run niet stoppen, maar moet
                # wél zichtbaar zijn (de ticker wordt dan overgeslagen).
                print(f"[news] {futures[fut]}: nieuws ophalen mislukt "
                      f"({str(exc)[:60]}).")

    cached_n = sum(1 for t, e in news_map.items()
                   if e.get("analysis") is not None)
    no_news = sum(1 for e in news_map.values()
                  if len(e.get("articles") or []) < config.NEWS_MIN_ARTICLES)
    print(f"[news] {len(news_map)} bedrijven: {cached_n} analyses uit cache, "
          f"{len(to_analyze)} vers te analyseren, {no_news} zonder relevant nieuws.")

    # Stap 2: LLM-analyse (sequentieel, met pauze i.v.m. rate limits).
    # De wachtrij kan groeien: wie faalde vóór een model-doorschakeling
    # krijgt daarna in dezelfde run één herkansing.
    if to_analyze:
        global _gemini_model
        print(f"[news] {len(to_analyze)} bedrijven naar {provider.upper()} "
              f"(~{config.NEWS_AI_DELAY}s per stuk)...")
        name_by_ticker = {r["ticker"]: r.get("name") for r in top}
        queue = list(to_analyze)
        failed_before_switch = []
        retried = set()
        consecutive_failures = 0
        done = 0
        i = 0
        while i < len(queue):
            ticker = queue[i]
            i += 1
            entry = news_map[ticker]
            analysis = analyze_articles(ticker, name_by_ticker.get(ticker),
                                        entry["articles"], provider)
            entry["analysis"] = analysis
            # Alleen geslaagde analyses cachen: een tijdelijke fout (429)
            # mag de herkansing bij de volgende run niet 6 uur blokkeren.
            if analysis is not None:
                _save_cache(ticker, entry)
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                failed_before_switch.append(ticker)
                if consecutive_failures >= 3:
                    if (provider == "gemini"
                            and _gemini_model != config.AI_GEMINI_MODEL):
                        # Quotum van het nieuws-model lijkt op: schakel
                        # door naar het ranking-model (eigen quotum) en
                        # geef de eerdere mislukkingen een herkansing.
                        print(f"[news] {_gemini_model} lijkt uitgeput; "
                              f"doorschakelen naar {config.AI_GEMINI_MODEL}.")
                        _gemini_model = config.AI_GEMINI_MODEL
                        consecutive_failures = 0
                        for t in failed_before_switch:
                            if t not in retried:
                                retried.add(t)
                                queue.append(t)
                        failed_before_switch = []
                    else:
                        # Stroomonderbreker: dode key of alle quota op —
                        # verder proberen heeft geen zin. Niet-gecachte
                        # bedrijven krijgen volgende run een herkansing.
                        print(f"[news] {consecutive_failures} mislukkingen "
                              f"op rij; nieuws-analyse afgebroken voor "
                              f"deze run.")
                        break
            done += 1
            if done % 5 == 0 or done == len(queue):
                print(f"[news] {done}/{len(queue)} geanalyseerd...")
            if i < len(queue):
                time.sleep(config.NEWS_AI_DELAY)

    # Stap 3: inmengen in de scores
    analyzed = 0
    for r in top:
        entry = news_map.get(r["ticker"]) or {}
        analysis = entry.get("analysis")
        if not analysis:
            continue
        sentiment = analysis["sentiment"]
        news_score = round((sentiment + 1.0) * 50.0, 1)   # -1..1 -> 0..100
        w = config.NEWS_WEIGHT
        r["news_sentiment"] = sentiment
        r["news_kernpunten"] = analysis["kernpunten"]
        r["news_risicos"] = analysis["risicos"]
        r["news_kansen"] = analysis["kansen"]
        r["news_count"] = len(entry.get("articles") or [])
        r["news_score"] = news_score
        r["total_combined"] = round((1.0 - w) * r["total"] + w * news_score, 1)
        if sentiment <= config.NEWS_FLAG_THRESHOLD:
            r.setdefault("flags", []).append(
                "Negatief nieuwssentiment (LLM-analyse)")
        analyzed += 1

    print(f"[news] Klaar: {analyzed} bedrijven met nieuws-analyse "
          f"(gewicht {config.NEWS_WEIGHT:.0%} in de gecombineerde score).")
    return scored
