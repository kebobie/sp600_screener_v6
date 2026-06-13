import json
import os
import time
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import config

# Bestand voor tickers die consistent 404 geven — worden overgeslagen bij volgende run
_DEAD_TICKERS_FILE = os.path.join(config.CACHE_DIR, "_dead_tickers.json")

# Wikipedia-pagina met de actuele S&P 600 samenstelling
_WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies"


# ---------------------------------------------------------------------------
# DODE TICKERS (persistent)
# ---------------------------------------------------------------------------
def _load_dead_tickers():
    if not os.path.exists(_DEAD_TICKERS_FILE):
        return set()
    try:
        with open(_DEAD_TICKERS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_dead_tickers(dead):
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    try:
        with open(_DEAD_TICKERS_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(dead), f)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# TICKERS OPHALEN
# ---------------------------------------------------------------------------
def get_tickers():
    """
    Haal de actuele S&P 600 tickers op. Volgorde van bronnen:
      1. Wikipedia (altijd actueel, scrape via pandas.read_html)
      2. Lokale fallback-CSV (als Wikipedia niet bereikbaar is)

    Als Wikipedia werkt, wordt de fallback-CSV automatisch bijgewerkt
    zodat de volgende run ook zonder internet kan starten.
    Bekende dode tickers worden gefilterd voor ze de run ingaan.
    """
    tickers = _tickers_from_wikipedia() or _tickers_from_fallback()
    if not tickers:
        return []

    dead = _load_dead_tickers()
    if dead:
        before = len(tickers)
        tickers = [t for t in tickers if t not in dead]
        skipped = before - len(tickers)
        if skipped:
            print(f"[tickers] {skipped} bekende dode ticker(s) overgeslagen.")

    return tickers


def _tickers_from_wikipedia():
    """Scrape de actuele S&P 600 lijst van Wikipedia."""
    try:
        import io
        import requests
        import pandas as pd

        print("[tickers] Actuele lijst ophalen van Wikipedia...")
        resp = requests.get(_WIKIPEDIA_URL, headers=config.HTTP_HEADERS, timeout=30)
        resp.raise_for_status()

        tables = pd.read_html(io.StringIO(resp.text), attrs={"id": "constituents"})
        if not tables:
            tables = pd.read_html(io.StringIO(resp.text))

        for df in tables:
            for col in df.columns:
                if str(col).strip().lower() in ("symbol", "ticker"):
                    vals = df[col].astype(str).str.strip().tolist()
                    tickers = [v.replace(".", "-") for v in vals
                               if v and v.lower() not in ("nan", "symbol", "ticker")]
                    if len(tickers) > 50:
                        print(f"[tickers] {len(tickers)} actuele bedrijven van Wikipedia.")
                        _update_fallback_csv(tickers)
                        return tickers

        raise ValueError("Geen tickerkolom gevonden op Wikipedia.")
    except Exception as exc:
        print(f"[tickers] Wikipedia mislukt ({exc}). Probeer lokale fallback...")
        return None


def _tickers_from_fallback():
    """Lees tickers uit de lokale fallback-CSV."""
    if not os.path.exists(config.TICKERS_FALLBACK_CSV):
        print(f"[tickers] Geen fallback-CSV gevonden op {config.TICKERS_FALLBACK_CSV}.")
        return None
    try:
        import pandas as pd
        df = pd.read_csv(config.TICKERS_FALLBACK_CSV)
        for col in df.columns:
            if str(col).strip().lower() in ("symbol", "ticker"):
                vals = df[col].astype(str).str.strip().tolist()
                tickers = [v.replace(".", "-") for v in vals
                           if v and v.lower() not in ("nan", "symbol", "ticker")]
                print(f"[tickers] {len(tickers)} bedrijven uit lokale fallback-CSV.")
                return tickers
        print("[tickers] Geen tickerkolom gevonden in fallback-CSV.")
        return None
    except Exception as exc:
        print(f"[tickers] Fout bij lezen fallback-CSV: {exc}")
        return None


def _update_fallback_csv(tickers):
    """Overschrijf de fallback-CSV met de meest actuele lijst."""
    try:
        import pandas as pd
        df = pd.DataFrame({"Symbol": tickers})
        df.to_csv(config.TICKERS_FALLBACK_CSV, index=False)
        print(f"[tickers] Fallback-CSV bijgewerkt ({len(tickers)} tickers).")
    except Exception as exc:
        print(f"[tickers] Kon fallback-CSV niet bijwerken: {exc}")


# ---------------------------------------------------------------------------
# CACHE
# ---------------------------------------------------------------------------
def _cache_path(ticker):
    return os.path.join(config.CACHE_DIR, f"{ticker}.json")


# Versie van het cache-formaat: ophogen wanneer er velden bijkomen, zodat
# oude records (zonder die velden) automatisch vers worden opgehaald.
_CACHE_VERSION = 2


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
        if datetime.now() - fetched_at < timedelta(hours=config.CACHE_TTL_HOURS):
            return cached["data"]
    except Exception:
        pass
    return None


def _save_cache(ticker, data):
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    path = _cache_path(ticker)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"_v": _CACHE_VERSION,
                       "_fetched_at": datetime.now().isoformat(),
                       "data": data}, f)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# ÉÉN TICKER OPHALEN
