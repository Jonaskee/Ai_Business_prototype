"""
Mini MQTT-broker in pure Python, voor de demo (MQTT 3.1.1, geen extra pakketten nodig).

Op de winkel-PC blijft Mosquitto de keuze (`docker-compose up -d`). Deze broker bestaat
zodat `python start.py` overal werkt zonder Docker of een andere installatie.

Ondersteunt wat de simulator en de backend gebruiken: CONNECT, PUBLISH (QoS 0, 1 en 2
worden aangenomen, doorgestuurd als QoS 0), SUBSCRIBE en UNSUBSCRIBE met wildcards
(+ en #), retained berichten, PINGREQ en DISCONNECT. Luistert standaard enkel op deze
computer (127.0.0.1 en ::1), niet op het netwerk.

    python mqtt_broker.py              # poort 1883
    python mqtt_broker.py --poort 1884
"""
from __future__ import annotations

import argparse
import asyncio
import struct
import sys

CONNECT, CONNACK, PUBLISH, PUBACK, PUBREC, PUBREL, PUBCOMP = 1, 2, 3, 4, 5, 6, 7
SUBSCRIBE, SUBACK, UNSUBSCRIBE, UNSUBACK, PINGREQ, PINGRESP, DISCONNECT = 8, 9, 10, 11, 12, 13, 14


def past(filter_: str, topic: str) -> bool:
    """Komt `topic` overeen met `filter_` (met + en #)?"""
    if topic.startswith("$") and not filter_.startswith("$"):
        return False
    f, t = filter_.split("/"), topic.split("/")
    for i, deel in enumerate(f):
        if deel == "#":
            return True
        if i >= len(t) or (deel != "+" and deel != t[i]):
            return False
    return len(f) == len(t)


