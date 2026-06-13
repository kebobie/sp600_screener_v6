"""
query.py
========
Vraag de resultaten van de laatste run op — zonder internet, zonder opnieuw
data op te halen. Leest output/scores.json (gemaakt door main.py).

Voorbeelden:

    python3 query.py AAON          # volledige scorekaart van één bedrijf
    python3 query.py AAON HCI IRWD # meerdere bedrijven naast elkaar
    python3 query.py --top 20      # de 20 best scorende bedrijven
    python3 query.py --parels      # alleen de pareltjes
    python3 query.py --vlaggen     # bedrijven met de meeste waarschuwingen
    python3 query.py --sector Healthcare   # beste bedrijven per sector
    python3 query.py --beweging    # grootste stijgers/dalers t.o.v. vorige run
"""

import argparse
import json
import os
import sys

import config
from scoring import METRIC_LABELS


# ---------------------------------------------------------------------------
# DATA LADEN
# ---------------------------------------------------------------------------
def load_scores():
    if not os.path.exists(config.SCORES_JSON):
        print("Geen resultaten gevonden. Draai eerst:  python3 main.py")
        sys.exit(1)
    with open(config.SCORES_JSON, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload


# ---------------------------------------------------------------------------
# WEERGAVE-HULPJES
# ---------------------------------------------------------------------------
def _bar(score, width=10):
    if score is None:
        return "·" * width
    filled = round(score / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _num(v, decimals=2, default="—"):
    if v is None:
        return default
    try:
        return f"{float(v):.{decimals}f}"
    except (TypeError, ValueError):
        return default


def _pct(v, default="—"):
    if v is None:
        return default
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# ÉÉN BEDRIJF: VOLLEDIGE SCOREKAART
# ---------------------------------------------------------------------------
def show_company(payload, ticker):
    ticker = ticker.upper().replace(".", "-")
    match = next((r for r in payload["stocks"] if r.get("ticker") == ticker), None)
    if not match:
        print(f"'{ticker}' niet gevonden in de laatste run "
              f"({payload.get('count')} bedrijven, {payload.get('generated_at','?')[:16]}).")
        print("Tip: draai 'python3 main.py' (zonder --limit) voor alle bedrijven.")
        return

    r = match
    pearl = "JA ★" if r.get("is_pearl") else "nee"
    print("=" * 64)
    print(f"  {r.get('ticker')} — {r.get('name') or '?'}")
    print(f"  {r.get('sector') or '—'}  |  {r.get('industry') or ''}")
    print("=" * 64)
    delta = r.get("delta")
    delta_txt = f"   ({'▲' if delta > 0 else '▼'} {delta:+.1f} t.o.v. vorige run)" \
        if delta else ""
    print(f"  TOTAALSCORE : {_num(r.get('total'), 0)}/100{delta_txt}      Parel: {pearl}")
    if r.get("sector_rank"):
        print(f"  Sector-rang : #{r['sector_rank']} van {r['sector_size']} "
              f"in {r.get('sector') or '?'}")
    print(f"  Datadekking : {_pct(r.get('coverage'))} van de cijfers bekend")
    print("-" * 64)
    for cat in config.CATEGORY_WEIGHTS:
        sc = (r.get("cat_scores") or {}).get(cat)
        print(f"  {cat.capitalize():<12} {_num(sc, 0):>4}  {_bar(sc)}")
    print("-" * 64)

    if r.get("strong"):
        print("  Sterke punten : " + ", ".join(r["strong"]))
    if r.get("weak"):
        print("  Zwakke punten : " + ", ".join(r["weak"]))
    flags = r.get("flags") or []
    print("  Vlaggen       : " + (" | ".join(flags) if flags else "geen"))
    print("-" * 64)
    print("  Kerncijfers:")
    print(f"    Koers {_num(r.get('price'))} | Koersdoel {_num(r.get('target_price'))} "
          f"| Advies {r.get('recommendation') or '—'} ({_num(r.get('num_analysts'),0)} analisten)")
    print(f"    Fwd P/E {_num(r.get('forward_pe'))} | PEG {_num(r.get('peg_ratio'))} "
          f"| P/B {_num(r.get('price_book'))} | EV/EBITDA {_num(r.get('ev_ebitda'))}")
    print(f"    ROE {_pct(r.get('roe'))} | Nettomarge {_pct(r.get('profit_margin'))} "
          f"| Omzetgroei {_pct(r.get('revenue_growth'))} | Winstgroei {_pct(r.get('earnings_growth'))}")
    print(f"    Schuld/EV {_num(r.get('debt_to_equity'),0)} | Current ratio {_num(r.get('current_ratio'))} "
          f"| Short float {_pct(r.get('short_pct_float'))} | 52w {_pct(r.get('change_52w'))}")
    print(f"    Netto schuld/EBITDA {_num(r.get('net_debt_ebitda'),1)} "
          f"| ROIC {_pct(r.get('roic'))} | Brutomarge {_pct(r.get('gross_margin'))} "
          f"| vs SMA200 {_pct(r.get('sma200'))}")
    sentiment = r.get("news_sentiment")
    if sentiment is not None:
        label = ("positief" if sentiment >= 0.25
                 else "negatief" if sentiment <= -0.25 else "neutraal")
        print("-" * 64)
        print(f"  Nieuws ({r.get('news_count') or 0} berichten): "
              f"sentiment {sentiment:+.2f} ({label})"
              + (f" | score met nieuws: {_num(r.get('total_combined'), 1)}"
                 if r.get("total_combined") is not None else ""))
        for p in (r.get("news_kernpunten") or [])[:10]:
            print(f"    • {p}")
        if r.get("news_risicos"):
            print("    Risico's : " + " | ".join(r["news_risicos"]))
        if r.get("news_kansen"):
            print("    Kansen   : " + " | ".join(r["news_kansen"]))
    print("-" * 64)
    print("  Score per cijfer (percentiel t.o.v. de hele groep):")
    ms = r.get("metric_scores") or {}
    for field, label in METRIC_LABELS.items():
        if field in ms:
            print(f"    {label:<34} {_num(ms[field],0):>4}  {_bar(ms[field])}")
    print("=" * 64)
    print("  Geen financieel advies — startpunt voor eigen onderzoek.")


# ---------------------------------------------------------------------------
# MEERDERE BEDRIJVEN NAAST ELKAAR
# ---------------------------------------------------------------------------
def _cmp_num(r, field):
    v = r.get(field)
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def compare_companies(payload, tickers):
    """Uitgebreide vergelijking: alle categorieën en kerncijfers naast
    elkaar; het beste cijfer per regel krijgt een ◄-markering."""
    tickers = [t.upper().replace(".", "-") for t in tickers]
    found = []
    for t in tickers:
        match = next((r for r in payload["stocks"] if r.get("ticker") == t), None)
        if match:
            found.append(match)
        else:
            print(f"  ('{t}' niet gevonden, overgeslagen)")
    if not found:
        return

    col = 13
    width = 24 + col * len(found)

    def line(ch="-"):
        print("  " + ch * (width - 4))

    def section(title):
        print(f"\n  {title}")
        line()

    def row(label, fn, fmt=str, direction=None):
        vals = [fn(r) for r in found]
        best = None
        if direction:
            nums = [(i, v) for i, v in enumerate(vals)
                    if isinstance(v, (int, float))]
            if len(nums) >= 2:
                best = (max if direction == "high" else min)(
                    nums, key=lambda x: x[1])[0]
        cells = ""
        for i, v in enumerate(vals):
            txt = fmt(v)
            if i == best:
                txt += " ◄"
            cells += f"{txt:>{col}}"
        print(f"  {label:<22}{cells}")

    n0 = lambda v: _num(v, 0)
    n1 = lambda v: _num(v, 1)
    n2 = lambda v: _num(v, 2)

    line("=")
    row("", lambda r: r.get("ticker") or "?")
    line("=")

    section("SCORES")
    row("Totaalscore", lambda r: _cmp_num(r, "total"), n0, "high")
    row("Verandering (run)", lambda r: _cmp_num(r, "delta"),
        lambda v: f"{v:+.1f}" if isinstance(v, float) else "—", "high")
    row("Parel", lambda r: "★ JA" if r.get("is_pearl") else "nee")
    for cat in config.CATEGORY_WEIGHTS:
        row(cat.capitalize(),
            lambda r, c=cat: (r.get("cat_scores") or {}).get(c), n0, "high")
    row("Vlaggen", lambda r: len(r.get("flags") or []), str, "low")
    row("Sector-rang", lambda r: f"#{r['sector_rank']}/{r['sector_size']}"
        if r.get("sector_rank") else "—")
    row("Datadekking", lambda r: _cmp_num(r, "coverage"), _pct, "high")

    section("WAARDERING")
    row("Forward P/E", lambda r: _cmp_num(r, "forward_pe"), n1, "low")
    row("PEG-ratio", lambda r: _cmp_num(r, "peg_ratio"), n2, "low")
    row("Prijs/boekwaarde", lambda r: _cmp_num(r, "price_book"), n2, "low")
    row("EV/EBITDA", lambda r: _cmp_num(r, "ev_ebitda"), n1, "low")
    row("FCF-rendement", lambda r: _cmp_num(r, "fcf_yield"), _pct, "high")

    section("KWALITEIT")
    row("ROE", lambda r: _cmp_num(r, "roe"), _pct, "high")
    row("ROIC", lambda r: _cmp_num(r, "roic"), _pct, "high")
    row("Brutomarge", lambda r: _cmp_num(r, "gross_margin"), _pct, "high")
    row("EBITDA-marge", lambda r: _cmp_num(r, "ebitda_margin"), _pct, "high")
    row("Nettomarge", lambda r: _cmp_num(r, "profit_margin"), _pct, "high")

    section("GROEI")
    row("Omzetgroei (jaar)", lambda r: _cmp_num(r, "revenue_growth"), _pct, "high")
    row("Omzetgroei 5 jaar", lambda r: _cmp_num(r, "sales_growth_5y"), _pct, "high")
    row("Winstgroei (jaar)", lambda r: _cmp_num(r, "earnings_growth"), _pct, "high")
    row("EPS-groei 5 jaar", lambda r: _cmp_num(r, "eps_growth_5y"), _pct, "high")
    row("EPS kwartaal/kw.", lambda r: _cmp_num(r, "eps_growth_qq"), _pct, "high")
    row("EPS-verrassing", lambda r: _cmp_num(r, "eps_surprise"), _pct, "high")

    section("GEZONDHEID")
    row("Schuld/eigen verm.", lambda r: _cmp_num(r, "debt_to_equity"), n0, "low")
    row("Netto schuld/EBITDA", lambda r: _cmp_num(r, "net_debt_ebitda"), n1, "low")
    row("Current ratio", lambda r: _cmp_num(r, "current_ratio"), n2, "high")
    row("Quick ratio", lambda r: _cmp_num(r, "quick_ratio"), n2, "high")
    row("Vrije kasstroom", lambda r: _cmp_num(r, "free_cashflow"),
        lambda v: f"{v/1e6:,.0f}M" if isinstance(v, float) else "—", "high")

    section("SENTIMENT & MOMENTUM")
    row("Analistenadvies", lambda r: str(r.get("recommendation") or "—"))
    row("Koersdoel-upside", lambda r: _cmp_num(r, "upside"), _pct, "high")
    row("Insider-transacties", lambda r: _cmp_num(r, "insider_trans"), _pct, "high")
    row("Short float", lambda r: _cmp_num(r, "short_pct_float"), _pct, "low")
    row("Short-trend (1 mnd)", lambda r: _cmp_num(r, "short_trend"), _pct, "low")
    row("Koers vs SMA200", lambda r: _cmp_num(r, "sma200"), _pct, "high")
    row("RSI (14)", lambda r: _cmp_num(r, "rsi"), n0)
    row("52w verandering", lambda r: _cmp_num(r, "change_52w"), _pct, "high")

    print()
    line("=")
    print("  ◄ = beste van de vergelijking | details: python3 query.py TICKER")


# ---------------------------------------------------------------------------
# SECTOR-OVERZICHT
# ---------------------------------------------------------------------------
def show_sector(payload, sector_query, n=25):
    q = sector_query.lower()
    rows = [r for r in payload["stocks"]
            if r.get("total") is not None and q in str(r.get("sector") or "").lower()]
    if not rows:
        sectors = sorted({str(r.get("sector")) for r in payload["stocks"]
                          if r.get("sector")})
        print(f"Geen sector gevonden die op '{sector_query}' lijkt. Beschikbaar:")
        for s in sectors:
            print(f"  - {s}")
        return
    _print_list(rows[:n], f"SECTOR: {rows[0].get('sector')} ({len(rows)} bedrijven)")


# ---------------------------------------------------------------------------
# STIJGERS / DALERS
# ---------------------------------------------------------------------------
def show_movers(payload, n=10):
    movers = [r for r in payload["stocks"]
              if r.get("delta") is not None and r["delta"] != 0]
    if not movers:
        print("Geen vergelijkingsdata (eerste run, of scores onveranderd).")
        return
    movers.sort(key=lambda r: -r["delta"])
    print("=" * 60)
    print(f"  GROOTSTE BEWEGINGEN T.O.V. VORIGE RUN")
    print("=" * 60)
    for r in movers[:n]:
        if r["delta"] <= 0:
            break
        print(f"  ▲ {r['ticker']:<8} {_num(r.get('prev_total'),1):>6} -> "
              f"{_num(r.get('total'),1):<6} (+{r['delta']})")
    for r in sorted(movers, key=lambda r: r["delta"])[:n]:
        if r["delta"] >= 0:
            break
        print(f"  ▼ {r['ticker']:<8} {_num(r.get('prev_total'),1):>6} -> "
              f"{_num(r.get('total'),1):<6} ({r['delta']})")
    print("=" * 60)


# ---------------------------------------------------------------------------
# LIJSTEN
# ---------------------------------------------------------------------------
def _print_list(rows, title):
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)
    print(f"  {'#':<3} {'TICKER':<8} {'SCORE':<6} {'P':<2} {'SECTOR':<20} VLAGGEN")
    print("-" * 72)
    for i, r in enumerate(rows, 1):
        pearl = "★" if r.get("is_pearl") else " "
        sector = (str(r.get("sector") or "—"))[:19]
        nflags = len(r.get("flags") or [])
        print(f"  {i:<3} {r.get('ticker'):<8} {_num(r.get('total'),0):<6} "
              f"{pearl:<2} {sector:<20} {nflags}")
    print("-" * 72)
    print("  P = parel | vraag details op met: python3 query.py TICKER")


def show_top(payload, n):
    rows = [r for r in payload["stocks"] if r.get("total") is not None][:n]
    _print_list(rows, f"TOP {n} OP TOTAALSCORE")


def show_pearls(payload):
    rows = [r for r in payload["stocks"] if r.get("is_pearl")]
    if not rows:
        print("Geen pareltjes gevonden in de laatste run.")
        print("Tip: een volledige run (zonder --limit) geeft meer kandidaten, "
              "en in config.py kun je de parel-criteria versoepelen.")
        return
    _print_list(rows, f"PARELTJES ({len(rows)} gevonden)")


def show_flagged(payload, n):
    rows = sorted(payload["stocks"], key=lambda r: -len(r.get("flags") or []))[:n]
    _print_list(rows, f"MEESTE WAARSCHUWINGSVLAGGEN (top {n})")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="Vraag de laatste screener-run op.")
    p.add_argument("tickers", nargs="*", help="Eén of meer tickers, bv. AAON HCI")
    p.add_argument("--top", type=int, metavar="N", help="Toon de top N op score")
    p.add_argument("--parels", action="store_true", help="Toon alleen de pareltjes")
    p.add_argument("--vlaggen", action="store_true", help="Toon bedrijven met de meeste vlaggen")
    p.add_argument("--sector", metavar="NAAM", help="Beste bedrijven binnen een sector")
    p.add_argument("--beweging", action="store_true",
                   help="Grootste stijgers/dalers t.o.v. de vorige run")
    args = p.parse_args()

    payload = load_scores()
    print(f"(run van {payload.get('generated_at','?')[:16].replace('T',' ')} — "
          f"{payload.get('count')} bedrijven)\n")

    if len(args.tickers) > 1:
        compare_companies(payload, args.tickers)
    elif len(args.tickers) == 1:
        show_company(payload, args.tickers[0])
    elif args.top:
        show_top(payload, args.top)
    elif args.parels:
        show_pearls(payload)
    elif args.vlaggen:
        show_flagged(payload, 15)
    elif args.sector:
        show_sector(payload, args.sector)
    elif args.beweging:
        show_movers(payload)
    else:
        show_pearls(payload)
        print()
        show_top(payload, 10)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        # Gebeurt alleen als je de output doorsluist naar bv. head; onschuldig.
        sys.exit(0)
