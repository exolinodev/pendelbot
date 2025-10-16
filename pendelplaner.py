from __future__ import annotations

def render_weekly_output(base: datetime, cfg: dict) -> None:
    plan = weekly_plan(base, cfg)
    def fmt(dt: datetime) -> str:
        return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M")
    week_title = f"{EMO_CAL} === Weekly Plan: {base.strftime('%b %d')} - {(base + timedelta(days=4)).strftime('%b %d')} ==="
    print("\n" + bold(week_title))
    weekly_office = 0
    weekly_ho = 0
    weekly_travel_standard = 0.0
    weekly_travel_chosen = 0.0
    gym_days_used = 0
    # Weekly progress reporter (Mon-Fri)
    weekly_pr = None
    if logger.isEnabledFor(logging.INFO):
        try:
            weekly_pr = ProgressReporter("Weekly Report", max(1, len(plan)))
            logger.info("Weekly rendering started: %d days", len(plan))
        except Exception:
            weekly_pr = None
    preferred_days = _parse_days_list(GYM_PREFERRED_DAYS)
    # Pre-select gym days: require up to GYM_MAX_DAYS_PER_WEEK office days for gym
    force_gym_set: set[int] = set()
    if GYM_ENABLED and GYM_MAX_DAYS_PER_WEEK > 0:
        desired_gym = GYM_MAX_DAYS_PER_WEEK
        candidates_pref: list[int] = []
        candidates_other: list[int] = []
        for i, entry in enumerate(plan):
            if entry.get("plan") is None:
                continue
            # Skip days without office presence entirely
            if entry.get("mode") in {"HO", "OFF"}:
                continue
            token = _weekday_token(entry["day"])
            if token in preferred_days:
                candidates_pref.append(i)
            else:
                candidates_other.append(i)
        chosen = candidates_pref[:desired_gym]
        if len(chosen) < desired_gym:
            chosen += candidates_other[: (desired_gym - len(chosen))]
        force_gym_set = set(chosen)
    # Running timebank balance across the week
    timebank_balance = TIMEBANK_CURRENT_MIN
    for idx, entry in enumerate(plan, start=1):
        day_dt = entry["day"]
        day_label = day_dt.strftime("%a, %b %d")
        mode = entry["mode"]
        logger.info("Planning day %d/%d: %s (mode=%s)", idx, len(plan), day_label, mode)
        if entry["plan"] is None:
            if mode.startswith("PAST-"):
                print(dim(f"{day_label}: {mode}"))
            elif mode == "HO":
                print(f"{EMO_HOME} {bold(day_label)}")
                print(" " * 3 + "Work From Home")
                weekly_ho += 1
            elif mode == "OFF":
                print(f"{EMO_HOME} {bold(day_label)}")
                print(" " * 3 + "Day Off")
            else:
                print(f"{bold(day_label)}: {mode}")
            print(magenta(hr()))
            if weekly_pr:
                weekly_pr.update(1)
            logger.info("Finished day %d/%d: %s (no commute)", idx, len(plan), day_label)
            continue

        # Office (or half-day office) plan
        weekly_office += 1
        m = entry["plan"]["outbound"]
        e = entry["plan"]["inbound"]

        work_minutes = int(WORK_HOURS * 60)
        # Baseline morning arrival for this day
        arrival_office = m['best_arrival']

        # Consider optimized day including extension/morning tweak
        with suppress_info_logs():
            # Keep gym exploration enabled even with no-cache (fast mode inside chooser), but skip heavy day re-optimization
            improved = None if DISABLE_ROUTE_CACHE else (optimize_day_with_extension(day_dt) or None)
            # Timebank-aware option: allow earlier leave + wait if activity is gym and we can spend timebank
            available_tb = timebank_balance if EXTENSION_ACTIVITY == "gym" else 0
            timebank_option = None
            timebank_any = None
            try:
                tb = choose_best_evening_departure_with_timebank(arrival_office, available_tb)
                timebank_option = tb.get("spend")
                timebank_any = tb.get("best_any")
            except Exception as ex:
                logger.warning("Gym/timebank evaluation failed: %s", ex)
                timebank_option = None
                timebank_any = None

        # Decide best option with gym cap enforcement
        improved_total = improved['total_travel_minutes'] if improved else float('inf')
        tb_total = (m['best_duration_minutes'] + (timebank_option['evening_duration_minutes'] if timebank_option else 0)) if timebank_option else float('inf')
        # Enforce gym target: must_do_gym if this day is pre-selected
        must_do_gym = (idx - 1) in force_gym_set
        can_use_gym_today = GYM_ENABLED and ((gym_days_used < GYM_MAX_DAYS_PER_WEEK and (_weekday_token(day_dt) in preferred_days)) or must_do_gym)
        # Prefer/force gym on pre-selected days when available; fall back to best_any if no spend option
        if timebank_option is None and must_do_gym and timebank_any is not None:
            timebank_option = timebank_any
        choose_timebank = (timebank_option is not None) and (must_do_gym or can_use_gym_today)
        if choose_timebank:
            rec_m_dep = m['best_departure']
            rec_m_arr = m['best_arrival']
            rec_m_dur = m['best_duration_minutes']
            rec_e_dep = timebank_option['evening_departure']
            rec_e_dur = timebank_option['evening_duration_minutes']
            rec_e_arr = timebank_option['evening_arrival_home']
            extend_minutes = -int(round(timebank_option.get('spend_minutes', 0)))  # negative indicates spend
            off2gym = timebank_option.get('office_to_gym_minutes', 0)
            chosen_total = rec_m_dur + off2gym + rec_e_dur
            chosen_mode = "timebank"
            chosen_lunch_minutes = e['lunch_minutes']
            # If leave mode is 'earliest', spending is zero; do not reduce timebank
            if GYM_LEAVE_MODE == "earliest":
                extend_minutes = 0
        elif improved:
            rec_m_dep = improved['morning']['best_departure']
            rec_m_arr = improved['morning']['best_arrival']
            rec_m_dur = improved['morning']['best_duration_minutes']
            rec_e_dep = improved['evening_departure']
            rec_e_dur = improved['evening_duration_minutes']
            rec_e_arr = improved['evening_arrival_home']
            extend_minutes = max(0, improved.get('extend_minutes', 0))
            chosen_total = improved['total_travel_minutes']
            chosen_mode = "extension"
            chosen_lunch_minutes = improved.get('lunch_minutes', e['lunch_minutes'])
            # If an extension was chosen, reflect any lifestyle penalty in benefits
            improved_penalty = max(0, int(round(improved.get('penalty_minutes', 0))))
        else:
            rec_m_dep = m['best_departure']
            rec_m_arr = m['best_arrival']
            rec_m_dur = m['best_duration_minutes']
            rec_e_dep = e['evening_departure']
            rec_e_dur = e['evening_duration_minutes']
            rec_e_arr = e['evening_arrival_home']
            # Extra time beyond base_end
            extend_minutes = 0
            chosen_total = rec_m_dur + rec_e_dur
            chosen_mode = "base"
            chosen_lunch_minutes = e['lunch_minutes']

        # Force gym on pre-selected days even if not strictly better
        if (not DISABLE_ROUTE_CACHE) and (chosen_mode != "timebank") and ((idx - 1) in force_gym_set) and (timebank_any is not None):
            rec_m_dep = m['best_departure']
            rec_m_arr = m['best_arrival']
            rec_m_dur = m['best_duration_minutes']
            rec_e_dep = timebank_any['evening_departure']
            rec_e_dur = timebank_any['evening_duration_minutes']
            rec_e_arr = timebank_any['evening_arrival_home']
            extend_minutes = -int(round(timebank_any.get('spend_minutes', 0)))
            off2gym = timebank_any.get('office_to_gym_minutes', 0)
            chosen_total = rec_m_dur + off2gym + rec_e_dur
            chosen_mode = "timebank"
            chosen_lunch_minutes = e['lunch_minutes']

        # Recompute earliest/base end using the actual chosen morning arrival and chosen lunch minutes
        earliest_end = rec_m_arr + timedelta(minutes=work_minutes)
        base_breaks = chosen_lunch_minutes + PERSONAL_BREAKS_MIN
        base_end = earliest_end + timedelta(minutes=base_breaks)

        # Standard plan based on this day's actual arrival and breaks, pessimized in rush window
        try:
            std_inbound_raw = compute_drive_duration_minutes(DESTINATION_ADDRESS, ORIGIN_ADDRESS, base_end)
        except Exception:
            std_inbound_raw = e["evening_duration_minutes"]
        std_inbound = std_inbound_raw
        if _is_in_rush_window(base_end):
            # Try small worst-of probing unless budget is tight or cache disabled
            probed = [std_inbound_raw]
            if (not DISABLE_ROUTE_CACHE) and (not _budget_soft_limit_reached()):
                for off in _parse_int_list(EVENING_STD_PROBE_OFFSETS_MIN):
                    try:
                        if off <= 0:
                            continue
                        dep = base_end + timedelta(minutes=off)
                        d = compute_drive_duration_minutes(DESTINATION_ADDRESS, ORIGIN_ADDRESS, dep)
                        probed.append(d)
                    except Exception:
                        break
            std_inbound = max(probed) + EVENING_STD_EXTRA_BUFFER_MIN
            std_inbound = max(std_inbound, std_inbound_raw + EVENING_STD_BUFFER_MIN)
        standard_total = rec_m_dur + std_inbound

        # Benefit is the reduction in evening drive time (morning is identical in standard vs chosen)
        # For gym days, Office→Gym is not part of regular commute and is excluded by comparing evening legs only.
        benefit_save = max(0, fmt_minutes(std_inbound) - fmt_minutes(rec_e_dur))

        # Render day
        day_prefix = f"{EMO_OK} " if EMO_OK else ""
        day_suffix = f"{EMO_OK_END}" if EMO_OK_END else ""
        print(f"{day_prefix}{bold(day_label)}{day_suffix}")
        print(hr())
        print("   " + bold("RECOMMENDED PLAN:"))
        print(f"   {EMO_CAR} Leave Home:      {fmt_hhmm(rec_m_dep)} ({fmt_minutes(rec_m_dur)} min commute)")
        print(f"   {EMO_OFFICE} Arrive Office:   {fmt_hhmm(rec_m_arr)}")
        if chosen_mode == "timebank":
            spend_abs = fmt_dur_hm(abs(extend_minutes))
            gym_addr = (timebank_option or {}).get('gym_address', 'Gym')
            gym_train = (timebank_option or {}).get('train_minutes', 0)
            off2gym = (timebank_option or {}).get('office_to_gym_minutes', 0)
            leave_office_for_gym = (timebank_option or {}).get('leave_office', base_end - timedelta(minutes=abs(extend_minutes)))
            print(f"   {EMO_OFFICE} Leave Office:    {fmt_hhmm(leave_office_for_gym)} (Leave early, spend {spend_abs} from timebank)")
            print(f"   {EMO_CAR} Travel to Gym:  {fmt_dur_h_colon(off2gym)} → {gym_addr}")
            print(f"   🏋️  Train:          {fmt_dur_h_colon(gym_train)}")
            print(f"   🏋️  Leave Gym:      {fmt_hhmm(rec_e_dep)}")
        elif extend_minutes > 0:
            print(f"   {EMO_OFFICE} Leave Office:    {fmt_hhmm(rec_e_dep)} (Stay {fmt_dur_hm(extend_minutes)} extra)")
        else:
            print(f"   {EMO_OFFICE} Leave Office:    {fmt_hhmm(rec_e_dep)}")
        print(f"   {EMO_HOME} Arrive Home:     {fmt_hhmm(rec_e_arr)} ({fmt_minutes(rec_e_dur)} min commute)")
        print("")
        # BENEFITS section
        print("   ✅ BENEFITS OF THIS PLAN:")
        print(f"    {_BULLET}Time Saved:  {fmt_minutes(benefit_save)} minutes")
        if chosen_mode == "extension":
            print(f"    {_BULLET}Traffic:     Completely avoids evening congestion by leaving after the rush.")
            if extend_minutes > 0:
                print(f"    {_BULLET}Productivity: Gain {fmt_dur_hm(extend_minutes)} of quiet, focused time at the office.")
            if 'improved_penalty' in locals() and improved_penalty > 0:
                print(f"    {_BULLET}Balance:     Includes {fmt_minutes(improved_penalty)} min lifestyle penalty for late departure.")
        elif chosen_mode == "timebank":
            # Estimate avoided stop-and-go as baseline evening drive minus late gym→home drive (approx)
            avoided = max(0, fmt_minutes(std_inbound) - fmt_minutes(rec_e_dur))
            print(f"    {_BULLET}Traffic:     Avoids {avoided}+ minutes of stressful, stop-and-go driving.")
            print(f"    {_BULLET}Efficiency:  Gym workout is completed, freeing up your evening.")
        else:
            print(f"    {_BULLET}Traffic:     Uses the lowest-traffic return window for today.")
        if chosen_mode == "timebank":
            # Show both: regular commute total and gym travel separately
            reg_total = fmt_minutes(rec_m_dur + rec_e_dur)
            print(f"    commutes: Morning {fmt_minutes(rec_m_dur)} min / Evening {fmt_minutes(rec_e_dur)} min | Regular Total: {reg_total} min (Gym extra: Office→Gym {fmt_minutes(off2gym)} min)")
        else:
            print(f"    commutes: Morning {fmt_minutes(rec_m_dur)} min / Evening {fmt_minutes(rec_e_dur)} min | Total: {fmt_minutes(chosen_total)} min")
        # Details section for the day
        print("")
        print("   " + bold("DETAILS:"))
        lunch_end = rec_m_arr + timedelta(minutes=work_minutes + chosen_lunch_minutes)
        personal_end = base_end  # cumulative after lunch + personal breaks
        print(f"   • Work Required:   {fmt_dur_hm(work_minutes)} (login)")
        print(f"   • Lunch:           +{fmt_minutes(chosen_lunch_minutes)} min → {fmt_hhmm(lunch_end)}")
        if PERSONAL_BREAKS_MIN:
            print(f"   • Personal Breaks: +{fmt_minutes(PERSONAL_BREAKS_MIN)} min → {fmt_hhmm(personal_end)}")
        print(f"   • Earliest Leave:  {fmt_hhmm(base_end)}")
        if chosen_mode == "extension" and extend_minutes > 0:
            print(f"   • Extra at Office: {fmt_dur_hm(extend_minutes)} (beyond earliest)")
        if chosen_mode == "timebank":
            spend_abs = abs(extend_minutes)
            projected_tb = timebank_balance if (GYM_LEAVE_MODE == "earliest") else max(0, timebank_balance - spend_abs)
            gym_addr = (timebank_option or {}).get('gym_address', 'Gym')
            gym_train = (timebank_option or {}).get('train_minutes', 0)
            off2gym = (timebank_option or {}).get('office_to_gym_minutes', 0)
            if spend_abs > 0:
                print(f"   • Leave Early:     {fmt_dur_hm(spend_abs)} earlier → {gym_addr}")
            print(f"   • Gym Session:     {fmt_dur_hm(gym_train)} (Commute Office→Gym {fmt_minutes(off2gym)} min)")
            if spend_abs > 0:
                net_spend = max(0, fmt_minutes(spend_abs) - fmt_minutes(benefit_save))
                print(f"   • Timebank:        Spend {fmt_minutes(spend_abs)} min (net {fmt_minutes(net_spend)} min) | New Balance: {fmt_minutes(projected_tb)}/{fmt_minutes(TIMEBANK_CAP_MIN)}")
        # Traffic comparison
        print(f"   • Evening Traffic: baseline {fmt_minutes(int(round(std_inbound)))} min → chosen {fmt_minutes(int(round(rec_e_dur)))} min")
        print("")
        print("   ---")
        print("   " + bold("STANDARD PLAN (leave at the earliest time):"))
        print(f"   • Leave Office:    {fmt_hhmm(base_end)}")
        std_arr_home = base_end + timedelta(minutes=std_inbound)
        print(f"   • Arrive Home:     {fmt_hhmm(std_arr_home)} ({fmt_minutes(std_inbound)} min commute)")
        print(f"   • Total Commute:   {fmt_minutes(standard_total)} min")

        weekly_travel_standard += standard_total
        # Weekly chosen time excludes Office→Gym on gym days to reflect regular commute only
        weekly_travel_chosen += ((rec_m_dur + rec_e_dur) if (chosen_mode == "timebank") else chosen_total)
        if chosen_mode == "timebank":
            gym_days_used += 1
            # Update running timebank after using it (no deduction if earliest mode)
            if GYM_LEAVE_MODE != "earliest":
                timebank_balance = max(0, timebank_balance - abs(extend_minutes))
        print(magenta(hr()))
        if weekly_pr:
            weekly_pr.update(1)
        logger.info("Finished day %d/%d: %s", idx, len(plan), day_label)

    # Weekly footer
    print(bold("WEEKLY SUMMARY"))
    print_kv("Office Days / Home Office:", f"{weekly_office} / {weekly_ho} (HO cap {WEEKLY_HO_PERCENT}%)")
    print_kv("Total Commute Time:", f"Standard: {fmt_minutes(weekly_travel_standard)} min  |  With Optimizations: {fmt_minutes(weekly_travel_chosen)} min  (save {fmt_minutes(weekly_travel_standard - weekly_travel_chosen)} min)")
    if weekly_office:
        print_kv("Average Commute/Day:", f"{fmt_minutes(weekly_travel_chosen / max(1, weekly_office))} min")
    if weekly_pr:
        weekly_pr.done()
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
import sys
import shutil
import argparse
import json
import atexit
import time
import math
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import dotenv_values

