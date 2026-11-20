# GramIQ – prototype op echte verkoopdata

Zelfde opzet als `GramIQ_Project_v2_Met_Dashboard` (simulator → MQTT → backend met
AI en database → React-dashboard), maar de klanten komen nu uit **878.503 echte
verkooptransacties** van een supermarkt, in plaats van uit verzonnen data.

**Wat betekent alles op het dashboard?** Zie [UITLEG_DASHBOARD.md](UITLEG_DASHBOARD.md).

## De dataset

**Supermarket Sales Data**, Kaggle: <https://www.kaggle.com/datasets/yapwh1208/supermarket-sales-data>
(groenten van één supermarkt, 1 juli 2020 – 30 juni 2023, 251 producten in 6 categorieën).

| Bestand | Inhoud |
|---|---|
| `data/annex1.csv` | productcode, naam, categorie |
| `data/annex2.csv` | **elke verkoop**: datum, tijdstip (tot op de ms), product, **kg**, prijs/kg, verkoop of retour, korting |
| `data/annex3.csv` | groothandelsprijs per product per dag |
| `data/annex4.csv` | verliespercentage (bederf) per product |

`annex2.csv` (52 MB) staat niet nog eens in `data/`: de code vindt hem vanzelf in
`real_data_prototype/archive (1)/`, waar de volledige download al in de repo zit. Een andere
map kiezen kan met de omgevingsvariabele `GRAMIQ_DATA`.

Wat we in de data zien:
* open elke dag van ±9u tot 22u; drukst rond 10–11u en 16–18u, in het weekend ±40% meer;
* een klant koopt meestal 0,3–0,7 kg (mediaan 0,44 kg); 461 retours, 47.000 verkopen met korting;
* 10 dagen zonder verkopen (o.a. Chinees Nieuwjaar 2021).

## De vier bakken

| Bak | Product (naam in de data) | Verkoopdagen | Gem. per dag | Verlies |
|---|---|---|---|---|
| 01 | Broccoli | 1.076 | 25,6 kg | 9,3% |
| 02 | Lotuswortel (*Net Lotus Root (1)*) | 1.057 | 25,7 kg | 5,5% |
| 03 | Aubergine (*Eggplant (2)*) | 1.022 | 13,3 kg | 6,1% |
| 04 | Groene paprika (*Wuhu Green Pepper (1)*) | 858 (vanaf 2021) | 32,8 kg | 5,7% |

Losse groenten die per kg verkocht worden, bijna elke dag in de verkoop. Stukgewicht
(bv. broccoli 400 g) en capaciteit van de bakken (10–15 kg) zijn **aannames**: die
staan niet in de data. Aanpassen kan in `ai/weegschaal_model.py` → `PRODUCTEN`.

## Wat is echt en wat is gesimuleerd?

| Echt (uit de data) | Gesimuleerd (`ai/weegschaal_model.py`) |
|---|---|
| tijdstip van elke aankoop | klant staat 2–15 min vóór het afrekenen aan de bak |
| gewicht van elke aankoop (kg) | dat gewicht wordt stuk per stuk uit de bak genomen |
| welke dagen een product verkocht werd | sensorruis, hand in de bak, iemand die leunt, wifi-uitval |
| groothandelsprijs en verlies | personeel dat bijvult (ochtend + als de bak onder 12–30% zakt) |

## Opstarten

**Snel: één commando.** Vanuit de hoofdmap van de repo: `python start.py` (of dubbelklik op
`start.bat` op Windows, `./start.sh` op macOS/Linux). Dat installeert alles in `.venv`, start de
MQTT-broker, de backend en de simulator, en opent het dashboard op http://127.0.0.1:8000.
Enkel Python 3.10+ is nodig. Zie de [README in de hoofdmap](../../README.md) voor alle opties.

**Handmatig, in vier terminals** (zo draait het op de winkel-PC):

1. **DATA**: staat al in de repo (zie hierboven).

