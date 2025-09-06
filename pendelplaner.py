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
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import dotenv_values

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


"""Konfiguration ausschließlich über .env (siehe .env.example)."""
CONFIG = dotenv_values(os.path.join(os.path.dirname(__file__), ".env"))

API_KEY = CONFIG.get("GOOGLE_MAPS_API_KEY", "")
ORIGIN_ADDRESS = CONFIG.get("ORIGIN_ADDRESS", ORIGIN_ADDRESS)
DESTINATION_ADDRESS = CONFIG.get("DESTINATION_ADDRESS", DESTINATION_ADDRESS)
LATEST_ARRIVAL_LOCAL = CONFIG.get("LATEST_ARRIVAL_LOCAL", LATEST_ARRIVAL_LOCAL)
MORNING_WINDOW_START_LOCAL = CONFIG.get("MORNING_WINDOW_START_LOCAL", MORNING_WINDOW_START_LOCAL)
WORK_HOURS = float(CONFIG.get("WORK_HOURS", str(WORK_HOURS)))
LUNCH_MIN_MINUTES = int(CONFIG.get("LUNCH_MIN_MINUTES", str(LUNCH_MIN_MINUTES)))
LUNCH_MAX_MINUTES = int(CONFIG.get("LUNCH_MAX_MINUTES", str(LUNCH_MAX_MINUTES)))
LUNCH_STEP_MINUTES = int(CONFIG.get("LUNCH_STEP_MINUTES", str(LUNCH_STEP_MINUTES)))
STEP_MINUTES = int(CONFIG.get("STEP_MINUTES", str(STEP_MINUTES)))
DAY_OFFSET = int(CONFIG.get("DAY_OFFSET", str(DAY_OFFSET)))
tz_override = CONFIG.get("TZ")
if tz_override:
    TZ = ZoneInfo(tz_override)

# --------------- Logging ---------------
LOG_LEVEL = CONFIG.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("pendelplaner")
logger.debug("Logger initialized with level %s", LOG_LEVEL)

# Afternoon (half-day PM) defaults
AFTERNOON_ARRIVAL_LOCAL = CONFIG.get("AFTERNOON_ARRIVAL_LOCAL", "13:30")
AFTERNOON_WINDOW_START_LOCAL = CONFIG.get("AFTERNOON_WINDOW_START_LOCAL", "11:00")

try:
    EXTEND_STEP_MINUTES = int(CONFIG.get("EXTEND_STEP_MINUTES", "30"))
except ValueError:
    EXTEND_STEP_MINUTES = 30
try:
    EXTEND_WORSE_STEPS = int(CONFIG.get("EXTEND_WORSE_STEPS", "6"))
except ValueError:
    EXTEND_WORSE_STEPS = 6
EXTEND_LATEST_LOCAL = CONFIG.get("EXTEND_LATEST_LOCAL", "22:00")
try:
    EXTEND_TARGET_SAVE_MIN = float(CONFIG.get("EXTEND_TARGET_SAVE_MIN", "10"))
except ValueError:
    EXTEND_TARGET_SAVE_MIN = 10.0

# Weekly planning configuration
WEEKLY_BLOCKS = CONFIG.get("WEEKLY_BLOCKS", "")
WEEKLY_START_DATE = CONFIG.get("WEEKLY_START_DATE", "")  # YYYY-MM-DD optional
try:
    WEEKLY_HO_PERCENT = int(CONFIG.get("WEEKLY_HO_PERCENT", "0"))
except ValueError:
    WEEKLY_HO_PERCENT = 0
WEEKLY_HO_PERCENT = max(0, min(40, WEEKLY_HO_PERCENT))

def ensure_api_key_configured() -> None:
    """Fail fast with a helpful message when API key is missing."""
    if not API_KEY or not API_KEY.strip():
        raise RuntimeError(
            "Kein Google API Key gesetzt. Setze die Umgebungsvariable "
            "GOOGLE_MAPS_API_KEY und stelle sicher, dass das 'Routes API' "
            "in deinem Google Cloud Projekt aktiviert ist (Abrechnung aktiv)."
        )
    logger.debug("API key present (len=%d)", len(API_KEY.strip()))

def parse_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    v = value.strip().lower()
    return v in {"1", "true", "yes", "y", "on"}

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

