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
ORIGIN_ADDRESS=Bahnhofstrasse 1, 8000 Zürich
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

### Optionale Einstellungen (.env)
```ini
# Ausgabeformat
COLOR_OUTPUT=auto          # auto|always|never (Standard: 1/auto, respektiert NO_COLOR)
ASCII_OUTPUT=0             # 1 erzwingt ASCII-Bullets/Trennlinien
COMPACT_WEEKLY=0           # 1 aktiviert kompakte Wochenansicht (einzeilige Tageszusammenfassung)

# Persönliche Pausen (off-login), werden zur Arbeitszeit addiert
PERSONAL_BREAKS_MIN=60     # Minuten pro Office-Tag (0..60)

# Wochenplanung
WEEKLY_BLOCKS=OPEN,OPEN,OPEN,OPEN,OPEN   # Alternativ per Slots (siehe unten)
WEEKLY_START_DATE=2025-09-08             # Optional, sonst aktueller Montag
WEEKLY_HO_PERCENT=40                     # Ziel-Home-Office in % (max 40)

# Alternative Zeitslots für Halbtag (PM/AM)
AFTERNOON_ARRIVAL_LOCAL=13:30
AFTERNOON_WINDOW_START_LOCAL=11:00

# Abend-Optimierung
EXTEND_STEP_MINUTES=30
EXTEND_WORSE_STEPS=6
EXTEND_LATEST_LOCAL=22:00
EXTEND_TARGET_SAVE_MIN=10
AVOID_THRESHOLD_MIN=8
AVOID_STEP_MINUTES=15
# Menschliche Präferenzen / Grenzen
MAX_LEAVE_TIME_LOCAL=19:30         # späteste akzeptable Abfahrtszeit (hartes Tageslimit)
FRIDAY_EARLY_CUTOFF_LOCAL=17:30    # freitags bevorzugt früh gehen (härteste Schranke gewinnt)
LATE_PENALTY_START_LOCAL=18:00     # ab wann ein „Lifestyle“-Malus greift
LATE_PENALTY_PER_15_MIN=2          # Malus-Minuten je 15 Min nach LATE_PENALTY_START_LOCAL
# Zeitkonto (Kompensation)
TIMEBANK_CURRENT_MIN=0              # aktuelles Plus (Minuten), z. B. 120 für +2h
TIMEBANK_CAP_MIN=3000               # Obergrenze (Minuten), Standard 3000 (=50h)
TIMEBANK_MAX_SPEND_PER_DAY_MIN=0    # max. pro Tag früher gehen (Gym etc.), 0 = unbegrenzt
EXTENSION_ACTIVITY=gym              # gym|work – beschreibt, was du in der Extra-/Wartezeit machst

# Gym-Optionen
GYM_ENABLED=1
GYM_ADDRESS_1=Suurstoffi 8/10, 6343 Risch-Rotkreuz
GYM_ADDRESS_2=Baarerstrasse 53, 6300 Zug
GYM_TRAIN_MIN_MINUTES=90
GYM_TRAIN_MAX_MINUTES=120
GYM_TRAIN_STEP_MINUTES=15
GYM_MAX_DAYS_PER_WEEK=3
GYM_PREFERRED_DAYS=MO,WE,FR     # bevorzugte Wochentage für Gym (Auswahl priorisiert)
GYM_LEAVE_MODE=earliest         # earliest: leave at earliest end; early: allow leaving earlier using timebank
GYM_COMBO_MAX=60                # Performance-Kappe für Gym-Kombinationen pro Tag

# API-Budget & Caching
MAX_API_CALLS_PER_RUN=300       # harte Obergrenze pro Scriptlauf
ROUTE_CACHE_GRANULARITY_MIN=5..15   # Cache-Raster und Probe-Fenster ("Bucket..ProbeWindow" in Minuten)
ROUTE_CACHE_FILE=routes_cache.json
ROUTE_CACHE_MAX_ENTRIES=50000
```

Erläuterung Zeitkonto:
- Mit `EXTENSION_ACTIVITY=gym` kann das Tool vorschlagen, früher zu gehen (nach 8h Pensum) und ausserhalb zu warten/trainieren, bis der Verkehr abflaut.
- Dabei kann – falls konfiguriert – vom Zeitkonto „verbraucht“ werden (`TIMEBANK_CURRENT_MIN`), begrenzt pro Tag (`TIMEBANK_MAX_SPEND_PER_DAY_MIN`) und insgesamt (`TIMEBANK_CAP_MIN`).
- Das Ziel ist, die Gesamtfahrzeit zu reduzieren und die Wartezeit sinnvoll zu nutzen, ohne die Bank-Grenzen zu überschreiten.

# Optional pro Wochentag Halbtag-Slots statt WEEKLY_BLOCKS (office|home|off)
# Beispiel: Montag Office am Morgen, Home am Nachmittag
MO_AM=office
MO_PM=home
TU_AM=home
TU_PM=off
WE_AM=
WE_PM=
TH_AM=
TH_PM=
FR_AM=
FR_PM=
```

## Nutzung
```bash
source .venv/bin/activate
python pendelplaner.py
```

### CLI-Optionen
```bash
python pendelplaner.py [--color auto|always|never] [--ascii] [--width N] [--compact-weekly|--no-compact-weekly] [--quiet] [--no-cache]
```
- `--color`: Farbmodus (Standard aus `COLOR_OUTPUT` oder automatisch TTY-abhängig; respektiert `NO_COLOR`).
- `--ascii`: Erzwingt ASCII-Ausgabe (z. B. in Logs/CI ohne UTF‑8).
- `--width`: Maximale Breite für Trennlinien (Standard: automatische Terminalbreite, 40..120).
- `--compact-weekly`/`--no-compact-weekly`: Kompakte Wochenansicht umschalten.
- `--quiet`: Unterdrückt INFO-Logs während des Renderings (nur WARN/ERROR).
- `--no-cache`: Umgeht den lokalen Route-Cache für diesen Lauf (erzwingt frische API-Abfragen).

Beispiele:
```bash
# Automatische Farben, kompakte Wochenansicht
python pendelplaner.py --color auto --compact-weekly

# Immer Farben, fixe Breite und ASCII (z. B. für Umleitungen in Files)
python pendelplaner.py --color always --ascii --width 80
```

### Wochenplan-Modus
Der Wochenplan wird aktiviert, wenn `WEEKLY_BLOCKS` gesetzt ist oder irgendeiner der Slot-Keys (`MO_AM`, `TU_PM`, …).

Beispiel mit `WEEKLY_BLOCKS` und HO-Zielquote:
```ini
WEEKLY_BLOCKS=OPEN,HO,OPEN,OFFICE,OPEN
WEEKLY_HO_PERCENT=40
```
Startdatum optional setzen (sonst aktueller Montag):
```ini
WEEKLY_START_DATE=2025-09-08
```
Beispiel mit Halbtag-Slots:
```ini
MO_AM=office
MO_PM=home
TH_AM=office
TH_PM=off
```
Die HO-Quote wirkt als Obergrenze über `HO`, `HO-AM`, `HO-PM` und wird – falls nötig – durch Umwandlung von Tagen/Halbtagen in `OFFICE` eingehalten.

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
- Lege den API Key ausschliesslich in `.env` ab.
- Nutze API-Einschränkungen (HTTP-Referer/Quellen-IP) und rotiere Keys bei Bedarf.