# ---------------------------------------------------------------------------
def fetch_stock_data(ticker, use_cache=True):
    """Haal financiële data op voor één ticker via yfinance; gebruikt cache."""
    if use_cache:
        cached = _load_cache(ticker)
        if cached is not None:
            cached["_from_cache"] = True
            return cached

    for attempt in range(config.MAX_RETRIES + 1):
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).info

            # Een lege info-dict (alleen "trailingPegRatio" of niets) betekent
            # dat de ticker niet bestaat op Yahoo — markeer als dood.
            if len(info) < 5 or not info.get("symbol"):
                return {"ticker": ticker, "error": "not_found", "_from_cache": False}

            def _get(key, default=None):
                v = info.get(key)
                return v if v not in (None, "None", "N/A", float("inf"), float("-inf")) else default

            data = {
                "ticker": ticker,
                "name": _get("longName") or _get("shortName"),
                "sector": _get("sector"),
                "industry": _get("industry"),
                "price": _get("currentPrice") or _get("regularMarketPrice"),
                "market_cap": _get("marketCap"),
                "trailing_pe": _get("trailingPE"),
                "forward_pe": _get("forwardPE"),
                "peg_ratio": _get("pegRatio"),
                "price_book": _get("priceToBook"),
                "price_sales": _get("priceToSalesTrailing12Months"),
                "ev_ebitda": _get("enterpriseToEbitda"),
                "revenue_growth": _get("revenueGrowth"),
                "earnings_growth": _get("earningsGrowth"),
                "free_cashflow": _get("freeCashflow"),
                "operating_cashflow": _get("operatingCashflow"),
                "debt_to_equity": _get("debtToEquity"),
                "current_ratio": _get("currentRatio"),
                "roe": _get("returnOnEquity"),
                "roa": _get("returnOnAssets"),
                "profit_margin": _get("profitMargins"),
                "operating_margin": _get("operatingMargins"),
                "recommendation": _get("recommendationKey"),
                "num_analysts": _get("numberOfAnalystOpinions"),
                "target_price": _get("targetMeanPrice"),
                "target_high": _get("targetHighPrice"),
                "target_low": _get("targetLowPrice"),
                "held_institutions": _get("heldPercentInstitutions"),
                "held_insiders": _get("heldPercentInsiders"),
                "short_pct_float": _get("shortPercentOfFloat"),
                "beta": _get("beta"),
                "change_52w": _get("52WeekChange"),
                "fifty_two_week_high": _get("fiftyTwoWeekHigh"),
                "fifty_two_week_low": _get("fiftyTwoWeekLow"),
                "dividend_yield": _get("dividendYield"),
                # --- extra velden (zelfde API-call, dus gratis) ---
                "gross_margin": _get("grossMargins"),
                "ebitda_margin": _get("ebitdaMargins"),
                "quick_ratio": _get("quickRatio"),
                "earnings_quarterly_growth": _get("earningsQuarterlyGrowth"),
                "total_cash": _get("totalCash"),
                "total_debt": _get("totalDebt"),
                "ebitda": _get("ebitda"),
                "enterprise_value": _get("enterpriseValue"),
                "rec_mean": _get("recommendationMean"),
                "shares_short": _get("sharesShort"),
                "shares_short_prior": _get("sharesShortPriorMonth"),
                "fifty_day_avg": _get("fiftyDayAverage"),
                "two_hundred_day_avg": _get("twoHundredDayAverage"),
                "payout_ratio": _get("payoutRatio"),
                "forward_eps": _get("forwardEps"),
                "trailing_eps": _get("trailingEps"),
                "next_earnings": _get("earningsTimestamp"),
                "error": None,
                "_from_cache": False,
            }

            _save_cache(ticker, data)
            return data

        except Exception as exc:
            err = str(exc)
            # 404 = ticker bestaat niet meer op Yahoo — direct als dood markeren
            if "404" in err:
                return {"ticker": ticker, "error": "not_found", "_from_cache": False}
            if attempt < config.MAX_RETRIES:
                time.sleep(config.REQUEST_DELAY_SECONDS * (attempt + 2))
                continue
            return {"ticker": ticker, "error": err, "_from_cache": False}

    return {"ticker": ticker, "error": "max retries bereikt", "_from_cache": False}


