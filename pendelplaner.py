#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Bestes Abfahrts- und Rückfahrzeitfenster mit Google Routes API (Traffic-aware)

Vorgehen:
1) Scannt Abfahrten am Morgen in Intervallen (z.B. 5 Min) und wählt jene
   mit kürzester Fahrzeit, die trotzdem spätestens bis <latest_arrival_local>
   ankommt.
2) Berechnet daraus die Rückfahrzeit: Arbeitszeit + variable Mittagspause
   (Scan zwischen lunch_min..lunch_max, z.B. in 5-Min-Schritten) und wählt
   das Minimum der abendlichen Fahrzeit.

Nur Variablen im Block "KONFIG" anpassen. API-Key einsetzen.
"""

import requests
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# ------------------ KONFIG ------------------
API_KEY = ""
ORIGIN_ADDRESS = "Rümlangstrasse 54, 8052 Zürich"
DESTINATION_ADDRESS = "Bahnhofstrasse 25, 5647 Oberrüti"

# Späteste Ankunftszeit (lokal, 24h-Format "HH:MM")
LATEST_ARRIVAL_LOCAL = "09:00"

# Arbeitszeit/Break
WORK_HOURS = 8.0                 # z.B. 8 Stunden
LUNCH_MIN_MINUTES = 30           # min. Mittagspause in Minuten
LUNCH_MAX_MINUTES = 60           # max. Mittagspause in Minuten
LUNCH_STEP_MINUTES = 5           # Schrittweite beim Scannen

# Suchfenster & Raster am Morgen
MORNING_WINDOW_START_LOCAL = "05:00"  # ab wann frühestens abfahren (lokal)
STEP_MINUTES = 5                      # Raster für die Scans am Morgen

# Optional: Datum (Standard = heute). Für “morgen” -> +1 Tag.
DAY_OFFSET = 0

# Zeitzone
TZ = ZoneInfo("Europe/Zurich")
# --------------------------------------------


"""Optional overrides via environment variables so the script can be configured
without editing the file. Example env vars:
  - GOOGLE_MAPS_API_KEY
  - ORIGIN_ADDRESS
  - DESTINATION_ADDRESS
  - LATEST_ARRIVAL_LOCAL (HH:MM)
  - MORNING_WINDOW_START_LOCAL (HH:MM)
  - WORK_HOURS (float)
  - LUNCH_MIN_MINUTES, LUNCH_MAX_MINUTES, LUNCH_STEP_MINUTES
  - STEP_MINUTES
  - DAY_OFFSET
  - TZ (IANA timezone like Europe/Zurich)
