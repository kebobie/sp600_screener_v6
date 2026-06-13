"""
dashboard.py
============
Maakt na een run een zelfstandig HTML-dashboard (output/dashboard.html):
  - statistieken van de run bovenaan
  - de pareltjes in een eigen sectie
  - de AI-duiding
  - alle bedrijven als kaarten met totaalscore-ring en categorie-balkjes
  - zoeken, filteren op sector / minimumscore / alleen parels, en sorteren
  - per kaart uitklapbare details met alle cijfers, vlaggen en sterke punten

Geen server nodig: één HTML-bestand dat je in je browser opent.
"""

import html
import os
import re
import webbrowser
from datetime import datetime

import config
import history  # Geïmporteerd om historische datapunten voor de sparklines op te vragen
from scoring import METRIC_LABELS


# ---------------------------------------------------------------------------
# FORMATTERING (verzint nooit iets; onbekend = "—")
# ---------------------------------------------------------------------------
def _num(value, decimals=2, default="—"):
    if value is None:
        return default
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return default


def _pct(value, default="—"):
    if value is None:
        return default
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return default


def _money(value, default="—"):
    if value is None:
        return default
    try:
        v = float(value)
        for unit in ("", "K", "M", "B", "T"):
            if abs(v) < 1000:
                return f"${v:.1f}{unit}"
            v /= 1000
        return f"${v:.1f}P"
    except (TypeError, ValueError):
        return default


def _esc(text):
    return html.escape(str(text)) if text is not None else "—"


def _earnings_date(epoch, default="—"):
    """Unix-timestamp van Yahoo -> '07-05' (dag-maand), of —."""
    if not epoch:
        return default
    try:
        return datetime.fromtimestamp(float(epoch)).strftime("%d-%m-%Y")
    except (TypeError, ValueError, OSError, OverflowError):
        return default


def _rec_class(rec):
    if not rec:
        return "rec-none"
    r = str(rec).lower()
    if "buy" in r:
        return "rec-buy"
    if "sell" in r or "underperform" in r:
        return "rec-sell"
    return "rec-hold"


def _score_color(score):
    if score is None:
        return "var(--muted)"
    if score >= 70:
        return "var(--good)"
    if score >= 45:
        return "var(--mid)"
    return "var(--bad)"


# ---------------------------------------------------------------------------
# SPARKLINE GENERATOR (Pure SVG trendlijntjes)
# ---------------------------------------------------------------------------
def _generate_sparkline_svg(scores, width=100, height=26):
    """
    Genereert een pure, inline SVG-sparkline op basis van een lijst met scores.
    Als er te weinig data is (minder dan 2 punten), wordt een lege string geretourneerd.
    """
    if not scores or len(scores) < 2:
        return ""  # Niet genoeg data voor een trendlijn

    # Bepaal de grenzen voor dynamische schaling
    min_val = min(scores)
    max_val = max(scores)
    val_range = max_val - min_val
    
    # Voorkom delen door nul als de score altijd exact gelijk is gebleven
    if val_range == 0:
        val_range = 1.0
        min_val -= 0.5  # Centreer de lijn verticaal

    # Bereken de coördinaten voor de SVG-punten
    points = []
    num_points = len(scores)
    
    for idx, score in enumerate(scores):
        # X-as: verdeel gelijkmatig over de breedte
        x = (idx / (num_points - 1)) * width
        
        # Y-as: SVG 0,0 is LINKSBOVEN. Dus een hogere score moet een LAGERE Y-waarde krijgen.
        # We laten 3px marge aan de boven- en onderkant over zodat de lijn of stip niet wordt afgekapt.
        y = height - 3 - ((score - min_val) / val_range) * (height - 6)
        points.append(f"{x},{y}")
    
    points_str = " ".join(points)
    
    # Bepaal de kleur op basis van de algehele trend (laatste punt vs eerste punt)
    # Stijgend = groen, dalend = zacht rood
    line_color = "var(--good)" if scores[-1] >= scores[0] else "var(--bad)"
    
    # Bouw de SVG string
    svg = (
        f'<svg width="{width}" height="{height}" style="overflow: visible; display: block;" title="Trend van laatste runs: {" -> ".join(map(str, scores))}">'
        f'  <polyline fill="none" stroke="{line_color}" stroke-width="1.75" '
        f'            stroke-linecap="round" stroke-linejoin="round" '
        f'            points="{points_str}" />'
        f'  <circle cx="{points[-1].split(",")[0]}" cy="{points[-1].split(",")[1]}" '
        f'          r="2.5" fill="{line_color}" />'
        f'</svg>'
    )
    return svg