# ---------------------------------------------------------------------------
# ALLE TICKERS PARALLEL OPHALEN
# ---------------------------------------------------------------------------
def fetch_all(tickers, limit=None, use_cache=True):
    """
    Haal data op voor alle tickers via yfinance (parallel) én Finviz (parallel),
    en voeg beide samen tot één record per bedrijf.
    """
    if limit:
        tickers = tickers[:limit]

    total = len(tickers)
    yf_results = [None] * total
    counter = {"done": 0, "errors": 0, "cached": 0, "dead": 0}
    new_dead = set()
    lock = threading.Lock()

    print(f"[fetch] {total} bedrijven ophalen via Yahoo Finance...")

    max_workers = min(8, total)

    def _worker(idx, ticker):
        data = fetch_stock_data(ticker, use_cache=use_cache)
        with lock:
            yf_results[idx] = data
            counter["done"] += 1
            if data.get("error") == "not_found":
                counter["dead"] += 1
                new_dead.add(ticker)
            elif data.get("error"):
                counter["errors"] += 1
            if data.get("_from_cache"):
                counter["cached"] += 1
            done = counter["done"]
            if done % 50 == 0 or done == total:
                print(f"[fetch] {done}/{total} verwerkt "
                      f"({counter['cached']} cache, "
                      f"{counter['dead']} dood, "
                      f"{counter['errors']} fouten)...")
        if not data.get("_from_cache"):
            time.sleep(config.REQUEST_DELAY_SECONDS)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_worker, i, t): i for i, t in enumerate(tickers)}
        for future in as_completed(futures):
            exc = future.exception()
            if exc:
                idx = futures[future]
                with lock:
                    yf_results[idx] = {"ticker": tickers[idx], "error": str(exc),
                                       "_from_cache": False}

    if new_dead:
        existing_dead = _load_dead_tickers()
        combined = existing_dead | new_dead
        _save_dead_tickers(combined)
        print(f"[fetch] {len(new_dead)} nieuwe dode ticker(s) opgeslagen "
              f"(totaal {len(combined)} bekend).")

    # Haal aanvullende Finviz-data op voor tickers zonder fout
    valid_tickers = [r["ticker"] for r in yf_results
                     if r is not None and not r.get("error")]
    import finviz_fetcher
    fv_data = finviz_fetcher.fetch_all_finviz(valid_tickers, use_cache=use_cache)

    # Voeg yfinance en Finviz samen. yfinance is leidend, maar waar yfinance
    # een veld mist (None) vult Finviz het gat — dat verhoogt de datadekking
    # en dus de betrouwbaarheid van de scores, vooral bij dunne small-caps.
    merged = []
    filled = 0
    for r in yf_results:
        if r is None:
            continue
        fv = fv_data.get(r.get("ticker", ""), {})
        record = dict(fv)                       # begin met Finviz (kern + extra)
        for k, v in r.items():                  # overlay yfinance-waarden
            if v is not None:
                record[k] = v
            elif k not in record:
                record[k] = v                   # behoud None-velden zoals 'error'
        # Tel hoeveel kernvelden door Finviz zijn aangevuld
        for k in ("forward_pe", "peg_ratio", "roe", "debt_to_equity", "target_price"):
            if r.get(k) is None and fv.get(k) is not None:
                filled += 1
        # Zorg dat sleutelvelden altijd kloppen
        record["ticker"] = r.get("ticker")
        record["error"] = r.get("error")
        record["_from_cache"] = r.get("_from_cache", False)
        merged.append(record)

    if filled:
        print(f"[fetch] {filled} ontbrekende kernwaarden aangevuld vanuit Finviz.")

    return merged
