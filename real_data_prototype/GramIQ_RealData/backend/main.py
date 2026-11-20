from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import paho.mqtt.client as mqtt
import atexit
import json
import math
import os
import sys
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# gedeelde code (ruisfilter + features + model) staat in ../ai
HIER = os.path.dirname(os.path.abspath(__file__))
AI_MAP = os.path.join(HIER, "..", "ai")
DASHBOARD_MAP = os.path.join(HIER, "..", "frontend", "dist")   # gebouwd React-dashboard (npm run build)
sys.path.insert(0, AI_MAP)
from draagbaar_model import laad_pakket  # noqa: E402
from features import (BakHistoriek, MAX_HORIZON_MIN, Vraagprofiel, laad_vraagprofiel,  # noqa: E402
                      pas_marges_toe, tel_open_minuten_op)
from weegschaal_model import OPENING_UUR, PRODUCT_CODE, PRODUCTEN, SLUITING_UUR, _ts  # noqa: E402
from database import Database  # noqa: E402


@asynccontextmanager
async def levensloop(_app):
    yield
    db.sluit()


app = FastAPI(title="GramIQ backend", lifespan=levensloop)

# CORS toegevoegd zodat onze React frontend mag communiceren met deze backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------- AI-model
# laad_pakket gebruikt de draagbare JSON-versie (werkt met elke scikit-learn-versie)
# en valt terug op het .joblib-bestand als dat net opnieuw getraind is.
MODEL = None
MODEL_BRON = None
RAPPORT = None
try:
    MODEL, MODEL_BRON = laad_pakket("model_leeg", AI_MAP)
    with open(os.path.join(AI_MAP, "model_rapport.json")) as f:
        RAPPORT = json.load(f)
    print(f"AI-model geladen (ai/{MODEL_BRON})")
except Exception as e:  # model ontbreekt
    MODEL = None
    print(f"!! AI-model niet geladen ({e}). Gebruik baseline. "
          f"Train opnieuw met: cd ai && python genereer_dataset.py && python train_model.py")

PROFIELEN = laad_vraagprofiel()   # data/vraagprofiel.json (gemaakt door ai/genereer_dataset.py)
print(f"Vraagprofiel geladen voor bakken: {sorted(PROFIELEN) or 'geen'}")

# ---------------------------------------------------------------- bestelmodel (#20)
BESTEL = None
try:
    import bestelmodel
    import echte_data
    _bestel_model, _bestel_bron = laad_pakket("model_bestelling", AI_MAP)
    BESTEL = {"model": _bestel_model, "bron": _bestel_bron}
    with open(os.path.join(AI_MAP, "bestel_rapport.json")) as f:
        BESTEL["rapport"] = json.load(f)
    print(f"Bestelmodel geladen (ai/{_bestel_bron}); dagcijfers voorbereiden ...")
    BESTEL["features"] = bestelmodel.bouw_features().set_index(["item_code", "datum"]).sort_index()
    BESTEL["cache"] = {}
except Exception as e:
    print(f"!! Bestelmodel niet geladen ({e}). Train met: cd ai && python bestelmodel.py")
    BESTEL = None

# ---------------------------------------------------------------- database
db = Database()
if db.vul_standaard(PRODUCTEN, PRODUCT_CODE):
    print("Nieuwe database aangemaakt met de standaard producten en weegschalen.")
print(f"Database: {db.pad}")
atexit.register(db.sluit)

lock = threading.Lock()
config = {}           # weegschaal_id -> product + tarra (uit de database)
historiek = {}        # bak_id -> BakHistoriek
laatste_metingen = {}  # bak_id -> dict voor het dashboard
laatste_voorspel_minuut = {}

TIJD_FORMAAT = "%Y-%m-%d %H:%M:%S"


def herlaad_config():
    """Leest weegschalen + gekoppelde producten opnieuw uit de database."""
    global config
    config = db.weegschaal_config()


def product_van(c: dict) -> dict:
    """Database-rij -> product-dict zoals BakHistoriek het verwacht."""
    return {"naam": c["naam"], "stuk_gewicht": c["stuk_gewicht_gram"],
            "capaciteit": c["capaciteit_gram"], "stuk_sd": c["stuk_sd_gram"], "tros": bool(c["per_tros"])}