# ---------------------------------------------------------------------------
# AI-TEKST NAAR HTML
# ---------------------------------------------------------------------------
def _ranking_html(ranking_text):
    if not ranking_text:
        return ('<p class="muted">Geen AI-duiding beschikbaar. Zet een '
                'GOOGLE_GEMINI_API_KEY in je <code>.env</code> en draai opnieuw.</p>')
    safe = html.escape(ranking_text)
    safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
    safe = safe.replace("\n", "<br>")
    return f'<div class="ranking-text">{safe}</div>'


# ---------------------------------------------------------------------------
# ÉÉN BEDRIJFSKAART
# ---------------------------------------------------------------------------
_CAT_SHORT = {
    "waardering": "WRD", "kwaliteit": "KWL", "groei": "GRO",
    "gezondheid": "GZH", "sentiment": "SNT",
}


def _attr(v):
    try:
        return f"{float(v):.4f}"
    except (TypeError, ValueError):
        return "-999999"


def _card(r):
    ticker = _esc(r.get("ticker"))
    name = _esc(r.get("name") or "")
    sector = _esc(r.get("sector") or "—")
    total = r.get("total")
    is_pearl = bool(r.get("is_pearl"))
    cats = r.get("cat_scores") or {}
    flags = r.get("flags") or []
    rec = r.get("recommendation")

    # Haal de historische scores op (max 10 runs) en genereer de sparkline SVG
    hist_points = history.get_ticker_history_points(ticker, max_points=10)
    sparkline_html = _generate_sparkline_svg(hist_points)

    # Categorie-balkjes
    bars = ""
    for cat in config.CATEGORY_WEIGHTS:
        sc = cats.get(cat)
        width = 0 if sc is None else max(2, sc)
        bars += (
            f'<div class="cat" title="{cat.capitalize()}: {_num(sc,0)}">'
            f'<span>{_CAT_SHORT[cat]}</span>'
            f'<div class="bar"><i style="width:{width}%;background:{_score_color(sc)}"></i></div>'
            f'<b>{_num(sc,0)}</b></div>'
        )

    # Uitklapbare details
    detail_rows = ""
    pairs = [
        ("Koers", _money(r.get("price"))), ("Marktwaarde", _money(r.get("market_cap"))),
        ("Forward P/E", _num(r.get("forward_pe"))), ("PEG", _num(r.get("peg_ratio"))),
        ("P/B", _num(r.get("price_book"))), ("EV/EBITDA", _num(r.get("ev_ebitda"))),
        ("ROE", _pct(r.get("roe"))), ("Nettomarge", _pct(r.get("profit_margin"))),
        ("Omzetgroei", _pct(r.get("revenue_growth"))), ("Winstgroei", _pct(r.get("earnings_growth"))),
        ("Schuld/EV", _num(r.get("debt_to_equity"), 0)), ("Current ratio", _num(r.get("current_ratio"))),
        ("Vrije kasstroom", _money(r.get("free_cashflow"))), ("Koersdoel", _money(r.get("target_price"))),
        ("Analisten", _num(r.get("num_analysts"), 0)), ("Inst. bezit", _pct(r.get("held_institutions"))),
        ("Insider bezit", _pct(r.get("held_insiders"))), ("Short float", _pct(r.get("short_pct_float"))),
        ("52w verandering", _pct(r.get("change_52w"))), ("Beta", _num(r.get("beta"))),
        # Finviz-aanvullingen
        ("Netto schuld/EBITDA", _num(r.get("net_debt_ebitda"), 1)),
        ("Volgende cijfers", _earnings_date(r.get("next_earnings"))),
        ("ROIC", _pct(r.get("roic"))), ("Brutomarge", _pct(r.get("gross_margin"))),
        ("EPS-groei 5j", _pct(r.get("eps_growth_5y"))), ("Omzetgroei 5j", _pct(r.get("sales_growth_5y"))),
        ("EPS Q/Q", _pct(r.get("eps_growth_qq"))), ("EPS-verrassing", _pct(r.get("eps_surprise"))),
        ("Insider trans.", _pct(r.get("insider_trans"))), ("Quick ratio", _num(r.get("quick_ratio"))),
        ("Koers vs SMA50", _pct(r.get("sma50"))), ("Koers vs SMA200", _pct(r.get("sma200"))),
        ("RSI (14)", _num(r.get("rsi"), 0)), ("Prestatie YTD", _pct(r.get("perf_ytd"))),
    ]
    for label, value in pairs:
        detail_rows += (f'<div class="metric"><span class="m-label">{label}</span>'
                        f'<span class="m-value">{value}</span></div>')

    # Delta t.o.v. vorige run + rang binnen de sector
    delta = r.get("delta")
    if delta is None or abs(delta) < 0.05:
        delta_html = ""
    elif delta > 0:
        delta_html = f'<span class="delta up" title="t.o.v. vorige run">▲ {delta:+.1f}</span>'
    else:
        delta_html = f'<span class="delta down" title="t.o.v. vorige run">▼ {delta:+.1f}</span>'

    srank = r.get("sector_rank")
    ssize = r.get("sector_size")
    sector_extra = f" · #{srank}/{ssize} in sector" if srank and ssize else ""

    # Nieuws-analyse (alleen aanwezig voor de top-N met nieuws)
    news_html = ""
    sentiment = r.get("news_sentiment")
    if sentiment is not None:
        if sentiment >= 0.25:
            s_cls, s_label = "good-t", "positief"
        elif sentiment <= -0.25:
            s_cls, s_label = "bad-t", "negatief"
        else:
            s_cls, s_label = "flag-t", "neutraal"
        points = "".join(f"<li>{_esc(p)}</li>"
                         for p in (r.get("news_kernpunten") or [])[:10])
        risks = "".join(f"<li>{_esc(p)}</li>" for p in (r.get("news_risicos") or []))
        opps = "".join(f"<li>{_esc(p)}</li>" for p in (r.get("news_kansen") or []))
        combined = r.get("total_combined")
        news_html = (
            f'<div class="news-block">'
            f'<p class="note"><b class="{s_cls}">Nieuws: {s_label} '
            f'({sentiment:+.2f})</b> · {r.get("news_count") or 0} berichten'
            + (f' · score met nieuws: <b>{_num(combined, 1)}</b>' if combined is not None else '')
            + '</p>'
            + (f'<ul class="news-list">{points}</ul>' if points else '')
            + (f'<p class="note bad-t">Risico\'s:</p><ul class="news-list">{risks}</ul>' if risks else '')
            + (f'<p class="note good-t">Kansen:</p><ul class="news-list">{opps}</ul>' if opps else '')
            + '</div>'
        )

    strong = r.get("strong") or []
    weak = r.get("weak") or []
    notes = ""
    if strong:
        notes += f'<p class="note good-t">Sterk: {_esc(", ".join(strong))}</p>'
    if weak:
        notes += f'<p class="note bad-t">Zwak: {_esc(", ".join(weak))}</p>'
    if flags:
        notes += f'<p class="note flag-t">⚑ {_esc(" | ".join(flags))}</p>'

    pearl_badge = '<span class="pearl-badge">★ PAREL</span>' if is_pearl else ""
    rec_label = _esc(rec).upper().replace("_", " ") if rec else "GEEN ADVIES"

    return f"""
    <article class="card{' pearl' if is_pearl else ''}"
        data-ticker="{ticker}" data-name="{name.lower()}" data-sector="{sector}"
        data-score="{_attr(total)}" data-pe="{_attr(r.get('forward_pe'))}"
        data-roe="{_attr(r.get('roe'))}" data-growth="{_attr(r.get('revenue_growth'))}"
        data-mcap="{_attr(r.get('market_cap'))}" data-pearl="{1 if is_pearl else 0}"
        data-delta="{_attr(delta)}" data-flags="{len(flags)}">
      <header class="card-head">
        <div class="ring" style="--p:{(total or 0)};--rc:{_score_color(total)}">
          <span>{_num(total, 0)}</span>
        </div>
        <div class="head-mid">
          <h3>{ticker} {pearl_badge}</h3>
          <p class="company">{name}</p>
          <p class="sector">{sector}{sector_extra}</p>
        </div>
        <div class="head-right" style="display: flex; flex-direction: column; align-items: flex-end; gap: 4px; min-width: 100px;">
          <span class="rec {_rec_class(rec)}">{rec_label}</span>
          <div style="height: 26px; display: flex; align-items: center; margin-top: 2px;">
            {sparkline_html if sparkline_html else delta_html}
          </div>
        </div>
      </header>
      <div class="cats">{bars}</div>
      <button class="toggle" onclick="this.closest('.card').classList.toggle('open')">details</button>
      <div class="details">
        {notes}
        {news_html}
        <div class="metrics">{detail_rows}</div>
        <p class="coverage">Datadekking: {_pct(r.get('coverage'))} · vlaggen: {len(flags)}</p>
      </div>
    </article>
    """