ORIGIN_ADDRESS = "<HOME_ADDRESS>"
DESTINATION_ADDRESS = "<OFFICE_ADDRESS>"

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
LOG_LEVEL = CONFIG.get("LOG_LEVEL", "WARNING").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("pendelplaner")
logger.debug("Logger initialized with level %s", LOG_LEVEL)

@contextmanager
def suppress_info_logs():
    """Temporarily raise logger level to WARNING to avoid noisy INFO logs."""
    previous = logger.level
    try:
        logger.setLevel(logging.WARNING)
        yield
    finally:
        logger.setLevel(previous)

# ---------------- AppConfig and API client (architectural step) ----------------

@dataclass
class AppConfig:
    api_key: str
    origin_address: str
    destination_address: str
    latest_arrival_local: str
    morning_window_start_local: str
    work_hours: float
    lunch_min_minutes: int
    lunch_max_minutes: int
    lunch_step_minutes: int
    step_minutes: int
    day_offset: int
    tz_name: str
    log_level: str
    # Optional tuning
    ascii_output: bool = False
    compact_weekly: bool = False
    color_output: str = "auto"
    # Personal breaks
    personal_breaks_min: int = 0
    # Weekly
    weekly_blocks: str = ""
    weekly_start_date: str = ""
    weekly_ho_percent: int = 0
    # Half-day alt
    afternoon_arrival_local: str = "13:30"
    afternoon_window_start_local: str = "11:00"
    # Extension scanning
    extend_step_minutes: int = 30
    extend_worse_steps: int = 6
    extend_latest_local: str = "22:00"
    extend_target_save_min: float = 10.0
    avoid_threshold_min: float = 8.0
    avoid_step_minutes: int = 15
    # Timebank
    timebank_current_min: int = 0
    timebank_cap_min: int = 50 * 60
    timebank_max_spend_per_day_min: int = 0
    extension_activity: str = "gym"

    @classmethod
    def from_env(cls) -> "AppConfig":
        cfg = CONFIG
        return cls(
            api_key=cfg.get("GOOGLE_MAPS_API_KEY", ""),
            origin_address=cfg.get("ORIGIN_ADDRESS", ORIGIN_ADDRESS),
            destination_address=cfg.get("DESTINATION_ADDRESS", DESTINATION_ADDRESS),
            latest_arrival_local=cfg.get("LATEST_ARRIVAL_LOCAL", LATEST_ARRIVAL_LOCAL),
            morning_window_start_local=cfg.get("MORNING_WINDOW_START_LOCAL", MORNING_WINDOW_START_LOCAL),
            work_hours=float(cfg.get("WORK_HOURS", str(WORK_HOURS))),
            lunch_min_minutes=int(cfg.get("LUNCH_MIN_MINUTES", str(LUNCH_MIN_MINUTES))),
            lunch_max_minutes=int(cfg.get("LUNCH_MAX_MINUTES", str(LUNCH_MAX_MINUTES))),
            lunch_step_minutes=int(cfg.get("LUNCH_STEP_MINUTES", str(LUNCH_STEP_MINUTES))),
            step_minutes=int(cfg.get("STEP_MINUTES", str(STEP_MINUTES))),
            day_offset=int(cfg.get("DAY_OFFSET", str(DAY_OFFSET))),
            tz_name=cfg.get("TZ", TZ.key if hasattr(TZ, "key") else "Europe/Zurich"),
            log_level=cfg.get("LOG_LEVEL", LOG_LEVEL),
            ascii_output=parse_bool(cfg.get("ASCII_OUTPUT", "0"), False),
            compact_weekly=parse_bool(cfg.get("COMPACT_WEEKLY", "0"), False),
            color_output=cfg.get("COLOR_OUTPUT", "auto"),
            personal_breaks_min=int(cfg.get("PERSONAL_BREAKS_MIN", str(PERSONAL_BREAKS_MIN))),
            weekly_blocks=cfg.get("WEEKLY_BLOCKS", WEEKLY_BLOCKS),
            weekly_start_date=cfg.get("WEEKLY_START_DATE", WEEKLY_START_DATE),
            weekly_ho_percent=int(cfg.get("WEEKLY_HO_PERCENT", str(WEEKLY_HO_PERCENT))),
            afternoon_arrival_local=cfg.get("AFTERNOON_ARRIVAL_LOCAL", AFTERNOON_ARRIVAL_LOCAL),
            afternoon_window_start_local=cfg.get("AFTERNOON_WINDOW_START_LOCAL", AFTERNOON_WINDOW_START_LOCAL),
            extend_step_minutes=int(cfg.get("EXTEND_STEP_MINUTES", str(EXTEND_STEP_MINUTES))),
            extend_worse_steps=int(cfg.get("EXTEND_WORSE_STEPS", str(EXTEND_WORSE_STEPS))),
            extend_latest_local=cfg.get("EXTEND_LATEST_LOCAL", EXTEND_LATEST_LOCAL),
            extend_target_save_min=float(cfg.get("EXTEND_TARGET_SAVE_MIN", str(EXTEND_TARGET_SAVE_MIN))),
            avoid_threshold_min=float(cfg.get("AVOID_THRESHOLD_MIN", str(AVOID_THRESHOLD_MIN))),
            avoid_step_minutes=int(cfg.get("AVOID_STEP_MINUTES", str(AVOID_STEP_MINUTES))),
            timebank_current_min=int(cfg.get("TIMEBANK_CURRENT_MIN", str(TIMEBANK_CURRENT_MIN))),
            timebank_cap_min=int(cfg.get("TIMEBANK_CAP_MIN", str(TIMEBANK_CAP_MIN))),
            timebank_max_spend_per_day_min=int(cfg.get("TIMEBANK_MAX_SPEND_PER_DAY_MIN", str(TIMEBANK_MAX_SPEND_PER_DAY_MIN))),
            extension_activity=cfg.get("EXTENSION_ACTIVITY", EXTENSION_ACTIVITY),
        )

