"""
scoring.py
==========
Het scoremodel van versie 6.

Hoe het werkt, in gewone taal:
  1. Voor elk cijfer (bv. forward P/E) kijken we waar een bedrijf staat
     t.o.v. ALLE andere bedrijven in de run: een percentiel van 0-100.
     Geen vaste drempels meer — "goedkoop" betekent: goedkoper dan de rest.
  2. Die percentielen worden gebundeld in vijf categorieën:
     waardering, kwaliteit, groei, gezondheid en sentiment.
  3. De categorieën samen (gewogen, zie config.py) geven een totaalscore 0-100.
  4. Per bedrijf bepalen we automatisch sterke punten, zwakke punten en
     waarschuwingsvlaggen (rode vlaggen).
  5. Een "parel" is een bedrijf dat over de hele linie goed scoort en
     (vrijwel) geen vlaggen heeft — zie de criteria in config.py.

Belangrijk: ontbrekende cijfers worden NOOIT verzonnen. Een metric zonder
waarde telt gewoon niet mee, en de "datadekking" laat zien hoeveel procent
van de cijfers bekend was.
"""

import bisect

import config


# ---------------------------------------------------------------------------
# AFGELEIDE VELDEN (berekend uit de ruwe data, alleen als de bron er is)
# ---------------------------------------------------------------------------
def _safe_float(v):
    try:
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):  # NaN/inf
            return None
        return f
    except (TypeError, ValueError):
        return None


def _derive(s):
    """Voeg berekende velden toe aan een kopie van het bedrijf."""
    d = dict(s)

    fcf = _safe_float(s.get("free_cashflow"))
    mcap = _safe_float(s.get("market_cap"))
    d["fcf_yield"] = (fcf / mcap) if (fcf is not None and mcap and mcap > 0) else None

    price = _safe_float(s.get("price"))
    target = _safe_float(s.get("target_price"))
    d["upside"] = ((target - price) / price) if (price and target and price > 0) else None

    rec = str(s.get("recommendation") or "").lower().replace(" ", "_")
    d["rec_score"] = {
        "strong_buy": 100.0, "buy": 80.0, "hold": 50.0,
        "underperform": 25.0, "sell": 0.0,
    }.get(rec)
    # Fallback: het numerieke analistengemiddelde (1=strong buy .. 5=sell)
    # is fijnmaziger dan de tekstcategorie; gebruik het als die ontbreekt.
    if d["rec_score"] is None:
        rm = _safe_float(s.get("rec_mean"))
        if rm is not None and 1 <= rm <= 5:
            d["rec_score"] = (5.0 - rm) / 4.0 * 100.0

    # Netto schuld / EBITDA: betere schuldmaat dan schuld/EV omdat de
    # kaspositie meetelt (negatief = meer kas dan schuld).
    cash = _safe_float(s.get("total_cash"))
    debt = _safe_float(s.get("total_debt"))
    ebitda = _safe_float(s.get("ebitda"))
    d["net_debt_ebitda"] = ((debt - cash) / ebitda) \
        if (debt is not None and cash is not None and ebitda and ebitda > 0) else None

    # Short-interest trend: >0 betekent dat shorts toenemen (negatief signaal).
    ss = _safe_float(s.get("shares_short"))
    ssp = _safe_float(s.get("shares_short_prior"))
    d["short_trend"] = (ss / ssp - 1.0) if (ss is not None and ssp and ssp > 0) else None

    # SMA-posities: als Finviz ze mist, bereken ze uit de Yahoo-gemiddelden.
    if d.get("sma50") is None:
        avg50 = _safe_float(s.get("fifty_day_avg"))
        if price and avg50 and avg50 > 0:
            d["sma50"] = price / avg50 - 1.0
    if d.get("sma200") is None:
        avg200 = _safe_float(s.get("two_hundred_day_avg"))
        if price and avg200 and avg200 > 0:
            d["sma200"] = price / avg200 - 1.0

    # EPS-groei kwartaal: Yahoo's earningsQuarterlyGrowth als Finviz-fallback.
    if d.get("eps_growth_qq") is None:
        d["eps_growth_qq"] = _safe_float(s.get("earnings_quarterly_growth"))

    return d