# ---------------------------------------------------------------------------
# PAGINA BOUWEN
# ---------------------------------------------------------------------------
def _last_saved_ranking():
    """Val terug op de laatst opgeslagen AI-ranking (zonder disclaimer-kop)."""
    try:
        with open(config.RANKING_TXT, "r", encoding="utf-8") as f:
            text = f.read()
        # Sla de disclaimer + kop over: alles na de eerste '====='-lijn
        sep = "=" * 70
        if sep in text:
            text = text.split(sep, 1)[1]
        text = text.strip()
        return text or None
    except OSError:
        return None


def build_dashboard(scored, ranking_text=None, open_browser=True):
    if not ranking_text:
        ranking_text = _last_saved_ranking()
        if ranking_text:
            print("[dashboard] Geen verse AI-duiding; laatst opgeslagen versie gebruikt.")
    usable = [r for r in scored if r.get("total") is not None]
    pearls = [r for r in usable if r.get("is_pearl")]
    sectors = sorted({str(r.get("sector")) for r in usable if r.get("sector")})

    cards = "".join(_card(r) for r in usable)
    pearl_cards = "".join(_card(r) for r in pearls) or \
        '<p class="muted">Geen bedrijven voldeden aan alle parel-criteria in deze run. ' \
        'Tip: draai zonder --limit voor meer kandidaten, of versoepel de criteria in config.py.</p>'

    sector_options = "".join(f'<option value="{_esc(s)}">{_esc(s)}</option>' for s in sectors)
    scores = [r["total"] for r in usable]
    median = sorted(scores)[len(scores) // 2] if scores else 0

    page = _PAGE_TEMPLATE.format(
        date=datetime.now().strftime("%d-%m-%Y %H:%M"),
        count=len(usable),
        pearls=len(pearls),
        median=f"{median:.0f}",
        sector_options=sector_options,
        ranking=_ranking_html(ranking_text),
        pearl_cards=pearl_cards,
        cards=cards,
    )

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    path = config.DASHBOARD_HTML
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(page)
        print(f"[dashboard] Opgeslagen -> {path}")
        if open_browser:
            webbrowser.open("file://" + os.path.abspath(path))
            print("[dashboard] Geopend in je browser.")
    except Exception as exc:
        print(f"[dashboard] Kon dashboard niet maken: {exc}")
    return path


# ---------------------------------------------------------------------------
# HTML/CSS/JS — let op: letterlijke accolades zijn verdubbeld vanwege .format()
# ---------------------------------------------------------------------------
_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>S&amp;P 600 Parel-screener</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,900&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg:#0c0f0e; --panel:#15191a; --panel-2:#1b2022; --ink:#e8e6df;
    --muted:#86908c; --line:#272e30; --accent:#c7f24a; --gold:#e6c068;
    --good:#6ee7a8; --mid:#e8c95c; --bad:#f08b7a;
  }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:var(--bg); color:var(--ink);
    font-family:'IBM Plex Mono',monospace; line-height:1.5; padding-bottom:80px; }}
  .wrap {{ max-width:1280px; margin:0 auto; padding:0 28px; }}

  header.top {{ border-bottom:1px solid var(--line); padding:44px 0 28px; margin-bottom:36px; }}
  header.top h1 {{ font-family:'Fraunces',serif; font-weight:900;
    font-size:clamp(2.2rem,5vw,3.4rem); letter-spacing:-.02em; line-height:1; }}
  header.top h1 .dot {{ color:var(--accent); }}
  .stats {{ display:flex; gap:28px; flex-wrap:wrap; margin-top:18px; }}
  .stat b {{ font-family:'Fraunces',serif; font-size:1.6rem; display:block; }}
  .stat span {{ color:var(--muted); font-size:.72rem; text-transform:uppercase; letter-spacing:.08em; }}
  .stat.gold b {{ color:var(--gold); }}
  .disclaimer {{ margin-top:18px; padding:12px 16px; border:1px solid var(--line);
    border-left:3px solid var(--mid); background:var(--panel); color:var(--muted);
    font-size:.8rem; border-radius:4px; }}

  section {{ margin-bottom:46px; }}
  .section-label {{ font-family:'Fraunces',serif; font-weight:600; font-size:1.45rem;
    margin-bottom:16px; display:flex; align-items:baseline; gap:12px; }}
  .section-label .count {{ font-family:'IBM Plex Mono'; font-size:.85rem; color:var(--accent); }}
  .section-label.gold .count {{ color:var(--gold); }}

  .ranking-panel {{ background:linear-gradient(160deg,var(--panel),var(--panel-2));
    border:1px solid var(--line); border-radius:8px; padding:26px 30px; }}
  .ranking-text {{ font-size:.9rem; }}
  .ranking-text strong {{ color:var(--accent); font-weight:600; }}

  .controls {{ display:flex; gap:10px; flex-wrap:wrap; margin-bottom:20px; align-items:center; }}
  .controls input[type=text], .controls select {{
    background:var(--panel); color:var(--ink); border:1px solid var(--line);
    padding:8px 12px; border-radius:6px; font-family:inherit; font-size:.8rem; }}
  .controls input[type=text] {{ width:220px; }}
  .controls button {{ background:var(--panel); color:var(--ink); border:1px solid var(--line);
    padding:7px 13px; border-radius:999px; cursor:pointer; font-family:inherit;
    font-size:.76rem; transition:all .15s; }}
  .controls button:hover {{ border-color:var(--accent); }}
  .controls button.active {{ background:var(--accent); color:#10130f;
    border-color:var(--accent); font-weight:600; }}
  .controls label.chk {{ font-size:.76rem; color:var(--muted); display:flex;
    align-items:center; gap:6px; cursor:pointer; }}
  .lbl {{ color:var(--muted); font-size:.74rem; }}

  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(330px,1fr)); gap:16px; }}

  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:10px;
    padding:16px 16px 14px; transition:border-color .15s, transform .15s; }}
  .card:hover {{ border-color:var(--accent); transform:translateY(-2px); }}
  .card.pearl {{ border-color:rgba(230,192,104,.55);
    box-shadow:0 0 0 1px rgba(230,192,104,.25), 0 8px 26px -18px rgba(230,192,104,.5); }}
  .card.pearl:hover {{ border-color:var(--gold); }}

  .card-head {{ display:flex; gap:14px; align-items:center; }}
  .ring {{ --p:0; width:62px; height:62px; border-radius:50%; flex:none;
    background:conic-gradient(var(--rc) calc(var(--p)*1%), var(--line) 0);
    display:grid; place-items:center; }}
  .ring span {{ width:48px; height:48px; border-radius:50%; background:var(--panel);
    display:grid; place-items:center; font-family:'Fraunces',serif;
    font-weight:900; font-size:1.25rem; }}
  .head-mid {{ flex:1; min-width:0; }}
  .head-mid h3 {{ font-family:'Fraunces',serif; font-weight:900; font-size:1.3rem;
    display:flex; align-items:center; gap:8px; flex-wrap:wrap; }}
  .pearl-badge {{ font-family:'IBM Plex Mono'; font-size:.6rem; font-weight:600;
    color:#1a1407; background:var(--gold); padding:3px 8px; border-radius:999px;
    letter-spacing:.06em; }}
  .delta {{ font-family:'IBM Plex Mono'; font-size:.62rem; font-weight:600;
    padding:2px 7px; border-radius:999px; }}
  .delta.up {{ background:rgba(110,231,168,.14); color:var(--good); }}
  .delta.down {{ background:rgba(240,139,122,.14); color:var(--bad); }}
  .company {{ color:var(--muted); font-size:.7rem; white-space:nowrap;
    overflow:hidden; text-overflow:ellipsis; }}
  .sector {{ color:var(--muted); font-size:.66rem; text-transform:uppercase;
    letter-spacing:.08em; margin-top:2px; }}
  .rec {{ font-size:.62rem; font-weight:600; padding:4px 9px; border-radius:999px;
    white-space:nowrap; letter-spacing:.05em; align-self:flex-end; }}
  .rec-buy {{ background:rgba(110,231,168,.14); color:var(--good); }}
  .rec-hold {{ background:rgba(232,201,92,.14); color:var(--mid); }}
  .rec-sell {{ background:rgba(240,139,122,.14); color:var(--bad); }}
  .rec-none {{ background:var(--panel-2); color:var(--muted); }}

  .cats {{ margin-top:14px; display:flex; flex-direction:column; gap:5px; }}
  .cat {{ display:flex; align-items:center; gap:8px; font-size:.64rem; }}
  .cat span {{ color:var(--muted); width:30px; letter-spacing:.05em; }}
  .cat b {{ width:24px; text-align:right; font-weight:500; }}
  .bar {{ flex:1; height:5px; background:var(--panel-2); border-radius:99px; overflow:hidden; }}
  .bar i {{ display:block; height:100%; border-radius:99px; }}

  .toggle {{ margin-top:12px; width:100%; background:var(--panel-2); color:var(--muted);
    border:1px solid var(--line); border-radius:6px; padding:6px; cursor:pointer;
    font-family:inherit; font-size:.7rem; }}
  .toggle:hover {{ color:var(--ink); border-color:var(--accent); }}
  .details {{ display:none; margin-top:12px; }}
  .card.open .details {{ display:block; }}
  .card.open .toggle {{ color:var(--ink); }}

  .note {{ font-size:.7rem; margin-bottom:6px; }}
  .good-t {{ color:var(--good); }} .bad-t {{ color:var(--bad); }} .flag-t {{ color:var(--mid); }}
  .news-block {{ border:1px solid var(--line); border-radius:6px;
    padding:9px 11px; margin-bottom:8px; background:var(--panel-2); }}
  .news-list {{ font-size:.68rem; color:var(--ink); margin:2px 0 8px 16px; }}
  .news-list li {{ margin-bottom:2px; }}
  .metrics {{ display:grid; grid-template-columns:1fr 1fr; gap:1px; background:var(--line);
    border:1px solid var(--line); border-radius:6px; overflow:hidden; margin-top:8px; }}
  .metric {{ background:var(--panel); padding:6px 9px; display:flex; flex-direction:column; }}
  .m-label {{ color:var(--muted); font-size:.6rem; text-transform:uppercase; letter-spacing:.04em; }}
  .m-value {{ font-size:.86rem; font-weight:500; }}
  .coverage {{ color:var(--muted); font-size:.66rem; margin-top:8px; }}

  .muted {{ color:var(--muted); }}
  code {{ background:var(--panel-2); padding:2px 6px; border-radius:4px; font-size:.85em; }}
  footer {{ color:var(--muted); font-size:.74rem; text-align:center; margin-top:56px;
    padding-top:22px; border-top:1px solid var(--line); }}
  .hidden {{ display:none !important; }}
