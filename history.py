"""
history.py
==========
Houdt de totaalscores van eerdere runs bij (output/history.json) zodat je
per bedrijf kunt zien of de score stijgt of daalt t.o.v. de vorige run.

Wat er gebeurt na elke run:
  1. annotate_deltas(): vergelijkt de nieuwe scores met de vorige run en
     zet per bedrijf "prev_total" en "delta" (kan None zijn bij een nieuw
     bedrijf of een eerste run).
  2. record_run(): slaat de nieuwe scores op in de historie (max
     HISTORY_MAX_RUNS runs, oudste eruit).

De historie blijft compact: alleen ticker -> totaalscore per run.
"""

import json
import os
from datetime import datetime

import config

HISTORY_JSON = os.path.join(config.OUTPUT_DIR, "history.json")
HISTORY_MAX_RUNS = 30  # ruim een maand aan dagelijkse runs


def _load_history():
    if not os.path.exists(HISTORY_JSON):
        return []
    try:
        with open(HISTORY_JSON, "r", encoding="utf-8") as f:
            runs = json.load(f)
        return runs if isinstance(runs, list) else []
    except Exception:
        return []


def annotate_deltas(scored):
    """
    Zet per bedrijf 'prev_total' en 'delta' op basis van de laatste
    opgeslagen run. Past de lijst in-place aan en geeft hem ook terug.
    """
    runs = _load_history()
    prev = runs[-1]["scores"] if runs else {}
    changed = 0
    for r in scored:
        prev_total = prev.get(r.get("ticker"))
        r["prev_total"] = prev_total
        if prev_total is not None and r.get("total") is not None:
            r["delta"] = round(r["total"] - prev_total, 1)
            if abs(r["delta"]) >= 0.05:
                changed += 1
        else:
            r["delta"] = None
    if prev:
        print(f"[historie] Vergeleken met vorige run "
              f"({runs[-1].get('date', '?')[:16]}); {changed} scores veranderd.")
    return scored


def record_run(scored):
    """Voeg de huidige run toe aan de historie en schrijf history.json."""
    scores = {r["ticker"]: r["total"] for r in scored
              if r.get("ticker") and r.get("total") is not None}
    if not scores:
        return

    runs = _load_history()

    # Overschrijf de laatste entry als die van vandaag is (meerdere runs
    # per dag vervuilen de historie anders).
    today = datetime.now().date().isoformat()
    if runs and runs[-1].get("date", "").startswith(today):
        runs[-1] = {"date": datetime.now().isoformat(), "scores": scores}
    else:
        runs.append({"date": datetime.now().isoformat(), "scores": scores})

    runs = runs[-HISTORY_MAX_RUNS:]
    try:
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        with open(HISTORY_JSON, "w", encoding="utf-8") as f:
            json.dump(runs, f)
        print(f"[historie] Run opgeslagen ({len(runs)} run(s) in historie).")
    except Exception as exc:
        print(f"[historie] Kon historie niet opslaan: {exc}")


def biggest_movers(scored, n=10):
    """De n grootste stijgers en dalers t.o.v. de vorige run."""
    movers = [r for r in scored if r.get("delta") is not None and r["delta"] != 0]
    movers.sort(key=lambda r: -r["delta"])
    risers = [r for r in movers if r["delta"] > 0][:n]
    fallers = [r for r in reversed(movers) if r["delta"] < 0][:n]
    return risers, fallers





def get_ticker_history_points(ticker, max_points=10):
    """
    Geeft een chronologische lijst van totaalscores terug voor een specifieke ticker.
    Gesorteerd van OUD naar NIEUW (perfect voor een grafiek van links naar rechts).
    Gelimiteerd tot de laatste 'max_points' runs om de sparkline compact te houden.
    """
    runs = _load_history()
    points = []
    
    # Loop door de runs (historie bevat runs van oud naar nieuw)
    for run in runs:
        scores = run.get("scores", {})
        if ticker in scores and scores[ticker] is not None:
            points.append(float(scores[ticker]))
            
    # Pak de meest recente x punten, maar behoud de chronologische volgorde
    return points[-max_points:]