# ---------------------------------------------------------------------------
# METRIC-DEFINITIES
# Elke regel: (veldnaam, label NL, richting, categorie, gewicht, geldig-filter)
#   richting "low"  = lager is beter (bv. P/E)
#   richting "high" = hoger is beter (bv. ROE)
#   geldig-filter   = welke waarden meetellen in de vergelijking
# ---------------------------------------------------------------------------
_POS = lambda v: v > 0          # alleen positieve waarden zijn zinvol
_ANY = lambda v: True

METRICS = [
    # --- waardering ---
    ("forward_pe",    "Forward P/E",        "low",  "waardering", 1.0, _POS),
    ("peg_ratio",     "PEG-ratio",          "low",  "waardering", 0.7, _POS),
    ("price_book",    "Prijs/boekwaarde",   "low",  "waardering", 0.6, _POS),
    ("price_sales",   "Prijs/omzet",        "low",  "waardering", 0.5, _POS),
    ("ev_ebitda",     "EV/EBITDA",          "low",  "waardering", 0.8, _POS),
    # --- kwaliteit ---
    ("roe",           "Rendement op EV (ROE)", "high", "kwaliteit", 1.0, _ANY),
    ("roa",           "Rendement op activa",   "high", "kwaliteit", 0.6, _ANY),
    ("profit_margin", "Nettomarge",            "high", "kwaliteit", 0.8, _ANY),
    ("operating_margin", "Operationele marge", "high", "kwaliteit", 0.7, _ANY),
    # --- groei ---
    ("revenue_growth",  "Omzetgroei",  "high", "groei", 1.0, _ANY),
    ("earnings_growth", "Winstgroei",  "high", "groei", 1.0, _ANY),
    # --- gezondheid ---
    ("debt_to_equity", "Schuld/eigen vermogen", "low",  "gezondheid", 1.0, _ANY),
    ("current_ratio",  "Current ratio",         "high", "gezondheid", 0.7, _ANY),
    ("fcf_yield",      "Vrije-kasstroomrendement", "high", "gezondheid", 0.9, _ANY),
    # --- sentiment ---
    ("rec_score",      "Analistenadvies",     "high", "sentiment", 1.0, _ANY),
    ("upside",         "Opwaarts potentieel (koersdoel)", "high", "sentiment", 0.8, _ANY),
    ("held_insiders",  "Insider-bezit",       "high", "sentiment", 0.5, _ANY),
    ("held_institutions", "Institutioneel bezit", "high", "sentiment", 0.4, _ANY),
    ("short_pct_float", "Short interest",     "low",  "sentiment", 0.7, _ANY),
    ("change_52w",     "Koers 52 weken",      "high", "sentiment", 0.6, _ANY),
    # --- extra via Finviz ---
    # groei
    ("eps_growth_5y",   "EPS-groei 5 jaar",        "high", "groei", 0.8, _ANY),
    ("sales_growth_5y", "Omzetgroei 5 jaar",        "high", "groei", 0.7, _ANY),
    ("eps_growth_qq",   "EPS-groei kwartaal/kwartaal", "high", "groei", 0.6, _ANY),
    ("sales_growth_qq", "Omzetgroei kwartaal/kwartaal","high", "groei", 0.5, _ANY),
    ("eps_surprise",    "EPS-verrassing",           "high", "groei", 0.5, _ANY),
    # kwaliteit
    ("gross_margin",    "Brutomarge",               "high", "kwaliteit", 0.6, _ANY),
    ("ebitda_margin",   "EBITDA-marge",             "high", "kwaliteit", 0.5, _ANY),
    ("roic",            "Rendement op geïnvesteerd kapitaal (ROIC)", "high", "kwaliteit", 0.7, _ANY),
    # gezondheid
    ("quick_ratio",     "Quick ratio",              "high", "gezondheid", 0.6, _POS),
    ("lt_debt_equity",  "Langlopende schuld/EV",    "low",  "gezondheid", 0.7, _POS),
    ("net_debt_ebitda", "Netto schuld/EBITDA",      "low",  "gezondheid", 0.9, _ANY),
    # sentiment / momentum
    ("insider_trans",   "Insider-transacties (netto)", "high", "sentiment", 0.6, _ANY),
    ("inst_trans",      "Institutionele transacties",  "high", "sentiment", 0.4, _ANY),
    ("sma50",           "Koers vs. SMA50",           "high", "sentiment", 0.4, _ANY),
    ("sma200",          "Koers vs. SMA200",           "high", "sentiment", 0.5, _ANY),
    ("perf_ytd",        "Prestatie year-to-date",    "high", "sentiment", 0.3, _ANY),
    ("short_trend",     "Short-interest trend",      "low",  "sentiment", 0.4, _ANY),
]