2. **AI TRAINEN** (enkel als je de modellen wil vernieuwen; getrainde modellen zitten al in `ai/`):
   ```
   cd ai
   pip install -r requirements.txt
   python genereer_dataset.py   # ±9 min: 3 jaar weegschaalmetingen -> data/samples_echt.csv.gz + data/vraagprofiel.json
   python train_model.py        # ±1 min: AI 'wanneer is de bak leeg'  -> ai/model_leeg.joblib + .json
   python bestelmodel.py        # ±7 min: AI 'hoeveel bestellen'       -> ai/model_bestelling.joblib + .json
   python visualiseer.py        # optioneel: grafiek voor het verslag  -> ai/voorbeeld_voorspelling.png
   ```
   Heb je `data/samples_echt.csv.gz` al, dan kan je `genereer_dataset.py` overslaan
   wanneer je enkel opnieuw wil trainen.

   **Draagbare modellen.** Een `.joblib`-bestand laadt enkel met dezelfde scikit-learn-versie als
   waarmee getraind is (hier 1.5.1). Daarom schrijven de train-scripts ook een `.json`-versie weg
   (`ai/draagbaar_model.py`): dezelfde bomen als gewone JSON, met exact dezelfde voorspellingen
   (controle: `python draagbaar_model.py --controleer`), maar zonder scikit-learn en dus op elke
   Python-versie. De backend gebruikt die JSON.

3. **MQTT-BROKER** (terminal 1): `docker-compose up -d` (Mosquitto). Geen Docker?
   `python mqtt_broker.py` start een kleine broker in pure Python, enkel bereikbaar vanaf deze computer.

4. **BACKEND** (terminal 2):
   ```
   cd backend
   pip install -r requirements.txt
   uvicorn main:app --reload
   ```

5. **DASHBOARD**: de backend serveert de gebouwde versie (`frontend/dist`) zelf op
   http://127.0.0.1:8000. Aan het dashboard werken (terminal 3, Node.js nodig):
   ```
   cd frontend
   npm install
   npm run dev          # http://localhost:3000, met live herladen; /api gaat naar de backend
   npm run build        # nieuwe versie in frontend/dist (start.py doet dit zelf als Node.js er is)
   ```

6. **SIMULATOR** (terminal 4):
   ```
   cd simulator
   pip install -r requirements.txt
   python simulator.py
   ```
   Standaard: een week vanaf 1 juni 2023 (testperiode, nooit gezien door de AI),
   1 echte seconde = 1 gesimuleerde minuut. Opties: `--start "2023-03-11 08:30"`,
   `--dagen 3`, `--snelheid 300`, `--stil`, `--geen-mqtt`.
   Elke datum tussen 1 juli 2020 en 30 juni 2023 kan.

## AI 1 – wanneer is de bak leeg?

Zelfde aanpak als in v2 (`features.py`, Gradient Boosting met een foutmarge van 80%),
maar getraind op **echte vraag** en met een extra **vraagprofiel**: hoeveel er normaal
per minuut verkocht wordt, per product en weekdag (`data/vraagprofiel.json`, enkel uit
de trainingsperiode). Daarmee weet de AI dat er om 17u een piek komt, en of het vandaag
drukker is dan normaal (`drukte`).

* **Train**: jul 2020 – jun 2022 · **validatie**: jul – dec 2022 · **test**: jan – jun 2023 (ongezien)
* Label: aantal open minuten tot de bak leeg is als niemand bijvult (berekend uit de echte toekomstige verkopen).

| Testperiode jan–jun 2023 | AI | Baseline (gewicht ÷ verbruik) |
|---|---|---|
| Gemiddelde fout, alle momenten | 80 min | 123 min |
| Gemiddelde fout als de bak < 25% vol is | **50 min** | 81 min |
| Echte waarde binnen de foutmarge | 84% | – |

| Als de AI zegt… | …is de bak echt leeg na (mediaan) | helft van de gevallen tussen |
|---|---|---|
| 0–30 min | 22 min | 11 – 40 min |
| 30–60 min | 51 min | 33 – 80 min |
| 60–120 min | 102 min | 70 – 148 min |