class RoutesApiClient:
    BASE_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("API key cannot be empty.")
        self.api_key = api_key
        self.headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.legs.duration",
        }

    @staticmethod
    def _parse_duration_to_minutes(duration_str: str) -> float:
        s = duration_str.strip().rstrip("s")
        return float(s) / 60.0

    def compute_drive_duration_minutes(self, origin_addr: str, destination_addr: str, departure_dt_local: datetime) -> float:
        """Traffic-aware duration with shared cache and per-run budget.
        Uses time-bucketing via ROUTE_CACHE_GRANULARITY_MIN to maximize cache hits.
        """
        # Shared globals for cache and budgeting
        global API_CALL_COUNT, ROUTE_CACHE, ROUTE_CACHE_TS, SESSION_ROUTE_CACHE

        # Normalize time to cache granularity bucket and build candidates
        key_time = _floor_dt_to_step(departure_dt_local, ROUTE_CACHE_GRANULARITY_MIN)
        canonical_key = _canonical_key(origin_addr, destination_addr, key_time.strftime('%Y-%m-%d %H:%M'))
        # Check session cache first (always on), then persistent cache if allowed
        for k in _candidate_cache_keys(origin_addr, destination_addr, departure_dt_local):
            if k in SESSION_ROUTE_CACHE:
                return SESSION_ROUTE_CACHE[k]
            if (not DISABLE_ROUTE_CACHE) and (k in ROUTE_CACHE):
                dur = ROUTE_CACHE[k]
                # Promote to canonical for faster next hits
                ROUTE_CACHE[canonical_key] = dur
                ROUTE_CACHE_TS[canonical_key] = ROUTE_CACHE_TS.get(k, time.time())
                SESSION_ROUTE_CACHE[canonical_key] = dur
                return dur

        # Budget check
        if API_CALL_COUNT >= MAX_API_CALLS_PER_RUN:
            raise RuntimeError(
                f"API call budget exceeded ({MAX_API_CALLS_PER_RUN}). Increase MAX_API_CALLS_PER_RUN or widen cache granularity."
            )

        body = {
            "origin": {"address": origin_addr},
            "destination": {"address": destination_addr},
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
            # Use floored bucket time for departure
            "departureTime": to_rfc3339_local(key_time),
        }

        # Perform request
        resp = requests.post(self.BASE_URL, headers=self.headers, json=body, timeout=20)
        if resp.status_code != 200:
            logger.error("Routes API HTTP %s: %s", resp.status_code, resp.text[:300])
            raise RuntimeError(f"Routes API Fehler {resp.status_code}: {resp.text}")

        data = resp.json()
        routes = data.get("routes", [])
        if not routes:
            raise RuntimeError("Keine Route gefunden (leere routes-Liste).")
        dur = routes[0].get("duration")
        if not dur:
            raise RuntimeError("Antwort enthält keine duration.")

        dur_min = self._parse_duration_to_minutes(dur)

        # Save to shared cache and update budget counter
        SESSION_ROUTE_CACHE[canonical_key] = dur_min
        if not DISABLE_ROUTE_CACHE:
            ROUTE_CACHE[canonical_key] = dur_min
            ROUTE_CACHE_TS[canonical_key] = time.time()
        API_CALL_COUNT += 1

        return dur_min

# Global, optional: client instance used by helper if available
API_CLIENT = None

@contextmanager
def using_config(cfg: AppConfig, overrides: dict | None = None):
    """Temporarily apply configuration values to module-level globals.
    This is a transitional helper to phase out globals while keeping existing functions intact.
    """
    global ORIGIN_ADDRESS, DESTINATION_ADDRESS, LATEST_ARRIVAL_LOCAL, MORNING_WINDOW_START_LOCAL
    global WORK_HOURS, LUNCH_MIN_MINUTES, LUNCH_MAX_MINUTES, LUNCH_STEP_MINUTES, STEP_MINUTES
    global DAY_OFFSET, TZ, PERSONAL_BREAKS_MIN
    global AFTERNOON_ARRIVAL_LOCAL, AFTERNOON_WINDOW_START_LOCAL
    global EXTEND_STEP_MINUTES, EXTEND_WORSE_STEPS, EXTEND_LATEST_LOCAL, EXTEND_TARGET_SAVE_MIN
    global AVOID_THRESHOLD_MIN, AVOID_STEP_MINUTES
    global TIMEBANK_CURRENT_MIN, TIMEBANK_CAP_MIN, TIMEBANK_MAX_SPEND_PER_DAY_MIN, EXTENSION_ACTIVITY

    # Snapshot
    snapshot = (
        ORIGIN_ADDRESS, DESTINATION_ADDRESS, LATEST_ARRIVAL_LOCAL, MORNING_WINDOW_START_LOCAL,
        WORK_HOURS, LUNCH_MIN_MINUTES, LUNCH_MAX_MINUTES, LUNCH_STEP_MINUTES, STEP_MINUTES,
        DAY_OFFSET, TZ, PERSONAL_BREAKS_MIN,
        AFTERNOON_ARRIVAL_LOCAL, AFTERNOON_WINDOW_START_LOCAL,
        EXTEND_STEP_MINUTES, EXTEND_WORSE_STEPS, EXTEND_LATEST_LOCAL, EXTEND_TARGET_SAVE_MIN,
        AVOID_THRESHOLD_MIN, AVOID_STEP_MINUTES,
        TIMEBANK_CURRENT_MIN, TIMEBANK_CAP_MIN, TIMEBANK_MAX_SPEND_PER_DAY_MIN, EXTENSION_ACTIVITY,
    )
    try:
        # Apply cfg
        ORIGIN_ADDRESS = cfg.origin_address
        DESTINATION_ADDRESS = cfg.destination_address
        LATEST_ARRIVAL_LOCAL = cfg.latest_arrival_local
        MORNING_WINDOW_START_LOCAL = cfg.morning_window_start_local
        WORK_HOURS = float(cfg.work_hours)
        LUNCH_MIN_MINUTES = int(cfg.lunch_min_minutes)
        LUNCH_MAX_MINUTES = int(cfg.lunch_max_minutes)
        LUNCH_STEP_MINUTES = int(cfg.lunch_step_minutes)
        STEP_MINUTES = int(cfg.step_minutes)
        DAY_OFFSET = int(cfg.day_offset)
        try:
            TZ = ZoneInfo(cfg.tz_name)
        except Exception:
            TZ = ZoneInfo("Europe/Zurich")
        PERSONAL_BREAKS_MIN = int(cfg.personal_breaks_min)
        AFTERNOON_ARRIVAL_LOCAL = cfg.afternoon_arrival_local
        AFTERNOON_WINDOW_START_LOCAL = cfg.afternoon_window_start_local
        EXTEND_STEP_MINUTES = int(cfg.extend_step_minutes)
        EXTEND_WORSE_STEPS = int(cfg.extend_worse_steps)
        EXTEND_LATEST_LOCAL = cfg.extend_latest_local
        EXTEND_TARGET_SAVE_MIN = float(cfg.extend_target_save_min)
        AVOID_THRESHOLD_MIN = float(cfg.avoid_threshold_min)
        AVOID_STEP_MINUTES = int(cfg.avoid_step_minutes)
        TIMEBANK_CURRENT_MIN = max(0, int(cfg.timebank_current_min))
        TIMEBANK_CAP_MIN = max(0, int(cfg.timebank_cap_min))
        TIMEBANK_MAX_SPEND_PER_DAY_MIN = max(0, int(cfg.timebank_max_spend_per_day_min))
        EXTENSION_ACTIVITY = (cfg.extension_activity or "gym").strip().lower()

        # Apply overrides if provided (camelCase mapping to module vars)
        if overrides:
            for k, v in overrides.items():
                if k == "latest_arrival_local":
                    LATEST_ARRIVAL_LOCAL = str(v)
                elif k == "window_start_local":
                    MORNING_WINDOW_START_LOCAL = str(v)
                elif k == "work_hours":
                    WORK_HOURS = float(v)
                elif k == "lunch_min":
                    LUNCH_MIN_MINUTES = int(v)
                elif k == "lunch_max":
                    LUNCH_MAX_MINUTES = int(v)
                elif k == "lunch_step":
                    LUNCH_STEP_MINUTES = int(v)
                elif k == "step_minutes":
                    STEP_MINUTES = int(v)
        yield
    finally:
        (
            ORIGIN_ADDRESS, DESTINATION_ADDRESS, LATEST_ARRIVAL_LOCAL, MORNING_WINDOW_START_LOCAL,
            WORK_HOURS, LUNCH_MIN_MINUTES, LUNCH_MAX_MINUTES, LUNCH_STEP_MINUTES, STEP_MINUTES,
            DAY_OFFSET, TZ, PERSONAL_BREAKS_MIN,
            AFTERNOON_ARRIVAL_LOCAL, AFTERNOON_WINDOW_START_LOCAL,
            EXTEND_STEP_MINUTES, EXTEND_WORSE_STEPS, EXTEND_LATEST_LOCAL, EXTEND_TARGET_SAVE_MIN,
            AVOID_THRESHOLD_MIN, AVOID_STEP_MINUTES,
            TIMEBANK_CURRENT_MIN, TIMEBANK_CAP_MIN, TIMEBANK_MAX_SPEND_PER_DAY_MIN, EXTENSION_ACTIVITY,
        ) = snapshot

