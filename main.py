"""
main.py
=======
Het startpunt van versie 6.

Voorbeelden:

    python3 main.py                 # volledige run (alle ~600 bedrijven)
    python3 main.py --limit 50      # snelle test met 50 bedrijven
    python3 main.py --no-ai         # zonder AI-duiding
    python3 main.py --refresh       # negeer de cache, haal alles vers op
    python3 main.py --no-browser    # dashboard wel maken, niet openen

Na de run kun je alles opvragen ZONDER opnieuw op te halen:

    python3 query.py AAON           # scorekaart van één bedrijf
    python3 query.py --top 20       # top 20 op score
    python3 query.py --parels       # alleen de pareltjes
"""

import argparse
import json
import os
import sys
from datetime import datetime

# Laad .env expliciet vanaf de plek van dit bestand (robuust, ook als je
# vanuit een andere map start). Vereist: pip3 install python-dotenv
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
                override=True)
except ImportError:
    print("[let op] python-dotenv ontbreekt; .env wordt niet geladen. "
          "Installeer met: pip3 install python-dotenv")

import config
import screener
import scoring
import ai_ranking
import dashboard
import history
import news_analyzer


DISCLAIMER = (
    "LET OP: dit programma geeft GEEN financieel advies. Het scoort bedrijven "
    "op hun cijfers en draagt kandidaten aan om ZELF verder te onderzoeken. "
    "Een hoge score is geen koopsignaal; doe altijd je eigen onderzoek."
)


def parse_args():
    p = argparse.ArgumentParser(
        description="S&P SmallCap 600 parel-screener (geen financieel advies)."
    )
    p.add_argument("--limit", type=int, default=None,
                   help="Beperk het aantal bedrijven (bv. 50 om te testen).")
    p.add_argument("--no-ai", action="store_true",
                   help="Sla de AI-duiding over.")
    p.add_argument("--no-news", action="store_true",
                   help="Sla de nieuws-analyse over.")
    p.add_argument("--refresh", action="store_true",
                   help="Negeer de cache en haal alle data opnieuw op.")
    p.add_argument("--no-browser", action="store_true",
                   help="Maak het dashboard wel, maar open het niet automatisch.")
    p.add_argument("--ai-only", action="store_true",
                   help="Alleen de AI-duiding en het dashboard opnieuw doen, "
                        "op basis van de laatste run (geen data ophalen).")
    return p.parse_args()


def _rerun_ai_only(args):
    """Herdoe alleen de AI-stap + dashboard met de scores van de laatste run."""
    if not os.path.exists(config.SCORES_JSON):
        print("Geen eerdere run gevonden. Draai eerst: python3 main.py")
        sys.exit(1)
    with open(config.SCORES_JSON, "r", encoding="utf-8") as f:
        payload = json.load(f)
    scored = payload["stocks"]
    print(f"[ai-only] Scores van {payload.get('generated_at','?')[:16]} geladen "
          f"({len(scored)} bedrijven).")
    text = ai_ranking.run_ai_ranking(scored, use_ai=True)
    dashboard.build_dashboard(scored, ranking_text=text,
                              open_browser=not args.no_browser)


def _save_scores(scored):
    payload = {
        "generated_at": datetime.now().isoformat(),
        "count": len(scored),
        "pearls": sum(1 for r in scored if r.get("is_pearl")),
        "stocks": scored,
    }
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    with open(config.SCORES_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, default=str)
    print(f"[scores] Opgeslagen -> {config.SCORES_JSON}")
    return payload


def _print_top(scored, n=12):
    rows = [r for r in scored if r.get("total") is not None][:n]
    if not rows:
        return
    print("\n  TOP OP TOTAALSCORE")
    print("  " + "-" * 60)
    print(f"  {'#':<3} {'TICKER':<8} {'SCORE':<6} {'P':<2} {'SECTOR':<22} VLAGGEN")
    for i, r in enumerate(rows, 1):
        pearl = "★" if r.get("is_pearl") else " "
        sector = (str(r.get("sector") or "—"))[:21]
        print(f"  {i:<3} {r.get('ticker'):<8} {r.get('total'):<6} "
              f"{pearl:<2} {sector:<22} {len(r.get('flags') or [])}")
    print("  " + "-" * 60)


def _print_movers(scored, n=5):
    risers, fallers = history.biggest_movers(scored, n=n)
    if not risers and not fallers:
        return
    print("\n  GROOTSTE BEWEGINGEN T.O.V. VORIGE RUN")
    print("  " + "-" * 60)
    for r in risers:
        print(f"  ▲ {r['ticker']:<8} {r['prev_total']:>5} -> {r['total']:<5} (+{r['delta']})")
    for r in fallers:
        print(f"  ▼ {r['ticker']:<8} {r['prev_total']:>5} -> {r['total']:<5} ({r['delta']})")
    print("  " + "-" * 60)


def main():
    args = parse_args()

    print("=" * 70)
    print("  S&P SmallCap 600 — parel-screener (v6)")
    print("=" * 70)
    print(DISCLAIMER)
    print("=" * 70)

    if args.ai_only:
        _rerun_ai_only(args)
        return

    # 1. Data ophalen
    try:
        stocks = screener.run_fetch(limit=args.limit, use_cache=not args.refresh)
    except Exception as exc:
        print(f"\n[fout] De screening kon niet worden voltooid: {exc}")
        sys.exit(1)

    if not stocks:
        print("\nGeen bruikbare resultaten. Controleer je internetverbinding of "
              "zorg dat tickers_fallback.csv in deze map staat.")
        sys.exit(0)

    # 2. Scoren (percentielen over de hele groep)
    print("[score] Bedrijven scoren op 5 categorieën...")
    scored = scoring.score_all(stocks)
    pearls = sum(1 for r in scored if r.get("is_pearl"))
    print(f"[score] {len(scored)} bedrijven gescoord; {pearls} pareltjes gevonden.")

    # 3. Vergelijk met de vorige run (delta's) en werk de historie bij
    history.annotate_deltas(scored)
    history.record_run(scored)

    # 4. Nieuws-analyse van de top (sentiment + kernpunten via de LLM)
    news_analyzer.run_news_analysis(
        scored,
        enabled=config.NEWS_ENABLED and not args.no_news,
        use_cache=not args.refresh)

    # 5. Opslaan: CSV + JSON (voor query.py)
    screener.write_results_csv(scored)
    _save_scores(scored)

    # 6. AI-duiding van de top
    text = ai_ranking.run_ai_ranking(scored, use_ai=not args.no_ai)

    # 7. Dashboard
    dashboard.build_dashboard(scored, ranking_text=text,
                              open_browser=not args.no_browser)

    # 8. Samenvatting in de terminal
    _print_top(scored)
    _print_movers(scored)
    print("\n" + "=" * 70)
    print("  KLAAR")
    print("=" * 70)
    print(f"  - Dashboard (browser): {config.DASHBOARD_HTML}")
    print(f"  - Alle scores (CSV):   {config.RESULTS_CSV}")
    if text:
        print(f"  - AI-pareltjes:        {config.RANKING_TXT}")
    print("\n  Opvragen zonder nieuwe run:")
    print("    python3 query.py TICKER   |   --top 20   |   --parels")
    print("=" * 70)


if __name__ == "__main__":
    main()
