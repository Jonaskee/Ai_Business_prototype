#!/usr/bin/env python3
"""
GramIQ in één keer opstarten.

    Windows       dubbelklik op start.bat        (of: py start.py)
    macOS/Linux   ./start.sh                     (of: python3 start.py)

Wat er gebeurt:
  1. een eigen Python-omgeving (.venv) met alle pakketten (enkel de eerste keer, 1 à 3 min)
  2. het dashboard: meegeleverd in frontend/dist, opnieuw gebouwd als je de code aanpast en Node.js hebt
  3. een MQTT-broker: Mosquitto als die al draait, anders de ingebouwde mini-broker
  4. de backend: FastAPI + de twee AI-modellen + SQLite-database
  5. de simulator: echte verkopen van juni 2023, als metingen van vier weegschalen
  6. je browser opent http://127.0.0.1:8000

Stoppen: Ctrl+C of het venster sluiten.

Voorbeelden:
    python start.py --snelheid 180              # 1 seconde = 3 minuten in de winkel
    python start.py --start "2023-01-20 08:30"  # de drukste dag van de testperiode
    python start.py --schoon                    # met een lege database beginnen
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

MIN_PYTHON = (3, 10)
ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "real_data_prototype", "GramIQ_RealData")
FRONTEND = os.path.join(APP, "frontend")
VENV = os.path.join(ROOT, ".venv")
REQUIREMENTS = os.path.join(ROOT, "requirements.txt")
WINDOWS = os.name == "nt"
MERK_PAKKETTEN = os.path.join(VENV, ".gramiq-pakketten")
MERK_DASHBOARD = os.path.join(FRONTEND, "dist", ".bron-sha256")
DASHBOARD_BRONNEN = ("index.html", "package.json", "package-lock.json", "vite.config.js", "src")

try:  # nooit crashen op een teken dat de console niet kent; naar een bestand of pijp: UTF-8
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(errors="replace") if _s.isatty() else _s.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


# ---------------------------------------------------------------- uitvoer
def _kleuren_aan() -> bool:
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if not WINDOWS:
        return True
    try:  # ANSI-kleuren aanzetten in de Windows-console
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        modus = ctypes.c_uint32()
        return bool(k.GetConsoleMode(h, ctypes.byref(modus)) and k.SetConsoleMode(h, modus.value | 4))
    except Exception:  # noqa: BLE001
        return False


class K:
    aan = _kleuren_aan()
    vet, dim, uit = ("\033[1m", "\033[2m", "\033[0m") if aan else ("", "", "")
    groen, geel, rood, blauw, paars, cyaan = (("\033[32m", "\033[33m", "\033[31m", "\033[34m", "\033[35m", "\033[36m")
                                              if aan else ("",) * 6)


_print_lock = threading.Lock()


def zeg(*delen, **kw):
    with _print_lock:
        print(*delen, flush=True, **kw)


def stap(nr: int, tekst: str):
    zeg(f"\n{K.blauw}{K.vet}[{nr}/5]{K.uit} {K.vet}{tekst}{K.uit}")


def stop_met_fout(tekst: str, hint: str = ""):
    zeg(f"\n{K.rood}{K.vet}Fout:{K.uit} {tekst}")
    if hint:
        zeg(f"{K.dim}{hint}{K.uit}")
    sys.exit(1)


# ---------------------------------------------------------------- hulpjes
def venv_python() -> str:
    return os.path.join(VENV, "Scripts", "python.exe") if WINDOWS else os.path.join(VENV, "bin", "python")


def sha256_van(paden: list[str], basis: str) -> str:
    """Vingerafdruk van bestanden (regeleindes genormaliseerd, zodat git op Windows niet meetelt)."""
    h = hashlib.sha256()
    for pad in sorted(paden):
        h.update(os.path.relpath(pad, basis).replace("\\", "/").encode())
        with open(pad, "rb") as f:
            h.update(f.read().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def dashboard_bronnen() -> list[str]:
    uit = []
    for naam in DASHBOARD_BRONNEN:
        pad = os.path.join(FRONTEND, naam)
        if os.path.isdir(pad):
            for map_, _, bestanden in os.walk(pad):
                uit += [os.path.join(map_, b) for b in bestanden]
        elif os.path.isfile(pad):
            uit.append(pad)
    return uit


def poort_in_gebruik(poort: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", poort)) == 0


def wacht_op_poort(poort: int, seconden: float) -> bool:
    einde = time.time() + seconden
    while time.time() < einde:
        if poort_in_gebruik(poort):
            return True
        time.sleep(0.2)
    return False


def http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


class Proces:
    """Een kindproces waarvan elke regel uitvoer met een gekleurd label getoond wordt."""

    def __init__(self, naam: str, kleur: str, cmd: list[str], cwd: str, env: dict):
        self.naam, self.kleur = naam, kleur
        self.laatste: list[str] = []
        self.p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", bufsize=1)
        threading.Thread(target=self._lees, daemon=True).start()

    def _lees(self):
        for regel in self.p.stdout:
            regel = regel.rstrip()
            if not regel:
                continue
            self.laatste = (self.laatste + [regel])[-15:]
            zeg(f"{self.kleur}{self.naam:>9} |{K.uit} {regel}")

    def leeft(self) -> bool:
        return self.p.poll() is None

    def stop(self):
        if self.leeft():
            self.p.terminate()


# ---------------------------------------------------------------- stappen
def controleer_python():
    if sys.version_info < MIN_PYTHON:
        stop_met_fout(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} of nieuwer is nodig, dit is {sys.version.split()[0]}.",
                      "Download de nieuwste versie op https://www.python.org/downloads/ en start opnieuw.")


def maak_omgeving(opnieuw: bool) -> str:
    py = venv_python()
    if opnieuw and os.path.isdir(VENV):
        zeg("  .venv wordt opnieuw aangemaakt ...")
        shutil.rmtree(VENV, ignore_errors=True)
    if os.path.exists(py):
        test = subprocess.run([py, "-c", "import sys"], capture_output=True)
        if test.returncode == 0:
            zeg(f"  {K.groen}OK{K.uit} .venv bestaat al")
            return py
        zeg("  .venv werkt niet meer (andere Python?), opnieuw aanmaken ...")
        shutil.rmtree(VENV, ignore_errors=True)
    zeg(f"  Python {sys.version.split()[0]} -> nieuwe omgeving in .venv ...")
    r = subprocess.run([sys.executable, "-m", "venv", VENV])
    if r.returncode != 0 or not os.path.exists(py):
        stop_met_fout("Kon geen Python-omgeving (.venv) aanmaken.",
                      "Op Ubuntu/Debian: sudo apt install python3-venv   en start daarna opnieuw.")
    return py


def installeer_pakketten(py: str):
    with open(REQUIREMENTS, "rb") as f:
        vinger = hashlib.sha256(f.read().replace(b"\r\n", b"\n")).hexdigest()
    if os.path.exists(MERK_PAKKETTEN):
        with open(MERK_PAKKETTEN) as f:
            al = f.read().strip() == vinger
        if al and subprocess.run([py, "-c", "import fastapi, uvicorn, paho.mqtt, pandas, numpy"],
                                 capture_output=True).returncode == 0:
            zeg(f"  {K.groen}OK{K.uit} pakketten zijn al geïnstalleerd")
            return
    zeg("  Pakketten installeren (de eerste keer duurt dit 1 à 3 minuten) ...")
    subprocess.run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "--upgrade", "pip"])
    r = subprocess.run([py, "-m", "pip", "install", "--disable-pip-version-check", "-r", REQUIREMENTS])
    if r.returncode != 0:
        stop_met_fout("Het installeren van de pakketten is mislukt (zie de melding hierboven).",
                      "Controleer je internetverbinding en probeer opnieuw met: python start.py --opnieuw")
    with open(MERK_PAKKETTEN, "w") as f:
        f.write(vinger)
    zeg(f"  {K.groen}OK{K.uit} pakketten geïnstalleerd")


def zorg_voor_dashboard(forceer: bool):
    index = os.path.join(FRONTEND, "dist", "index.html")
    vinger = sha256_van(dashboard_bronnen(), FRONTEND)
    actueel = False
    if os.path.exists(index) and os.path.exists(MERK_DASHBOARD):
        with open(MERK_DASHBOARD) as f:
            actueel = f.read().strip() == vinger
    if actueel and not forceer:
        zeg(f"  {K.groen}OK{K.uit} dashboard is klaar (frontend/dist)")
        return
    npm = shutil.which("npm")
    if not npm:
        if os.path.exists(index):
            zeg(f"  {K.geel}!{K.uit}  de code van het dashboard is aangepast, maar Node.js is niet gevonden:"
                " de meegeleverde (oudere) versie wordt gebruikt.")
        else:
            zeg(f"  {K.geel}!{K.uit}  geen dashboard gevonden en geen Node.js om het te bouwen (https://nodejs.org)."
                " De backend werkt wel: zie /docs.")
        return
    zeg("  Dashboard bouwen met Node.js ...")
    if not os.path.isdir(os.path.join(FRONTEND, "node_modules")):
        r = subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=FRONTEND)
        if r.returncode != 0:
            zeg(f"  {K.geel}!{K.uit}  npm install mislukt; de meegeleverde versie wordt gebruikt.")
            return
    r = subprocess.run([npm, "run", "build"], cwd=FRONTEND)
    if r.returncode != 0:
        zeg(f"  {K.geel}!{K.uit}  npm run build mislukt; de meegeleverde versie wordt gebruikt.")
        return
    with open(MERK_DASHBOARD, "w") as f:
        f.write(vinger)
    zeg(f"  {K.groen}OK{K.uit} dashboard gebouwd")


def controleer_data():
    zoek = [os.path.join(APP, "data"), os.path.join(ROOT, "real_data_prototype", "archive (1)")]
    ontbreekt = [n for n in ("annex1.csv", "annex2.csv", "annex3.csv", "annex4.csv")
                 if not any(os.path.exists(os.path.join(m, n)) for m in zoek)]
    if ontbreekt:
        stop_met_fout(f"Deze databestanden ontbreken: {', '.join(ontbreekt)}.",
                      "Download 'Supermarket Sales Data' van Kaggle "
                      "(kaggle.com/datasets/yapwh1208/supermarket-sales-data) en zet de CSV's in "
                      "real_data_prototype/GramIQ_RealData/data/.")


def main():
    ap = argparse.ArgumentParser(description="Start het GramIQ-prototype (broker, backend, simulator, dashboard).",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Voorbeeld: python start.py --snelheid 180 --start \"2023-01-20 08:30\"")
    ap.add_argument("--snelheid", type=float, default=60, help="1 seconde = zoveel seconden in de winkel (standaard 60)")
    ap.add_argument("--start", default="2023-06-01 08:30", help="starttijd van de simulator (2020-07-01 .. 2023-06-30)")
    ap.add_argument("--dagen", type=float, default=7, help="hoeveel dagen de simulator afspeelt (standaard 7)")
    ap.add_argument("--poort", type=int, default=8000, help="poort van het dashboard (standaard 8000)")
    ap.add_argument("--mqtt-poort", type=int, default=1883, help="poort van de MQTT-broker (standaard 1883)")
    ap.add_argument("--schoon", action="store_true", help="begin met een lege database")
    ap.add_argument("--geen-browser", action="store_true", help="de browser niet automatisch openen")
    ap.add_argument("--geen-simulator", action="store_true", help="enkel broker + backend (bv. met echte weegschalen)")
    ap.add_argument("--bouw", action="store_true", help="het dashboard altijd opnieuw bouwen (Node.js nodig)")
    ap.add_argument("--opnieuw", action="store_true", help=".venv opnieuw aanmaken en alles opnieuw installeren")
    ap.add_argument("--installeer", action="store_true", help="enkel installeren, niets starten")
    args = ap.parse_args()

    zeg(f"\n{K.groen}{K.vet}  GramIQ{K.uit}{K.vet} · een weegschaal onder elke groentebak{K.uit}")
    zeg(f"{K.dim}  prototype op echte verkoopdata · {ROOT}{K.uit}")

    stap(1, "Python-omgeving")
    controleer_python()
    py = maak_omgeving(args.opnieuw)
    installeer_pakketten(py)

    stap(2, "Dashboard en data")
    zorg_voor_dashboard(args.bouw)
    controleer_data()
    if args.installeer:
        zeg(f"\n{K.groen}Alles is geïnstalleerd.{K.uit} Start met: python start.py")
        return

    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8",
               MQTT_HOST="127.0.0.1", MQTT_POORT=str(args.mqtt_poort))
    processen: list[Proces] = []

    def stop_alles():
        for p in reversed(processen):
            p.stop()
        einde = time.time() + 5
        for p in processen:
            try:
                p.p.wait(timeout=max(0.1, einde - time.time()))
            except subprocess.TimeoutExpired:
                p.p.kill()

    try:
        stap(3, "MQTT-broker")
        if poort_in_gebruik(args.mqtt_poort):
            zeg(f"  {K.groen}OK{K.uit} er draait al een broker op poort {args.mqtt_poort} (bv. Mosquitto); die wordt gebruikt")
        else:
            processen.append(Proces("mqtt", K.paars, [py, "mqtt_broker.py", "--poort", str(args.mqtt_poort)], APP, env))
            if not wacht_op_poort(args.mqtt_poort, 15):
                stop_met_fout("De MQTT-broker start niet.", "\n".join(processen[-1].laatste))

        stap(4, "Backend met de AI-modellen")
        poort = args.poort
        while poort_in_gebruik(poort) and poort < args.poort + 20:
            poort += 1
        if poort != args.poort:
            zeg(f"  {K.geel}!{K.uit}  poort {args.poort} is bezet, ik gebruik {poort}")
        if args.schoon:
            for s in ("", "-wal", "-shm"):
                pad = os.path.join(APP, "data", "gramiq.db" + s)
                if os.path.exists(pad):
                    os.remove(pad)
            zeg("  lege database")
        backend = Proces("backend", K.cyaan, [py, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
                                              "--port", str(poort), "--log-level", "warning"],
                         os.path.join(APP, "backend"), env)
        processen.append(backend)
        url = f"http://127.0.0.1:{poort}"
        einde = time.time() + 240
        while not http_ok(url + "/api/status"):
            if not backend.leeft():
                stop_met_fout("De backend is gestopt tijdens het opstarten (zie de meldingen hierboven).")
            if time.time() > einde:
                stop_met_fout("De backend start niet binnen 4 minuten.")
            time.sleep(0.5)
        zeg(f"  {K.groen}OK{K.uit} backend draait op {url}")

        stap(5, "Simulator")
        if args.geen_simulator:
            zeg("  overgeslagen (--geen-simulator)")
        else:
            processen.append(Proces("simulator", K.geel, [py, "simulator.py", "--stil", "--snelheid", str(args.snelheid),
                                                          "--start", args.start, "--dagen", str(args.dagen),
                                                          "--broker", "127.0.0.1", "--poort", str(args.mqtt_poort)],
                                    os.path.join(APP, "simulator"), env))

        lijn = "-" * 58
        zeg(f"\n  {K.groen}{lijn}{K.uit}")
        zeg(f"  {K.groen}{K.vet}GramIQ draait!{K.uit}")
        zeg(f"    Dashboard + AI-lab   {K.vet}{url}{K.uit}")
        zeg(f"    API (uitproberen)    {url}/docs")
        zeg(f"    3D-winkel + spel     docs/index.html  of  https://jonaskee.github.io/Ai_Business_prototype/")
        zeg("    Stoppen              Ctrl+C")
        zeg(f"  {K.groen}{lijn}{K.uit}\n")
        if not args.geen_browser:
            webbrowser.open(url)

        gemeld = set()
        while True:
            time.sleep(0.5)
            for p in processen:
                if p.leeft() or p.naam in gemeld:
                    continue
                gemeld.add(p.naam)
                if p.naam == "simulator" and p.p.returncode == 0:
                    zeg(f"\n  {K.groen}De simulator is klaar.{K.uit} Het dashboard blijft open; "
                        f"herstart met andere instellingen, bv. python start.py --start \"2023-03-11 08:30\"")
                else:
                    zeg(f"\n  {K.rood}{p.naam} is onverwacht gestopt (code {p.p.returncode}).{K.uit}")
                    stop_alles()
                    sys.exit(1)
    except KeyboardInterrupt:
        zeg(f"\n  {K.dim}Stoppen ...{K.uit}")
    finally:
        stop_alles()
    zeg("  Tot de volgende keer!")


if __name__ == "__main__":
    main()
