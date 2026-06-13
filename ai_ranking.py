"""
ai_ranking.py
=============
De AI-laag van versie 6.

Anders dan in versie 5 sturen we niet alle ~600 bedrijven blind naar de AI,
maar eerst het scoremodel z'n werk laten doen en dan de TOP (standaard 40)
naar de AI sturen — mét hun categoriescores en waarschuwingsvlaggen.

De AI (Gemini gratis, of Claude) kiest daaruit de definitieve pareltjes,
mag gerust afwijken van de scorevolgorde, en moet per bedrijf ook het
risico en de "controleer zelf"-punten benoemen.

LET OP: dit is GEEN financieel advies; de AI toetst de cijfers niet
onafhankelijk. Behandel de ranking als startpunt voor eigen onderzoek.
"""

import os
import time

import config


# ---------------------------------------------------------------------------
# API-KEY DETECTIE
# ---------------------------------------------------------------------------
def _get_gemini_key():
    return os.environ.get("GOOGLE_GEMINI_API_KEY")


def _get_claude_key():
    return os.environ.get("ANTHROPIC_API_KEY")


def provider_available():
    if _get_gemini_key():
        return "gemini"
    if _get_claude_key():
        return "claude"
    return None


# ---------------------------------------------------------------------------
# COMPACTE TABEL VAN DE GESCOORDE TOP
# ---------------------------------------------------------------------------
def _fmt(value, decimals=2, pct=False, default="-"):
    if value is None:
        return default
    try:
        v = float(value)
        return f"{v * 100:.0f}%" if pct else f"{v:.{decimals}f}"
    except (TypeError, ValueError):
        return default


_TABLE_HEADER = (
    "ticker;sector;TOTAALSCORE;scoreDelta;waardering;kwaliteit;groei;gezondheid;"
    "sentiment;parel;vlaggen;fwdPE;ROE;ROIC;omzetgroei;EPSgr5j;schuld/EV;"
    "nettoSchuld/EBITDA;shortFloat;upside;analystRec;chg52w;vsSMA200;nieuwsSent"
)


def _row_for(r):
    cats = r.get("cat_scores", {})
    flags = r.get("flags", [])
    return ";".join([
        str(r.get("ticker") or "-"),
        (str(r.get("sector") or "-"))[:18],
        _fmt(r.get("total"), decimals=0),
        _fmt(r.get("delta"), decimals=1),
        _fmt(cats.get("waardering"), decimals=0),
        _fmt(cats.get("kwaliteit"), decimals=0),
        _fmt(cats.get("groei"), decimals=0),
        _fmt(cats.get("gezondheid"), decimals=0),
        _fmt(cats.get("sentiment"), decimals=0),
        "JA" if r.get("is_pearl") else "nee",
        (" | ".join(flags) if flags else "geen"),
        _fmt(r.get("forward_pe")),
        _fmt(r.get("roe"), pct=True),
        _fmt(r.get("roic"), pct=True),
        _fmt(r.get("revenue_growth"), pct=True),
        _fmt(r.get("eps_growth_5y"), pct=True),
        _fmt(r.get("debt_to_equity"), decimals=0),
        _fmt(r.get("net_debt_ebitda"), decimals=1),
        _fmt(r.get("short_pct_float"), pct=True),
        _fmt(r.get("upside"), pct=True),
        str(r.get("recommendation") or "-"),
        _fmt(r.get("change_52w"), pct=True),
        _fmt(r.get("sma200"), pct=True),
        _fmt(r.get("news_sentiment"), decimals=2),
    ])


def _build_table(scored):
    """Top AI_INPUT_TOP_N op totaalscore, met minimale datadekking."""
    candidates = [r for r in scored
                  if r.get("total") is not None and r.get("coverage", 0) >= 0.4]
    top = candidates[: config.AI_INPUT_TOP_N]
    lines = [_TABLE_HEADER] + [_row_for(r) for r in top]
    return "\n".join(lines), len(top)


# ---------------------------------------------------------------------------
# PROMPT
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = (
    "Je bent een nuchtere, kritische aandelenanalist. Je krijgt de best "
    "scorende small-cap bedrijven (S&P 600) uit een kwantitatief scoremodel. "
    "Per bedrijf zie je de totaalscore (0-100), de score per categorie "
    "(waardering, kwaliteit, groei, gezondheid, sentiment), of het aan alle "
    "parel-criteria voldoet, eventuele waarschuwingsvlaggen, en een aantal "
    "ruwe kerncijfers.\n\n"
    "Jouw taak: kies hieruit de ECHTE pareltjes — bedrijven met een sterke "
    "combinatie over de hele linie — en rangschik ze. Je mag afwijken van de "
    "scorevolgorde als je daar een goede cijfermatige reden voor hebt "
    "(bv. een hoge score die volledig op sentiment leunt, of een vlag die "
    "zwaarder weegt dan de score doet vermoeden).\n\n"
    "BELANGRIJKE REGELS:\n"
    "- Gebruik UITSLUITEND de gegevens in de tabel. Verzin niets.\n"
    "- Waar '-' staat is het cijfer onbekend; benoem dat als het relevant is.\n"
    "- Wees kritisch: een hoge score is geen garantie, een lage waardering "
    "kan een waardeval zijn.\n"
    "- Geef GEEN koopadvies. Formuleer als 'interessant om verder te "
    "onderzoeken' en benoem per bedrijf wat de lezer zelf moet controleren.\n"
    "- Schrijf in begrijpelijk Nederlands."
)