def nieuwe_historiek(bak_id: str) -> BakHistoriek:
    c = config[bak_id]
    return BakHistoriek(bak_id, product=product_van(c), product_code=c["ai_code"],
                        profiel=PROFIELEN.get(bak_id))


def voorspel(f: dict) -> dict:
    if MODEL is not None and f["product_code"] is not None:
        X = pd.DataFrame([f])[MODEL["features"]]
        m = min(max(float(MODEL["hoofd"].predict(X)[0]), 0.0), MAX_HORIZON_MIN)
        lo, hi = pas_marges_toe([m], MODEL["marges"])
        lo, hi = float(lo[0]), float(hi[0])
        methode = "AI (gradient boosting)"
    else:
        m = lo = hi = f["baseline_min"]
        methode = "baseline (gewicht / verbruik)"
    m = min(max(m, 0.0), MAX_HORIZON_MIN)
    lo = min(max(min(lo, m), 0.0), MAX_HORIZON_MIN)
    hi = min(max(hi, m), MAX_HORIZON_MIN)
    return {"minuten": round(m, 1), "minuten_min": round(lo, 1), "minuten_max": round(hi, 1), "methode": methode}


def bepaal_status(pct: float, minuten):
    if pct < 3:
        return "leeg"
    if (minuten is not None and minuten <= 15) or pct < 10:
        return "kritiek"
    if (minuten is not None and minuten <= 45) or pct < 25:
        return "laag"
    return "ok"


def dashboard_info(bak_id: str, h: BakHistoriek, ruw: float, timestamp: str) -> dict:
    c = config[bak_id]
    g = max(h.gefilterd, 0.0)
    return {
        "bak_id": bak_id,
        "product": c["naam"],
        "gewicht_ruw_gram": ruw,
        "gewicht_gram": round(g),
        "pct_vol": round(100 * g / c["capaciteit_gram"], 1),
        "stuks": int(round(g / c["stuk_gewicht_gram"])),
        "capaciteit_gram": c["capaciteit_gram"],
        "timestamp": timestamp,
    }


