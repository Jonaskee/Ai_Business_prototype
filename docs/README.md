# GramIQ winkelsimulatie

Een 3D-winkel die een echte verkoopdag uit de dataset afspeelt, twee keer tegelijk:
één winkel met GramIQ (weegschalen, dashboard en een AI-bijvulplan) en één zonder.

## Openen

**Online:** <https://jonaskee.github.io/Ai_Business_prototype/>
(staat ook op <https://boulezlouis.github.io/Ai_Business_prototype/> zodra Pages daar aanstaat, zie hieronder)

**Lokaal:** clone de repo en dubbelklik op `docs/index.html`. Er is niets te installeren.
Zonder internet werkt alles; alleen het lettertype valt dan terug op een standaardlettertype.

## GitHub Pages aanzetten (eenmalig, door de eigenaar van de repo)

1. Ga op GitHub naar **Settings → Pages**.
2. Kies bij **Source** voor **Deploy from a branch**.
3. Kies branch **main** en map **/docs**, en klik **Save**.
4. Na ongeveer een minuut staat de pagina op de link hierboven.

## Wat je ziet

| Onderdeel | Uitleg |
|---|---|
| 3D-winkel | Vier bakken op een weegschaal. Sleep om te draaien, klik op een bak om die te volgen. De plaat onder de bak kleurt groen, oranje of rood volgens de status. |
| Bijvulplan | Welke bak wanneer aan de beurt is en hoeveel kilo er mee moet uit het magazijn, in volgorde. |
| Dashboard | Per bak: gewicht, verbruik, "leeg om" met foutmarge, en wat de AI voor vandaag verwachtte. |
| De hele dag vooruit | Gemeten gewicht tot nu, verwacht verloop tot 22u en elke bijvulbeurt. |
| Dezelfde klanten, twee winkels | Live stand van gemiste klanten, gemiste kg, minuten met een lege bak en bijvulbeurten. |

Bovenaan kies je de dag (drukste dag van de testperiode, een gewone zaterdag of een rustige
weekdag), de snelheid en welke winkel je ziet.

## Het spel: jij bent de medewerker

Klik op **🎮 Speel zelf**. Er komt een derde winkel bij met exact dezelfde klanten, maar daar vult
niemand vanzelf bij: jij stuurt de medewerker. Klik op een bak in 3D, op een van de vier knoppen of
druk <kbd>1</kbd>–<kbd>4</kbd>. De medewerker haalt een krat in het magazijn (1 tot 2,5 minuten) en vult
de bak bij. Er is maar één medewerker: geef je een tweede opdracht, dan komt die erna.

- **Met weegschalen**: je ziet gewicht, % vol en de voorspelling *leeg om …*, zoals met GramIQ.
- **Blind**: je ziet enkel de winkel, zoals vandaag zonder GramIQ.

Om 22u volgt de uitslag naast de AI en de winkel zonder GramIQ. Winnaar: de minste klanten die
niet alles vonden; bij gelijkstand de minste bijvulbeurten. Met **Kopieer je score** daag je
iemand anders uit.

## Wat is echt en wat is aangenomen

**Echt**
- Tijdstip en gewicht van elke aankoop van de gekozen dag (Kaggle "Supermarket Sales Data").
- De dagvoorspelling per product komt van het bestelmodel (`ai/bestelmodel.py`), opnieuw
  getraind met dezelfde code, data en instellingen.
- De statusregels zijn die van `backend/main.py`; de foutmarge gebruikt de factoren uit
  `ai/model_rapport.json`.

**Aangenomen, zoals in `ai/weegschaal_model.py`**
- Grootte van een bak (10 tot 15 kg) en gewicht per stuk.
- Een klant staat 2 tot 15 minuten vóór het afrekenen aan de bak.
- Zonder GramIQ merkt het personeel een bak op onder 12 tot 30% en komt het meestal 5 tot 25
  minuten later.

**Eigen aannames van deze simulatie**
- Met het plan begint één medewerker binnen 1 tot 4 minuten nadat een bak aan de beurt is.
  Deze aanname bepaalt het grootste deel van het verschil tussen de twee winkels.
- Een bak komt op het plan als die binnen 45 minuten leeg is en minder dan 60% vol, of als
  die onder 25% zakt.
- De voorspelling "leeg om" is een vereenvoudigde versie die in de browser draait
  (vraagprofiel per uur maal de drukte van het laatste uur), niet het getrainde model.
- De weegschaal zelf is gesimuleerd. Met echte weegschalen is nog niet getest.

## Bestanden

| Bestand | Inhoud |
|---|---|
| `index.html` | De hele pagina: opmaak, simulatie en de ingebouwde data van de drie dagen. |
| `three.min.js` | three.js r128 voor het 3D-beeld (MIT-licentie, © three.js authors). |