class ProgressReporter:
    """Lightweight progress bar to INFO logger. Prints to stderr via logging.

    Usage:
        pr = ProgressReporter("Scan Morning", total=42)
        for i in range(42):
            pr.update(1)
        pr.done()
    """
    def __init__(self, title: str, total: int, *, throttle_ms: int = 200):
        self.title = title
        self.total = max(1, int(total))
        self.done_count = 0
        self._last_emit = 0.0
        self._throttle = max(0, int(throttle_ms)) / 1000.0

    def _should_emit(self, now: float) -> bool:
        return (now - self._last_emit) >= self._throttle

    def _bar(self, width: int = 20) -> str:
        frac = min(1.0, max(0.0, self.done_count / float(self.total)))
        filled = int(round(frac * width))
        return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"

    def _emit(self, final: bool = False) -> None:
        pct = int(round(100.0 * min(1.0, self.done_count / float(self.total))))
        bar = self._bar()
        msg = f"{self.title}: {bar} {pct}% ({self.done_count}/{self.total})"
        if final:
            logger.info(msg + " ✓")
        else:
            logger.info(msg)

    def update(self, inc: int = 1) -> None:
        try:
            self.done_count = min(self.total, self.done_count + int(inc))
            import time
            now = time.time()
            if self._should_emit(now):
                self._last_emit = now
                self._emit(final=False)
        except Exception:
            # Never break planning due to progress issues
            pass

    def done(self) -> None:
        try:
            self.done_count = self.total
            self._emit(final=True)
        except Exception:
            pass

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
try:
    AVOID_THRESHOLD_MIN = float(CONFIG.get("AVOID_THRESHOLD_MIN", "8"))
except ValueError:
    AVOID_THRESHOLD_MIN = 8.0
try:
    AVOID_STEP_MINUTES = int(CONFIG.get("AVOID_STEP_MINUTES", "15"))
except ValueError:
    AVOID_STEP_MINUTES = 15

# Standard-plan pessimism (rush-hour adjustment)
RUSH_WINDOW_START_LOCAL = CONFIG.get("RUSH_WINDOW_START_LOCAL", "16:30")
RUSH_WINDOW_END_LOCAL = CONFIG.get("RUSH_WINDOW_END_LOCAL", "19:00")
try:
    EVENING_STD_BUFFER_MIN = int(CONFIG.get("EVENING_STD_BUFFER_MIN", "8"))
except ValueError:
    EVENING_STD_BUFFER_MIN = 8
EVENING_STD_PROBE_OFFSETS_MIN = CONFIG.get("EVENING_STD_PROBE_OFFSETS_MIN", "0,10,20")
try:
    EVENING_STD_EXTRA_BUFFER_MIN = int(CONFIG.get("EVENING_STD_EXTRA_BUFFER_MIN", "3"))
except ValueError:
    EVENING_STD_EXTRA_BUFFER_MIN = 3
# Human-centric constraints and preferences
MAX_LEAVE_TIME_LOCAL = CONFIG.get("MAX_LEAVE_TIME_LOCAL", "")  # e.g., "19:30" or empty for none
FRIDAY_EARLY_CUTOFF_LOCAL = CONFIG.get("FRIDAY_EARLY_CUTOFF_LOCAL", "")  # e.g., "17:30"
LATE_PENALTY_START_LOCAL = CONFIG.get("LATE_PENALTY_START_LOCAL", "18:00")  # when late penalty starts
try:
    LATE_PENALTY_PER_15_MIN = int(CONFIG.get("LATE_PENALTY_PER_15_MIN", "2"))  # minutes of penalty per 15 minutes late
except ValueError:
    LATE_PENALTY_PER_15_MIN = 2

# Personal off-login breaks during the day (e.g., smoking/me time), minutes per office day
try:
    PERSONAL_BREAKS_MIN = int(CONFIG.get("PERSONAL_BREAKS_MIN", "0"))
except ValueError:
    PERSONAL_BREAKS_MIN = 0
if PERSONAL_BREAKS_MIN < 0:
    PERSONAL_BREAKS_MIN = 0
if PERSONAL_BREAKS_MIN > 60:
    PERSONAL_BREAKS_MIN = 60

# Timebank (compensation hours) configuration
try:
    TIMEBANK_CURRENT_MIN = int(CONFIG.get("TIMEBANK_CURRENT_MIN", "0"))
except ValueError:
    TIMEBANK_CURRENT_MIN = 0
try:
    TIMEBANK_CAP_MIN = int(CONFIG.get("TIMEBANK_CAP_MIN", str(50 * 60)))  # default 50h
except ValueError:
    TIMEBANK_CAP_MIN = 50 * 60
EXTENSION_ACTIVITY = (CONFIG.get("EXTENSION_ACTIVITY", "gym") or "gym").strip().lower()  # gym|work
try:
    TIMEBANK_MAX_SPEND_PER_DAY_MIN = int(CONFIG.get("TIMEBANK_MAX_SPEND_PER_DAY_MIN", "0"))
except ValueError:
    TIMEBANK_MAX_SPEND_PER_DAY_MIN = 0
if TIMEBANK_CURRENT_MIN < 0:
    TIMEBANK_CURRENT_MIN = 0
if TIMEBANK_CAP_MIN < 0:
    TIMEBANK_CAP_MIN = 0
TIMEBANK_CURRENT_MIN = min(TIMEBANK_CURRENT_MIN, TIMEBANK_CAP_MIN)
if TIMEBANK_MAX_SPEND_PER_DAY_MIN < 0:
    TIMEBANK_MAX_SPEND_PER_DAY_MIN = 0

# Gym configuration
try:
    GYM_ENABLED = (CONFIG.get("GYM_ENABLED", "1") or "1").strip().lower() in {"1","true","yes","y","on"}
except Exception:
    GYM_ENABLED = True
# Read gym addresses only from env; no hardcoded defaults
GYM_ADDRESS_1 = (CONFIG.get("GYM_ADDRESS_1", "") or "").strip()
GYM_ADDRESS_2 = (CONFIG.get("GYM_ADDRESS_2", "") or "").strip()
# Build a list of configured gym addresses (non-empty only)
GYM_ADDRESSES: list[str] = [addr for addr in [GYM_ADDRESS_1, GYM_ADDRESS_2] if addr]
try:
    GYM_TRAIN_MIN_MINUTES = int(CONFIG.get("GYM_TRAIN_MIN_MINUTES", "90"))
except ValueError:
    GYM_TRAIN_MIN_MINUTES = 90
try:
    GYM_TRAIN_MAX_MINUTES = int(CONFIG.get("GYM_TRAIN_MAX_MINUTES", "120"))
except ValueError:
    GYM_TRAIN_MAX_MINUTES = 120
try:
    GYM_TRAIN_STEP_MINUTES = int(CONFIG.get("GYM_TRAIN_STEP_MINUTES", "15"))
except ValueError:
    GYM_TRAIN_STEP_MINUTES = 15
try:
    GYM_MAX_DAYS_PER_WEEK = int(CONFIG.get("GYM_MAX_DAYS_PER_WEEK", "3"))
except ValueError:
    GYM_MAX_DAYS_PER_WEEK = 3
GYM_PREFERRED_DAYS = CONFIG.get("GYM_PREFERRED_DAYS", "MO,WE,FR")
GYM_LEAVE_MODE = (CONFIG.get("GYM_LEAVE_MODE", "earliest") or "earliest").strip().lower()  # earliest|early
try:
    GYM_COMBO_MAX = int(CONFIG.get("GYM_COMBO_MAX", "60"))  # hard cap on gym option evaluations per day