def lengte(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n % 128, n // 128
        out.append(b | (128 if n else 0))
        if not n:
            return bytes(out)


def pakket(soort: int, vlaggen: int, inhoud: bytes) -> bytes:
    return bytes([(soort << 4) | vlaggen]) + lengte(len(inhoud)) + inhoud


def tekst(b: bytes) -> bytes:
    return struct.pack("!H", len(b)) + b


class Klant:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader, self.writer = reader, writer
        self.id = "?"
        self.abonnementen: set[str] = set()
        self.keepalive = 0

    def stuur(self, data: bytes):
        if not self.writer.is_closing():
            self.writer.write(data)


class Broker:
    def __init__(self, stil: bool = False):
        self.klanten: set[Klant] = set()
        self.bewaard: dict[str, bytes] = {}   # retained berichten: topic -> payload
        self.stil = stil
        self.aantal = 0

    def log(self, *a):
        if not self.stil:
            print(*a, flush=True)

    async def lees(self, k: Klant):
        kop = await k.reader.readexactly(1)
        waarde, factor = 0, 1
        for _ in range(4):
            b = (await k.reader.readexactly(1))[0]
            waarde += (b & 127) * factor
            if not b & 128:
                break
            factor *= 128
        else:
            raise ValueError("ongeldige pakketlengte")
        return kop[0] >> 4, kop[0] & 15, (await k.reader.readexactly(waarde) if waarde else b"")

    async def behandel(self, reader, writer):
        k = Klant(reader, writer)
        try:
            soort, _, data = await asyncio.wait_for(self.lees(k), timeout=10)
            if soort != CONNECT:
                return
            n = struct.unpack("!H", data[:2])[0]
            pos = 2 + n
            niveau, vlaggen = data[pos], data[pos + 1]
            k.keepalive = struct.unpack("!H", data[pos + 2:pos + 4])[0]
            pos += 4
            n = struct.unpack("!H", data[pos:pos + 2])[0]
            k.id = data[pos + 2:pos + 2 + n].decode("utf-8", "replace") or f"klant-{id(k):x}"
            if niveau not in (3, 4):
                k.stuur(pakket(CONNACK, 0, b"\x00\x01"))   # protocolversie niet ondersteund
                return
            for ander in [x for x in self.klanten if x.id == k.id]:
                ander.writer.close()                        # zelfde id: de oude verbinding sluiten
            self.klanten.add(k)
            k.stuur(pakket(CONNACK, 0, b"\x00\x00"))
            self.log(f"[mqtt] verbonden: {k.id} ({len(self.klanten)} actief)")
            await self.lus(k)
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.TimeoutError, OSError):
            pass
        except Exception as e:  # noqa: BLE001 - één slechte klant mag de broker niet stoppen
            self.log(f"[mqtt] fout bij {k.id}: {e}")
        finally:
            if k in self.klanten:
                self.klanten.discard(k)
                self.log(f"[mqtt] verbroken: {k.id} ({len(self.klanten)} actief)")
            writer.close()

    async def lus(self, k: Klant):
        while True:
            timeout = k.keepalive * 1.5 if k.keepalive else None
            soort, vlaggen, data = await asyncio.wait_for(self.lees(k), timeout=timeout)
            if soort == PUBLISH:
                qos, retain = (vlaggen >> 1) & 3, vlaggen & 1
                n = struct.unpack("!H", data[:2])[0]
                topic = data[2:2 + n].decode("utf-8", "replace")
                pos = 2 + n
                if qos:
                    pid = data[pos:pos + 2]
                    pos += 2
                    k.stuur(pakket(PUBACK if qos == 1 else PUBREC, 0, pid))
                payload = data[pos:]
                if retain:
                    if payload:
                        self.bewaard[topic] = payload
                    else:
                        self.bewaard.pop(topic, None)
                self.verdeel(topic, payload)
            elif soort == PUBREL:
                k.stuur(pakket(PUBCOMP, 0, data[:2]))
            elif soort == SUBSCRIBE:
                pid, pos, codes, nieuw = data[:2], 2, bytearray(), []
                while pos < len(data):
                    n = struct.unpack("!H", data[pos:pos + 2])[0]
                    f = data[pos + 2:pos + 2 + n].decode("utf-8", "replace")
                    pos += 3 + n                                 # + gevraagde QoS
                    k.abonnementen.add(f)
                    nieuw.append(f)
                    codes.append(0)                              # we leveren met QoS 0
                k.stuur(pakket(SUBACK, 0, pid + bytes(codes)))
                self.log(f"[mqtt] {k.id} luistert op {', '.join(nieuw)}")
                for topic, payload in self.bewaard.items():
                    if any(past(f, topic) for f in nieuw):
                        k.stuur(pakket(PUBLISH, 1, tekst(topic.encode()) + payload))
            elif soort == UNSUBSCRIBE:
                pid, pos = data[:2], 2
                while pos < len(data):
                    n = struct.unpack("!H", data[pos:pos + 2])[0]
                    k.abonnementen.discard(data[pos + 2:pos + 2 + n].decode("utf-8", "replace"))
                    pos += 2 + n
                k.stuur(pakket(UNSUBACK, 0, pid))
            elif soort == PINGREQ:
                k.stuur(pakket(PINGRESP, 0, b""))
            elif soort == DISCONNECT:
                return
            # PUBACK/PUBREC/PUBCOMP van klanten: wij sturen enkel QoS 0, dus niets te doen
            await k.writer.drain()

    def verdeel(self, topic: str, payload: bytes):
        self.aantal += 1
        bericht = None
        for k in list(self.klanten):
            if any(past(f, topic) for f in k.abonnementen):
                bericht = bericht or pakket(PUBLISH, 0, tekst(topic.encode()) + payload)
                k.stuur(bericht)


async def draai(poort: int, stil: bool):
    broker = Broker(stil)
    servers = []
    for host in ("127.0.0.1", "::1"):
        try:
            servers.append(await asyncio.start_server(broker.behandel, host, poort))
        except OSError as e:
            if host == "127.0.0.1":
                sys.exit(f"[mqtt] kan poort {poort} niet openen ({e}). Draait er al een broker?")
    print(f"[mqtt] mini-broker klaar op poort {poort} (enkel deze computer)", flush=True)
    await asyncio.gather(*(s.serve_forever() for s in servers))


def main():
    ap = argparse.ArgumentParser(description="Mini MQTT-broker voor de GramIQ-demo")
    ap.add_argument("--poort", type=int, default=1883)
    ap.add_argument("--stil", action="store_true", help="geen verbindingen loggen")
    a = ap.parse_args()
    try:
        asyncio.run(draai(a.poort, a.stil))
    except KeyboardInterrupt:
        print("[mqtt] gestopt.")


if __name__ == "__main__":
    main()