# ---------------------------------------------------------------------------
# PERCENTIELEN
# ---------------------------------------------------------------------------
def _build_distributions(stocks):
    """Per metric: gesorteerde lijst van alle geldige waarden in de groep."""
    dists = {field: [] for field, *_ in METRICS}
    valid_fns = {field: valid for field, _label, _dir, _cat, _w, valid in METRICS}
    for s in stocks:
        for field, vals in dists.items():
            v = _safe_float(s.get(field))
            if v is not None and valid_fns[field](v):
                vals.append(v)
    for vals in dists.values():
        vals.sort()
    return dists


def _pct_rank(sorted_vals, v):
    """Percentiel (0-100) van waarde v binnen de gesorteerde lijst."""
    n = len(sorted_vals)
    if n == 0:
        return None
    lo = bisect.bisect_left(sorted_vals, v)
    hi = bisect.bisect_right(sorted_vals, v)
    return 100.0 * (lo + (hi - lo) / 2.0) / n


# ---------------------------------------------------------------------------
# VLAGGEN (waarschuwingen, los van de score)
# ---------------------------------------------------------------------------
def _flags(s):
    out = []
    fpe = _safe_float(s.get("forward_pe"))
    tpe = _safe_float(s.get("trailing_pe"))
    if (fpe is not None and fpe <= 0) or (fpe is None and tpe is not None and tpe <= 0):
        out.append("Negatieve (verwachte) winst")
    fcf = _safe_float(s.get("free_cashflow"))
    if fcf is not None and fcf < 0:
        out.append("Negatieve vrije kasstroom")
    de = _safe_float(s.get("debt_to_equity"))
    if de is not None and de > 200:
        out.append("Zeer hoge schuldgraad (>2x eigen vermogen)")
    cr = _safe_float(s.get("current_ratio"))
    if cr is not None and cr < 1:
        out.append("Krappe liquiditeit (current ratio < 1)")
    sf = _safe_float(s.get("short_pct_float"))
    if sf is not None and sf > 0.15:
        out.append("Hoge short interest (>15%)")
    chg = _safe_float(s.get("change_52w"))
    if chg is not None and chg < -0.40:
        out.append("Koers >40% gedaald in 52 weken")
    nde = _safe_float(s.get("net_debt_ebitda"))
    if nde is not None and nde > 4:
        out.append("Hoge netto schuld (>4x EBITDA)")
    po = _safe_float(s.get("payout_ratio"))
    if po is not None and po > 1:
        out.append("Dividend hoger dan de winst (payout >100%)")
    st = _safe_float(s.get("short_trend"))
    if st is not None and st > 0.30:
        out.append("Short interest >30% gestegen in een maand")
    return out


