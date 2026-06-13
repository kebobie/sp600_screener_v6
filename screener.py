"""
screener.py
===========
Bindt het ophalen samen en schrijft de verrijkte CSV (ruwe cijfers + scores).
De volgorde van een run staat in main.py; het scoren zelf in scoring.py.
"""

import csv
import os

import config
import data_fetcher


def run_fetch(limit=None, use_cache=True):
    """Tickers ophalen en per bedrijf de data verzamelen."""
    tickers = data_fetcher.get_tickers()
    if not tickers:
        return []
    return data_fetcher.fetch_all(tickers, limit=limit, use_cache=use_cache)


# Kolommen voor de CSV: eerst de scores, dan de ruwe cijfers.
_CSV_FIELDS = [
    "ticker", "name", "sector", "industry",
    "total", "is_pearl", "coverage",
    "score_waardering", "score_kwaliteit", "score_groei",
    "score_gezondheid", "score_sentiment", "flags",
    "price", "market_cap", "forward_pe", "trailing_pe", "peg_ratio",
    "price_book", "price_sales", "ev_ebitda", "revenue_growth",
    "earnings_growth", "free_cashflow", "operating_cashflow",
    "debt_to_equity", "current_ratio", "roe", "roa", "profit_margin",
    "operating_margin", "recommendation", "num_analysts", "target_price",
    "held_institutions", "held_insiders", "short_pct_float", "beta",
    "change_52w", "dividend_yield",
    # Finviz-aanvullingen
    "eps_growth_5y", "eps_growth_3y", "sales_growth_5y", "sales_growth_3y",
    "eps_growth_qq", "sales_growth_qq", "eps_growth_yy", "sales_growth_yy",
    "eps_surprise", "gross_margin", "roic", "quick_ratio", "lt_debt_equity",
    "insider_trans", "inst_trans", "sma20", "sma50", "sma200", "rsi",
    "perf_week", "perf_month", "perf_quarter", "perf_ytd", "perf_year",
    "dist_52w_high", "dist_52w_low",
    # afgeleide en extra Yahoo-velden
    "fcf_yield", "upside", "net_debt_ebitda", "ebitda_margin",
    "short_trend", "payout_ratio", "rec_mean",
    "total_cash", "total_debt", "ebitda",
    # nieuws-analyse
    "news_sentiment", "news_score", "news_count", "total_combined",
]


def write_results_csv(scored):
    """Schrijf de gescoorde resultaten naar output/sp600_results.csv."""
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    try:
        with open(config.RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for r in scored:
                row = dict(r)
                cats = r.get("cat_scores", {})
                row["score_waardering"] = cats.get("waardering")
                row["score_kwaliteit"] = cats.get("kwaliteit")
                row["score_groei"] = cats.get("groei")
                row["score_gezondheid"] = cats.get("gezondheid")
                row["score_sentiment"] = cats.get("sentiment")
                row["flags"] = " | ".join(r.get("flags", []))
                writer.writerow(row)
        print(f"[csv] {len(scored)} bedrijven (met scores) -> {config.RESULTS_CSV}")
    except Exception as exc:
        print(f"[csv] Kon {config.RESULTS_CSV} niet schrijven: {exc}")
