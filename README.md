# wisemirror-bridge

Liest Innentemperatur und Luftfeuchte aus einem WiseMirror-Smart-Spiegel (Adsmart, ESP-basiert, Hostname `LEDWifi`) aus und stellt sie per MQTT-Discovery in Home Assistant bereit.

## Protokoll

Rekonstruiert aus der WiseMirror-Android-App 1.3.9 (`com.smartteam.smartmirror`).

Anfrage per UDP an Port 8000 (die App nutzt Broadcast `255.255.255.255` und Quellport 4026):

```
What is the temperature of LEDWifi
```

Antwort im Klartext:

```
<prefix> <modell>+<temp>+<einheit>+<firmware>+<bssid>+<ip>[,<feuchte>]
```

| Feld | Bedeutung |
|---|---|
| modell | `8J11`, `8J12`, `2K02` oder `2M09` |
| temp | Ganzzahl |
| einheit | `1` = °C, `0` = °F |
| feuchte | optional, 1–99 % |

Port 8001 nutzt ein separates Binärprotokoll für Einstellungen (Rahmen `0xA5 … 0x5A`); das wird hier nicht verwendet.

## Schnelltest

```bash
python3 wisemirror_bridge.py --once -v
```

Benötigt kein MQTT. Kommt keine Antwort, `MIRROR_HOST` auf die IP des Spiegels setzen.

## Betrieb mit Docker / Portainer

Portainer: **Stacks → Add stack → Repository**, dieses Repo angeben, Compose-Pfad `docker-compose.yml`, Umgebungsvariablen aus `.env.example` im Formular setzen.

Lokal:

```bash
cp .env.example .env   # anpassen
docker compose up -d --build
```

`network_mode: host` ist erforderlich, sonst erreichen Broadcast und Antwort den Container nicht.

## Konfiguration

| Variable | Standard | Beschreibung |
|---|---|---|
| `MIRROR_HOST` | `255.255.255.255` | Broadcast oder feste IP des Spiegels |
| `MQTT_HOST` | `localhost` | MQTT-Broker |
| `MQTT_PORT` | `1883` | |
| `MQTT_USER` / `MQTT_PASSWORD` | – | optional |
| `INTERVAL` | `60` | Abfrageintervall in Sekunden |
| `SOURCE_PORT` | `4026` | Quellport wie die App; `0` = zufällig |
| `DEBUG` | – | ausführliches Logging |

In Home Assistant erscheint pro Spiegel ein Gerät mit den Sensoren Temperatur und (falls geliefert) Luftfeuchtigkeit.