except ValueError:
    GYM_COMBO_MAX = 60
try:
    GYM_DEFER_MAX_MINUTES = int(CONFIG.get("GYM_DEFER_MAX_MINUTES", "90"))  # max minutes to delay leaving office (no timebank)
except ValueError:
    GYM_DEFER_MAX_MINUTES = 90
try:
    GYM_DEFER_STEP_MINUTES = int(CONFIG.get("GYM_DEFER_STEP_MINUTES", "15"))
except ValueError:
    GYM_DEFER_STEP_MINUTES = 15

def _parse_days_list(days_csv: str) -> set[str]:
    if not days_csv:
        return set()
    allowed = {"MO","TU","WE","TH","FR","SA","SU"}
    parts = [p.strip().upper() for p in days_csv.split(",")]
    return {p for p in parts if p in allowed}

def _weekday_token(dt: datetime) -> str:
    # Monday=0
    tokens = ["MO","TU","WE","TH","FR","SA","SU"]
    return tokens[dt.weekday()]

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

# -------- Pretty printing helpers (ANSI colors) --------
_BULLET = "• "
HR_WIDTH = 64
HR_CHAR = "─"

# Optional output tweaks from env (overridable by CLI)
COMPACT_WEEKLY = False
try:
    COMPACT_WEEKLY = parse_bool(CONFIG.get("COMPACT_WEEKLY", "0"), False)
except Exception:
    COMPACT_WEEKLY = False
ASCII_OUTPUT = False
try:
    ASCII_OUTPUT = parse_bool(CONFIG.get("ASCII_OUTPUT", "0"), False)
except Exception:
    ASCII_OUTPUT = False

def _isatty() -> bool:
    try:
        return sys.stdout.isatty()
    except Exception:
        return False

def _supports_utf8() -> bool:
    enc = getattr(sys.stdout, "encoding", None) or ""
    return "UTF" in enc.upper()
def _color_enabled() -> bool:
    try:
        # Respect NO_COLOR if set, otherwise follow config (default on) and TTY
        if os.environ.get("NO_COLOR"):
            return False
        mode = CONFIG.get("COLOR_OUTPUT", "1").strip().lower()
        if mode in {"0", "false", "no", "off"}:
            return False
        if mode == "auto":
            return _isatty()
        return True if mode in {"1", "true", "yes", "on", "always"} else _isatty()
    except Exception:
        return True

_USE_COLOR = _color_enabled()

def _style(txt: str, code: str) -> str:
    if not _USE_COLOR:
        return txt
    return f"\033[{code}m{txt}\033[0m"

def bold(txt: str) -> str:
    return _style(txt, "1")

def dim(txt: str) -> str:
    return _style(txt, "2")

def cyan(txt: str) -> str:
    return _style(txt, "36")

def green(txt: str) -> str:
    return _style(txt, "32")

def yellow(txt: str) -> str:
    return _style(txt, "33")

def magenta(txt: str) -> str:
    return _style(txt, "35")

def red(txt: str) -> str:
    return _style(txt, "31")

def hr(width: int | None = None, ch: str | None = None) -> str:
    w = width if width and width > 0 else HR_WIDTH
    c = ch if ch else HR_CHAR
    return c * w

def print_kv(label: str, value: str, indent: int = 2, label_width: int = 18) -> None:
    spacer = " " * indent
    bullet_raw = _BULLET
    bullet = cyan(bullet_raw) if _USE_COLOR else bullet_raw
    print(f"{spacer}{bullet}{label:<{label_width}} {value}")

def _apply_runtime_output_prefs(
    force_color: str | None = None,
    force_ascii: bool | None = None,
    width: int | None = None,
    compact_weekly: bool | None = None,
) -> None:
    """Apply CLI overrides for output formatting. Mutates globals used by printers.
    force_color: one of {"auto","always","never"}
    """
    global _USE_COLOR, _BULLET, HR_CHAR, HR_WIDTH, COMPACT_WEEKLY
    # Color
    if force_color:
        choice = force_color.lower()
        if choice == "never":
            _USE_COLOR = False
        elif choice == "always":
            _USE_COLOR = True
        else:  # auto
            _USE_COLOR = (not os.environ.get("NO_COLOR")) and _isatty()
    # ASCII
    ascii_mode = ASCII_OUTPUT or (force_ascii is True) or (not _supports_utf8())
    if force_ascii is False:
        ascii_mode = False
    if ascii_mode:
        _BULLET = "- "
        HR_CHAR = "-"
    # Width
    if width and width > 0:
        HR_WIDTH = width
    else:
        try:
            cols = shutil.get_terminal_size(fallback=(HR_WIDTH, 20)).columns
            # keep reasonable bounds
            HR_WIDTH = max(40, min(120, cols))
        except Exception:
            pass
    # Compact weekly
    if compact_weekly is not None:
        COMPACT_WEEKLY = bool(compact_weekly)

def _emoji(txt: str, fallback: str) -> str:
    """Return emoji if terminal likely supports it, else fallback ASCII."""
    if ASCII_OUTPUT or not _supports_utf8():
        return fallback
    return txt

EMO_CAL = _emoji("🗓️", "[Week]")
EMO_OK = _emoji("✅", "[")
EMO_OK_END = "]" if (ASCII_OUTPUT or not _supports_utf8()) else ""
EMO_HOME = _emoji("🏠", "HOME")
EMO_CAR = _emoji("🚗", "Car")
EMO_OFFICE = _emoji("🏢", "Office")
EMO_STAR = _emoji("⭐", "*")

# ---------------- Data models (initial scaffold) ----------------
@dataclass
class CommuteLeg:
    departure: datetime
    arrival: datetime
    duration_minutes: float

@dataclass
class DayPlanDM:
    date: datetime
    mode: str  # OFFICE, HO, OFF, ERROR-...
    outbound: CommuteLeg | None = None
    inbound: CommuteLeg | None = None

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pendelplaner",
        description="Plan optimal morning and evening commute with Google Routes API",
    )
    p.add_argument(
        "--color",
        choices=["auto", "always", "never"],
        default=None,
        help="Color output mode (default from env COLOR_OUTPUT or auto)",
    )
    p.add_argument(
        "--ascii",
        action="store_true",
        help="Force ASCII-only output (bullets/separators)",
    )
    p.add_argument(
        "--width",
        type=int,
        help="Wrap/separator width (auto-detected if omitted)",
    )
    p.add_argument(
        "--compact-weekly",
        dest="compact_weekly",
        action="store_true",
        help="Compact weekly rendering (fewer lines per day)",
    )
    p.add_argument(
        "--no-compact-weekly",
        dest="compact_weekly",
        action="store_false",
        help="Disable compact weekly rendering",
    )
    p.set_defaults(compact_weekly=None)
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress INFO logs during rendering (sets log level to WARNING)",
    )
    p.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass route cache for this run (forces fresh Google Routes API requests)",
    )
    return p

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"

HEADERS = {
    "Content-Type": "application/json",
    "X-Goog-Api-Key": API_KEY,
    # Nur Felder anfordern, die wir brauchen -> Performant & Required
    # (siehe X-Goog-FieldMask-Anforderung in der Doku)
    "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.legs.duration",
}

# --------------- API call budgeting and response cache ---------------
DISABLE_ROUTE_CACHE = False
try:
    MAX_API_CALLS_PER_RUN = int(CONFIG.get("MAX_API_CALLS_PER_RUN", "1000"))
except ValueError:
    MAX_API_CALLS_PER_RUN = 1000
try:
    BUDGET_SOFT_PCT = float(CONFIG.get("BUDGET_SOFT_PCT", "0.9"))
except ValueError:
    BUDGET_SOFT_PCT = 0.9
def _parse_granularity(value: str | None, default_min: int = 5) -> tuple[int, int]:
    """Parse granularity from env.
    Accepts either a single int (e.g., "5") or a range "5..15".
    Returns (bucket_minutes, probe_window_minutes).
    """
    if not value:
        return (default_min, max(5, min(15, default_min)))
    v = value.strip()
    if ".." in v:
        parts = v.split("..")
        try:
            base = int(parts[0])
            probe = int(parts[1])
            return (max(1, base), max(5, min(30, probe)))
        except Exception:
            return (default_min, max(5, min(15, default_min)))
    try:
        base = int(v)
        return (max(1, base), max(5, min(15, base)))
    except Exception:
        return (default_min, max(5, min(15, default_min)))

ROUTE_CACHE_GRANULARITY_MIN, ROUTE_CACHE_PROBE_WINDOW_MIN = _parse_granularity(CONFIG.get("ROUTE_CACHE_GRANULARITY_MIN", "5"), 5)
ROUTE_CACHE: dict[tuple[str, str, str], float] = {}
ROUTE_CACHE_TS: dict[tuple[str, str, str], float] = {}
# Per-run in-memory cache (does not persist to disk); used even when DISABLE_ROUTE_CACHE is true
SESSION_ROUTE_CACHE: dict[tuple[str, str, str], float] = {}
API_CALL_COUNT = 0
ROUTE_CACHE_FILE = CONFIG.get("ROUTE_CACHE_FILE", os.path.join(os.path.dirname(__file__), "routes_cache.json"))
try:
    ROUTE_CACHE_MAX_ENTRIES = int(CONFIG.get("ROUTE_CACHE_MAX_ENTRIES", "50000"))
except ValueError:
    ROUTE_CACHE_MAX_ENTRIES = 50000
try:
    ROUTE_CACHE_TTL_DAYS = int(CONFIG.get("ROUTE_CACHE_TTL_DAYS", "14"))
except ValueError:
    ROUTE_CACHE_TTL_DAYS = 14
ROUTE_CACHE_TTL_SEC = max(0, ROUTE_CACHE_TTL_DAYS) * 24 * 60 * 60

def _serialize_cache_key(t: tuple[str, str, str]) -> str:
    return "\u241f".join(t)  # use unit separator-like char to avoid collisions

