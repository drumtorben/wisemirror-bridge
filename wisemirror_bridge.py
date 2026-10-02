#!/usr/bin/env python3
"""
WiseMirror -> Home Assistant (MQTT) Bridge

Protokoll (aus der WiseMirror-App 1.3.9 rekonstruiert):
  Anfrage:  ASCII "What is the temperature of LEDWifi"
            per UDP an Port 8000 (App: Broadcast 255.255.255.255, Quellport 4026)
  Antwort:  ASCII "<x> <modell>+<temp>+<einheit>+<firmware>+<bssid>+<ip>[,<feuchte>]"
            temp:    Ganzzahl
            einheit: 1 = °C, 0 = °F
            feuchte: optional, 1..99 %

Nutzung:
  python wisemirror_bridge.py --once          # einmal abfragen, Rohantwort ausgeben (kein MQTT nötig)
  python wisemirror_bridge.py                 # Dauerbetrieb, publiziert per MQTT-Discovery

Konfiguration über Umgebungsvariablen (siehe unten).
"""

import argparse
import json
import logging
import os
import socket
import sys
import time

QUERY = b"What is the temperature of LEDWifi"
MIRROR_PORT = 8000

MIRROR_HOST = os.getenv("MIRROR_HOST", "255.255.255.255")  # IP des Spiegels oder Broadcast
SOURCE_PORT = int(os.getenv("SOURCE_PORT", "4026"))         # wie die App; 0 = zufällig
TIMEOUT = float(os.getenv("TIMEOUT", "2.0"))
INTERVAL = int(os.getenv("INTERVAL", "60"))
RETRIES = int(os.getenv("RETRIES", "3"))
# Manche Modelle haben keinen Feuchtesensor und senden einen festen Platzhalterwert.
PUBLISH_HUMIDITY = os.getenv("PUBLISH_HUMIDITY", "true").strip().lower() in ("1", "true", "yes", "on")

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
DISCOVERY_PREFIX = os.getenv("DISCOVERY_PREFIX", "homeassistant")
BASE_TOPIC = os.getenv("BASE_TOPIC", "wisemirror")

log = logging.getLogger("wisemirror")


def parse_reply(text: str):
    """Parst eine Antwort analog zu a0.b.a(TYPE_TEMP, ...) in der App."""
    text = text.strip()
    if not text:
        return None
    humidity = None
    if "," in text:
        main, hum = text.split(",", 1)
        try:
            humidity = max(1, min(99, int(hum.strip())))
        except ValueError:
            humidity = None
    else:
        main = text
    parts = main.split("+")
    if len(parts) < 6:
        return None
    head = parts[0].strip().split(" ")
    if len(head) < 2:
        return None
    model = head[1]
    if not any(m in model for m in ("8J11", "8J12", "2K02", "2M09")):
        log.debug("Unbekanntes Modell in Antwort: %r", text)
    try:
        temp = int(parts[1])
        unit = int(parts[2])
    except ValueError:
        return None
    ip = parts[-1].strip()
    if ip == "0.0.0.0":
        return None
    return {
        "model": model,
        "temperature": temp,
        "unit": "°C" if unit == 1 else "°F",
        "firmware": parts[3],
        "bssid": parts[-2].strip(),
        "ip": ip,
        "humidity": humidity,
        "raw": text,
    }


