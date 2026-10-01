"""
Shared training configuration and plan generation.
Reads .env, computes derived values, generates the full day-by-day training plan.
Imported by webapp.py and weekly_checkin.py.
"""
import hashlib
import os
from datetime import datetime, timedelta

# ─── .env parsing (no external dependency) ───

def _load_env(path=None):
    if path is None:
        base = os.path.dirname(os.path.abspath(__file__))
        # Try env first, fall back to .env
        path = os.path.join(base, "env")
        if not os.path.exists(path):
            path = os.path.join(base, ".env")
    env = {}
    if not os.path.exists(path):
        return env
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            if value:
                env[key.strip()] = value.strip()
    return env


DAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _parse_days(s):
    """Parse comma-separated day abbreviations into sorted weekday indices."""
    return sorted(DAY_MAP[d.strip().lower()] for d in s.split(",") if d.strip().lower() in DAY_MAP)


def _parse_pace(s):
    """Parse 'M:SS' pace string to total seconds per km."""
    parts = s.strip().split(":")
    return int(parts[0]) * 60 + int(parts[1])


WARMUP_MINUTES = 5  # warmup/ramp-up time added to every run


def _run_minutes(distance_km, pace_str):
    """Pure running time in minutes (no warmup)."""
    if not distance_km or not pace_str:
        return 0
    if "-" in pace_str:
        parts = pace_str.split("-")
        pace_sec = (_parse_pace(parts[0]) + _parse_pace(parts[1])) / 2
    else:
        pace_sec = _parse_pace(pace_str)
    return round(distance_km * pace_sec / 60)


def _estimate_run_minutes(distance_km, pace_str):
    """Total session time: running + warmup ramp-up."""
    return _run_minutes(distance_km, pace_str) + WARMUP_MINUTES