"""
# Load environment variables from a .env file if present (project root)
load_dotenv()
API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", API_KEY)
ORIGIN_ADDRESS = os.environ.get("ORIGIN_ADDRESS", ORIGIN_ADDRESS)
DESTINATION_ADDRESS = os.environ.get("DESTINATION_ADDRESS", DESTINATION_ADDRESS)
LATEST_ARRIVAL_LOCAL = os.environ.get("LATEST_ARRIVAL_LOCAL", LATEST_ARRIVAL_LOCAL)
MORNING_WINDOW_START_LOCAL = os.environ.get("MORNING_WINDOW_START_LOCAL", MORNING_WINDOW_START_LOCAL)
WORK_HOURS = float(os.environ.get("WORK_HOURS", WORK_HOURS))
LUNCH_MIN_MINUTES = int(os.environ.get("LUNCH_MIN_MINUTES", LUNCH_MIN_MINUTES))
LUNCH_MAX_MINUTES = int(os.environ.get("LUNCH_MAX_MINUTES", LUNCH_MAX_MINUTES))
LUNCH_STEP_MINUTES = int(os.environ.get("LUNCH_STEP_MINUTES", LUNCH_STEP_MINUTES))
STEP_MINUTES = int(os.environ.get("STEP_MINUTES", STEP_MINUTES))
DAY_OFFSET = int(os.environ.get("DAY_OFFSET", DAY_OFFSET))
tz_override = os.environ.get("TZ")
if tz_override:
    TZ = ZoneInfo(tz_override)

def ensure_api_key_configured() -> None:
    """Fail fast with a helpful message when API key is missing."""
    if not API_KEY or not API_KEY.strip():
        raise RuntimeError(
            "Kein Google API Key gesetzt. Setze die Umgebungsvariable "
            "GOOGLE_MAPS_API_KEY und stelle sicher, dass das 'Routes API' "
            "in deinem Google Cloud Projekt aktiviert ist (Abrechnung aktiv)."
        )

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"

HEADERS = {
    "Content-Type": "application/json",
    "X-Goog-Api-Key": API_KEY,
    # Nur Felder anfordern, die wir brauchen -> Performant & Required
    # (siehe X-Goog-FieldMask-Anforderung in der Doku)
    "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.legs.duration",
}

def to_rfc3339_local(dt_local: datetime) -> str:
    """Datetime mit lokaler TZ in RFC3339 (mit Offset) für departureTime."""
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=TZ)
    return dt_local.isoformat()

def parse_duration_to_minutes(duration_str: str) -> float:
    """
    Google liefert Durations als Protobuf-Format z.B. "1234.56s".
    Wandelt in Minuten um.
    """
    s = duration_str.strip().rstrip("s")
    seconds = float(s)
    return seconds / 60.0

def compute_drive_duration_minutes(origin_addr: str, destination_addr: str, departure_dt_local: datetime) -> float:
    """
    Fragt die Fahrdauer (traffic-aware) für eine konkrete Abfahrtszeit ab.
    Gibt Minuten zurück. Raises bei API-Fehlern mit detailierter Meldung.
    """
    body = {
        "origin": {"address": origin_addr},
        "destination": {"address": destination_addr},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
        "departureTime": to_rfc3339_local(departure_dt_local),
    }
    resp = requests.post(ROUTES_URL, headers=HEADERS, json=body, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"Routes API Fehler {resp.status_code}: {resp.text}")
    data = resp.json()
    routes = data.get("routes", [])
    if not routes:
        raise RuntimeError("Keine Route gefunden (leere routes-Liste).")
    # Erste (empfohlene) Route nehmen
    route = routes[0]
    dur = route.get("duration")
    if not dur:
        raise RuntimeError("Antwort enthält keine duration.")
    return parse_duration_to_minutes(dur)

def scan_morning_best_departure(day_local: datetime) -> dict:
    """
    Scannt Abfahrten am Morgen im STEP_MINUTES-Raster ab MORNING_WINDOW_START_LOCAL
    bis LATEST_ARRIVAL_LOCAL. Wählt die kürzeste Dauer, die rechtzeitig ankommt.
    Rückgabe: Dict mit keys:
      - best_departure (datetime)
      - best_arrival (datetime)
      - best_duration_minutes (float)
    Falls keine passende Abfahrt gefunden wird, wird eine Exception geworfen.
    """
    # Basisdatum lokalisieren
    day_local = day_local.replace(tzinfo=TZ, hour=0, minute=0, second=0, microsecond=0)

    # Zeitpunkte bauen
    h_s, m_s = map(int, MORNING_WINDOW_START_LOCAL.split(":"))
    h_deadline, m_deadline = map(int, LATEST_ARRIVAL_LOCAL.split(":"))

    start_dt = day_local.replace(hour=h_s, minute=m_s)
    latest_arrival_dt = day_local.replace(hour=h_deadline, minute=m_deadline)
    now_local = datetime.now(TZ)
    if latest_arrival_dt <= now_local:
        raise RuntimeError(
            "Die Zielankunft für heute liegt in der Vergangenheit. Setze DAY_OFFSET=1 "
            "oder wähle eine spätere LATEST_ARRIVAL_LOCAL."
        )

    best = {
        "best_departure": None,
        "best_arrival": None,
        "best_duration_minutes": None,
    }
    last_error_message = None

    # Wir scannen alle Abfahrten, die potenziell bis zur Deadline ankommen können
    current = start_dt
    while current <= latest_arrival_dt:
        # Nur zukünftige Zeitpunkte an die API senden
        if current <= now_local:
            current += timedelta(minutes=STEP_MINUTES)
            continue
        try:
            dur_min = compute_drive_duration_minutes(ORIGIN_ADDRESS, DESTINATION_ADDRESS, current)
        except Exception as e:
            last_error_message = str(e)
            current += timedelta(minutes=STEP_MINUTES)
            continue

        arrival = current + timedelta(minutes=dur_min)

        if arrival <= latest_arrival_dt:
            if best["best_duration_minutes"] is None or dur_min < best["best_duration_minutes"]:
                best["best_departure"] = current
                best["best_arrival"] = arrival
                best["best_duration_minutes"] = dur_min

        current += timedelta(minutes=STEP_MINUTES)

    if best["best_departure"] is None:
        msg = (
            "Keine Abfahrt gefunden, die rechtzeitig ankommt. "
            "Erweitere das Suchfenster nach vorne oder erhöhe STEP_MINUTES-Auflösung."
        )
        if last_error_message:
            msg += f" Letzte Fehlermeldung: {last_error_message}"
        raise RuntimeError(msg)

    return best

def choose_best_evening_departure(morning_arrival_local: datetime) -> dict:
    """
    Geht von gegebener Ankunft (morgens) aus. Berechnet für jede erlaubte
    Mittagspause die Endzeit (Abfahrt abends) und fragt dafür die Rückfahrdauer ab.
    Wählt die minimale Rückfahrdauer.
    Rückgabe: Dict mit keys:
      - lunch_minutes
      - evening_departure (datetime)
      - evening_duration_minutes (float)
      - evening_arrival_home (datetime)
    """
    work_minutes = int(WORK_HOURS * 60)

    best = {
        "lunch_minutes": None,
        "evening_departure": None,
        "evening_duration_minutes": None,
        "evening_arrival_home": None,
    }
    last_error_message = None

    for L in range(LUNCH_MIN_MINUTES, LUNCH_MAX_MINUTES + 1, LUNCH_STEP_MINUTES):
        evening_departure = morning_arrival_local + timedelta(minutes=work_minutes + L)
        try:
            dur_min = compute_drive_duration_minutes(DESTINATION_ADDRESS, ORIGIN_ADDRESS, evening_departure)
        except Exception as e:
            last_error_message = str(e)
            continue

        if best["evening_duration_minutes"] is None or dur_min < best["evening_duration_minutes"]:
            best["lunch_minutes"] = L
            best["evening_departure"] = evening_departure
            best["evening_duration_minutes"] = dur_min
            best["evening_arrival_home"] = evening_departure + timedelta(minutes=dur_min)

    if best["evening_departure"] is None:
        msg = "Konnte keine Rückfahrt berechnen (Abend)."
        if last_error_message:
            msg += f" Letzte Fehlermeldung: {last_error_message}"
        raise RuntimeError(msg)

    return best

def main():
    ensure_api_key_configured()
    # Basisdatum heute (mit Offset, z.B. morgen)
    today_local = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    day_local = today_local + timedelta(days=DAY_OFFSET)

    morning = scan_morning_best_departure(day_local)
    evening = choose_best_evening_departure(morning["best_arrival"])

    def fmt(dt: datetime) -> str:
        return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M")

    print("\n=== Ergebnisse (lokal: Europe/Zurich) ===")
    print(f"Späteste gewünschte Ankunft: {day_local.strftime('%Y-%m-%d')} {LATEST_ARRIVAL_LOCAL}")
    print("\n-- Hinfahrt --")
    print(f"Beste Abfahrt:      {fmt(morning['best_departure'])}")
    print(f"Ankunft (effektiv): {fmt(morning['best_arrival'])}")
    print(f"Fahrzeit:           {morning['best_duration_minutes']:.1f} min")

    print("\n-- Rückfahrt --")
    print(f"Gewählte Mittagspause:  {evening['lunch_minutes']} min")
    print(f"Abfahrt abends:         {fmt(evening['evening_departure'])}")
    print(f"Fahrzeit zurück:        {evening['evening_duration_minutes']:.1f} min")
    print(f"Ankunft zu Hause:       {fmt(evening['evening_arrival_home'])}")

    total_travel = morning["best_duration_minutes"] + evening["evening_duration_minutes"]
    print(f"\nGesamte Pendelzeit (hin+zurück): {total_travel:.1f} min")

if __name__ == "__main__":
    main()