def verwerk(data: dict):
    bak_id = str(data.get("bak_id", "")).strip().zfill(2)
    if not str(data.get("bak_id", "")).strip() or "gewicht_gram" not in data:
        return
    ts = datetime.strptime(data["timestamp"], TIJD_FORMAAT)
    t = _ts(ts)
    ruw = float(data["gewicht_gram"])
    with lock:
        if bak_id not in config:
            herlaad_config()            # misschien net in de database toegevoegd
        c = config.get(bak_id)
        if c is None:
            db.registreer_onbekende_weegschaal(bak_id, data["timestamp"])
            herlaad_config()
            print(f"Nieuwe weegschaal {bak_id} geregistreerd; koppel er nog een product aan.")
            return
        if not c["actief"] or c["product_id"] is None:
            return                      # nog geen product gekoppeld -> niets te berekenen

        h = historiek.get(bak_id)
        if h is None or (h.laatste_t is not None and t < h.laatste_t - 60):
            # nieuwe bak of simulator herstart -> opnieuw beginnen
            h = historiek[bak_id] = nieuwe_historiek(bak_id)
            laatste_voorspel_minuut.pop(bak_id, None)
        h.voeg_toe(t, ruw - c["tarra_gram"])

        info = laatste_metingen.get(bak_id, {})
        info.update(dashboard_info(bak_id, h, data["gewicht_gram"], data["timestamp"]))
        db.bewaar_meting(bak_id, c["product_id"], data["timestamp"], ruw,
                         round(max(h.gefilterd, 0.0), 1), info["pct_vol"], info["stuks"])

        # voorspelling 1x per gesimuleerde minuut
        minuut = int(t // 60)
        if laatste_voorspel_minuut.get(bak_id) != minuut:
            laatste_voorspel_minuut[bak_id] = minuut
            f = h.features()
            if f is None:
                info["voorspelling"] = None
            else:
                v = voorspel(f)
                v["leeg_om"] = tel_open_minuten_op(t, v["minuten"]).strftime("%Y-%m-%d %H:%M")
                v["leeg_vroegst"] = tel_open_minuten_op(t, v["minuten_min"]).strftime("%H:%M")
                v["leeg_laatst"] = tel_open_minuten_op(t, v["minuten_max"]).strftime("%H:%M")
                v["verbruik_g_per_min"] = round(f["verbruik_30"], 1)
                info["voorspelling"] = v
                db.bewaar_voorspelling(bak_id, ts.strftime("%Y-%m-%d %H:%M"), v)
        vm = info.get("voorspelling")
        info["status"] = bepaal_status(info["pct_vol"], vm["minuten"] if vm else None)
        laatste_metingen[bak_id] = info


def herstel_uit_database():
    """Na een herstart: filter, verbruik en dashboard terugzetten uit de opgeslagen metingen."""
    herlaad_config()
    for bak_id, c in config.items():
        if not c["actief"] or c["product_id"] is None:
            continue
        rijen = db.laatste_dag_metingen(bak_id)
        if not rijen:
            continue
        h = historiek[bak_id] = nieuwe_historiek(bak_id)
        for r in rijen:
            h.voeg_toe(_ts(datetime.strptime(r["tijdstip"], TIJD_FORMAAT)), r["gewicht_ruw_gram"] - c["tarra_gram"])
        laatste = rijen[-1]
        info = dashboard_info(bak_id, h, laatste["gewicht_ruw_gram"], laatste["tijdstip"])
        v = db.laatste_voorspelling(bak_id)
        if v and v["tijdstip"] == laatste["tijdstip"][:16]:
            t = _ts(datetime.strptime(v["tijdstip"], "%Y-%m-%d %H:%M"))
            info["voorspelling"] = {
                "minuten": v["minuten"], "minuten_min": v["minuten_min"], "minuten_max": v["minuten_max"],
                "methode": v["methode"], "leeg_om": v["leeg_om"],
                "leeg_vroegst": tel_open_minuten_op(t, v["minuten_min"]).strftime("%H:%M"),
                "leeg_laatst": tel_open_minuten_op(t, v["minuten_max"]).strftime("%H:%M"),
                "verbruik_g_per_min": v["verbruik_g_per_min"],
            }
            laatste_voorspel_minuut[bak_id] = int(t // 60)
        else:
            info["voorspelling"] = None
        vm = info["voorspelling"]
        info["status"] = bepaal_status(info["pct_vol"], vm["minuten"] if vm else None)
        laatste_metingen[bak_id] = info
        print(f"  Bak {bak_id} hersteld uit database ({len(rijen)} metingen van {laatste['tijdstip'][:10]})")


# ---------------------------------------------------------------- MQTT
MQTT_STATUS = {"verbonden": False, "broker": None, "berichten": 0}


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        MQTT_STATUS["verbonden"] = True
        client.subscribe("gramiq/weegschaal/#")   # na elke (her)verbinding opnieuw abonneren
        print(f"MQTT verbonden met {MQTT_STATUS['broker']}, luistert op gramiq/weegschaal/#")
    else:
        print(f"MQTT-broker weigert de verbinding (code {rc})")


def on_disconnect(client, userdata, rc):
    if MQTT_STATUS["verbonden"]:
        print("MQTT-verbinding verbroken, opnieuw proberen ...")
    MQTT_STATUS["verbonden"] = False


def on_message(client, userdata, msg):
    MQTT_STATUS["berichten"] += 1
    try:
        verwerk(json.loads(msg.payload.decode()))
    except Exception as e:
        print(f"Fout bij verwerken bericht: {e}")


def start_mqtt():
    host = os.environ.get("MQTT_HOST", "localhost")
    poort = int(os.environ.get("MQTT_POORT", "1883"))
    MQTT_STATUS["broker"] = f"{host}:{poort}"
    mqtt_client = mqtt.Client()
    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message
    mqtt_client.reconnect_delay_set(min_delay=1, max_delay=10)
    try:
        # blijft proberen tot de broker er is (bv. als die later opstart dan de backend)
        mqtt_client.connect_async(host, poort, 60)
        mqtt_client.loop_forever(retry_first_connection=True)
    except Exception as e:
        print(f"Kan niet verbinden met MQTT: {e}")


with lock:
    herstel_uit_database()
if os.environ.get("GRAMIQ_GEEN_MQTT") != "1":
    threading.Thread(target=start_mqtt, daemon=True).start()


def _status():
    with lock:
        tijden = [v["timestamp"] for v in laatste_metingen.values()]
    return {"status": "GramIQ Backend is online", "ai_model": MODEL is not None, "ai_model_bron": MODEL_BRON,
            "bestelmodel": BESTEL is not None, "database": db.pad,
            "mqtt": dict(MQTT_STATUS), "simulatietijd": max(tijden) if tijden else None}


@app.get("/api/status")
def get_status():
    return _status()


@app.get("/api/bakken")
def get_bakken():
    with lock:
        return dict(sorted(laatste_metingen.items()))


@app.get("/api/historiek/{bak_id}")
def get_historiek(bak_id: str, uren: float = Query(4, gt=0, le=24 * 31)):
    """Gefilterd gewicht + voorspelling per minuut, de laatste `uren` tot de laatste meting."""
    bak_id = bak_id.zfill(2)
    with lock:
        info = laatste_metingen.get(bak_id)
    if info is None:
        raise HTTPException(404, "Onbekende bak of nog geen metingen")
    tot = datetime.strptime(info["timestamp"], TIJD_FORMAAT)
    van = tot - timedelta(hours=uren)
    return db.historiek_per_minuut(bak_id, van.strftime(TIJD_FORMAAT), tot.strftime(TIJD_FORMAAT))


@app.get("/api/metingen/{bak_id}")
def get_metingen(bak_id: str, van: str | None = None, tot: str | None = None,
                 limiet: int = Query(1000, gt=0, le=100000)):
    """Ruwe opgeslagen metingen, bv. ?van=2026-10-24 08:00:00&tot=2026-10-24 12:00:00"""
    return db.metingen(bak_id.zfill(2), van, tot, limiet)


@app.get("/api/producten")
def get_producten():
    return db.producten()


@app.get("/api/weegschalen")
def get_weegschalen():
    return db.weegschalen()


@app.get("/api/database")
def get_database():
    return db.statistiek()


def bestelvoorstel(item_code: int, datum: pd.Timestamp) -> dict | None:
    """Bestelling voor `datum`, zoals berekend om 4u 's ochtends (enkel data tot gisteren)."""
    sleutel = (item_code, datum)
    if sleutel in BESTEL["cache"]:
        return BESTEL["cache"][sleutel]
    try:
        rij = BESTEL["features"].loc[[sleutel]]
    except KeyError:
        return None
    m = BESTEL["model"]
    X = rij.reset_index()[m["features"]]
    med = max(float(m["mediaan"].predict(X)[0]), 0.0)
    q = max(float(m["kwantiel"].predict(X)[0]), med)
    verlies = float(rij.verlies_pct.iloc[0])
    bestel = float(bestelmodel.bestel_kg(q, verlies))
    prijs = rij.inkoopprijs.iloc[0]
    r = {
        "verwacht_kg": round(med, 1),
        "veilig_kg": round(q, 1),
        "verlies_pct": round(verlies, 1),
        "bestel_kg": round(bestel, 1),
        "inkoopprijs_per_kg": None if pd.isna(prijs) else round(float(prijs), 2),
        "kost": None if pd.isna(prijs) else round(bestel * float(prijs), 2),
        "vorige_week_kg": None if pd.isna(rij.lag7.iloc[0]) else round(float(rij.lag7.iloc[0]), 1),
    }
    BESTEL["cache"][sleutel] = r
    return r


@app.get("/api/bestelling")
def get_bestelling():
    """Bestelvoorstel van vandaag per bak + wat er tot nu toe echt verkocht is."""
    if BESTEL is None:
        return {"beschikbaar": False}
    with lock:
        tijden = [v["timestamp"] for v in laatste_metingen.values()]
    if not tijden:
        return {"beschikbaar": True, "datum": None, "bakken": {}}
    nu = pd.Timestamp(max(tijden))
    datum = nu.normalize()
    tx = echte_data.transacties()
    vandaag = tx[(tx.ts >= datum) & (tx.ts <= nu) & ~tx.is_retour]
    bakken = {}
    for bak_id, c in sorted(config.items()):
        code = PRODUCTEN.get(bak_id, {}).get("item_code")
        if code is None or c["product_id"] is None:
            continue
        v = bestelvoorstel(code, datum)
        if v is None:
            continue
        v = dict(v, product=c["naam"],
                 verkocht_tot_nu_kg=round(float(vandaag.loc[vandaag.item_code == code, "kg"].sum()), 1))
        bakken[bak_id] = v
    test = BESTEL["rapport"].get("test (jan-jun 2023)", {}).get("alle_producten", {})
    return {"beschikbaar": True, "datum": datum.strftime("%Y-%m-%d"), "tijd": nu.strftime("%H:%M"),
            "bakken": bakken, "kwantiel": BESTEL["model"]["kwantiel_niveau"], "test": test}


@app.get("/api/model")
def get_model():
    return {"geladen": MODEL is not None, "bron": MODEL_BRON, "rapport": RAPPORT}


# ---------------------------------------------------------------- AI-lab
# Zelf waarden invullen en de echte modellen laten rekenen (voor demo's en om te testen).
LAB_MAANDAG = datetime(2023, 6, 5)   # een maandag in de testperiode; enkel weekdag en uur tellen
WEEKDAGEN = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
CATEGORIE_NL = {"Flower/Leaf Vegetables": "Bladgroenten", "Cabbage": "Kool",
                "Aquatic Tuberous Vegetables": "Waterwortels", "Solanum": "Nachtschade",
                "Capsicum": "Paprika en peper", "Edible Mushroom": "Paddenstoelen"}


def _minuten(tekst: str) -> int:
    """'14:30' -> minuten sinds middernacht."""
    try:
        u, m = str(tekst).split(":")
        return int(u) * 60 + int(m)
    except ValueError:
        raise HTTPException(422, f"Ongeldig tijdstip {tekst!r}; gebruik UU:MM") from None


def _klok(d: datetime, vandaag: datetime) -> dict:
    dagen = (d.date() - vandaag.date()).days
    return {"tijd": d.strftime("%H:%M"), "dag": dagen,
            "tekst": d.strftime("%H:%M") if dagen == 0 else
            f"morgen {d:%H:%M}" if dagen == 1 else f"{WEEKDAGEN[d.weekday()]} {d:%H:%M}"}


@app.get("/api/lab/leeg")
def lab_leeg(bak: str = "01", kg: float = Query(6.0, ge=0, le=60), tijd: str = "14:00",
             weekdag: int = Query(5, ge=0, le=6), drukte: float = Query(1.0, ge=0.2, le=5),
             bijgevuld: str = "08:30"):
    """Wat als ...? Vul zelf de toestand van een bak in; het AI-model voorspelt wanneer die leeg is.

    `drukte` = hoeveel drukker dan normaal het vandaag is (1 = een gewone dag voor die weekdag).
    Het verbruik van de laatste 10-120 minuten en de verkoop van vandaag tot nu volgen uit het
    vraagprofiel maal de drukte, zoals de backend ze live uit de metingen berekent.
    """
    bak = bak.zfill(2)
    if bak not in PRODUCTEN:
        raise HTTPException(404, f"Onbekende bak {bak}")
    p = PRODUCTEN[bak]
    cap, stuk = p["capaciteit"], p["stuk_gewicht"]
    nu_min = min(max(_minuten(tijd), OPENING_UUR * 60), SLUITING_UUR * 60 - 1)
    d = LAB_MAANDAG + timedelta(days=weekdag, minutes=nu_min)
    t = _ts(d)
    g = min(kg * 1000.0, cap * 1.2)
    open_nu = nu_min - OPENING_UUR * 60          # open minuten vandaag
    meet_nu = nu_min - 8 * 60                    # de weegschaal meet vanaf 8u
    profiel = PROFIELEN.get(bak)
    per_min = Vraagprofiel(profiel).g[weekdag] if profiel is not None else np.zeros(1)
    verwacht_tot_nu = float(per_min[:open_nu].sum())

    def verbruik(venster: int) -> float:     # zoals BakHistoriek.verbruik, maar uit het profiel x drukte
        a = max(0, open_nu - venster)
        return drukte * float(per_min[a:open_nu].sum()) / max(min(venster, meet_nu), 1)

    v = {w: verbruik(w) for w in (10, 30, 60, 120)}
    afname = drukte * verwacht_tot_nu
    verbruik_vandaag = afname / open_nu if open_nu >= 1 else v[60]
    basis = v[60] if v[60] > 0 else verbruik_vandaag
    baseline = min(g / basis, MAX_HORIZON_MIN) if basis > 0 else MAX_HORIZON_MIN
    f = {"product_code": PRODUCT_CODE[bak], "stuk_gewicht": stuk, "gewicht": g, "pct_vol": 100 * g / cap,
         "stuks": g / stuk, "verbruik_10": v[10], "verbruik_30": v[30], "verbruik_60": v[60],
         "verbruik_120": v[120], "verbruik_vandaag": verbruik_vandaag, "uur": nu_min / 60, "weekdag": weekdag,
         "min_tot_sluiting": max(0.0, SLUITING_UUR * 60 - nu_min),
         "min_sinds_bijvulling": float(min(max(nu_min - _minuten(bijgevuld), 0), 999)), "baseline_min": baseline}
    h = BakHistoriek(bak, profiel=profiel)
    h.afname_vandaag = afname
    f.update(h._profiel_features(d, g))    # zelfde profiel-features als live en tijdens het trainen

    vs = voorspel(f)
    leeg = tel_open_minuten_op(t, vs["minuten"])
    # verwacht verloop volgens het vraagprofiel x drukte (voor de grafiek)
    verloop = []
    if profiel is not None:
        komend = Vraagprofiel(profiel).komende(d, MAX_HORIZON_MIN) * drukte
        rest = g - np.concatenate([[0.0], np.cumsum(komend)])
        tot = int(min(MAX_HORIZON_MIN, max(vs["minuten_max"], vs["minuten"]) + 30))
        for i in range(0, tot + 1, 5):
            verloop.append({"min": i, "kg": round(max(float(rest[i]), 0.0) / 1000, 2),
                            **_klok(tel_open_minuten_op(t, i), d)})
            if rest[i] <= 0 and i > vs["minuten"]:
                break
    test = (RAPPORT or {}).get("Test (jan-jun 2023)", {})
    return {
        "bak": bak, "product": p["naam"], "capaciteit_kg": cap / 1000, "stuk_gewicht_gram": stuk,
        "invoer": {"kg": kg, "tijd": f"{nu_min // 60:02d}:{nu_min % 60:02d}", "weekdag": weekdag,
                   "weekdag_naam": WEEKDAGEN[weekdag], "drukte": drukte, "bijgevuld": bijgevuld},
        "pct_vol": round(100 * g / cap, 1), "stuks": int(round(g / stuk)),
        "status": bepaal_status(100 * g / cap, vs["minuten"]),
        "voorspelling": {**vs, "leeg": _klok(leeg, d), "vroegst": _klok(tel_open_minuten_op(t, vs["minuten_min"]), d),
                         "laatst": _klok(tel_open_minuten_op(t, vs["minuten_max"]), d),
                         "binnen_horizon": vs["minuten"] < MAX_HORIZON_MIN},
        "baseline_min": round(baseline, 1),
        "kenmerken": {k: (None if isinstance(x, float) and math.isnan(x) else round(float(x), 2)) for k, x in f.items()},
        "verloop": verloop,
        "test": {k: test.get(k) for k in ("MAE_model_min", "MAE_baseline_min", "MAE_model_bak_laag",
                                          "MAE_baseline_bak_laag", "dekking_marge")},
    }


@app.get("/api/lab/producten")
def lab_producten():
    """Producten waarvoor het bestelmodel kan voorspellen; de vier bakken eerst."""
    if BESTEL is None:
        return {"beschikbaar": False, "producten": []}
    if "producten" not in BESTEL:
        info = echte_data.productinfo().set_index("item_code")
        dv = echte_data.dagverkopen()
        dagen = dv[dv.kg > 0].groupby("item_code").datum.nunique()
        bak_van = {p["item_code"]: b for b, p in PRODUCTEN.items()}
        lijst = []
        for code in BESTEL["features"].index.get_level_values(0).unique():
            code = int(code)
            bak = bak_van.get(code)
            cat = " ".join(str(info.categorie.get(code, "")).split())   # de data bevat harde spaties
            lijst.append({"item_code": code, "naam": PRODUCTEN[bak]["naam"] if bak else str(info.naam.get(code, code)),
                          "naam_dataset": str(info.naam.get(code, "")), "categorie": CATEGORIE_NL.get(cat, cat),
                          "verlies_pct": None if pd.isna(info.verlies_pct.get(code)) else float(info.verlies_pct.get(code)),
                          "verkoopdagen": int(dagen.get(code, 0)), "bak": bak})
        lijst.sort(key=lambda r: (r["bak"] is None, r["bak"] or "", -r["verkoopdagen"]))
        gezien = {}
        for r in lijst:   # de data heeft enkele producten met dezelfde naam (andere code)
            gezien[r["naam"]] = gezien.get(r["naam"], 0) + 1
            if gezien[r["naam"]] > 1:
                r["naam"] = f"{r['naam']} #{gezien[r['naam']]}"
        BESTEL["producten"] = lijst
    datums = BESTEL["features"].index.get_level_values(1)
    return {"beschikbaar": True, "producten": BESTEL["producten"],
            "van": (datums.min() + pd.Timedelta(days=28)).strftime("%Y-%m-%d"), "tot": datums.max().strftime("%Y-%m-%d"),
            "test_vanaf": echte_data.VAL_TOT}


@app.get("/api/lab/bestelling")
def lab_bestelling(product: int = PRODUCTEN["01"]["item_code"], datum: str = "2023-06-03"):
    """Bestelvoorstel voor één product op een gekozen dag, naast wat er die dag echt verkocht werd."""
    if BESTEL is None:
        raise HTTPException(503, "Bestelmodel niet geladen")
    try:
        dag = pd.Timestamp(datum).normalize()
    except (ValueError, TypeError):
        raise HTTPException(422, f"Ongeldige datum {datum!r}; gebruik JJJJ-MM-DD") from None
    v = bestelvoorstel(product, dag)
    if v is None:
        raise HTTPException(404, "Geen gegevens voor dit product op deze dag")
    feats = BESTEL["features"]
    stuk = feats.loc[(product, slice(dag - pd.Timedelta(days=27), dag)), :]
    m = BESTEL["model"]
    X = stuk.reset_index()[m["features"]]
    med = np.clip(m["mediaan"].predict(X), 0, None)
    q = np.maximum(np.clip(m["kwantiel"].predict(X), 0, None), med)
    bestel = bestelmodel.bestel_kg(q, stuk.verlies_pct.values)
    reeks = []
    for (_, d), echt, a, b, c in zip(stuk.index, stuk.kg.values, med, q, bestel):
        reeks.append({"datum": d.strftime("%Y-%m-%d"), "label": d.strftime("%d/%m"), "weekdag": WEEKDAGEN[d.weekday()][:2],
                      "echt_kg": None if pd.isna(echt) else round(float(echt), 1),
                      "verwacht_kg": round(float(a), 1), "veilig_kg": round(float(b), 1), "bestel_kg": round(float(c), 1)})
    echt = reeks[-1]["echt_kg"]
    namen = {r["item_code"]: r["naam"] for r in lab_producten()["producten"]}
    vw = v["vorige_week_kg"]
    return {
        **v, "item_code": product, "product": namen.get(product, str(product)),
        "datum": dag.strftime("%Y-%m-%d"), "weekdag": WEEKDAGEN[dag.weekday()],
        "echt_kg": echt, "aangeboden": echt is not None and echt > 0,
        "fout_kg": None if echt is None else round(abs(v["verwacht_kg"] - echt), 1),
        "fout_vorige_week_kg": None if echt is None or vw is None else round(abs(vw - echt), 1),
        "volstond": None if echt is None else bool(v["veilig_kg"] >= echt),
        "gezien_tijdens_training": bool(dag < pd.Timestamp(echte_data.VAL_TOT)),
        "reeks": reeks,
        "test": BESTEL["rapport"].get("test (jan-jun 2023)", {}).get("alle_producten", {}),
    }


# ---------------------------------------------------------------- dashboard
# Het gebouwde React-dashboard (frontend/dist) wordt hier mee geserveerd, zodat alles op
# één adres draait (http://127.0.0.1:8000). Dit moet de laatste route zijn.
if os.path.isfile(os.path.join(DASHBOARD_MAP, "index.html")):
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=DASHBOARD_MAP, html=True), name="dashboard")
else:
    @app.get("/")
    def read_root():
        return _status()