def _user_prompt(table, n):
    return (
        f"Hieronder de gescoorde topkandidaten, puntkomma-gescheiden.\n\n"
        f"Kies en rangschik de {n} meest overtuigende pareltjes. Formaat:\n"
        f"1. **TICKER** (sector, totaalscore) — 2 tot 4 zinnen: waarom dit "
        f"bedrijf opvalt, waar de kracht zit (welke categorieën), wat het "
        f"belangrijkste risico of de zwakke plek is, en wat de lezer zelf "
        f"nog moet controleren voor verder onderzoek.\n\n"
        f"Sluit af met een alinea **Algemene observaties**: wat valt op aan "
        f"de groep als geheel, en welke hoog-scorende bedrijven heb je "
        f"bewust NIET opgenomen en waarom.\n\n"
        f"Kolommen: {_TABLE_HEADER}\n"
        f"(scoreDelta = verandering van de totaalscore t.o.v. de vorige run; "
        f"nettoSchuld/EBITDA negatief = meer kas dan schuld; nieuwsSent = "
        f"LLM-sentiment over recent nieuws van -1 tot 1, '-' = niet geanalyseerd)"
        f"\n\nTABEL:\n{table}"
    )


# ---------------------------------------------------------------------------
# AI-CALLS
# ---------------------------------------------------------------------------
def _rank_with_gemini(table, n):
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=_get_gemini_key())

        # Probeer het "denkbudget" van gemini-2.5-flash op 0 te zetten zodat
        # alle output-tokens naar het zichtbare antwoord gaan (anders kan het
        # antwoord afgekapt raken). Valt terug op de standaard als de
        # geïnstalleerde SDK dit veld niet kent.
        try:
            gen_config = types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                max_output_tokens=config.AI_RANKING_MAX_TOKENS,
                temperature=0.4,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            )
        except Exception:
            gen_config = types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                max_output_tokens=config.AI_RANKING_MAX_TOKENS,
                temperature=0.4,
            )

        for attempt in range(config.AI_MAX_RETRIES + 1):
            try:
                resp = client.models.generate_content(
                    model=config.AI_GEMINI_MODEL,
                    contents=_user_prompt(table, n),
                    config=gen_config,
                )
                text = (resp.text or "").strip()
                if text:
                    return text
                raise ValueError("leeg antwoord van Gemini")
            except Exception as exc:
                msg = str(exc)
                if ("429" in msg or "leeg antwoord" in msg) and attempt < config.AI_MAX_RETRIES:
                    wait = config.AI_REQUEST_DELAY * (attempt + 2)
                    print(f"[ai] Probleem ({msg[:60]}); {wait}s wachten en opnieuw...")
                    time.sleep(wait)
                    continue
                raise
    except Exception as exc:
        print(f"[ai] Fout bij Gemini-ranking: {exc}")
        return None


def _rank_with_claude(table, n):
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=_get_claude_key())
        msg = client.messages.create(
            model=config.AI_MODEL_CLAUDE,
            max_tokens=config.AI_RANKING_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _user_prompt(table, n)}],
        )
        parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
        return "\n".join(parts).strip()
    except Exception as exc:
        print(f"[ai] Fout bij Claude-ranking: {exc}")
        return None


# ---------------------------------------------------------------------------
# HOOFDFUNCTIE
# ---------------------------------------------------------------------------
def run_ai_ranking(scored, use_ai=True):
    """Stuur de gescoorde top naar de AI; schrijft output/ai_ranking.txt."""
    provider = provider_available() if use_ai else None
    if not provider:
        print("\n[ai-ranking] Geen API-key gevonden -> AI-duiding overgeslagen.")
        print("[ai-ranking] Zet GOOGLE_GEMINI_API_KEY in je .env om dit te gebruiken.")
        return None

    table, used = _build_table(scored)
    if used == 0:
        print("\n[ai-ranking] Geen gescoorde bedrijven om te duiden.")
        return None

    n = min(config.AI_RANKING_TOP_N, used)
    print(f"\n[ai-ranking] Top {used} (op score) naar {provider.upper()} "
          f"sturen; AI kiest daaruit {n} pareltjes...")

    text = _rank_with_gemini(table, n) if provider == "gemini" else _rank_with_claude(table, n)
    if not text:
        print("[ai-ranking] Geen antwoord van de AI gekregen.")
        return None

    _write_ranking_file(text, used, provider)
    return text


def _write_ranking_file(text, used, provider):
    disclaimer = (
        "LET OP: dit is GEEN financieel advies. Een AI-model heeft de best "
        "scorende kandidaten uit een kwantitatief model kritisch bekeken en "
        "een ranking gemaakt van bedrijven om ZELF verder te onderzoeken. "
        "De AI toetst de cijfers niet onafhankelijk en kan zich vergissen.\n"
    )
    header = f"AI-PARELTJES door {provider.upper()} — gekozen uit de top {used}\n"
    try:
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        with open(config.RANKING_TXT, "w", encoding="utf-8") as f:
            f.write(disclaimer + "\n" + header + "=" * 70 + "\n\n" + text + "\n")
        print(f"[ai-ranking] Opgeslagen -> {config.RANKING_TXT}")
    except Exception as exc:
        print(f"[ai-ranking] Kon ranking niet opslaan: {exc}")