def load_config():
    env = _load_env()

    race_date = datetime.strptime(env.get("RACE_DATE", "2026-09-20"), "%Y-%m-%d")
    plan_start = datetime.strptime(env.get("PLAN_START", "2026-05-04"), "%Y-%m-%d")

    # Weeks from start to race date, rounded UP so race day always falls inside
    # the final (race) week. Deliberately NOT snapped to a multiple of 4 --
    # snapping either way moves race week off the actual race date.
    plan_weeks = max(4, -(-(race_date - plan_start).days // 7))

    max_hr = int(env.get("MAX_HR", "185"))

    # HR zones
    if env.get("HR_ZONE_BOUNDARIES"):
        bounds = [int(x) for x in env["HR_ZONE_BOUNDARIES"].split(",")]
    else:
        pcts = [int(x) for x in env.get("HR_ZONE_PCTS", "70,80,88,95").split(",")]
        bounds = [round(max_hr * p / 100) for p in pcts]

    zone_boundaries = {
        "Z1": (0, bounds[0]),
        "Z2": (bounds[0], bounds[1]),
        "Z3": (bounds[1], bounds[2]),
        "Z4": (bounds[2], bounds[3]),
        "Z5": (bounds[3], 220),
    }

    run_days = _parse_days(env.get("RUN_DAYS", "tue,thu,sat"))
    gym_days = _parse_days(env.get("GYM_DAYS", "wed"))
    long_run_day = DAY_MAP[env.get("LONG_RUN_DAY", "sat").strip().lower()]
    rest_days = sorted(set(range(7)) - set(run_days) - set(gym_days))

    easy_pace_str = env.get("EASY_PACE", "6:30-7:00")
    race_pace_str = env.get("RACE_PACE", "5:40")

    config = {
        "race_date": race_date,
        "plan_start": plan_start,
        "plan_weeks": plan_weeks,
        "weight_target_kg": float(env.get("ATHLETE_WEIGHT_TARGET_KG", "70")),
        "max_hr": max_hr,
        "zone_boundaries": zone_boundaries,
        "run_days": run_days,
        "gym_days": gym_days,
        "long_run_day": long_run_day,
        "rest_days": rest_days,
        "easy_pace": easy_pace_str,
        "race_pace": race_pace_str,
        "base_weekly_km": int(env.get("BASE_WEEKLY_KM", "20")),
        "peak_weekly_km": int(env.get("PEAK_WEEKLY_KM", "45")),
        "race_week_km": int(env.get("RACE_WEEK_KM", "10")),
        "deload_pct": int(env.get("DELOAD_REDUCTION_PCT", "35")),
        "quality_start_week": int(env.get("QUALITY_START_WEEK", "5")),
        "race_pace_start_week": int(env.get("RACE_PACE_START_WEEK", "9")),
        "event_start_hour": int(env.get("EVENT_START_HOUR", "18")),
        "gym_duration_min": int(env.get("GYM_DURATION_MIN", "90")),
    }

    _validate(config)
    return config


def _validate(config):
    assert config["long_run_day"] in config["run_days"], \
        f"LONG_RUN_DAY must be one of RUN_DAYS"
    assert not set(config["run_days"]) & set(config["gym_days"]), \
        f"RUN_DAYS and GYM_DAYS must not overlap"
    assert config["plan_weeks"] >= 4, \
        f"Plan must be at least 4 weeks (got {config['plan_weeks']})"


# ─── Plan Generation ───

def _generate_weekly_volumes(base_km, peak_km, race_week_km, num_weeks, deload_pct):
    """Generate weekly km targets using progressive build + deload + taper.

    Rules:
    - Build weeks increase by max 10% from the previous build week
    - Every 4th week is a deload (reduced by deload_pct)
    - Post-deload week resumes at the pre-deload level (not a jump)
    - Last 4 weeks: sharpening + taper + race week
    """
    volumes = {}
    taper_start = num_weeks - 3

    # Every 4th week is a deload; the rest before the taper are build weeks.
    # Iterating weeks directly (not 4-week blocks) keeps this correct when
    # num_weeks is not a multiple of 4.
    build_weeks = [w for w in range(1, taper_start) if w % 4 != 0]
    if len(build_weeks) > 1:
        increment = (peak_km - base_km) / (len(build_weeks) - 1)
    else:
        increment = 0

    current = base_km
    for i, wk in enumerate(build_weeks):
        if i:
            # Even distribution toward peak, capped at 10% per build week
            current = min(base_km + increment * i, current * 1.10)
        volumes[wk] = round(current)
        current = volumes[wk]

    for wk in range(4, taper_start, 4):
        volumes[wk] = round(volumes[wk - 1] * (1 - deload_pct / 100))

    # Taper block (last 4 weeks)
    volumes[taper_start] = round(peak_km * 0.89)
    volumes[taper_start + 1] = round(peak_km * 0.78)
    volumes[taper_start + 2] = round(peak_km * 0.56)
    volumes[num_weeks] = race_week_km

    return volumes


def _long_run_km(weekly_km, week_num, num_weeks):
    if week_num == num_weeks:
        return 0
    is_deload = (week_num % 4 == 0 and week_num < num_weeks - 3)
    if is_deload:
        pct = 0.33
    elif week_num <= 4:
        pct = 0.35
    elif week_num <= 8:
        pct = 0.37
    elif week_num <= 12:
        pct = 0.40
    elif week_num == num_weeks - 3:
        pct = 0.50  # sharpening: last big long run
    else:
        pct = 0.35  # taper
    return round(weekly_km * pct)


def _get_phase(week_num, num_weeks):
    if week_num == num_weeks:
        return "Race Week"
    if week_num >= num_weeks - 2:
        return "Taper"
    if week_num == num_weeks - 3:
        return "Sharpening"

    num_blocks = num_weeks // 4
    build_blocks = num_blocks - 1  # last block is taper
    block = (week_num - 1) // 4  # 0-indexed
    pos_in_block = (week_num - 1) % 4

    # Dynamic phase names based on number of build blocks
    if build_blocks <= 3:
        names = ["Base Building", "Aerobic Dev", "Race-Specific"]
    elif build_blocks == 4:
        names = ["Base Building", "Aerobic Dev", "Race-Specific", "Race-Specific"]
    else:
        names = ["Base Building", "Base Building", "Aerobic Dev", "Aerobic Dev", "Race-Specific"]
        # Extend if needed
        while len(names) < build_blocks:
            names.insert(-1, "Race-Specific")

    if block < len(names):
        base = names[block]
        # Peak week: last build week before taper block (3rd week of last build block)
        last_build_block = build_blocks - 1
        if block == last_build_block and pos_in_block == 2:
            return f"{base} (Peak)"
        if pos_in_block == 3:
            return f"{base} (Deload)"
        return base
    return "Training"


def _generate_week_schedule(week_num, weekly_km, long_km, config):
    """Generate 7-day schedule for a week. Returns list of 7 session dicts (Mon=0)."""
    schedule = [None] * 7
    run_days = config["run_days"]
    gym_days = config["gym_days"]
    long_day = config["long_run_day"]
    num_weeks = config["plan_weeks"]
    easy_pace = config["easy_pace"]
    race_pace = config["race_pace"]

    is_deload = (week_num % 4 == 0 and week_num < num_weeks - 3)
    is_race_week = (week_num == num_weeks)

    # Gym sessions
    gym_dur = config["gym_duration_min"]
    deload_gym_dur = round(gym_dur * 0.67)  # lighter on deload
    for d in gym_days:
        dur = deload_gym_dur if is_deload else gym_dur
        schedule[d] = {
            "type": "gym",
            "distance_km": None,
            "session_type": "strength",
            "pace_target": None,
            "description": f"Strength (light, {dur} min)" if is_deload else f"Strength training ({dur} min)",
            "duration_min": dur,
        }

    # Rest days
    for d in range(7):
        if d not in run_days and d not in gym_days:
            schedule[d] = {
                "type": "rest",
                "distance_km": None,
                "session_type": None,
                "pace_target": None,
                "description": "Rest / active recovery",
            }

    # Race week special handling.
    # The race lands on RACE_DATE's weekday, which is NOT necessarily a configured
    # run day -- a Sunday race with RUN_DAYS=mon,wed,sat is the normal case.
    if is_race_week:
        race_dow = config["race_date"].weekday()
        day_before = (race_dow - 1) % 7
        shakeout_days = [d for d in run_days if d not in (race_dow, day_before)]
        km = round(weekly_km / max(len(shakeout_days), 1), 1) if shakeout_days else 0

        for d in sorted(set(run_days) | {race_dow, day_before}):
            if d == race_dow:
                schedule[d] = {
                    "type": "run",
                    "distance_km": 21.1,
                    "session_type": "race",
                    "pace_target": race_pace,
                    "description": "RACE DAY - Half Marathon",
                    "duration_min": _estimate_run_minutes(21.1, race_pace),
                }
            elif d == day_before:
                schedule[d] = {
                    "type": "rest",
                    "distance_km": None,
                    "session_type": None,
                    "pace_target": None,
                    "description": "Rest - race tomorrow. Light walk, lay out gear, early bed.",
                }
            else:
                schedule[d] = {
                    "type": "run",
                    "distance_km": km,
                    "session_type": "shakeout",
                    "pace_target": easy_pace,
                    "description": f"Shakeout {km} km",
                    "duration_min": _estimate_run_minutes(km, easy_pace),
                }

        # No loading in the 48h before the race, whatever GYM_DAYS says.
        for offset in (1, 2):
            d = (race_dow - offset) % 7
            if schedule[d] and schedule[d]["type"] == "gym":
                schedule[d] = {
                    "type": "rest",
                    "distance_km": None,
                    "session_type": None,
                    "pace_target": None,
                    "description": "Rest - no strength work this close to the race",
                }
        return schedule

    # Determine quality session
    quality_type = None
    if not is_deload:
        if week_num >= config["race_pace_start_week"]:
            quality_type = "race_pace"
        elif week_num >= config["quality_start_week"]:
            quality_type = "tempo"

    # Distribute km
    non_long_days = [d for d in run_days if d != long_day]
    remaining_km = weekly_km - long_km

    quality_day = None
    quality_km = 0
    if quality_type and len(non_long_days) >= 2:
        # Pick the later non-long run day for quality
        quality_day = max(non_long_days)
        quality_km = round(remaining_km * 0.45, 1)
        remaining_km -= quality_km

    easy_days = [d for d in non_long_days if d != quality_day]
    if easy_days:
        base_easy = round(remaining_km / len(easy_days), 1)
        # Assign, and give remainder to last easy day
        easy_kms = [base_easy] * len(easy_days)
        leftover = round(remaining_km - sum(easy_kms), 1)
        easy_kms[-1] = round(easy_kms[-1] + leftover, 1)
    elif quality_day is not None:
        # Only quality day + long run
        quality_km = round(quality_km + remaining_km, 1)
        easy_kms = []
    else:
        easy_kms = []

    # Build run sessions
    easy_idx = 0
    for d in run_days:
        if d == long_day:
            schedule[d] = {
                "type": "run",
                "distance_km": long_km,
                "session_type": "long",
                "pace_target": easy_pace,
                "description": f"Long run {long_km} km",
                "duration_min": _estimate_run_minutes(long_km, easy_pace),
            }
        elif d == quality_day:
            warmup = 2.0
            cooldown = 2.0
            work = round(quality_km - warmup - cooldown, 1)
            if work < 1:
                work = 1.0
            pace = race_pace if quality_type == "race_pace" else "6:00-6:30"
            label = "Race pace" if quality_type == "race_pace" else "Tempo"
            # Quality sessions: WU/CD at easy pace, work at target pace
            dur = _run_minutes(warmup + cooldown, easy_pace) + _run_minutes(work, pace) + WARMUP_MINUTES
            schedule[d] = {
                "type": "run",
                "distance_km": quality_km,
                "session_type": quality_type,
                "pace_target": pace,
                "description": f"{label}: 2km WU + {work}km @ {pace}/km + 2km CD",
                "duration_min": dur,
            }
        else:
            km = easy_kms[easy_idx] if easy_idx < len(easy_kms) else 0
            easy_idx += 1
            schedule[d] = {
                "type": "run",
                "distance_km": km,
                "session_type": "easy",
                "pace_target": easy_pace,
                "description": f"Easy run {km} km",
                "duration_min": _estimate_run_minutes(km, easy_pace),
            }

    return schedule


def generate_full_plan(config):
    """Generate the complete day-by-day training plan. Returns {week_num: {km, long, phase, days}}."""
    volumes = _generate_weekly_volumes(
        config["base_weekly_km"], config["peak_weekly_km"],
        config["race_week_km"], config["plan_weeks"], config["deload_pct"],
    )
    plan = {}
    for wk in range(1, config["plan_weeks"] + 1):
        weekly_km = volumes[wk]
        long_km = _long_run_km(weekly_km, wk, config["plan_weeks"])
        phase = _get_phase(wk, config["plan_weeks"])
        days = _generate_week_schedule(wk, weekly_km, long_km, config)
        plan[wk] = {
            "km": weekly_km,
            "long": long_km,
            "phase": phase,
            "days": days,
        }
    return plan


# ─── iCalendar Export ───

def config_hash(config):
    """Deterministic hash of plan-affecting config values."""
    key = f"{config['race_date'].isoformat()}|{config['plan_start'].isoformat()}|" \
          f"{config['base_weekly_km']}|{config['peak_weekly_km']}|" \
          f"{','.join(str(d) for d in config['run_days'])}|" \
          f"{','.join(str(d) for d in config['gym_days'])}"
    return hashlib.sha256(key.encode()).hexdigest()[:8]


def generate_ics(plan, config, start_date=None, end_date=None):
    """Generate .ics calendar content as a string."""
    chash = config_hash(config)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//HalfMarathonTrainer//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Training Plan",
    ]

    now_stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    for wk in range(1, config["plan_weeks"] + 1):
        week_data = plan[wk]
        week_start = config["plan_start"] + timedelta(weeks=wk - 1)

        for day_offset in range(7):
            date = week_start + timedelta(days=day_offset)
            if start_date and date < start_date:
                continue
            if end_date and date > end_date:
                continue

            session = week_data["days"][day_offset]
            if session is None:
                continue

            uid = f"training-{date.strftime('%Y%m%d')}-{chash}@halfmarathon"
            summary = _ics_summary(session, wk)
            desc = _ics_description(session, week_data["phase"], wk, config["plan_weeks"])

            # Rest days: all-day events. Run/gym: timed events.
            if session["type"] == "rest":
                lines.extend([
                    "BEGIN:VEVENT",
                    f"UID:{uid}",
                    f"DTSTAMP:{now_stamp}",
                    f"DTSTART;VALUE=DATE:{date.strftime('%Y%m%d')}",
                    f"DTEND;VALUE=DATE:{(date + timedelta(days=1)).strftime('%Y%m%d')}",
                    f"SUMMARY:{_ics_escape(summary)}",
                    f"DESCRIPTION:{_ics_escape(desc)}",
                    f"CATEGORIES:Training,Rest",
                    "TRANSP:TRANSPARENT",
                    "END:VEVENT",
                ])
            else:
                # Timed event
                start_hour = config["event_start_hour"]
                dur_min = session.get("duration_min", 60)
                dt_start = datetime(date.year, date.month, date.day, start_hour, 0)
                dt_end = dt_start + timedelta(minutes=dur_min)
                lines.extend([
                    "BEGIN:VEVENT",
                    f"UID:{uid}",
                    f"DTSTAMP:{now_stamp}",
                    f"DTSTART:{dt_start.strftime('%Y%m%dT%H%M%S')}",
                    f"DTEND:{dt_end.strftime('%Y%m%dT%H%M%S')}",
                    f"SUMMARY:{_ics_escape(summary)}",
                    f"DESCRIPTION:{_ics_escape(desc)}",
                    f"CATEGORIES:Training,{session['type'].capitalize()}",
                    "END:VEVENT",
                ])

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def _ics_summary(session, week_num):
    t = session["type"]
    if session.get("session_type") == "race":
        return "RACE DAY - Half Marathon"
    if t == "run":
        km = session["distance_km"]
        stype = (session.get("session_type") or "run").replace("_", " ").title()
        return f"{stype} Run {km}km (W{week_num})"
    if t == "gym":
        dur = session.get("duration_min", 30)
        return f"Strength {dur}min (W{week_num})"
    return f"Rest Day (W{week_num})"


def _ics_description(session, phase, week_num, total_weeks):
    parts = [session.get("description", "")]
    if session.get("pace_target"):
        parts.append(f"Pace: {session['pace_target']}/km")
    parts.append(f"Phase: {phase}")
    parts.append(f"Week {week_num} of {total_weeks}")
    return "\\n".join(parts)


def _ics_escape(s):
    """Escape special characters for iCalendar text fields."""
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


# ─── Flatten plan to date-indexed schedule ───

def plan_to_schedule(plan, config):
    """Convert plan to {date_str: session} dict for easy lookup."""
    schedule = {}
    for wk in range(1, config["plan_weeks"] + 1):
        week_start = config["plan_start"] + timedelta(weeks=wk - 1)
        for day_offset in range(7):
            date = week_start + timedelta(days=day_offset)
            session = plan[wk]["days"][day_offset]
            if session:
                session_copy = dict(session)
                session_copy["week"] = wk
                session_copy["phase"] = plan[wk]["phase"]
                schedule[date.strftime("%Y-%m-%d")] = session_copy
    return schedule


# ─── Module-level exports ───

CONFIG = load_config()
PLAN = generate_full_plan(CONFIG)
PLAN_SCHEDULE = plan_to_schedule(PLAN, CONFIG)

# Backward-compatible exports
PLAN_START = CONFIG["plan_start"]
HALF_MARATHON_DATE = CONFIG["race_date"]
ZONE_BOUNDARIES = CONFIG["zone_boundaries"]
WEEKLY_TARGETS = {
    wk: {"km": data["km"], "long": data["long"], "phase": data["phase"]}
    for wk, data in PLAN.items()
}