def fmt_hhmm(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%H:%M")

def fmt_minutes(mins: float) -> int:
    try:
        return int(round(float(mins)))
    except Exception:
        return int(mins)

def fmt_dur_hm(mins: float) -> str:
    m = fmt_minutes(mins)
    h = m // 60
    r = m % 60
    if h > 0:
        return f"{h}h {r}min"
    return f"{r}min"

def suggest_evening_extension(
    baseline_departure: datetime,
    baseline_duration_min: float,
    step_minutes: int,
    worse_steps_limit: int,
) -> dict | None:
    """Search later departure times in step_minutes increments until we observe
    consecutive non-improvements (worse_steps_limit). Return best improvement or None.
    """
    best = None
    worse_streak = 0
    extra = step_minutes
    # Stop if we pass the configured latest local time
    h_latest, m_latest = map(int, EXTEND_LATEST_LOCAL.split(":"))
    while True:
        depart = baseline_departure + timedelta(minutes=extra)
        if depart.astimezone(TZ).hour > h_latest or (
            depart.astimezone(TZ).hour == h_latest and depart.astimezone(TZ).minute > m_latest
        ):
            break
        try:
            dur = compute_drive_duration_minutes(DESTINATION_ADDRESS, ORIGIN_ADDRESS, depart)
        except Exception:
            worse_streak += 1
            if worse_streak >= worse_steps_limit:
                break
            extra += step_minutes
            continue
        save = baseline_duration_min - dur
        if save > 0.5:  # require at least 0.5 minute improvement
            worse_streak = 0
            if best is None or save > best["save"]:
                best = {
                    "dep": depart,
                    "dur": dur,
                    "save": save,
                    "arr": depart + timedelta(minutes=dur),
                    "extend_minutes": extra,
                }
            if save >= EXTEND_TARGET_SAVE_MIN:
                break
        else:
            worse_streak += 1
            if worse_streak >= worse_steps_limit:
                break
        extra += step_minutes
    return best

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
    logger.debug(
        "Requesting route: %s -> %s at %s",
        origin_addr,
        destination_addr,
        departure_dt_local.astimezone(TZ).strftime("%Y-%m-%d %H:%M"),
    )
    resp = requests.post(ROUTES_URL, headers=HEADERS, json=body, timeout=20)
    if resp.status_code != 200:
        logger.error("Routes API HTTP %s: %s", resp.status_code, resp.text[:300])
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

def plan_halfday_commute(
    day_local: datetime,
    section: str,
    latest_arrival_local: str,
    window_start_local: str,
    work_hours: float,
    lunch_min: int,
    lunch_max: int,
    lunch_step: int,
    step_minutes: int,
) -> dict:
    """
    Plan commute for a half-day.
    - section 'AM': arrive in the morning (latest_arrival_local/window_start_local), work ~4h, return around noon
    - section 'PM': arrive around midday (AFTERNOON_*), work ~4h, return evening
    Returns dict with outbound/inbound details.
    """
    assert section in {"AM", "PM"}

    # Save globals override temporarily
    global LATEST_ARRIVAL_LOCAL, MORNING_WINDOW_START_LOCAL, WORK_HOURS, LUNCH_MIN_MINUTES, LUNCH_MAX_MINUTES, LUNCH_STEP_MINUTES, STEP_MINUTES
    backup = (
        LATEST_ARRIVAL_LOCAL,
        MORNING_WINDOW_START_LOCAL,
        WORK_HOURS,
        LUNCH_MIN_MINUTES,
        LUNCH_MAX_MINUTES,
        LUNCH_STEP_MINUTES,
        STEP_MINUTES,
    )
    try:
        LATEST_ARRIVAL_LOCAL = latest_arrival_local
        MORNING_WINDOW_START_LOCAL = window_start_local
        WORK_HOURS = work_hours
        LUNCH_MIN_MINUTES = lunch_min
        LUNCH_MAX_MINUTES = lunch_max
        LUNCH_STEP_MINUTES = lunch_step
        STEP_MINUTES = step_minutes

        morning = scan_morning_best_departure(day_local)
        evening = choose_best_evening_departure(morning["best_arrival"])
        return {"outbound": morning, "inbound": evening}
    finally:
        (
            LATEST_ARRIVAL_LOCAL,
            MORNING_WINDOW_START_LOCAL,
            WORK_HOURS,
            LUNCH_MIN_MINUTES,
            LUNCH_MAX_MINUTES,
            LUNCH_STEP_MINUTES,
            STEP_MINUTES,
        ) = backup

def weekly_plan(start_date_local: datetime, config: dict) -> list:
    """Compute a weekly plan starting at start_date_local (Monday recommended).
    Config keys:
      - ho_percent (0..40)
      - blocks: list of 5 elements (Mon..Fri), each element one of: 'HO-AM', 'HO-PM', 'HO', 'OFF', 'OPEN', 'OFFICE'
      - defaults for times: latest_arrival_local, morning_window_start_local, work_hours, lunch_*
    Returns list of day plans.
    """
    logger.info("Weekly plan start: base=%s", start_date_local.strftime("%Y-%m-%d"))
    # Prepare initial blocks
    blocks = config.get("blocks", ["OPEN"] * 5)
    blocks = [b.strip().upper() for b in blocks]
    while len(blocks) < 5:
        blocks.append("OPEN")
    logger.info("Blocks initial: %s", ",".join(blocks))

    # Helper to compute total travel minutes for a full office day
    def compute_full_day_minutes(day_dt: datetime) -> float | None:
        try:
            plan = plan_halfday_commute(
                day_dt,
                section="AM",
                latest_arrival_local=config.get("latest_arrival_local", LATEST_ARRIVAL_LOCAL),
                window_start_local=config.get("morning_window_start_local", MORNING_WINDOW_START_LOCAL),
                work_hours=config.get("work_hours", WORK_HOURS),
                lunch_min=config.get("lunch_min", LUNCH_MIN_MINUTES),
                lunch_max=config.get("lunch_max", LUNCH_MAX_MINUTES),
                lunch_step=config.get("lunch_step", LUNCH_STEP_MINUTES),
                step_minutes=config.get("step_minutes", STEP_MINUTES),
            )
            return plan["outbound"]["best_duration_minutes"] + plan["inbound"]["evening_duration_minutes"]
        except Exception:
            return None

    # Allocate HO based on percent for OPEN days (simple greedy by longest commute)
    ho_hours_target = int(round(config.get("ho_percent", 0) / 100.0 * 40))
    ho_hours_target = max(0, min(16, ho_hours_target))
    if ho_hours_target > 0:
        candidates: list[tuple[int, float]] = []  # (index, commute_minutes)
        for i in range(5):
            if blocks[i] in {"OPEN", "OFFICE"}:
                day = (start_date_local + timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
                minutes = compute_full_day_minutes(day)
                if minutes is not None:
                    candidates.append((i, minutes))
        candidates.sort(key=lambda x: x[1], reverse=True)
        full_days = ho_hours_target // 8
        half_day = (ho_hours_target % 8) >= 4
        logger.info("HO optimizer: target_hours=%d candidates=%s", ho_hours_target, candidates)
        for idx, _ in candidates[:full_days]:
            blocks[idx] = "HO"
        if half_day and len(candidates) > full_days:
            idx, _ = candidates[full_days]
            # choose HO-AM by default
            blocks[idx] = "HO-AM" if blocks[idx] != "HO" else "HO"
    else:
        logger.info("HO optimizer: disabled or zero target")

    # Enforce HO maximum if provided (cap hours across HO/HO-AM/HO-PM)
    if ho_hours_target >= 0:
        # compute current HO hours
        def day_full_minutes(day_dt: datetime) -> float | None:
            return compute_full_day_minutes(day_dt)

        def half_minutes(day_dt: datetime, section: str) -> float | None:
            try:
                hp = plan_halfday_commute(
                    day_dt,
                    section=section,
                    latest_arrival_local=(AFTERNOON_ARRIVAL_LOCAL if section == "PM" else config.get("latest_arrival_local", LATEST_ARRIVAL_LOCAL)),
                    window_start_local=(AFTERNOON_WINDOW_START_LOCAL if section == "PM" else config.get("morning_window_start_local", MORNING_WINDOW_START_LOCAL)),
                    work_hours=4.0,
                    lunch_min=0,
                    lunch_max=0,
                    lunch_step=0,
                    step_minutes=config.get("step_minutes", STEP_MINUTES),
                )
                return hp["outbound"]["best_duration_minutes"] + hp["inbound"]["evening_duration_minutes"]
            except Exception:
                return None

        current_hours = 0
        for i in range(5):
            if blocks[i] == "HO":
                current_hours += 8
            elif blocks[i] in {"HO-AM", "HO-PM"}:
                current_hours += 4
        if current_hours > ho_hours_target:
            # build candidate flips to OFFICE with minimal added commute
            candidates: list[tuple[int, int, float]] = []  # (i, hours_reduced, added_minutes)
            for i in range(5):
                day_dt = (start_date_local + timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
                if blocks[i] == "HO":
                    fullm = day_full_minutes(day_dt)
                    if fullm is not None:
                        candidates.append((i, 8, fullm))
                elif blocks[i] == "HO-AM":
                    fullm = day_full_minutes(day_dt)
                    pm_m = half_minutes(day_dt, "PM")
                    if fullm is not None and pm_m is not None:
                        candidates.append((i, 4, max(0.0, fullm - pm_m)))
                elif blocks[i] == "HO-PM":
                    fullm = day_full_minutes(day_dt)
                    am_m = half_minutes(day_dt, "AM")
                    if fullm is not None and am_m is not None:
                        candidates.append((i, 4, max(0.0, fullm - am_m)))
            candidates.sort(key=lambda x: x[2])
            logger.info("HO cap: current=%d target=%d candidates=%s", current_hours, ho_hours_target, candidates)
            for i, hrs, cost in candidates:
                if current_hours <= ho_hours_target:
                    break
                # flip to OFFICE
                blocks[i] = "OFFICE"
                current_hours -= hrs
            logger.info("HO cap applied: final_hours=%d blocks=%s", current_hours, ",".join(blocks))

    results = []
    now_local = datetime.now(TZ)
    for i in range(5):
        day = (start_date_local + timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        slot = blocks[i]
        # Skip past days
        if day.date() < now_local.date():
            results.append({"day": day, "mode": f"PAST-{slot}", "plan": None})
            continue
        try:
            if slot in {"HO", "OFF"}:
                results.append({"day": day, "mode": slot, "plan": None})
            elif slot == "HO-AM":
                # Office only in PM
                plan = plan_halfday_commute(
                    day,
                    section="PM",
                    latest_arrival_local=AFTERNOON_ARRIVAL_LOCAL,
                    window_start_local=AFTERNOON_WINDOW_START_LOCAL,
                    work_hours=4.0,
                    lunch_min=0,
                    lunch_max=0,
                    lunch_step=0,
                    step_minutes=config.get("step_minutes", STEP_MINUTES),
                )
                results.append({"day": day, "mode": slot, "plan": plan})
            elif slot == "HO-PM":
                # Office only in AM
                plan = plan_halfday_commute(
                    day,
                    section="AM",
                    latest_arrival_local=config.get("latest_arrival_local", LATEST_ARRIVAL_LOCAL),
                    window_start_local=config.get("morning_window_start_local", MORNING_WINDOW_START_LOCAL),
                    work_hours=4.0,
                    lunch_min=0,
                    lunch_max=0,
                    lunch_step=0,
                    step_minutes=config.get("step_minutes", STEP_MINUTES),
                )
                results.append({"day": day, "mode": slot, "plan": plan})
            else:
                # OFFICE or OPEN -> full day office
                plan = plan_halfday_commute(
                    day,
                    section="AM",
                    latest_arrival_local=config.get("latest_arrival_local", LATEST_ARRIVAL_LOCAL),
                    window_start_local=config.get("morning_window_start_local", MORNING_WINDOW_START_LOCAL),
                    work_hours=config.get("work_hours", WORK_HOURS),
                    lunch_min=config.get("lunch_min", LUNCH_MIN_MINUTES),
                    lunch_max=config.get("lunch_max", LUNCH_MAX_MINUTES),
                    lunch_step=config.get("lunch_step", LUNCH_STEP_MINUTES),
                    step_minutes=config.get("step_minutes", STEP_MINUTES),
                )
                results.append({"day": day, "mode": "OFFICE", "plan": plan})
        except Exception as e:
            logger.error("Planning error for %s (%s): %s", day.strftime("%Y-%m-%d"), slot, e)
            results.append({"day": day, "mode": f"ERROR-{slot}", "error": str(e), "plan": None})
    return results

def _normalize_slot(value: str) -> str:
    if not value:
        return ""
    v = value.strip().lower()
    if v in {"office", "o", "work"}:
        return "office"
    if v in {"home", "h", "ho"}:
        return "home"
    if v in {"off", "x", "none"}:
        return "off"
    return ""

def build_blocks_from_env_slots(config_map: dict) -> list:
    """Build 5 day-blocks (Mon..Fri) from per-slot keys like MO_AM, MO_PM, ...
    Values: office|home|off. Returns list with values among {OFFICE, HO-AM, HO-PM, HO, OFF}.
    Empty list if no slot keys found.
    """
    days = ["MO", "TU", "WE", "TH", "FR"]
    found_any = False
    out: list[str] = []
    for d in days:
        am = _normalize_slot(config_map.get(f"{d}_AM", ""))
        pm = _normalize_slot(config_map.get(f"{d}_PM", ""))
        if am or pm:
            found_any = True
        # Mapping logic
        # Off dominates if both off or off+home => OFF (no commute)
        if (am == "off" and pm in {"off", "home", ""}) or (pm == "off" and am in {"off", "home", ""}):
            out.append("OFF")
            continue
        if am == "office" and pm == "office":
            out.append("OFFICE")
        elif am == "office":
            out.append("HO-PM")  # office in AM only
        elif pm == "office":
            out.append("HO-AM")  # office in PM only
        elif am == "home" or pm == "home":
            # If one half is home and the other empty -> choose corresponding half-day HO
            if am == "home" and pm == "":
                out.append("HO-PM")  # home in AM implies office only PM if set later
            elif pm == "home" and am == "":
                out.append("HO-AM")
            else:
                out.append("HO")
        else:
            out.append("HO")  # default to home if unspecified
    return out if found_any else []

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
    logger.info(
        "Scan Morning: day=%s start=%02d:%02d latest_arrival=%02d:%02d step=%d",
        day_local.strftime("%Y-%m-%d"), h_s, m_s, h_deadline, m_deadline, STEP_MINUTES,
    )
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
            logger.debug("Candidate departure: %s", current.astimezone(TZ).strftime("%H:%M"))
            dur_min = compute_drive_duration_minutes(ORIGIN_ADDRESS, DESTINATION_ADDRESS, current)
        except Exception as e:
            last_error_message = str(e)
            logger.debug("Slot error: %s", last_error_message)
            current += timedelta(minutes=STEP_MINUTES)
            continue

        arrival = current + timedelta(minutes=dur_min)

        if arrival <= latest_arrival_dt:
            if best["best_duration_minutes"] is None or dur_min < best["best_duration_minutes"]:
                best["best_departure"] = current
                best["best_arrival"] = arrival
                best["best_duration_minutes"] = dur_min
                logger.debug(
                    "New best: dep=%s arr=%s dur=%.1f",
                    current.astimezone(TZ).strftime("%H:%M"),
                    arrival.astimezone(TZ).strftime("%H:%M"),
                    dur_min,
                )

        current += timedelta(minutes=STEP_MINUTES)

    if best["best_departure"] is None:
        msg = (
            "Keine Abfahrt gefunden, die rechtzeitig ankommt. "
            "Erweitere das Suchfenster nach vorne oder erhöhe STEP_MINUTES-Auflösung."
        )
        if last_error_message:
            msg += f" Letzte Fehlermeldung: {last_error_message}"
        raise RuntimeError(msg)
    logger.info(
        "Morning best: dep=%s arr=%s dur=%.1f",
        best["best_departure"].astimezone(TZ).strftime("%H:%M"),
        best["best_arrival"].astimezone(TZ).strftime("%H:%M"),
        best["best_duration_minutes"],
    )

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

    logger.info(
        "Scan Evening: start_from=%s work_hours=%.1f lunch=%d..%d step=%d",
        morning_arrival_local.astimezone(TZ).strftime("%H:%M"), WORK_HOURS,
        LUNCH_MIN_MINUTES, LUNCH_MAX_MINUTES, LUNCH_STEP_MINUTES,
    )
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
    logger.info(
        "Evening best: dep=%s dur=%.1f arr=%s lunch=%s",
        best["evening_departure"].astimezone(TZ).strftime("%H:%M"),
        best["evening_duration_minutes"],
        best["evening_arrival_home"].astimezone(TZ).strftime("%H:%M"),
        best["lunch_minutes"],
    )

    return best

def choose_best_evening_departure_with_extension(morning_arrival_local: datetime) -> dict:
    """Like choose_best_evening_departure, but also considers extending stay.
    Returns dict with baseline (no extension) and extended option if better.
    Keys:
      - base: {lunch_minutes, evening_departure, evening_duration_minutes, evening_arrival_home}
      - extended: optional dict with same keys plus extend_minutes if improved
    """
    base = choose_best_evening_departure(morning_arrival_local)
    # Try extension from the baseline evening departure
    ext = suggest_evening_extension(
        base["evening_departure"],
        base["evening_duration_minutes"],
        step_minutes=EXTEND_STEP_MINUTES,
        worse_steps_limit=EXTEND_WORSE_STEPS,
    )
    result = {"base": base}
    if ext and ext["save"] >= EXTEND_TARGET_SAVE_MIN:
        result["extended"] = {
            "lunch_minutes": base["lunch_minutes"],
            "evening_departure": ext["dep"],
            "evening_duration_minutes": ext["dur"],
            "evening_arrival_home": ext["arr"],
            "extend_minutes": ext["extend_minutes"],
            "save_minutes": ext["save"],
        }
    return result

def optimize_day_with_extension(day_local: datetime) -> dict | None:
    """Re-optimize the whole day if we allow staying longer.
    Explore morning departure around the baseline in steps up to 60 minutes later,
    pick the combination (morning + evening with extension) that minimizes total travel time.
    Returns a dict with keys: morning, lunch_minutes, evening_departure, evening_duration_minutes,
    evening_arrival_home, extend_minutes, total_travel_minutes. None if no improvement.
    """
    # Build latest arrival boundary
    h_deadline, m_deadline = map(int, LATEST_ARRIVAL_LOCAL.split(":"))
    latest_arrival_dt = day_local.replace(hour=h_deadline, minute=m_deadline, second=0, microsecond=0, tzinfo=TZ)

    # Baseline morning
    base_morning = scan_morning_best_departure(day_local)
    base_evening = choose_best_evening_departure_with_extension(base_morning["best_arrival"])
    base_total = base_morning["best_duration_minutes"] + base_evening["base"]["evening_duration_minutes"]
    best = None

    # Candidate morning departures: baseline +/- up to +60 minutes (not earlier than now and must arrive before deadline)
    start_dep = base_morning["best_departure"]
    for plus in range(0, 61, STEP_MINUTES):
        cand_dep = start_dep + timedelta(minutes=plus)
        # compute morning drive
        try:
            dur_min = compute_drive_duration_minutes(ORIGIN_ADDRESS, DESTINATION_ADDRESS, cand_dep)
        except Exception:
            continue
        cand_arr = cand_dep + timedelta(minutes=dur_min)
        if cand_arr > latest_arrival_dt:
            break
        # evening with extension
        eve = choose_best_evening_departure_with_extension(cand_arr)
        eve_best_dur = eve.get("extended", eve["base"]) ["evening_duration_minutes"]
        total = dur_min + eve_best_dur
        if best is None or total < best["total_travel_minutes"]:
            chosen = eve.get("extended", None)
            best = {
                "morning": {"best_departure": cand_dep, "best_arrival": cand_arr, "best_duration_minutes": dur_min},
                "lunch_minutes": (chosen or eve["base"]) ["lunch_minutes"],
                "evening_departure": (chosen or eve["base"]) ["evening_departure"],
                "evening_duration_minutes": (chosen or eve["base"]) ["evening_duration_minutes"],
                "evening_arrival_home": (chosen or eve["base"]) ["evening_arrival_home"],
                "extend_minutes": (chosen or {}).get("extend_minutes", 0),
                "save_minutes": (chosen or {}).get("save_minutes", 0),
                "total_travel_minutes": total,
            }
    if best and best["total_travel_minutes"] + 0.1 < base_total:
        return best
    return None

def main():
    ensure_api_key_configured()
    # Weekly mode: enabled if WEEKLY_BLOCKS or per-slot keys are provided
    slot_blocks = build_blocks_from_env_slots(CONFIG)
    blocks_source = ",".join(slot_blocks) or WEEKLY_BLOCKS
    if blocks_source:
        # Determine start Monday
        if WEEKLY_START_DATE:
            base = datetime.strptime(WEEKLY_START_DATE, "%Y-%m-%d").replace(tzinfo=TZ)
        else:
            today_local = datetime.now(TZ)
            base = today_local - timedelta(days=today_local.weekday())
        blocks = [b.strip().upper() for b in blocks_source.split(",")]
        while len(blocks) < 5:
            blocks.append("OPEN")
        logger.info("Weekly blocks source: %s", blocks_source)
        cfg = {
            "blocks": blocks[:5],
            "latest_arrival_local": LATEST_ARRIVAL_LOCAL,
            "morning_window_start_local": MORNING_WINDOW_START_LOCAL,
            "work_hours": WORK_HOURS,
            "lunch_min": LUNCH_MIN_MINUTES,
            "lunch_max": LUNCH_MAX_MINUTES,
            "lunch_step": LUNCH_STEP_MINUTES,
            "step_minutes": STEP_MINUTES,
            "ho_percent": WEEKLY_HO_PERCENT,
        }
        plan = weekly_plan(base, cfg)
        def fmt(dt: datetime) -> str:
            return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M")
        print("\n=== Wochenplan (Mon-Fri) ===")
        for entry in plan:
            day = entry["day"].strftime("%Y-%m-%d (%a)")
            mode = entry["mode"]
            if entry["plan"] is None:
                if mode.startswith("PAST-"):
                    print(f"{day}: {mode}")
                elif mode in {"HO", "OFF"}:
                    print(f"{day}: Stay@Home")
                else:
                    print(f"{day}: {mode}")
            else:
                m = entry["plan"]["outbound"]
                e = entry["plan"]["inbound"]
                total = m["best_duration_minutes"] + e["evening_duration_minutes"]
                improved = optimize_day_with_extension(entry["day"]) or None
                # Derived times for transparency
                work_minutes = int(WORK_HOURS * 60)
                arrival_office = m['best_arrival']
                earliest_end = arrival_office + timedelta(minutes=work_minutes)
                planned_end = e['evening_departure']
                flex_break = max(0, (planned_end - earliest_end).total_seconds() / 60.0)
                print(f"{day}:")
                print(f"--Start: {fmt_hhmm(m['best_departure'])}h | {fmt_minutes(m['best_duration_minutes'])}min")
                print(f"--Arrive Office: {fmt_hhmm(arrival_office)}h")
                print(f"--Work Required: {fmt_dur_hm(work_minutes)} (login time)")
                print(f"--Earliest End: {fmt_hhmm(earliest_end)}h")
                print(f"--Planned Return: {fmt_hhmm(e['evening_departure'])}h | {fmt_minutes(e['evening_duration_minutes'])}min")
                print(f"--Flexible Breaks (total): {fmt_minutes(flex_break)}min")
                print(f"--Total-Travel-Time: {fmt_minutes(total)}min")
                if improved:
                    arr2 = improved['morning']['best_arrival']
                    earliest2 = arr2 + timedelta(minutes=work_minutes)
                    flex2 = max(0, (improved['evening_departure'] - earliest2).total_seconds() / 60.0)
                    print(f"--Alt Option -> Start: {fmt_hhmm(improved['morning']['best_departure'])}h | {fmt_minutes(improved['morning']['best_duration_minutes'])}min")
                    print(f"               Arrive Office: {fmt_hhmm(arr2)}h | Earliest End: {fmt_hhmm(earliest2)}h")
                    print(f"               Retourn: {fmt_hhmm(improved['evening_departure'])}h | {fmt_minutes(improved['evening_duration_minutes'])}min")
                    print(f"               Extend:  {fmt_minutes(improved['extend_minutes'])}min | Flex-Breaks: {fmt_minutes(flex2)}min | Save: {fmt_minutes(total - improved['total_travel_minutes'])}min | New Total: {fmt_minutes(improved['total_travel_minutes'])}min")
                # Suggest staying longer in steps up to 120 minutes
                suggestion = suggest_evening_extension(
                    e['evening_departure'],
                    e['evening_duration_minutes'],
                    step_minutes=EXTEND_STEP_MINUTES,
                    worse_steps_limit=EXTEND_WORSE_STEPS,
                )
                if suggestion:
                    print(f"--Extend your stay by {fmt_minutes(suggestion['extend_minutes'])}min until {fmt_hhmm(suggestion['dep'])}h to save up {fmt_minutes(suggestion['save'])}min and arrive at home at {fmt_hhmm(suggestion['arr'])}h")
                else:
                    print(f"--No time saving found by extending in {EXTEND_STEP_MINUTES}min steps")
        return

    # Single-day mode (default)
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