def _deserialize_cache_key(s: str) -> tuple[str, str, str] | None:
    try:
        parts = s.split("\u241f")
        if len(parts) != 3:
            return None
        return (parts[0], parts[1], parts[2])
    except Exception:
        return None

def _tz_name() -> str:
    try:
        return getattr(TZ, "key", str(TZ))
    except Exception:
        return "TZ"

def _canonical_key(origin_addr: str, destination_addr: str, stamp: str) -> tuple[str, str, str]:
    """Return canonical cache key tuple including TZ-qualified stamp."""
    tz = _tz_name()
    third = stamp if ("|" in stamp) else f"{tz}|{stamp}"
    return (origin_addr, destination_addr, third)

def _budget_soft_limit_reached() -> bool:
    try:
        threshold = int(MAX_API_CALLS_PER_RUN * max(0.1, min(1.0, BUDGET_SOFT_PCT)))
        return API_CALL_COUNT >= threshold
    except Exception:
        return False

def load_route_cache() -> None:
    try:
        if not os.path.exists(ROUTE_CACHE_FILE):
            return
        with open(ROUTE_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        entries = 0
        deduped = 0
        for k, v in data.items():
            key = _deserialize_cache_key(k)
            if not key:
                continue
            dur = float(v.get("dur"))
            ts = float(v.get("ts", 0))
            # Promote legacy keys to canonical form to avoid duplicates
            origin, dest, stamp = key
            canonical = _canonical_key(origin, dest, stamp)
            # Respect freshest timestamp if duplicates exist
            existing_ts = ROUTE_CACHE_TS.get(canonical, 0)
            if canonical in ROUTE_CACHE and ts <= existing_ts:
                deduped += 1
                continue
            ROUTE_CACHE[canonical] = dur
            ROUTE_CACHE_TS[canonical] = ts
            entries += 1
        logger.info("Loaded route cache: %d entries (deduped %d) from %s", entries, deduped, ROUTE_CACHE_FILE)
    except Exception as e:
        logger.warning("Could not load route cache %s: %s", ROUTE_CACHE_FILE, e)

def save_route_cache() -> None:
    try:
        # prune if needed
        if len(ROUTE_CACHE_TS) > ROUTE_CACHE_MAX_ENTRIES:
            # keep most recent
            items = sorted(ROUTE_CACHE_TS.items(), key=lambda kv: kv[1], reverse=True)
            keep = set(k for k, _ in items[:ROUTE_CACHE_MAX_ENTRIES])
            for k in list(ROUTE_CACHE.keys()):
                if k not in keep:
                    ROUTE_CACHE.pop(k, None)
                    ROUTE_CACHE_TS.pop(k, None)
        # write
        out: dict[str, dict] = {}
        for k, dur in ROUTE_CACHE.items():
            # Ensure we only write canonical keys
            origin, dest, stamp = k
            k_can = _canonical_key(origin, dest, stamp)
            out[_serialize_cache_key(k_can)] = {"dur": float(dur), "ts": float(ROUTE_CACHE_TS.get(k, time.time()))}
        tmp = ROUTE_CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
        os.replace(tmp, ROUTE_CACHE_FILE)
        logger.info("Saved route cache: %d entries to %s", len(out), ROUTE_CACHE_FILE)
    except Exception as e:
        logger.warning("Could not save route cache %s: %s", ROUTE_CACHE_FILE, e)

load_route_cache()
atexit.register(save_route_cache)

def to_rfc3339_local(dt_local: datetime) -> str:
    """Datetime mit lokaler TZ in RFC3339 (mit Offset) für departureTime."""
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=TZ)
    return dt_local.isoformat()

