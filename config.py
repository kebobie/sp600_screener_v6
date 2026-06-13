"""
config.py
=========
Alle instellingen op één plek. Pas hier dingen aan zonder de rest van de
code te hoeven openen.
"""

import os

# ---------------------------------------------------------------------------
# 1. ALGEMEEN / BESTANDEN
# ---------------------------------------------------------------------------
# Niet meer in gebruik — Wikipedia wordt nu direct in data_fetcher.py gescraped
# WIKI_URL = "https://raw.githubusercontent.com/..."
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

OUTPUT_DIR = "output"
CACHE_DIR = "cache"

RESULTS_CSV = os.path.join(OUTPUT_DIR, "sp600_results.csv")
SCORES_JSON = os.path.join(OUTPUT_DIR, "scores.json")
RANKING_TXT = os.path.join(OUTPUT_DIR, "ai_ranking.txt")
DASHBOARD_HTML = os.path.join(OUTPUT_DIR, "dashboard.html")

TICKERS_FALLBACK_CSV = "tickers_fallback.csv"

# ---------------------------------------------------------------------------
# 2. DATA OPHALEN
# ---------------------------------------------------------------------------
REQUEST_DELAY_SECONDS = 0.5   # pauze tussen Yahoo-requests
MAX_RETRIES = 2               # nieuwe pogingen per ticker bij een fout
CACHE_TTL_HOURS = 12          # hoe lang gecachte data vers blijft

# ---------------------------------------------------------------------------
# 3. SCOREMODEL
# ---------------------------------------------------------------------------
# Elk bedrijf wordt per cijfer vergeleken met ALLE andere bedrijven
# (percentielen). Daarna worden de cijfers gebundeld in vijf categorieën.
# De gewichten hieronder bepalen hoe zwaar elke categorie meetelt in de
# totaalscore (samen 100).
CATEGORY_WEIGHTS = {
    "waardering": 25,   # is het aandeel goedkoop t.o.v. de rest?
    "kwaliteit":  25,   # verdient het bedrijf goed geld? (ROE, marges)
    "groei":      15,   # groeien omzet en winst?
    "gezondheid": 20,   # balans: schuld, liquiditeit, kasstroom
    "sentiment":  15,   # analisten, insiders, short interest, momentum
}

# ---------------------------------------------------------------------------
# 4. PARELTJES
# ---------------------------------------------------------------------------
# Een "parel" is een bedrijf dat over de HELE linie goed scoort:
#   - totaalscore minstens PEARL_MIN_TOTAL
#   - geen enkele categorie onder PEARL_MIN_CATEGORY (geen fatale zwakte)
#   - voldoende data beschikbaar (PEARL_MIN_COVERAGE)
#   - hooguit PEARL_MAX_FLAGS waarschuwingsvlaggen
PEARL_MIN_TOTAL = 65
PEARL_MIN_CATEGORY = 35
PEARL_MIN_COVERAGE = 0.60
PEARL_MAX_FLAGS = 1

# ---------------------------------------------------------------------------
# 5. AI-DUIDING (Gemini gratis, of Claude)
# ---------------------------------------------------------------------------
# De best scorende AI_INPUT_TOP_N bedrijven gaan (met al hun scores en
# vlaggen) naar de AI, die daaruit de definitieve pareltjes-ranking kiest
# en per bedrijf een kritische redenering schrijft.
AI_INPUT_TOP_N = 40       # hoeveel bedrijven de AI te zien krijgt
AI_RANKING_TOP_N = 12     # hoeveel er in de eindranking komen

# --- Gemini (gratis) --- aanrader
# Actueel gratis model (juni 2026): "gemini-2.5-flash".
# LET OP: gemini-1.5-* en gemini-2.0-* zijn uitgefaseerd en werken NIET meer.
AI_GEMINI_MODEL = "gemini-2.5-flash"

# --- Claude (betalend) ---
AI_MODEL_CLAUDE = "claude-sonnet-4-6"

# --- Algemeen ---
AI_RANKING_MAX_TOKENS = 6000   # ruimte voor een uitgebreide ranking
AI_REQUEST_DELAY = 4           # seconden pauze bij een rate limit
AI_MAX_RETRIES = 2             # nieuwe pogingen bij een rate limit (429)

# ---------------------------------------------------------------------------
# 6. NIEUWS-ANALYSE (News Engine)
# ---------------------------------------------------------------------------
# Na het fundamentele scoren wordt voor de top NEWS_TOP_N bedrijven recent
# nieuws opgehaald (Yahoo Finance, gratis) en door de LLM samengevat tot een
# sentimentscore, kernpunten, risico's en kansen. Het sentiment wordt
# gemengd met de fundamentele score tot een gecombineerde totaalscore.
NEWS_ENABLED = True            # hoofdschakelaar (CLI: --no-news om uit te zetten)
NEWS_TOP_N = 25                # nieuws analyseren voor de top N op score
NEWS_CACHE_TTL_HOURS = 6       # hoe lang nieuws + analyse vers blijven
NEWS_MAX_ARTICLES = 10         # max artikelen per bedrijf naar de LLM
NEWS_MIN_ARTICLES = 2          # minder relevante artikelen -> geen analyse
NEWS_MAX_AGE_DAYS = 14         # ouder nieuws telt niet mee
NEWS_WEIGHT = 0.15             # aandeel van nieuws in de gecombineerde score
NEWS_FLAG_THRESHOLD = -0.4     # sentiment hieronder -> waarschuwingsvlag

# Eigen model voor de nieuws-analyse: Flash-Lite heeft een 4x ruimere
# gratis daglimiet (±15/min, ±1000/dag) dan gemini-2.5-flash én deelt
# geen dagquotum met de ranking-stap. Valt automatisch terug op
# AI_GEMINI_MODEL als dit model ooit verdwijnt.
NEWS_AI_MODEL = "gemini-2.5-flash-lite"
NEWS_AI_MODEL_CLAUDE = "claude-haiku-4-5"  # goedkope Claude-fallback (~$0,20/run)

NEWS_AI_MAX_TOKENS = 1500      # ruimte voor de JSON-analyse per bedrijf
NEWS_AI_DELAY = 6              # sec pauze tussen LLM-calls (vrije tier)
NEWS_AI_MAX_RETRIES = 3        # nieuwe pogingen bij een rate limit (429)
NEWS_FETCH_WORKERS = 3         # gelijktijdige nieuws-fetches (Yahoo)
# --------------------------------------------------------------------------
# 6. PORTFOLIO STRATEGIE & HARDE FILTERS (Versie 6+ Uitbreiding)
# --------------------------------------------------------------------------
STRATEGY_MIN_TREND_SMA = True       # Harde eis: Koers moet boven SMA 200 liggen
STRATEGY_MAX_SHORT_FLOAT = 0.15     # Maximaal 15% short float (tegen de 'lijken in de kast')
STRATEGY_EARNINGS_BUFFER_DAYS = 10  # Filter bedrijven die binnen 10 dagen cijfers publiceren