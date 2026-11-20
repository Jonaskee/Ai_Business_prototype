"""
Inlezen van de echte verkoopdata (Kaggle "Supermarket Sales Data", groenten
van één supermarkt, 1 juli 2020 - 30 juni 2023).

Bestanden in data/:
  annex1.csv  Item Code, Item Name, Category Code, Category Name       (251 producten)
  annex2.csv  Date, Time, Item Code, Quantity Sold (kilo),
              Unit Selling Price (RMB/kg), Sale or Return, Discount (Yes/No)   (878.503 transacties)
  annex3.csv  Date, Item Code, Wholesale Price (RMB/kg)                 (groothandelsprijs per dag)
  annex4.csv  Item Code, Item Name, Loss Rate (%)                       (verlies/bederf per product)

Gebruikt door de simulator, de dataset-generator, het bestelmodel en de backend.
"""
from __future__ import annotations

import os
from functools import lru_cache

import pandas as pd

HIER = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HIER, "..", "data")

# Waar de CSV-bestanden gezocht worden, in deze volgorde:
#   1. de map in de omgevingsvariabele GRAMIQ_DATA (optioneel)
#   2. data/ van dit prototype
#   3. real_data_prototype/archive (1)/: de volledige Kaggle-download zit al in de repo,
#      dus wie de repo clonet hoeft annex2.csv (52 MB) niet zelf te downloaden of te kopiëren.
DATA_MAPPEN = [m for m in (os.environ.get("GRAMIQ_DATA"), DATA,
                           os.path.join(HIER, "..", "..", "archive (1)")) if m]

# periodes voor training / validatie / test van de AI (nooit overlappen)
TRAIN_TOT = "2022-07-01"   # train: 2020-07-01 .. 2022-06-30
VAL_TOT = "2023-01-01"     # validatie: 2022-07-01 .. 2022-12-31
#                            test: 2023-01-01 .. 2023-06-30 (volledig ongezien)


def _pad(naam: str) -> str:
    for m in DATA_MAPPEN:
        p = os.path.join(m, naam)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(
        f"{naam} niet gevonden in {', '.join(os.path.abspath(m) for m in DATA_MAPPEN)}. Download de dataset "
        f"'Supermarket Sales Data' van Kaggle (kaggle.com/datasets/yapwh1208/supermarket-sales-data) "
        f"en zet annex1-4.csv in de map data/.")


@lru_cache(maxsize=1)
def transacties() -> pd.DataFrame:
    """Alle transacties met een echte tijdstempel `ts` (kolommen in het Nederlands)."""
    df = pd.read_csv(_pad("annex2.csv"))
    df = df.rename(columns={
        "Item Code": "item_code", "Quantity Sold (kilo)": "kg",
        "Unit Selling Price (RMB/kg)": "prijs", "Sale or Return": "soort", "Discount (Yes/No)": "korting",
    })
    df["ts"] = pd.to_datetime(df["Date"] + " " + df["Time"], format="ISO8601")
    df["korting"] = df["korting"].eq("Yes")
    df["is_retour"] = df["soort"].eq("return")
    return df[["ts", "item_code", "kg", "prijs", "korting", "is_retour"]].sort_values("ts", kind="stable").reset_index(drop=True)


def verkopen_per_bak(producten: dict, van=None, tot=None) -> pd.DataFrame:
    """Verkopen (geen retours) van de producten in de bakken: ts, bak_id, gram.

    Retours (461 van de 878.503 rijen) gaan terug naar de kassa, niet in de bak,
    dus die tellen niet mee voor het gewicht op de weegschaal.
    """
    df = transacties()
    code_naar_bak = {p["item_code"]: bak for bak, p in producten.items()}
    d = df[df.item_code.isin(code_naar_bak) & ~df.is_retour]
    if van is not None:
        d = d[d.ts >= pd.Timestamp(van)]
    if tot is not None:
        d = d[d.ts < pd.Timestamp(tot)]
    out = pd.DataFrame({"ts": d.ts, "bak_id": d.item_code.map(code_naar_bak), "gram": d.kg * 1000.0})
    return out.reset_index(drop=True)


@lru_cache(maxsize=1)
def productinfo() -> pd.DataFrame:
    """Per item_code: naam, categorie en verliespercentage."""
    a1 = pd.read_csv(_pad("annex1.csv")).rename(columns={
        "Item Code": "item_code", "Item Name": "naam", "Category Name": "categorie"})
    a4 = pd.read_csv(_pad("annex4.csv"), encoding="utf-8-sig").rename(columns={
        "Item Code": "item_code", "Loss Rate (%)": "verlies_pct"})
    return a1[["item_code", "naam", "categorie"]].merge(a4[["item_code", "verlies_pct"]], on="item_code", how="left")


@lru_cache(maxsize=1)
def groothandelsprijzen() -> pd.DataFrame:
    a3 = pd.read_csv(_pad("annex3.csv")).rename(columns={
        "Date": "datum", "Item Code": "item_code", "Wholesale Price (RMB/kg)": "inkoopprijs"})
    a3["datum"] = pd.to_datetime(a3["datum"])
    return a3.groupby(["datum", "item_code"], as_index=False).inkoopprijs.mean()


@lru_cache(maxsize=1)
def dagverkopen() -> pd.DataFrame:
    """Netto verkochte kg per product per dag (verkoop - retour), plus kortingsaandeel en prijs."""
    df = transacties().copy()
    df["datum"] = df.ts.dt.normalize()
    df["netto_kg"] = df.kg.where(~df.is_retour, -df.kg)
    df["korting_kg"] = df.kg.where(df.korting & ~df.is_retour, 0.0)
    df["omzet"] = df.kg * df.prijs * (~df.is_retour)
    g = df.groupby(["item_code", "datum"]).agg(
        kg=("netto_kg", "sum"), korting_kg=("korting_kg", "sum"), omzet=("omzet", "sum"),
        transacties=("kg", "size")).reset_index()
    g["korting_aandeel"] = (g.korting_kg / g.kg.where(g.kg > 0)).fillna(0).clip(0, 1)
    g["verkoopprijs"] = (g.omzet / g.kg.where(g.kg > 0)).fillna(0)
    return g.drop(columns=["korting_kg", "omzet"])