def _floor_dt_to_step(dt_local: datetime, step_min: int) -> datetime:
    base = dt_local.astimezone(TZ)
    minute = (base.minute // step_min) * step_min
    return base.replace(minute=minute, second=0, microsecond=0)

def _tz_name() -> str:
    try:
        return getattr(TZ, "key", str(TZ))
    except Exception:
        return "TZ"

def _canonical_key(origin_addr: str, destination_addr: str, stamp: str) -> tuple[str, str, str]:
    """Return canonical cache key tuple including TZ-qualified stamp."""
    tz = _tz_name()
    # If stamp already contains a pipe, assume it's canonical
    third = stamp if ("|" in stamp) else f"{tz}|{stamp}"
    return (origin_addr, destination_addr, third)

def _candidate_cache_keys(origin_addr: str, destination_addr: str, departure_dt_local: datetime) -> list[tuple[str, str, str]]:
    """Generate candidate cache keys for a given request, tolerant to env changes.
    Keys include a TZ-qualified form and a legacy form without TZ to maximize reuse.
    Also probes nearby bucket times within a small window to survive granularity tweaks.
    """
    tz_name = _tz_name()
    base_time = _floor_dt_to_step(departure_dt_local, ROUTE_CACHE_GRANULARITY_MIN)
    # Probe window derived from env (e.g., 5..15) in 5-minute steps
    window = max(5, min(30, ROUTE_CACHE_PROBE_WINDOW_MIN))
    offsets = [0]
    for off in range(5, window + 1, 5):
        offsets.extend([-off, off])
    candidates: list[tuple[str, str, str]] = []
    for off in offsets:
        t = base_time + timedelta(minutes=off)
        stamp = t.strftime("%Y-%m-%d %H:%M")
        # New-format key includes TZ to avoid cross-TZ collisions
        candidates.append((origin_addr, destination_addr, f"{tz_name}|{stamp}"))
        # Legacy key (pre-v2) without TZ for backward reuse
        candidates.append((origin_addr, destination_addr, stamp))
    return candidates

def _parse_int_list(csv: str) -> list[int]:
    out: list[int] = []
    for part in (csv or "").split(","):
        p = part.strip()
        if not p:
            continue
        try:
            out.append(int(p))
        except Exception:
            continue
    return out

def _is_in_rush_window(dt_local: datetime) -> bool:
    try:
        h1, m1 = map(int, RUSH_WINDOW_START_LOCAL.split(":"))
        h2, m2 = map(int, RUSH_WINDOW_END_LOCAL.split(":"))
        t = dt_local.astimezone(TZ)
        start = t.replace(hour=h1, minute=m1, second=0, microsecond=0)
        end = t.replace(hour=h2, minute=m2, second=0, microsecond=0)
        return start <= t <= end
    except Exception:
        return False

def _dt_with_time_local(base_dt: datetime, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return base_dt.astimezone(TZ).replace(hour=h, minute=m, second=0, microsecond=0)

def _latest_allowed_leave_for_day(baseline_departure: datetime) -> datetime:
    """Compute the latest allowed leave time for the given day, considering:
    - EXTEND_LATEST_LOCAL
    - MAX_LEAVE_TIME_LOCAL (if set)
    - FRIDAY_EARLY_CUTOFF_LOCAL (if Friday and set)
    Returns a datetime in TZ.
    """
    local = baseline_departure.astimezone(TZ)
    # Hard latest from extension config
    h_latest, m_latest = map(int, EXTEND_LATEST_LOCAL.split(":"))
    limit = local.replace(hour=h_latest, minute=m_latest, second=0, microsecond=0)
    # Daily max leave if set
    if MAX_LEAVE_TIME_LOCAL:
        try:
            t = _dt_with_time_local(local, MAX_LEAVE_TIME_LOCAL)
            if t < limit:
                limit = t
        except Exception:
            pass
    # Friday early cutoff
    if local.weekday() == 4 and FRIDAY_EARLY_CUTOFF_LOCAL:
        try:
            t = _dt_with_time_local(local, FRIDAY_EARLY_CUTOFF_LOCAL)
            if t < limit:
                limit = t
        except Exception:
            pass
    return limit

def _late_penalty_minutes(departure_dt: datetime) -> int:
    """Return penalty in 'minutes-equivalent' for leaving late.
    For every 15 minutes after LATE_PENALTY_START_LOCAL, apply LATE_PENALTY_PER_15_MIN.
    """
    try:
        start = _dt_with_time_local(departure_dt, LATE_PENALTY_START_LOCAL)
    except Exception:
        return 0
    if departure_dt <= start:
        return 0
    delta_min = max(0, int(math.ceil((departure_dt - start).total_seconds() / 60.0)))
    steps = int(math.ceil(delta_min / 15.0))
    return steps * max(0, int(LATE_PENALTY_PER_15_MIN))

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

def fmt_dur_h_colon(mins: float) -> str:
    m = fmt_minutes(mins)
    h = m // 60
    r = m % 60
    return f"{h}:{r:02d}h"

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
    best_combo_any = None
    worse_streak = 0
    extra = step_minutes
    # Stop if we pass the latest allowed leave time for the day (human-centric)
    day_limit = _latest_allowed_leave_for_day(baseline_departure)
    while True:
        depart = baseline_departure + timedelta(minutes=extra)
        if depart > day_limit:
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
        penalty = _late_penalty_minutes(depart)
        save_net = save - penalty
        if save_net > 0.5:  # require at least 0.5 minute net improvement
            worse_streak = 0
            if best is None or save_net > best["save_net"]:
                best = {
                    "dep": depart,
                    "dur": dur,
                    "save": save,
                    "save_net": save_net,
                    "penalty_minutes": penalty,
                    "arr": depart + timedelta(minutes=dur),
                    "extend_minutes": extra,
                }
            if save_net >= EXTEND_TARGET_SAVE_MIN:
                break
        else:
            worse_streak += 1
            if worse_streak >= worse_steps_limit:
                break
        extra += step_minutes
    return best

def enumerate_evening_extensions(
    baseline_departure: datetime,
    baseline_duration_min: float,
    step_minutes: int,
    worse_steps_limit: int,
    max_options: int = 3,
) -> list[dict]:
    """Enumerate later departure options with their savings.
    Returns up to max_options entries sorted by save desc. Each entry has keys:
      dep, dur, save, arr, extend_minutes.
    """
    options: list[dict] = []
    worse_streak = 0
    extra = step_minutes
    day_limit = _latest_allowed_leave_for_day(baseline_departure)
    while True:
        depart = baseline_departure + timedelta(minutes=extra)
        local = depart.astimezone(TZ)
        if depart > day_limit:
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
        penalty = _late_penalty_minutes(depart)
        save_net = save - penalty
        if save_net > 0.5:
            options.append({
                "dep": depart,
                "dur": dur,
                "save": save,
                "save_net": save_net,
                "penalty_minutes": penalty,
                "arr": depart + timedelta(minutes=dur),
                "extend_minutes": extra,
            })
            worse_streak = 0
            if save_net >= EXTEND_TARGET_SAVE_MIN and len(options) >= max_options:
                break
        else:
            worse_streak += 1
            if worse_streak >= worse_steps_limit:
                break
        extra += step_minutes
    options.sort(key=lambda o: o["save"], reverse=True)
    return options[:max_options]

def evaluate_evening_range(
    baseline_departure: datetime,
    baseline_duration_min: float,
    step_minutes: int,
    worse_steps_limit: int,
    max_options: int = 3,
) -> dict:
    """Explore later departures starting from baseline to collect:
    - options: top improvements (same shape as enumerate_evening_extensions)
    - worst_dur: worst (max) inbound duration seen in the explored range (incl. baseline)
    - worst_dep/arr: corresponding departure/arrival for worst case
    """
    options: list[dict] = []
    worse_streak = 0
    extra = 0
    day_limit = _latest_allowed_leave_for_day(baseline_departure)
    worst_dur = baseline_duration_min
    worst_dep = baseline_departure
    worst_arr = baseline_departure + timedelta(minutes=baseline_duration_min)
    while True:
        depart = baseline_departure + timedelta(minutes=extra)
        local = depart.astimezone(TZ)
        if depart > day_limit:
            break
        try:
            dur = compute_drive_duration_minutes(DESTINATION_ADDRESS, ORIGIN_ADDRESS, depart) if extra else baseline_duration_min
        except Exception:
            worse_streak += 1
            if worse_streak >= worse_steps_limit:
                break
            extra += step_minutes if extra else step_minutes
            continue
        # Track worst
        if dur > worst_dur:
            worst_dur = dur
            worst_dep = depart
            worst_arr = depart + timedelta(minutes=dur)
        # Track improvements (skip baseline extra=0 entry from options)
        if extra:
            save = baseline_duration_min - dur
            if save > 0.5:
                options.append({
                    "dep": depart,
                    "dur": dur,
                    "save": save,
                    "arr": depart + timedelta(minutes=dur),
                    "extend_minutes": extra,
                })
                worse_streak = 0
            else:
                worse_streak += 1
                if worse_streak >= worse_steps_limit:
                    break
        extra += AVOID_STEP_MINUTES if extra else AVOID_STEP_MINUTES
    options.sort(key=lambda o: o["save"], reverse=True)
    return {"options": options[:max_options], "worst_dur": worst_dur, "worst_dep": worst_dep, "worst_arr": worst_arr}

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
    # If an API client is set up, use it (towards refactor without globals)
    global API_CLIENT
    if API_CLIENT is not None:
        return API_CLIENT.compute_drive_duration_minutes(origin_addr, destination_addr, departure_dt_local)
    # Budget and caching
    global API_CALL_COUNT
    key_time = _floor_dt_to_step(departure_dt_local, ROUTE_CACHE_GRANULARITY_MIN)
    canonical_key = _canonical_key(origin_addr, destination_addr, key_time.strftime('%Y-%m-%d %H:%M'))
    # Check session cache first (always on), then persistent cache if allowed
    for k in _candidate_cache_keys(origin_addr, destination_addr, departure_dt_local):
        if k in SESSION_ROUTE_CACHE:
            return SESSION_ROUTE_CACHE[k]
        if (not DISABLE_ROUTE_CACHE) and (k in ROUTE_CACHE):
            dur = ROUTE_CACHE[k]
            ROUTE_CACHE[canonical_key] = dur
            ROUTE_CACHE_TS[canonical_key] = ROUTE_CACHE_TS.get(k, time.time())
            SESSION_ROUTE_CACHE[canonical_key] = dur
            return dur
    if API_CALL_COUNT >= MAX_API_CALLS_PER_RUN:
        raise RuntimeError(f"API call budget exceeded ({MAX_API_CALLS_PER_RUN}). Increase MAX_API_CALLS_PER_RUN or widen cache granularity.")

    body = {
        "origin": {"address": origin_addr},
        "destination": {"address": destination_addr},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
        "departureTime": to_rfc3339_local(key_time),
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
    dur_min = parse_duration_to_minutes(dur)
    SESSION_ROUTE_CACHE[canonical_key] = dur_min
    if not DISABLE_ROUTE_CACHE:
        ROUTE_CACHE[canonical_key] = dur_min
        ROUTE_CACHE_TS[canonical_key] = time.time()
    API_CALL_COUNT += 1
    return dur_min

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

    # Apply overrides via using_config context manager (no global mutation leakage)
    with using_config(AppConfig.from_env(), overrides={
        "latest_arrival_local": latest_arrival_local,
        "window_start_local": window_start_local,
        "work_hours": work_hours,
        "lunch_min": lunch_min,
        "lunch_max": lunch_max,
        "lunch_step": lunch_step,
        "step_minutes": step_minutes,
    }):
        morning = scan_morning_best_departure(day_local)
        evening = choose_best_evening_departure(morning["best_arrival"])
        return {"outbound": morning, "inbound": evening}

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
    logger.debug(
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
    # Pre-compute number of candidate steps for progress
    total_steps = max(1, int(((latest_arrival_dt - start_dt).total_seconds() // 60) // STEP_MINUTES) + 1)
    pr = None
    if logger.isEnabledFor(logging.INFO):
        try:
            pr = ProgressReporter("Scan Morning", total_steps)
        except Exception:
            pr = None

    current = start_dt
    calls_used = 0
    budget = MAX_API_CALLS_PER_RUN
    while current <= latest_arrival_dt:
        # Nur zukünftige Zeitpunkte an die API senden
        if current <= now_local:
            current += timedelta(minutes=STEP_MINUTES)
            continue
        try:
            logger.debug("Candidate departure: %s", current.astimezone(TZ).strftime("%H:%M"))
            dur_min = compute_drive_duration_minutes(ORIGIN_ADDRESS, DESTINATION_ADDRESS, current)
            calls_used += 1
        except Exception as e:
            last_error_message = str(e)
            logger.debug("Slot error: %s", last_error_message)
            current += timedelta(minutes=STEP_MINUTES)
            if pr:
                pr.update(1)
            continue
        # Soft-guard: if cache disabled and we're near budget, stop early to prevent hard failure
        if DISABLE_ROUTE_CACHE and calls_used >= max(1, int(budget * 0.8)):
            break

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
        if pr:
            pr.update(1)

    if best["best_departure"] is None:
        msg = (
            "Keine Abfahrt gefunden, die rechtzeitig ankommt. "
            "Erweitere das Suchfenster nach vorne oder erhöhe STEP_MINUTES-Auflösung."
        )
        if last_error_message:
            msg += f" Letzte Fehlermeldung: {last_error_message}"
        raise RuntimeError(msg)
    if pr:
        pr.done()
    logger.debug(
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

    logger.debug(
        "Scan Evening: start_from=%s work_hours=%.1f lunch=%d..%d step=%d",
        morning_arrival_local.astimezone(TZ).strftime("%H:%M"), WORK_HOURS,
        LUNCH_MIN_MINUTES, LUNCH_MAX_MINUTES, LUNCH_STEP_MINUTES,
    )
    total_steps = max(1, int(((LUNCH_MAX_MINUTES - LUNCH_MIN_MINUTES) // max(1, LUNCH_STEP_MINUTES)) + 1))
    pr = None
    if logger.isEnabledFor(logging.INFO):
        try:
            pr = ProgressReporter("Scan Evening", total_steps)
        except Exception:
            pr = None

    calls_used = 0
    budget = MAX_API_CALLS_PER_RUN
    for L in range(LUNCH_MIN_MINUTES, LUNCH_MAX_MINUTES + 1, LUNCH_STEP_MINUTES):
        # Personal breaks are mandatory and stack with lunch
        mandatory_breaks = L + PERSONAL_BREAKS_MIN
        evening_departure = morning_arrival_local + timedelta(minutes=work_minutes + mandatory_breaks)
        try:
            dur_min = compute_drive_duration_minutes(DESTINATION_ADDRESS, ORIGIN_ADDRESS, evening_departure)
            calls_used += 1
        except Exception as e:
            last_error_message = str(e)
            if pr:
                pr.update(1)
            continue
        if DISABLE_ROUTE_CACHE and calls_used >= max(1, int(budget * 0.8)):
            break

        if best["evening_duration_minutes"] is None or dur_min < best["evening_duration_minutes"]:
            best["lunch_minutes"] = L
            best["evening_departure"] = evening_departure
            best["evening_duration_minutes"] = dur_min
            best["evening_arrival_home"] = evening_departure + timedelta(minutes=dur_min)

    if pr:
        pr.done()
    if best["evening_departure"] is None:
        msg = "Konnte keine Rückfahrt berechnen (Abend)."
        if last_error_message:
            msg += f" Letzte Fehlermeldung: {last_error_message}"
        raise RuntimeError(msg)
    logger.debug(
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
    if ext and ext.get("save_net", ext.get("save", 0)) >= EXTEND_TARGET_SAVE_MIN:
        result["extended"] = {
            "lunch_minutes": base["lunch_minutes"],
            "evening_departure": ext["dep"],
            "evening_duration_minutes": ext["dur"],
            "evening_arrival_home": ext["arr"],
            "extend_minutes": ext["extend_minutes"],
            "save_minutes": ext.get("save_net", ext.get("save", 0)),
            "penalty_minutes": ext.get("penalty_minutes", 0),
        }
    return result

def choose_best_evening_departure_with_timebank(morning_arrival_local: datetime, timebank_available_min: int) -> dict:
    """Variant that allows leaving at earliest end and waiting (gym) until traffic eases,
    spending from timebank (negative balance) up to timebank_available_min and daily max.

    Returns dict with keys similar to choose_best_evening_departure_with_extension but with
    fields:
      - base: as from choose_best_evening_departure (no timebank spend)
      - spend: optional dict if earlier leave + wait reduces commute:
            {leave_office, wait_minutes, evening_departure, evening_duration_minutes,
             evening_arrival_home, spend_minutes, save_minutes}
    """
    base = choose_best_evening_departure(morning_arrival_local)
    work_minutes = int(WORK_HOURS * 60)
    earliest_end = morning_arrival_local + timedelta(minutes=work_minutes + base["lunch_minutes"] + PERSONAL_BREAKS_MIN)
    # Baseline evening direct drive at earliest_end (used for savings comparisons)
    try:
        dur_base = compute_drive_duration_minutes(DESTINATION_ADDRESS, ORIGIN_ADDRESS, earliest_end)
    except Exception:
        dur_base = base["evening_duration_minutes"]

    # If no timebank to spend, still consider gym after earliest end with spend=0
    max_spend = max(0, min(timebank_available_min, TIMEBANK_MAX_SPEND_PER_DAY_MIN or timebank_available_min))
    if max_spend <= 0:
        best_combo_any = None
        leave_office = earliest_end
        for gym_addr in (GYM_ADDRESSES or []):
            try:
                off2gym = compute_drive_duration_minutes(DESTINATION_ADDRESS, gym_addr, leave_office)
                for train_min in range(GYM_TRAIN_MIN_MINUTES, GYM_TRAIN_MAX_MINUTES + 1, GYM_TRAIN_STEP_MINUTES):
                    depart_homeward = leave_office + timedelta(minutes=off2gym + train_min)
                    gym2home = compute_drive_duration_minutes(gym_addr, ORIGIN_ADDRESS, depart_homeward)
                    combo_any = {
                        "leave_office": leave_office,
                        "wait_minutes": 0,
                        "gym_address": gym_addr,
                        "train_minutes": train_min,
                        "evening_departure": depart_homeward,
                        "evening_duration_minutes": gym2home,
                        "evening_arrival_home": depart_homeward + timedelta(minutes=gym2home),
                        "spend_minutes": 0,
                        "save_minutes": 0,
                        "office_to_gym_minutes": off2gym,
                    }
                    if best_combo_any is None or (off2gym + gym2home) < (best_combo_any.get("office_to_gym_minutes", 0) + best_combo_any.get("evening_duration_minutes", 1e9)):
                        best_combo_any = combo_any
            except Exception:
                continue
        out = {"base": base}
        if best_combo_any:
            out["spend"] = best_combo_any
            out["best_any"] = best_combo_any
        return out

    # Try leaving earlier and waiting outside office up to max_spend
    best = None
    best_combo_any = None
    step = max(5, STEP_MINUTES)
    spend = step if GYM_LEAVE_MODE == "early" else max(step, GYM_TRAIN_MIN_MINUTES)
    # Gym options progress reporter: approximate combinations across spend, gyms, and training durations
    pr = None
    if logger.isEnabledFor(logging.INFO) and (not _budget_soft_limit_reached()):
        try:
            total_spend_steps = max(1, timebank_available_min // step)
            total_train_steps = max(1, (GYM_TRAIN_MAX_MINUTES - GYM_TRAIN_MIN_MINUTES) // max(1, GYM_TRAIN_STEP_MINUTES) + 1)
            total = total_spend_steps * 2 * total_train_steps
            pr = ProgressReporter("Gym Options", total)
        except Exception:
            pr = None
    combos_used = 0
    stop_due_to_cap = False
    while spend <= max_spend and not stop_due_to_cap:
        # Leave early (reduce office) by 'spend' minutes if mode=early, else leave at earliest_end and only vary training
        leave_office = earliest_end - timedelta(minutes=spend) if GYM_LEAVE_MODE == "early" else earliest_end
        # we then go to gym and depart later when traffic improves; compute travel via gym
        # scan training durations and both gym locations, include office->gym and gym->home drives
        try:
            best_combo = None
            for gym_addr in (GYM_ADDRESSES or []):
                # commute office -> gym at leave_office
                off2gym = compute_drive_duration_minutes(DESTINATION_ADDRESS, gym_addr, leave_office)
                for train_min in range(GYM_TRAIN_MIN_MINUTES, GYM_TRAIN_MAX_MINUTES + 1, GYM_TRAIN_STEP_MINUTES):
                    # cap by spend window: only consider if waiting/training time <= spend
                    if GYM_LEAVE_MODE == "early" and train_min > spend:
                        if pr:
                            pr.update(1)
                        break
                    depart_homeward = leave_office + timedelta(minutes=off2gym + train_min)
                    gym2home = compute_drive_duration_minutes(gym_addr, ORIGIN_ADDRESS, depart_homeward)
                    total_evening_drive = off2gym + gym2home
                    save = dur_base - total_evening_drive
                    spend_used = spend if GYM_LEAVE_MODE == "early" else 0
                    combo_any = {
                        "leave_office": leave_office,
                        "wait_minutes": spend_used,
                        "gym_address": gym_addr,
                        "train_minutes": train_min,
                        "evening_departure": depart_homeward,  # leaving gym by car
                        "evening_duration_minutes": gym2home,
                        "evening_arrival_home": depart_homeward + timedelta(minutes=gym2home),
                        "spend_minutes": spend_used,
                        "save_minutes": save,
                        "office_to_gym_minutes": off2gym,
                    }
                    # Track best by savings if any positive improvement
                    if save > 0.5:
                        if best_combo is None or combo_any["save_minutes"] > best_combo["save_minutes"]:
                            best_combo = combo_any
                    # Always track absolute minimum total evening drive as fallback
                    if best_combo_any is None or total_evening_drive < (best_combo_any.get("office_to_gym_minutes", 0) + best_combo_any.get("evening_duration_minutes", 1e9)):
                        best_combo_any = combo_any
                    if pr:
                        pr.update(1)
                    combos_used += 1
                    if combos_used >= max(1, int(GYM_COMBO_MAX)):
                        stop_due_to_cap = True
                        break
                if stop_due_to_cap:
                    break
        except Exception:
            spend += step
            continue
        if best_combo is None:
            spend += step
            continue
        if best is None or best_combo["save_minutes"] > best["save_minutes"]:
            best = best_combo
        spend += step
        # If approaching budget, stop exploring further to avoid hard cap
        if _budget_soft_limit_reached():
            break
    if pr:
        pr.done()
    # If no saving option found, fall back to the absolute-min drive combo so we can still go to the gym
    if best is None and best_combo_any is not None:
        best = best_combo_any
    out = {"base": base}
    if best:
        out["spend"] = best
    if best_combo_any:
        out["best_any"] = best_combo_any
    return out

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
    steps = list(range(0, 61, STEP_MINUTES))
    pr = None
    if logger.isEnabledFor(logging.INFO):
        try:
            pr = ProgressReporter("Optimize Day", len(steps))
        except Exception:
            pr = None
    start_dep = base_morning["best_departure"]
    for plus in steps:
        cand_dep = start_dep + timedelta(minutes=plus)
        # compute morning drive
        try:
            dur_min = compute_drive_duration_minutes(ORIGIN_ADDRESS, DESTINATION_ADDRESS, cand_dep)
        except Exception:
            if pr:
                pr.update(1)
            continue
        cand_arr = cand_dep + timedelta(minutes=dur_min)
        if cand_arr > latest_arrival_dt:
            if pr:
                pr.update(1)
            break
        # evening with extension
        eve = choose_best_evening_departure_with_extension(cand_arr)
        chosen_eve = eve.get("extended") or eve["base"]
        eve_best_dur = chosen_eve["evening_duration_minutes"]
        # Add lifestyle penalty if extending
        penalty = 0
        if "penalty_minutes" in chosen_eve:
            penalty = max(0, int(round(chosen_eve.get("penalty_minutes", 0))))
        elif chosen_eve is not eve["base"]:
            penalty = _late_penalty_minutes(chosen_eve["evening_departure"])  # fallback
        total_score = dur_min + eve_best_dur + penalty
        if best is None or total_score < best.get("total_score", 1e9):
            chosen = eve.get("extended", None)
            best = {
                "morning": {"best_departure": cand_dep, "best_arrival": cand_arr, "best_duration_minutes": dur_min},
                "lunch_minutes": (chosen or eve["base"]) ["lunch_minutes"],
                "evening_departure": (chosen or eve["base"]) ["evening_departure"],
                "evening_duration_minutes": (chosen or eve["base"]) ["evening_duration_minutes"],
                "evening_arrival_home": (chosen or eve["base"]) ["evening_arrival_home"],
                "extend_minutes": (chosen or {}).get("extend_minutes", 0),
                "save_minutes": (chosen or {}).get("save_minutes", 0),
                "penalty_minutes": penalty,
                "total_travel_minutes": dur_min + eve_best_dur,
                "total_score": total_score,
            }
        if pr:
            pr.update(1)
    if pr:
        pr.done()
    # Compare using score (travel + penalty) to accept improvements
    if best and (best.get("total_score", 1e9) + 0.1) < (base_total + _late_penalty_minutes(base_evening["base"]["evening_departure"])):
        return best
    return None

def main():
    # Parse CLI and apply output prefs early
    parser = _build_arg_parser()
    args = parser.parse_args()
    _apply_runtime_output_prefs(
        force_color=args.color,
        force_ascii=args.ascii,
        width=args.width,
        compact_weekly=args.compact_weekly,
    )
    ensure_api_key_configured()
    if getattr(args, "quiet", False):
        logger.setLevel(logging.WARNING)
    # Disable cache per flag
    global DISABLE_ROUTE_CACHE
    if getattr(args, "no_cache", False):
        DISABLE_ROUTE_CACHE = True
    # Prepare API client (towards refactor)
    global API_CLIENT
    try:
        API_CLIENT = RoutesApiClient(API_KEY)
        logger.info("API client initialized")
    except Exception as e:
        logger.debug("API client fallback to direct requests: %s", e)
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
            # If the entire Mon-Fri window is in the past (e.g., running on Sat/Sun),
            # shift to next week so the plan is forward-looking.
            if (base.date() + timedelta(days=4)) < today_local.date():
                base = base + timedelta(days=7)
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
        render_weekly_output(base, cfg)
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
