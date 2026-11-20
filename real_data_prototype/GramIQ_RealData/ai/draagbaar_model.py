"""
Draagbare versie van de getrainde AI-modellen (JSON in plaats van een pickle).

Waarom
------
`model_leeg.joblib` en `model_bestelling.joblib` zijn pickles. Die laden enkel met
(ongeveer) dezelfde scikit-learn-versie als waarmee ze getraind zijn (1.5.1). Wie de
repo clonet met een nieuwere Python of scikit-learn krijgt dan "AI-model niet geladen"
en de backend valt stil terug op de simpele berekening.

Dit bestand zet de bomen van een HistGradientBoostingRegressor om naar gewone JSON en
voorspelt daar zelf mee, met exact dezelfde uitkomst als scikit-learn (controle:
`python draagbaar_model.py --controleer`). Er is enkel numpy voor nodig, dus het werkt
op elke Python-versie.

Gebruik
-------
    python draagbaar_model.py                 # *.joblib -> *.json (met de scikit-learn waarmee getraind is)
    python draagbaar_model.py --controleer    # + vergelijk de voorspellingen met scikit-learn

    from draagbaar_model import laad_pakket
    pakket, bron = laad_pakket("model_leeg")  # {"features": [...], "hoofd": model, "marges": ...}
    pakket["hoofd"].predict(X)

De train-scripts (train_model.py, bestelmodel.py) schrijven de JSON automatisch mee weg.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import warnings

import numpy as np

HIER = os.path.dirname(os.path.abspath(__file__))
FORMAAT = "gramiq-pakket-1"
MODEL_SLEUTEL = "__hgb__"


def _sha256(pad: str) -> str:
    h = hashlib.sha256()
    with open(pad, "rb") as f:
        for blok in iter(lambda: f.read(1 << 20), b""):
            h.update(blok)
    return h.hexdigest()


# --------------------------------------------------------------------------
# Exporteren (heeft scikit-learn nodig, dezelfde versie als bij het trainen)
# --------------------------------------------------------------------------
def exporteer_hgb(est) -> dict:
    """HistGradientBoostingRegressor -> dict met gewone lijsten (JSON-klaar).

    Per knoop i van een boom:
      f[i] = -1             -> blad met waarde w[i]
      f[i] >= 0, i in c     -> categorische splitsing: codes in c[i] gaan links,
                               andere gekende codes rechts, onbekend volgt m[i]
      anders                -> x <= w[i] gaat links; ontbrekend (NaN) volgt m[i]
    f verwijst naar de kolomvolgorde zoals de bomen ze zien (`volgorde`).
    """
    if getattr(est, "n_trees_per_iteration_", 1) != 1:
        raise ValueError("Enkel regressie (één boom per iteratie) wordt ondersteund.")
    link = type(est._loss.link).__name__
    if link != "IdentityLink":
        raise ValueError(f"Link {link} wordt niet ondersteund.")

    n = int(est.n_features_in_)
    is_cat = np.zeros(n, dtype=bool) if est.is_categorical_ is None else np.asarray(est.is_categorical_, dtype=bool)
    # De ColumnTransformer van scikit-learn zet de categorische kolommen vooraan.
    volgorde = [int(i) for i in np.flatnonzero(is_cat)] + [int(i) for i in np.flatnonzero(~is_cat)]

    categorieen = {}
    if est._preprocessor is not None:
        enc = est._preprocessor.named_transformers_["encoder"]
        for kol, cats in zip(np.flatnonzero(is_cat), enc.categories_):
            categorieen[str(int(kol))] = [float(c) for c in cats]

    bekend = {}
    if is_cat.any():
        bitsets, f_idx_map = est._bin_mapper.make_known_categories_bitsets()
        for j, orig in enumerate(volgorde):
            if is_cat[orig]:
                bekend[str(j)] = _bitset_naar_lijst(bitsets[f_idx_map[j]])

    bomen = []
    for iteratie in est._predictors:
        p = iteratie[0]
        nd = p.nodes
        blad = nd["is_leaf"].astype(bool)
        boom = {
            "f": np.where(blad, -1, nd["feature_idx"]).astype(int).tolist(),
            "w": np.where(blad, nd["value"], nd["num_threshold"]).astype(float).tolist(),
            "l": nd["left"].astype(int).tolist(),
            "r": nd["right"].astype(int).tolist(),
            "m": nd["missing_go_to_left"].astype(int).tolist(),
        }
        cat = {}
        for i in np.flatnonzero(nd["is_categorical"].astype(bool) & ~blad):
            cat[str(int(i))] = _bitset_naar_lijst(p.raw_left_cat_bitsets[nd["bitset_idx"][i]])
        if cat:
            boom["c"] = cat
        bomen.append(boom)

    return {
        "soort": "HistGradientBoostingRegressor",
        "n_features": n,
        "basis": float(np.asarray(est._baseline_prediction).ravel()[0]),
        "volgorde": volgorde,
        "categorieen": categorieen,
        "bekend": bekend,
        "bomen": bomen,
    }


def _bitset_naar_lijst(bitset) -> list[int]:
    b = np.asarray(bitset, dtype=np.uint32)
    return [int(c) for c in range(32 * len(b)) if (int(b[c // 32]) >> (c % 32)) & 1]


def pakket_naar_json(pakket: dict, bron_pad: str | None = None) -> dict:
    """Het joblib-pakket (dict) -> JSON-klare dict; modellen worden omgezet, de rest blijft."""
    import sklearn
    from sklearn.ensemble import HistGradientBoostingRegressor

    inhoud = {}
    for k, v in pakket.items():
        if isinstance(v, HistGradientBoostingRegressor):
            inhoud[k] = {MODEL_SLEUTEL: exporteer_hgb(v)}
        else:
            inhoud[k] = json.loads(json.dumps(v))  # moet gewone JSON zijn (lijsten, getallen, ...)
    uit = {"formaat": FORMAAT, "sklearn": sklearn.__version__, "inhoud": inhoud}
    if bron_pad:
        uit["bron"] = os.path.basename(bron_pad)
        uit["bron_sha256"] = _sha256(bron_pad)
    return uit


def schrijf_json(pakket: dict, joblib_pad: str, json_pad: str | None = None) -> str:
    """Schrijft de draagbare versie naast het .joblib-bestand. Geeft het pad terug."""
    json_pad = json_pad or os.path.splitext(joblib_pad)[0] + ".json"
    d = pakket_naar_json(pakket, joblib_pad)
    tmp = json_pad + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, separators=(",", ":"))
    os.replace(tmp, json_pad)
    return json_pad


# --------------------------------------------------------------------------
# Voorspellen (enkel numpy)
# --------------------------------------------------------------------------
class DraagbaarHGB:
    """Voorspelt exact zoals HistGradientBoostingRegressor.predict, zonder scikit-learn."""

    KLEIN = 32  # tot zoveel rijen rekent gewone Python sneller dan numpy

    def __init__(self, d: dict):
        self.n_features_in_ = int(d["n_features"])
        self.basis = float(d["basis"])
        self.volgorde = np.asarray(d["volgorde"], dtype=np.int64)
        self.categorieen = {int(k): np.asarray(v, dtype=float) for k, v in d["categorieen"].items()}
        self.bekend = {int(k): frozenset(v) for k, v in d["bekend"].items()}
        self._bomen_lijst = []
        self._bomen_np = []
        for b in d["bomen"]:
            cat = [None] * len(b["f"])
            for i, codes in b.get("c", {}).items():
                cat[int(i)] = frozenset(codes)
            self._bomen_lijst.append((b["f"], b["w"], b["l"], b["r"], [bool(x) for x in b["m"]], cat))
            self._bomen_np.append((np.asarray(b["f"], dtype=np.int64), np.asarray(b["w"], dtype=float),
                                   np.asarray(b["l"], dtype=np.int64), np.asarray(b["r"], dtype=np.int64),
                                   np.asarray(b["m"], dtype=bool), cat))

    def _transformeer(self, X) -> np.ndarray:
        X = np.asarray(X, dtype=float)
        if X.ndim == 1:
            X = X[None, :]
        if X.shape[1] != self.n_features_in_:
            raise ValueError(f"{X.shape[1]} kolommen, model verwacht er {self.n_features_in_}")
        Z = X[:, self.volgorde].copy()
        for j, orig in enumerate(self.volgorde):
            cats = self.categorieen.get(int(orig))
            if cats is None:
                continue
            kol = X[:, orig]
            idx = np.clip(np.searchsorted(cats, kol), 0, len(cats) - 1)
            Z[:, j] = np.where(cats[idx] == kol, idx, np.nan)  # onbekende categorie -> ontbrekend
        return Z

    def predict(self, X) -> np.ndarray:
        Z = self._transformeer(X)
        if len(Z) <= self.KLEIN:
            return np.array([self._een(z) for z in Z.tolist()])
        return self._veel(Z)

    def _een(self, z: list) -> float:
        s = self.basis
        bekend = self.bekend
        for f, w, l, r, m, cat in self._bomen_lijst:
            i = 0
            while True:
                fi = f[i]
                if fi < 0:
                    s += w[i]
                    break
                x = z[fi]
                if x != x:                       # NaN
                    i = l[i] if m[i] else r[i]
                elif cat[i] is not None:
                    c = int(x)
                    if x >= 0 and c in cat[i]:
                        i = l[i]
                    elif x >= 0 and c in bekend[fi]:
                        i = r[i]
                    else:
                        i = l[i] if m[i] else r[i]
                else:
                    i = l[i] if x <= w[i] else r[i]
        return s

    def _veel(self, Z: np.ndarray) -> np.ndarray:
        n = len(Z)
        rij = np.arange(n)
        uit = np.full(n, self.basis)
        for f, w, l, r, m, cat in self._bomen_np:
            k = np.zeros(n, dtype=np.int64)
            while True:
                fk = f[k]
                actief = fk >= 0
                if not actief.any():
                    break
                ka, fa = k[actief], fk[actief]
                x = Z[rij[actief], fa]
                nan = np.isnan(x)
                links = np.where(nan, m[ka], x <= w[ka])
                for p in np.flatnonzero(~nan):   # categorische knopen (zeldzaam): per stuk
                    c = cat[ka[p]]
                    if c is not None:
                        xv = x[p]
                        if xv >= 0 and int(xv) in c:
                            links[p] = True
                        elif xv >= 0 and int(xv) in self.bekend[int(fa[p])]:
                            links[p] = False
                        else:
                            links[p] = m[ka[p]]
                k[actief] = np.where(links, l[ka], r[ka])
            uit += w[k]
        return uit


def _uit_json(d: dict) -> dict:
    if d.get("formaat") != FORMAAT:
        raise ValueError(f"Onbekend formaat {d.get('formaat')!r}")
    return {k: DraagbaarHGB(v[MODEL_SLEUTEL]) if isinstance(v, dict) and MODEL_SLEUTEL in v else v
            for k, v in d["inhoud"].items()}


def laad_pakket(naam: str, map: str = HIER):
    """Laadt `naam` (zonder extensie, bv. "model_leeg"). Geeft (pakket, bron).

    1. de JSON, als die bij het huidige .joblib-bestand hoort (zelfde vingerafdruk)
    2. anders het .joblib-bestand (net opnieuw getraind?) en meteen een nieuwe JSON
    3. anders een oudere JSON, met een waarschuwing
    """
    jl = os.path.join(map, naam + ".joblib")
    js = os.path.join(map, naam + ".json")
    sha = _sha256(jl) if os.path.exists(jl) else None
    d = None
    if os.path.exists(js):
        with open(js, encoding="utf-8") as f:
            d = json.load(f)
        if sha is None or d.get("bron_sha256") == sha:
            return _uit_json(d), f"{naam}.json (draagbaar)"
    fout = None
    if sha is not None:
        try:
            import joblib
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                pakket = joblib.load(jl)
            try:
                schrijf_json(pakket, jl, js)
            except Exception as e:  # noqa: BLE001 - niet erg, volgende keer opnieuw
                print(f"   (kon {naam}.json niet bijwerken: {e})")
            return pakket, f"{naam}.joblib"
        except Exception as e:  # noqa: BLE001
            fout = e
    if d is not None:
        print(f"!! {naam}.json hoort bij een ouder {naam}.joblib en {naam}.joblib laadt niet ({fout}). "
              f"De oudere JSON wordt gebruikt.")
        return _uit_json(d), f"{naam}.json (verouderd)"
    raise RuntimeError(f"{naam} niet gevonden of niet te laden: {fout}")


# --------------------------------------------------------------------------
# Opdrachtregel: exporteren en controleren
# --------------------------------------------------------------------------
def _testinvoer(est, n: int, rng) -> np.ndarray:
    """Willekeurige invoer rond de drempels van het model, met wat NaN en onbekende categorieën."""
    X = np.empty((n, est.n_features_in_))
    drempels = est._bin_mapper.bin_thresholds_
    is_cat = np.zeros(est.n_features_in_, bool) if est.is_categorical_ is None else est.is_categorical_
    enc = est._preprocessor.named_transformers_["encoder"] if est._preprocessor is not None else None
    cat_i = 0
    volgorde = list(np.flatnonzero(is_cat)) + list(np.flatnonzero(~is_cat))
    for j, orig in enumerate(volgorde):
        if is_cat[orig]:
            cats = np.asarray(enc.categories_[cat_i], dtype=float)
            cat_i += 1
            X[:, orig] = rng.choice(np.concatenate([cats, [cats.max() + 1]]), n)
        else:
            t = np.asarray(drempels[j], dtype=float)
            lo, hi = (t.min(), t.max()) if len(t) else (0.0, 1.0)
            X[:, orig] = rng.uniform(lo - 0.1 * abs(lo) - 1, hi + 0.1 * abs(hi) + 1, n)
            if len(t):  # ook exact op een drempel
                op = rng.random(n) < 0.1
                X[op, orig] = rng.choice(t, op.sum())
        X[rng.random(n) < 0.03, orig] = np.nan
    return X


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--controleer", action="store_true", help="vergelijk met scikit-learn na het exporteren")
    ap.add_argument("namen", nargs="*", default=["model_leeg", "model_bestelling"])
    a = ap.parse_args(argv)

    import joblib
    from sklearn.ensemble import HistGradientBoostingRegressor
    rng = np.random.default_rng(0)
    ok = True
    for naam in a.namen:
        jl = os.path.join(HIER, naam + ".joblib")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            pakket = joblib.load(jl)
        js = schrijf_json(pakket, jl)
        print(f"{naam}.joblib -> {os.path.basename(js)} ({os.path.getsize(js) / 1e6:.1f} MB)")
        if not a.controleer:
            continue
        draagbaar, _ = laad_pakket(naam)
        for k, est in pakket.items():
            if not isinstance(est, HistGradientBoostingRegressor):
                continue
            X = _testinvoer(est, 20000, rng)
            verwacht = est.predict(X)
            veel = draagbaar[k].predict(X)                           # numpy-pad
            weinig = np.concatenate([draagbaar[k].predict(X[i:i + 1]) for i in range(300)])  # Python-pad
            v1, v2 = float(np.max(np.abs(veel - verwacht))), float(np.max(np.abs(weinig - verwacht[:300])))
            goed = v1 == 0.0 and v2 == 0.0
            ok &= goed
            print(f"   {k:10s} {len(est._predictors)} bomen · max. verschil met scikit-learn: "
                  f"{v1:.3g} (20.000 rijen) / {v2:.3g} (1 rij) {'OK' if goed else 'FOUT'}")
    if a.controleer and not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