</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <h1>Parel-screener<span class="dot">.</span></h1>
    <div class="stats">
      <div class="stat"><b>{count}</b><span>bedrijven gescoord</span></div>
      <div class="stat gold"><b>{pearls} ★</b><span>pareltjes</span></div>
      <div class="stat"><b>{median}</b><span>mediaan score</span></div>
      <div class="stat"><b>{date}</b><span>laatste run</span></div>
    </div>
    <div class="disclaimer"><strong>Geen financieel advies.</strong>
      Scores vergelijken bedrijven onderling op hun cijfers; een hoge score is
      geen koopsignaal en de AI toetst niets onafhankelijk. Doe altijd je eigen onderzoek.</div>
  </header>

  <section>
    <div class="section-label gold">Pareltjes <span class="count">over de hele linie sterk, zonder ernstige vlaggen</span></div>
    <div class="grid">{pearl_cards}</div>
  </section>

  <section>
    <div class="section-label">AI-duiding <span class="count">door Gemini, kritisch op de top</span></div>
    <div class="ranking-panel">{ranking}</div>
  </section>

  <section>
    <div class="section-label">Alle bedrijven <span class="count">{count} stuks</span></div>
    <div class="controls">
      <input type="text" id="search" placeholder="Zoek ticker of naam...">
      <select id="sector"><option value="">Alle sectoren</option>{sector_options}</select>
      <select id="minscore">
        <option value="0">Elke score</option>
        <option value="50">Score ≥ 50</option>
        <option value="70">Score ≥ 70</option>
      </select>
      <label class="chk"><input type="checkbox" id="pearlsOnly"> alleen parels ★</label>
      <span class="lbl">| Sorteer:</span>
      <button data-sort="score" class="active">Score</button>
      <button data-sort="delta">Δ vorige run</button>
      <button data-sort="pe">Laagste P/E</button>
      <button data-sort="roe">ROE</button>
      <button data-sort="growth">Groei</button>
      <button data-sort="mcap">Marktwaarde</button>
      <button data-sort="ticker">A–Z</button>
    </div>
    <div class="grid" id="grid">{cards}</div>
  </section>

  <footer>Gegenereerd door de S&amp;P 600 parel-screener · data via Yahoo Finance &amp; Finviz ·
    AI-duiding via Google Gemini · geen financieel advies</footer>