De fout is groter dan in v2 (15 min): echte klanten komen in golven en soms koopt één
klant 5 kg in één keer. Dat is niet te voorspellen, maar wel eerlijk gemeten. De AI is
wel goed gekalibreerd: de voorspellingen kloppen gemiddeld en de marge is betrouwbaar.

## AI 2 – hoeveel moet er besteld worden? (#20)

`ai/bestelmodel.py`. De groothandel levert 's ochtends vroeg; op dat moment voorspelt
het model per product de verkoop van vandaag uit de verkopen tot en met gisteren
(vorige dagen, zelfde weekdag vorige weken, kortingen, prijs, inkoopprijs van vandaag).
Eén model voor de 57 producten met minstens 300 verkoopdagen.

* **verwacht** = mediaan · **veilig** = 80%-kwantiel (volstaat op ±80% van de dagen)
* **bestellen** = veilig ÷ (1 − verlies%) · **kost** = bestellen × inkoopprijs van vandaag

| Testperiode jan–jun 2023 | Gem. fout AI | Zelfde dag vorige week | Gem. 7 dagen | Bestelling volstaat |
|---|---|---|---|---|
| Alle 57 producten | **4,7 kg** | 6,6 kg | 5,5 kg | 78% van de dagen |
| Broccoli | 5,0 kg | 7,8 kg | 6,4 kg | 83% |
| Lotuswortel | 4,8 kg | 6,7 kg | 5,9 kg | 81% |
| Aubergine | 4,5 kg | 6,3 kg | 5,7 kg | 76% |
| Groene paprika | 6,7 kg | 10,9 kg | 9,0 kg | 84% |

Het dashboard toont het bestelvoorstel van de gesimuleerde dag, met wat er tot nu toe
echt verkocht is.

## Backend-API

Zelfde als v2 (met de SQLite-database van issue #6/#7, `data/gramiq.db`), plus:

* `GET /api/bestelling` → bestelvoorstel per bak voor de huidige (gesimuleerde) dag:
  `verwacht_kg`, `veilig_kg`, `verlies_pct`, `bestel_kg`, `inkoopprijs_per_kg`, `kost`,
  `verkocht_tot_nu_kg`
* `GET /api/bakken`, `/api/historiek/{bak}?uren=4`, `/api/metingen/{bak}`, `/api/producten`,
  `/api/weegschalen`, `/api/database`, `/api/model`
* `GET /api/status` → MQTT verbonden?, aantal ontvangen metingen, simulatietijd, welke modellen geladen zijn

**AI-lab** (zelf waarden invullen, gebruikt door het tabblad 🧪 in het dashboard):

* `GET /api/lab/leeg?bak=01&kg=4&tijd=16:30&weekdag=5&drukte=1.3&bijgevuld=08:30` → wanneer is de bak
  leeg, met marge, de simpele berekening ernaast, het verwachte verloop en wat het model als invoer zag.
  Het verbruik volgt uit het vraagprofiel × `drukte` (1 = een gewone dag voor die weekdag).
* `GET /api/lab/producten` → de 57 producten van het bestelmodel (de vier bakken eerst)
* `GET /api/lab/bestelling?product=102900005116714&datum=2023-06-01` → bestelvoorstel voor die dag,
  wat er echt verkocht werd, de fout, of de dag in de testperiode valt, en de vier weken ervoor

Alle routes zijn uit te proberen op http://127.0.0.1:8000/docs.

## Mappen

| Map | Inhoud |
|---|---|
| `ai/` | `echte_data.py` (inlezen), weegschaalmodel, features + vraagprofiel, datasetgenerator, beide modellen |
| `simulator/` | speelt echte verkopen af als weegschaalmetingen over MQTT |
| `backend/` | FastAPI + SQLite: metingen ontvangen, filteren, bewaren, voorspellen; serveert ook het dashboard |
| `frontend/` | React-dashboard (bakken, grafiek, bestelvoorstel, AI-lab); `dist/` is de gebouwde versie |
| `mqtt_broker.py` | mini MQTT-broker in pure Python, voor wie geen Docker/Mosquitto heeft |
| `data/` | dataset + gegenereerde bestanden |