# ---------------------------------------------------------------------------
# HOOFD: ALLES SCOREN
# ---------------------------------------------------------------------------
def score_all(stocks):
    """
    Geeft een nieuwe lijst terug: per bedrijf de ruwe data PLUS
      metric_scores  : percentielscore per cijfer (0-100)
      cat_scores     : score per categorie (0-100 of None)
      total          : totaalscore 0-100 (of None bij te weinig data)
      coverage       : welk deel van de metrics bekend was (0-1)
      flags          : lijst waarschuwingen
      strong / weak  : opvallend sterke / zwakke punten (labels)
      is_pearl       : voldoet aan alle parel-criteria
    Bedrijven met een ophaal-fout worden overgeslagen.
    """
    usable = [_derive(s) for s in stocks if not s.get("error")]
    dists = _build_distributions(usable)

    results = []
    for s in usable:
        metric_scores = {}
        cat_acc = {c: [0.0, 0.0] for c in config.CATEGORY_WEIGHTS}  # [som, gewicht]

        for field, label, direction, cat, weight, valid in METRICS:
            v = _safe_float(s.get(field))
            if v is None or not valid(v):
                continue
            pct = _pct_rank(dists[field], v)
            if pct is None:
                continue
            score = (100.0 - pct) if direction == "low" else pct
            metric_scores[field] = round(score, 1)
            cat_acc[cat][0] += score * weight
            cat_acc[cat][1] += weight

        cat_scores = {}
        for cat, (tot, w) in cat_acc.items():
            cat_scores[cat] = round(tot / w, 1) if w > 0 else None

        # Totaal: gewogen over de categorieën waarvoor we data hebben.
        num, den = 0.0, 0.0
        for cat, weight in config.CATEGORY_WEIGHTS.items():
            if cat_scores[cat] is not None:
                num += cat_scores[cat] * weight
                den += weight
        total = round(num / den, 1) if den > 0 else None

        coverage = round(len(metric_scores) / len(METRICS), 2)
        flags = _flags(s)

        # Sterke en zwakke punten: extreme percentielen, max 4 elk.
        labeled = [(METRIC_LABELS[f], sc) for f, sc in metric_scores.items()]
        strong = [lab for lab, sc in sorted(labeled, key=lambda x: -x[1]) if sc >= 80][:4]
        weak = [lab for lab, sc in sorted(labeled, key=lambda x: x[1]) if sc <= 20][:4]

        present_cats = [v for v in cat_scores.values() if v is not None]
        is_pearl = bool(
            total is not None
            and total >= config.PEARL_MIN_TOTAL
            and coverage >= config.PEARL_MIN_COVERAGE
            and present_cats
            and min(present_cats) >= config.PEARL_MIN_CATEGORY
            and len(flags) <= config.PEARL_MAX_FLAGS
        )

        out = dict(s)
        out.update({
            "metric_scores": metric_scores,
            "cat_scores": cat_scores,
            "total": total,
            "coverage": coverage,
            "flags": flags,
            "strong": strong,
            "weak": weak,
            "is_pearl": is_pearl,
        })
        results.append(out)

    # Sorteer op totaalscore (hoog -> laag), bedrijven zonder score achteraan.
    results.sort(key=lambda r: (r["total"] is None, -(r["total"] or 0)))

    # Sector-rang: positie binnen de eigen sector (1 = beste van de sector).
    # Zo zie je of een bedrijf echt uitblinkt of gewoon in een "goedkope"
    # sector zit. De lijst is al gesorteerd, dus tellen per sector volstaat.
    seen_per_sector = {}
    sector_sizes = {}
    for r in results:
        if r["total"] is not None:
            sec = str(r.get("sector") or "?")
            sector_sizes[sec] = sector_sizes.get(sec, 0) + 1
    for r in results:
        if r["total"] is None:
            r["sector_rank"] = None
            r["sector_size"] = None
            continue
        sec = str(r.get("sector") or "?")
        seen_per_sector[sec] = seen_per_sector.get(sec, 0) + 1
        r["sector_rank"] = seen_per_sector[sec]
        r["sector_size"] = sector_sizes[sec]

    return results


# Handige lookup: veldnaam -> Nederlands label.
METRIC_LABELS = {field: label for field, label, *_ in METRICS}