def query_mirrors():
    """Sendet die Anfrage und sammelt alle Antworten bis zum Timeout."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        sock.bind(("", SOURCE_PORT))
    except OSError:
        log.warning("Quellport %d belegt, nutze zufälligen Port", SOURCE_PORT)
        sock.bind(("", 0))
    sock.settimeout(TIMEOUT)
    results = {}
    try:
        sock.sendto(QUERY, (MIRROR_HOST, MIRROR_PORT))
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(1024)
            except socket.timeout:
                break
            if data.strip() == QUERY:  # eigenes Broadcast-Echo ignorieren
                continue
            text = data.decode("utf-8", errors="replace")
            log.debug("Antwort von %s: %r", addr, text)
            parsed = parse_reply(text)
            if parsed:
                results[parsed["bssid"]] = parsed
            else:
                log.info("Nicht parsebare Antwort von %s: %r", addr, text)
    finally:
        sock.close()
    return list(results.values())


def device_id(m):
    return "wisemirror_" + m["bssid"].replace(":", "").lower()


def publish_discovery(client, m):
    did = device_id(m)
    device = {
        "identifiers": [did],
        "name": "WiseMirror " + m["bssid"].replace(":", "")[-5:].upper(),
        "manufacturer": "Adsmart",
        "model": m["model"],
        "sw_version": m["firmware"],
    }
    state_topic = f"{BASE_TOPIC}/{did}/state"
    avail_topic = f"{BASE_TOPIC}/{did}/availability"
    common = {
        "state_topic": state_topic,
        "availability_topic": avail_topic,
        "device": device,
        "state_class": "measurement",
    }
    sensors = {
        "temperature": {
            "name": "Temperatur",
            "device_class": "temperature",
            "unit_of_measurement": m["unit"],
            "value_template": "{{ value_json.temperature }}",
        },
    }
    hum_topic = f"{DISCOVERY_PREFIX}/sensor/{did}/humidity/config"
    if not PUBLISH_HUMIDITY or m["humidity"] is None:
        # leerer retained Payload entfernt eine evtl. früher angelegte Entität aus HA
        client.publish(hum_topic, "", retain=True)
    else:
        sensors["humidity"] = {
            "name": "Luftfeuchtigkeit",
            "device_class": "humidity",
            "unit_of_measurement": "%",
            "value_template": "{{ value_json.humidity }}",
        }
    for key, cfg in sensors.items():
        payload = {**common, **cfg, "unique_id": f"{did}_{key}", "object_id": f"{did}_{key}"}
        client.publish(f"{DISCOVERY_PREFIX}/sensor/{did}/{key}/config",
                       json.dumps(payload), retain=True)


def run_loop():
    import paho.mqtt.client as mqtt

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="wisemirror-bridge")
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    if not MQTT_HOST:
        log.error("MQTT_HOST ist nicht gesetzt")
        sys.exit(2)
    delay = 5
    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            break
        except OSError as e:
            log.error("MQTT-Broker %s:%s nicht erreichbar (%s), neuer Versuch in %ss",
                      MQTT_HOST, MQTT_PORT, e, delay)
            time.sleep(delay)
            delay = min(delay * 2, 300)
    log.info("Verbunden mit MQTT-Broker %s:%s", MQTT_HOST, MQTT_PORT)
    client.loop_start()

    announced = set()
    misses = {}
    while True:
        mirrors = []
        for _ in range(RETRIES):
            mirrors = query_mirrors()
            if mirrors:
                break
            time.sleep(1)

        seen = set()
        for m in mirrors:
            did = device_id(m)
            seen.add(did)
            misses[did] = 0
            if did not in announced:
                publish_discovery(client, m)
                announced.add(did)
                log.info("Spiegel gefunden: %s (%s, %s)", m["bssid"], m["ip"], m["model"])
            client.publish(f"{BASE_TOPIC}/{did}/availability", "online", retain=True)
            client.publish(f"{BASE_TOPIC}/{did}/state", json.dumps({
                "temperature": m["temperature"],
                "humidity": m["humidity"] if PUBLISH_HUMIDITY else None,
                "ip": m["ip"],
            }), retain=True)
            log.debug("%s: %s%s, %s %%", did, m["temperature"], m["unit"], m["humidity"])

        for did in announced - seen:
            misses[did] = misses.get(did, 0) + 1
            if misses[did] >= 3:
                client.publish(f"{BASE_TOPIC}/{did}/availability", "offline", retain=True)

        time.sleep(INTERVAL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="einmal abfragen und Ergebnis ausgeben")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose or os.getenv("DEBUG") else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.once:
        mirrors = query_mirrors()
        if not mirrors:
            print("Keine Antwort. MIRROR_HOST, Firewall und Netzwerkmodus prüfen.", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(mirrors, indent=2, ensure_ascii=False))
        return
    run_loop()


if __name__ == "__main__":
    main()