</div>

<script>
  const grid = document.getElementById('grid');
  const search = document.getElementById('search');
  const sectorSel = document.getElementById('sector');
  const minScoreSel = document.getElementById('minscore');
  const pearlsOnly = document.getElementById('pearlsOnly');
  const buttons = document.querySelectorAll('.controls button');

  function applyFilters() {{
    const q = search.value.trim().toLowerCase();
    const sec = sectorSel.value;
    const min = parseFloat(minScoreSel.value);
    const onlyPearls = pearlsOnly.checked;
    for (const card of grid.children) {{
      const okQ = !q || card.dataset.ticker.toLowerCase().includes(q)
                     || card.dataset.name.includes(q);
      const okS = !sec || card.dataset.sector === sec;
      const okM = parseFloat(card.dataset.score) >= min;
      const okP = !onlyPearls || card.dataset.pearl === '1';
      card.classList.toggle('hidden', !(okQ && okS && okM && okP));
    }}
  }}

  function sortCards(key) {{
    const cards = Array.from(grid.children);
    cards.sort((a, b) => {{
      if (key === 'ticker') return a.dataset.ticker.localeCompare(b.dataset.ticker);
      const av = parseFloat(a.dataset[key]); const bv = parseFloat(b.dataset[key]);
      if (key === 'pe') {{
        const ax = av > 0 ? av : 1e12; const bx = bv > 0 ? bv : 1e12;
        return ax - bx;
      }}
      return bv - av;
    }});
    cards.forEach(c => grid.appendChild(c));
  }}

  [search, sectorSel, minScoreSel, pearlsOnly].forEach(el =>
    el.addEventListener('input', applyFilters));
  buttons.forEach(btn => btn.addEventListener('click', () => {{
    buttons.forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    sortCards(btn.dataset.sort);
  }}));
</script>
</body>
</html>
"""
