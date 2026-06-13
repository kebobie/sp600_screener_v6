"""
finviz_fetcher.py
=================
Haalt aanvullende metrics op via Finviz (finvizfinance library).
Wordt parallel naast yfinance gedraaid; resultaten worden apart gecached
als cache/_fv_TICKER.json.

Finviz heeft striktere rate-limits dan Yahoo, dus:
  - max 3 gelijktijdige verzoeken
  - 1.5 seconden pauze per vers verzoek
  - zelfde cache-TTL als yfinance (config.CACHE_TTL_HOURS)
"""

import json
import os
import re
import time
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import config

_CACHE_PREFIX = "_fv_"


# ---------------------------------------------------------------------------
# PARSEER-HULPJES
# ---------------------------------------------------------------------------
def _pct(raw):
    """'24.54%' → 0.2454  |  '-1.08%' → -0.0108  |  '-' → None"""
    if not raw or raw in ("-", "N/A"):
        return None
    try:
        return float(str(raw).replace("%", "").replace(",", "").strip()) / 100
    except (ValueError, TypeError):
        return None


def _float(raw):
    """'1.75' → 1.75  |  '-' → None"""
    if not raw or raw in ("-", "N/A"):
        return None
    try:
        return float(str(raw).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _scale(value, factor):
    """Vermenigvuldig (voor schaal-uitlijning tussen bronnen). None blijft None."""
    return value * factor if value is not None else None


def _split_pct(raw, index=0):
    """
    Veld met twee getallen: 'EPS past 3/5Y': '1.53% 5.45%'
    index=0 → 3Y-waarde, index=1 → 5Y-waarde
    """
    if not raw or raw in ("-", "N/A"):
        return None
    parts = str(raw).split()
    if len(parts) <= index:
        return None
    return _pct(parts[index])


def _split_pct_second(raw):
    """'150.46 -19.39%' → -0.1939  (tweede deel, afstand tot high/low)"""
    return _split_pct(raw, index=1)


# ---------------------------------------------------------------------------
# CACHE
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
        fetched_at = datetime.fromisoformat(cached["_fetched_at"])
        if datetime.now() - fetched_at < timedelta(hours=config.CACHE_TTL_HOURS):
            return cached["data"]
    except Exception:
        pass
    return None


def _save_cache(ticker, data):
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    try:
        with open(_cache_path(ticker), "w", encoding="utf-8") as f:
            json.dump({"_fetched_at": datetime.now().isoformat(), "data": data}, f)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# ÉÉN TICKER
# ---------------------------------------------------------------------------
def fetch_finviz(ticker, use_cache=True):
    """
    Retourneert een dict met aanvullende Finviz-metrics, of een lege dict
    bij een fout. Nooit een harde fout — het is een aanvulling op yfinance.
    """
    if use_cache:
        cached = _load_cache(ticker)
        if cached is not None:
            return cached
    return _fetch_network(ticker)


def _fetch_network(ticker):
    """Echte Finviz-aanroep (zonder cache). Lege dict bij een fout."""
    try:
        from finvizfinance.quote import finvizfinance
        raw = finvizfinance(ticker).ticker_fundament()

        data = {
            # --- Kernvelden onder yfinance-namen (zelfde schaal) ---
            # Dienen als aanvulling/fallback wanneer yfinance ze mist.
            "forward_pe":      _float(raw.get("Forward P/E")),
            "peg_ratio":       _float(raw.get("PEG")),
            "price_book":      _float(raw.get("P/B")),
            "price_sales":     _float(raw.get("P/S")),
            "ev_ebitda":       _float(raw.get("EV/EBITDA")),
            "roe":             _pct(raw.get("ROE")),
            "roa":             _pct(raw.get("ROA")),
            "profit_margin":   _pct(raw.get("Profit Margin")),
            "operating_margin": _pct(raw.get("Oper. Margin")),
            "current_ratio":   _float(raw.get("Current Ratio")),
            "short_pct_float": _pct(raw.get("Short Float")),
            "beta":            _float(raw.get("Beta")),
            "target_price":    _float(raw.get("Target Price")),
            # Finviz geeft Debt/Eq als ratio (0.49); yfinance als percentage
            # (47.5). ×100 om beide op dezelfde schaal te zetten.
            "debt_to_equity":  _scale(_float(raw.get("Debt/Eq")), 100),
            # --- Aanvullende metrics (alleen Finviz) ---
            # Groei (lange termijn)
            "eps_growth_5y":   _split_pct(raw.get("EPS past 3/5Y"), index=1),
            "eps_growth_3y":   _split_pct(raw.get("EPS past 3/5Y"), index=0),
            "sales_growth_5y": _split_pct(raw.get("Sales past 3/5Y"), index=1),
            "sales_growth_3y": _split_pct(raw.get("Sales past 3/5Y"), index=0),
            # Groei (recent kwartaal)
            "eps_growth_qq":   _pct(raw.get("EPS Q/Q")),
            "sales_growth_qq": _pct(raw.get("Sales Q/Q")),
            "eps_growth_yy":   _pct(raw.get("EPS Y/Y TTM")),
            "sales_growth_yy": _pct(raw.get("Sales Y/Y TTM")),
            # Kwaliteit
            "gross_margin":    _pct(raw.get("Gross Margin")),
            "roic":            _pct(raw.get("ROIC")),
            # Gezondheid
            "quick_ratio":     _float(raw.get("Quick Ratio")),
            "lt_debt_equity":  _float(raw.get("LT Debt/Eq")),
            # Sentiment / momentum
            "insider_trans":   _pct(raw.get("Insider Trans")),
            "inst_trans":      _pct(raw.get("Inst Trans")),
            "sma20":           _pct(raw.get("SMA20")),
            "sma50":           _pct(raw.get("SMA50")),
            "sma200":          _pct(raw.get("SMA200")),
            "rsi":             _float(raw.get("RSI (14)")),
            "rel_volume":      _float(raw.get("Rel Volume")),
            "perf_week":       _pct(raw.get("Perf Week")),
            "perf_month":      _pct(raw.get("Perf Month")),
            "perf_quarter":    _pct(raw.get("Perf Quarter")),
            "perf_ytd":        _pct(raw.get("Perf YTD")),
            "perf_year":       _pct(raw.get("Perf Year")),
            # EPS verassing (eerste deel van 'EPS/Sales Surpr.')
            "eps_surprise":    _split_pct(raw.get("EPS/Sales Surpr."), index=0),
            # Afstand tot 52-weeks high/low (negatief = ver onder high)
            "dist_52w_high":   _split_pct_second(raw.get("52W High")),
            "dist_52w_low":    _split_pct_second(raw.get("52W Low")),
        }

        _save_cache(ticker, data)
        return data

    except Exception:
        return {}


# ---------------------------------------------------------------------------
# ALLE TICKERS
# ---------------------------------------------------------------------------
def fetch_all_finviz(tickers, use_cache=True):
    """
    Haalt Finviz-data op voor alle tickers en retourneert een dict
    ticker → data. Fouten leveren een lege dict op (niet fataal).

    Belangrijk: alleen echte netwerk-verzoeken worden afgeremd (1,5s).
    Cache-hits gaan vol vooruit, zodat een herhaalrun in seconden klaar is
    in plaats van minuten.
    """
    # Controleer éérst of de library er is — anders zou elke ticker stil
    # mislukken en daalt de datadekking (en het aantal parels) onverklaarbaar.
    try:
        import finvizfinance  # noqa: F401
    except ImportError:
        print("=" * 70)
        print("[finviz] WAARSCHUWING: de 'finvizfinance' library ontbreekt!")
        print("[finviz] Installeer met:  pip3 install finvizfinance")
        print("[finviz] Zonder deze data daalt de datadekking en vind je")
        print("[finviz] mogelijk geen parels. Finviz-stap wordt overgeslagen.")
        print("=" * 70)
        return {t: {} for t in tickers}

    total = len(tickers)
    results = {}
    counter = {"done": 0, "cached": 0, "fetched": 0, "failed": 0}
    lock = threading.Lock()

    print(f"[finviz] {total} tickers aanvullende data ophalen...")

    # Finviz is strenger dan Yahoo: max 3 gelijktijdige verzoeken
    max_workers = min(3, total)

    def _worker(ticker):
        cached = _load_cache(ticker) if use_cache else None
        hit_network = cached is None
        data = _fetch_network(ticker) if hit_network else cached
        with lock:
            results[ticker] = data
            counter["done"] += 1
            counter["fetched" if hit_network else "cached"] += 1
            if hit_network and not data:
                counter["failed"] += 1
            done = counter["done"]
            if done % 100 == 0 or done == total:
                print(f"[finviz] {done}/{total} verwerkt "
                      f"({counter['cached']} cache, {counter['fetched']} vers, "
                      f"{counter['failed']} mislukt)...")
        # Alleen na een echt verzoek pauzeren om rate-limits te vermijden
        if hit_network:
            time.sleep(1.5)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        list(as_completed([pool.submit(_worker, t) for t in tickers]))

    # Luid alarm als een groot deel mislukte (rate-limit of blokkade)
    if counter["failed"] > total * 0.3:
        print(f"[finviz] WAARSCHUWING: {counter['failed']} van {total} tickers "
              f"mislukt — mogelijk tijdelijk geblokkeerd door Finviz. "
              f"Probeer het later opnieuw; de cache bewaart wat wél lukte.")

    return results
