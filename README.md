# Pendelplaner

Ein kleines Tool, das mit der Google Routes API (verkehrsabhängig) die beste Abfahrtszeit am Morgen und die beste Rückfahrzeit am Abend findet.

## Voraussetzungen
- Python 3.11+
- Google Cloud Projekt mit aktivierter „Routes API“ und hinterlegter Abrechnung
- Ein API Key, der Zugriff auf `routes.googleapis.com` hat

## Installation
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Konfiguration (.env)
Erstelle eine Datei `.env` im Projektverzeichnis (oder passe die vorhandene an):
```ini
GOOGLE_MAPS_API_KEY=DEIN_API_KEY
ORIGIN_ADDRESS=Rümlangstrasse 54, 8052 Zürich
DESTINATION_ADDRESS=Bahnhofstrasse 25, 5647 Oberrüti
LATEST_ARRIVAL_LOCAL=09:00
MORNING_WINDOW_START_LOCAL=05:00
WORK_HOURS=8
LUNCH_MIN_MINUTES=30
LUNCH_MAX_MINUTES=60
LUNCH_STEP_MINUTES=5
STEP_MINUTES=5
DAY_OFFSET=0
TZ=Europe/Zurich
```
Hinweis: `.env` ist per `.gitignore` ausgeschlossen und wird nicht mit eingecheckt.

## Nutzung
```bash
source .venv/bin/activate
python pendelplaner.py
```

## Watch-Modus (optional)
```bash
nohup sh -c 'while true; do printf "\n===== %s =====\n" "$(date)"; \
  .venv/bin/python pendelplaner.py 2>&1; sleep 300; done' \
  >> watch.log 2>&1 & echo $! > watch.pid
```
Stoppen:
```bash
kill $(cat watch.pid)
```

## Sicherheit
- Lege den API Key ausschließlich in `.env` ab.
- Nutze API-Einschränkungen (HTTP-Referer/Quellen-IP) und rotiere Keys bei Bedarf.

