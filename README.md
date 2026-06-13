# S&P 600 Parel-screener (v6)

Scoort alle bedrijven uit de S&P SmallCap 600 op vijf categorieën
(waardering, kwaliteit, groei, gezondheid, sentiment) door ze onderling te
vergelijken (percentielen, 37 metrics uit Yahoo Finance + Finviz),
markeert "pareltjes" die over de hele linie sterk zijn zonder ernstige
waarschuwingsvlaggen, laat Gemini de top kritisch duiden, en toont alles
in een browser-dashboard.

> **Geen financieel advies.** Onderzoekshulpmiddel: een hoge score is geen
> koopsignaal. Doe altijd je eigen onderzoek.

## Eerste keer

```bash
pip3 install -r requirements.txt
cp .env.example .env        # en vul je Gemini-key in
python3 main.py --limit 50  # snelle test
python3 main.py             # volledige run
```

## Runnen

```bash
python3 main.py             # volledige run (~600 bedrijven)
python3 main.py --refresh   # negeer de cache, alles vers
python3 main.py --no-ai     # zonder AI-duiding
python3 main.py --no-news   # zonder nieuws-analyse
python3 main.py --ai-only   # alleen AI + dashboard opnieuw (geen data ophalen)
python3 main.py --no-browser
```

## Daarna opvragen (zonder nieuwe run, geen internet nodig)

```bash
python3 query.py AAON            # volledige scorekaart van één bedrijf
python3 query.py AAON HCI INSW   # uitgebreide vergelijking naast elkaar
python3 query.py --top 20        # top 20 op score
python3 query.py --parels        # alleen de pareltjes
python3 query.py --vlaggen       # meeste waarschuwingen
python3 query.py --sector health # beste bedrijven per sector (deelnaam mag)
python3 query.py --beweging      # grootste stijgers/dalers sinds vorige run
```

Het dashboard (output/dashboard.html) kun je altijd opnieuw openen in je
browser, ook zonder nieuwe run.

## Hoe het werkt

1. **Tickers**: actuele S&P 600-lijst van Wikipedia; valt terug op
   `tickers_fallback.csv` (wordt na elke geslaagde run automatisch
   bijgewerkt). Dode tickers worden onthouden en overgeslagen.
2. **Data**: Yahoo Finance (parallel, 8 workers) + Finviz (parallel, 3
   workers, voorzichtig i.v.m. rate-limits). Beide bronnen vullen
   elkaars gaten aan. Cache: 12 uur (`cache/`).
3. **Score**: 37 metrics → percentiel t.o.v. de hele groep → 5
   gewogen categorieën → totaalscore 0-100. Plus sector-rang,
   waarschuwingsvlaggen en delta t.o.v. de vorige run (`output/history.json`).
4. **Parel**: totaal ≥ 65, geen categorie < 35, datadekking ≥ 60%,
   max 1 vlag. Criteria aanpasbaar in `config.py`.
5. **Nieuws** (`news_analyzer.py`): voor de top 25 wordt recent nieuws
   opgehaald (Yahoo, gratis; 6 uur cache) en door de LLM samengevat tot
   een sentimentscore (-1..1), kernpunten, risico's en kansen. Het
   sentiment weegt 15% mee in een gecombineerde totaalscore; zeer
   negatief sentiment geeft een waarschuwingsvlag. Uitzetten kan met
   `--no-news` of `NEWS_ENABLED = False` in `config.py`.
6. **AI**: top 40 naar Gemini (of Claude); die kiest en motiveert de
   definitieve pareltjes-ranking (incl. het nieuwssentiment).

## Tips

- De map `cache/` bewaart opgehaalde data 12 uur; kopieer hem mee als je
  het project verplaatst.
- Parel-criteria en categoriegewichten pas je aan in `config.py`.
- Geen AI-tekst gekregen (rate-limit)? `python3 main.py --ai-only`.
