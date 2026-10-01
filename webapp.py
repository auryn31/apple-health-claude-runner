"""
Interactive training dashboard web app.
Parses Apple Health export, caches results, serves a local dashboard.

Usage: python webapp.py [--port 5050] [--export apple_health_export/export.xml]
"""
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request
import argparse
import json
import os
import sys

from config import (
    CONFIG, PLAN, PLAN_SCHEDULE, WEEKLY_TARGETS, ZONE_BOUNDARIES,
    PLAN_START, HALF_MARATHON_DATE, generate_ics, plan_to_schedule,
)

app = Flask(__name__)

CACHE_DIR = "_cache"
CACHE_FILE = os.path.join(CACHE_DIR, "health_data.json")

# Global data store
DATA = {}


def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d")
        except Exception:
            return None


def parse_date_simple(s):
    """Parse a YYYY-MM-DD string."""
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except Exception:
        return None


def get_stat(stats, stat_type, field="sum"):
    s = stats.get(stat_type, {})
    val = s.get(field)
    return float(val) if val else None


def classify_zone(avg_hr):
    if not avg_hr:
        return "Unknown"
    for name, (lo, hi) in ZONE_BOUNDARIES.items():
        if lo <= avg_hr < hi:
            return name
    return "Unknown"


def get_training_week(dt):
    if dt < PLAN_START:
        return None
    delta = (dt - PLAN_START).days
    week = delta // 7 + 1
    return week if week <= CONFIG["plan_weeks"] else None


def parse_export(path):
    print(f"Parsing {path} (this may take a minute for large files)...")
    workouts = []
    records_by_type = defaultdict(list)
    activity_summaries = []

    types_of_interest = {
        "HKQuantityTypeIdentifierRestingHeartRate",
        "HKQuantityTypeIdentifierVO2Max",
        "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
        "HKQuantityTypeIdentifierBodyMass",
        "HKQuantityTypeIdentifierStepCount",
        "HKCategoryTypeIdentifierSleepAnalysis",
        "HKQuantityTypeIdentifierWalkingHeartRateAverage",
        "HKQuantityTypeIdentifierHeartRateRecoveryOneMinute",
        "HKQuantityTypeIdentifierRespiratoryRate",
        "HKQuantityTypeIdentifierOxygenSaturation",
        "HKQuantityTypeIdentifierTimeInDaylight",
    }

    context = ET.iterparse(path, events=("end",))
    count = 0
    for event, elem in context:
        tag = elem.tag
        if tag == "Record":
            rtype = elem.get("type")
            if rtype in types_of_interest:
                records_by_type[rtype].append({
                    "value": elem.get("value"),
                    "unit": elem.get("unit"),
                    "startDate": elem.get("startDate"),
                    "endDate": elem.get("endDate"),
                })
            elem.clear()
        elif tag == "Workout":
            w = {
                "type": elem.get("workoutActivityType"),
                "duration": elem.get("duration"),
                "startDate": elem.get("startDate"),
                "endDate": elem.get("endDate"),
            }
            stats = {}
            for ws in elem.findall("WorkoutStatistics"):
                stats[ws.get("type")] = {
                    "average": ws.get("average"),
                    "minimum": ws.get("minimum"),
                    "maximum": ws.get("maximum"),
                    "sum": ws.get("sum"),
                    "unit": ws.get("unit"),
                }
            w["stats"] = stats
            meta = {}
            for me in elem.findall("MetadataEntry"):
                meta[me.get("key")] = me.get("value")
            w["metadata"] = meta
            workouts.append(w)
            elem.clear()
        elif tag == "ActivitySummary":
            activity_summaries.append({
                "date": elem.get("dateComponents"),
                "activeEnergyBurned": elem.get("activeEnergyBurned"),
                "appleExerciseTime": elem.get("appleExerciseTime"),
            })
            elem.clear()
        else:
            if tag in ("Correlation", "ClinicalRecord", "Audiogram"):
                elem.clear()
        count += 1
        if count % 1000000 == 0:
            print(f"  ...{count:,} elements")

    print(f"Done ({count:,} elements).")
    return workouts, dict(records_by_type), activity_summaries


def build_dashboard_data(workouts, records, activity_summaries):
    """Process raw parsed data into dashboard-ready structures."""
    now = datetime.now()
    current_week = get_training_week(now)
    days_to_race = (HALF_MARATHON_DATE - now).days

    # ─── Process runs ───
    runs = [w for w in workouts if "Running" in (w.get("type") or "")]
    runs.sort(key=lambda x: x.get("startDate") or "")

    run_list = []
    runs_by_week = defaultdict(list)

    for r in runs:
        dt = parse_date(r["startDate"])
        if not dt:
            continue
        dist = get_stat(r["stats"], "HKQuantityTypeIdentifierDistanceWalkingRunning") or 0
        dur = float(r["duration"] or 0)
        avg_hr = get_stat(r["stats"], "HKQuantityTypeIdentifierHeartRate", "average")
        max_hr = get_stat(r["stats"], "HKQuantityTypeIdentifierHeartRate", "maximum")
        indoor = r["metadata"].get("HKIndoorWorkout") == "1"
        cal = get_stat(r["stats"], "HKQuantityTypeIdentifierActiveEnergyBurned") or 0

        if dist > 0 and dur > 0:
            pace_sec = (dur * 60) / dist
            pace_min = int(pace_sec // 60)
            pace_s = int(pace_sec % 60)
            pace_str = f"{pace_min}:{pace_s:02d}"
        else:
            pace_str = None
            pace_sec = None

        zone = classify_zone(avg_hr)
        tw = get_training_week(dt)

        # Running form metrics
        stride = get_stat(r["stats"], "HKQuantityTypeIdentifierRunningStrideLength", "average")
        gct = get_stat(r["stats"], "HKQuantityTypeIdentifierRunningGroundContactTime", "average")
        vert_osc = get_stat(r["stats"], "HKQuantityTypeIdentifierRunningVerticalOscillation", "average")
        power = get_stat(r["stats"], "HKQuantityTypeIdentifierRunningPower", "average")

        entry = {
            "date": dt.strftime("%Y-%m-%d"),
            "dist": round(dist, 2),
            "dur": round(dur, 1),
            "pace": pace_str,
            "pace_sec": round(pace_sec, 1) if pace_sec else None,
            "avg_hr": round(avg_hr, 1) if avg_hr else None,
            "max_hr": round(max_hr, 1) if max_hr else None,
            "zone": zone,
            "indoor": indoor,
            "cal": round(cal),
            "week": tw,
            "stride": round(stride, 2) if stride else None,
            "gct": round(gct) if gct else None,
            "vert_osc": round(vert_osc, 1) if vert_osc else None,
            "power": round(power) if power else None,
        }
        run_list.append(entry)
        if tw:
            runs_by_week[tw].append(entry)

    # ─── Process gym/strength workouts ───
    gym_types = {
        "HKWorkoutActivityTypeTraditionalStrengthTraining",
        "HKWorkoutActivityTypeFunctionalStrengthTraining",
        "HKWorkoutActivityTypeCoreTraining",
    }
    gym_workouts = [w for w in workouts if w.get("type") in gym_types]
    gym_workouts.sort(key=lambda x: x.get("startDate") or "")

    gym_list = []
    gym_by_week = defaultdict(list)
    for g in gym_workouts:
        dt = parse_date(g["startDate"])
        if not dt:
            continue
        dur = float(g["duration"] or 0)
        cal = get_stat(g["stats"], "HKQuantityTypeIdentifierActiveEnergyBurned") or 0
        tw = get_training_week(dt)
        entry = {
            "date": dt.strftime("%Y-%m-%d"),
            "day_of_week": dt.strftime("%a"),
            "dur": round(dur, 1),
            "cal": round(cal),
            "week": tw,
        }
        gym_list.append(entry)
        if tw:
            gym_by_week[tw].append(entry)

    # ─── Pre-plan historical weeks (13 weeks before plan start) ───
    history_start = PLAN_START - timedelta(weeks=13)
    history_weekly = []
    # Group all runs into Monday-based weeks
    pre_plan_runs_by_week = defaultdict(list)
    for entry in run_list:
        dt = parse_date_simple(entry["date"])
        if dt and history_start <= dt < PLAN_START:
            # Monday of that week
            week_monday = dt - timedelta(days=dt.weekday())
            key = week_monday.strftime("%Y-%m-%d")
            pre_plan_runs_by_week[key].append(entry)

    # Build history entries for each week in the 13-week window
    wk_cursor = history_start - timedelta(days=history_start.weekday())  # snap to Monday
    while wk_cursor < PLAN_START:
        key = wk_cursor.strftime("%Y-%m-%d")
        wk_runs = pre_plan_runs_by_week.get(key, [])
        actual_km = sum(r["dist"] for r in wk_runs)
        longest = max((r["dist"] for r in wk_runs), default=0)
        wk_end = wk_cursor + timedelta(days=6)
        history_weekly.append({
            "label": wk_cursor.strftime("%b %d"),
            "start": key,
            "end": wk_end.strftime("%Y-%m-%d"),
            "actual_km": round(actual_km, 1),
            "longest": round(longest, 1),
            "num_runs": len(wk_runs),
        })
        wk_cursor += timedelta(weeks=1)

    # ─── Weekly compliance ───
    weekly_data = []
    num_gym_days = len(CONFIG["gym_days"])
    for wk in range(1, CONFIG["plan_weeks"] + 1):
        target = WEEKLY_TARGETS[wk]
        wk_runs = runs_by_week.get(wk, [])
        actual_km = sum(r["dist"] for r in wk_runs)
        longest = max((r["dist"] for r in wk_runs), default=0)
        pct = (actual_km / target["km"] * 100) if target["km"] > 0 else 0

        easy_km = sum(r["dist"] for r in wk_runs if r["zone"] in ("Z1", "Z2", "Unknown"))
        easy_pct = (easy_km / actual_km * 100) if actual_km > 0 else None

        wk_start = PLAN_START + timedelta(weeks=wk - 1)
        wk_end = wk_start + timedelta(days=6)

        status = "upcoming"
        if current_week is not None:
            if wk < current_week:
                status = "done" if pct >= 85 else ("partial" if pct >= 60 else ("low" if pct > 0 else "missed"))
            elif wk == current_week:
                status = "current"

        # Gym compliance
        wk_gym = gym_by_week.get(wk, [])
        gym_target = max(1, num_gym_days) if wk >= CONFIG["plan_weeks"] - 1 else num_gym_days

        weekly_data.append({
            "week": wk,
            "phase": target["phase"],
            "target_km": target["km"],
            "target_long": target["long"],
            "actual_km": round(actual_km, 1),
            "longest": round(longest, 1),
            "pct": round(pct),
            "easy_pct": round(easy_pct) if easy_pct is not None else None,
            "num_runs": len(wk_runs),
            "gym_actual": len(wk_gym),
            "gym_target": gym_target,
            "status": status,
            "start": wk_start.strftime("%Y-%m-%d"),
            "end": wk_end.strftime("%Y-%m-%d"),
        })

    # ─── Health markers ───
    # Only show data from 13 weeks before plan start onwards (matches history window)
    health_cutoff = PLAN_START - timedelta(weeks=13)

    def sorted_records(key):
        recs = records.get(key, [])
        return sorted(recs, key=lambda x: x.get("startDate") or "")

    # Weight
    weights = sorted_records("HKQuantityTypeIdentifierBodyMass")
    weight_series = []
    for w in weights:
        d = parse_date(w["startDate"])
        if d and d >= health_cutoff:
            weight_series.append({"date": d.strftime("%Y-%m-%d"), "value": round(float(w["value"]), 1)})

    # VO2max
    vo2_records = sorted_records("HKQuantityTypeIdentifierVO2Max")
    vo2_series = []
    for r in vo2_records:
        d = parse_date(r["startDate"])
        if d and d >= health_cutoff:
            vo2_series.append({"date": d.strftime("%Y-%m-%d"), "value": round(float(r["value"]), 1)})

    # RHR
    rhr_records = sorted_records("HKQuantityTypeIdentifierRestingHeartRate")
    rhr_series = []
    for r in rhr_records:
        d = parse_date(r["startDate"])
        if d and d >= health_cutoff:
            rhr_series.append({"date": d.strftime("%Y-%m-%d"), "value": round(float(r["value"]))})

    # HRV
    hrv_records = sorted_records("HKQuantityTypeIdentifierHeartRateVariabilitySDNN")
    hrv_series = []
    for r in hrv_records:
        d = parse_date(r["startDate"])
        if d and d >= health_cutoff:
            hrv_series.append({"date": d.strftime("%Y-%m-%d"), "value": round(float(r["value"]), 1)})

    # Walking HR Average
    walking_hr_records = sorted_records("HKQuantityTypeIdentifierWalkingHeartRateAverage")
    walking_hr_series = []
    for r in walking_hr_records:
        d = parse_date(r["startDate"])
        if d and d >= health_cutoff:
            walking_hr_series.append({"date": d.strftime("%Y-%m-%d"), "value": round(float(r["value"]))})

    # HR Recovery (1 min)
    hr_recovery_records = sorted_records("HKQuantityTypeIdentifierHeartRateRecoveryOneMinute")
    hr_recovery_series = []
    for r in hr_recovery_records:
        d = parse_date(r["startDate"])
        if d and d >= health_cutoff:
            hr_recovery_series.append({"date": d.strftime("%Y-%m-%d"), "value": round(float(r["value"]))})

    # Respiratory Rate
    resp_records = sorted_records("HKQuantityTypeIdentifierRespiratoryRate")
    resp_series = []
    for r in resp_records:
        d = parse_date(r["startDate"])
        if d and d >= health_cutoff:
            resp_series.append({"date": d.strftime("%Y-%m-%d"), "value": round(float(r["value"]), 1)})

    # SpO2
    spo2_records = sorted_records("HKQuantityTypeIdentifierOxygenSaturation")
    spo2_series = []
    for r in spo2_records:
        d = parse_date(r["startDate"])
        if d and d >= health_cutoff:
            val = float(r["value"])
            if val <= 1:
                val *= 100  # convert from 0-1 to percentage
            spo2_series.append({"date": d.strftime("%Y-%m-%d"), "value": round(val, 1)})

    # Sleep
    sleep_records = records.get("HKCategoryTypeIdentifierSleepAnalysis", [])
    sleep_by_night = defaultdict(float)
    for r in sleep_records:
        d = parse_date(r["startDate"])
        if d and d >= health_cutoff:
            night_date = d.date() if d.hour >= 18 else (d - timedelta(days=1)).date()
            end = parse_date(r["endDate"])
            if end:
                dur = (end - d).total_seconds() / 3600
                if 0 < dur < 15:
                    sleep_by_night[night_date] += dur

    sleep_series = []
    for night in sorted(sleep_by_night.keys()):
        sleep_series.append({"date": str(night), "value": round(sleep_by_night[night], 2)})

    # ─── Zone distribution ───
    zone_km = defaultdict(float)
    for r in run_list:
        if r["week"]:
            zone_km[r["zone"]] += r["dist"]
    zone_dist = {z: round(zone_km.get(z, 0), 1) for z in ["Z1", "Z2", "Z3", "Z4", "Z5", "Unknown"]}

    # ─── Suggestions ───
    suggestions = build_suggestions(current_week, runs_by_week, weights, rhr_records, hrv_records, zone_km, run_list)

    # ─── Latest values for summary cards ───
    latest = {}
    if weight_series:
        latest["weight"] = weight_series[-1]["value"]
        latest["weight_date"] = weight_series[-1]["date"]
    if vo2_series:
        latest["vo2max"] = vo2_series[-1]["value"]
        latest["vo2max_date"] = vo2_series[-1]["date"]
    if rhr_series:
        recent_rhr = [r["value"] for r in rhr_series[-7:]]
        latest["rhr"] = round(sum(recent_rhr) / len(recent_rhr))
    if hrv_series:
        recent_hrv = [r["value"] for r in hrv_series[-7:]]
        latest["hrv"] = round(sum(recent_hrv) / len(recent_hrv))
    if sleep_series:
        recent_sleep = [s["value"] for s in sleep_series[-7:]]
        latest["sleep"] = round(sum(recent_sleep) / len(recent_sleep), 1)

    return {
        "current_week": current_week,
        "days_to_race": days_to_race,
        "race_date": HALF_MARATHON_DATE.strftime("%Y-%m-%d"),
        "plan_start": PLAN_START.strftime("%Y-%m-%d"),
        "weight_target_kg": CONFIG["weight_target_kg"],
        "latest": latest,
        "runs": run_list,
        "weekly": weekly_data,
        "history_weekly": history_weekly,
        "weight": weight_series,
        "vo2max": vo2_series,
        "rhr": rhr_series,
        "hrv": hrv_series,
        "walking_hr": walking_hr_series,
        "hr_recovery": hr_recovery_series,
        "respiratory_rate": resp_series,
        "spo2": spo2_series,
        "sleep": sleep_series,
        "zone_distribution": zone_dist,
        "gym": gym_list,
        "plan_schedule": PLAN_SCHEDULE,
        "suggestions": suggestions,
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
    }


def build_suggestions(current_week, runs_by_week, weights, rhr_records, hrv_records, zone_km, run_list):
    suggestions = []
    now = datetime.now()

    if current_week is None:
        suggestions.append({
            "type": "info",
            "title": "Plan hasn't started yet",
            "text": f"Your 16-week plan starts {PLAN_START.strftime('%b %d')}. Focus on easy running to build a base.",
        })
        return suggestions

    # Volume compliance
    target = WEEKLY_TARGETS.get(current_week, {})
    wk_runs = runs_by_week.get(current_week, [])
    actual = sum(r["dist"] for r in wk_runs)
    day_of_week = now.weekday()  # 0=Mon

    if target:
        remaining = target["km"] - actual
        days_left = 6 - day_of_week
        if remaining > 0 and days_left > 0:
            per_run = remaining / min(days_left, 3)
            suggestions.append({
                "type": "info",
                "title": f"Week {current_week} progress: {actual:.0f}/{target['km']} km",
                "text": f"{remaining:.0f} km remaining with {days_left} days left. Aim for ~{per_run:.0f} km per run.",
            })
        elif remaining <= 0:
            suggestions.append({
                "type": "success",
                "title": f"Week {current_week} target hit!",
                "text": f"You've done {actual:.0f} km against a {target['km']} km target. Bank the fitness, don't add volume.",
            })

        # Long run check
        longest = max((r["dist"] for r in wk_runs), default=0)
        if target["long"] > 0 and longest < target["long"] * 0.8 and day_of_week >= 3:
            suggestions.append({
                "type": "warning",
                "title": "Long run still needed",
                "text": f"Target long run: {target['long']} km. Longest so far: {longest:.1f} km. Plan it for the weekend.",
            })

    # Intensity check
    total_km = sum(zone_km.values())
    if total_km > 0:
        easy_km = zone_km.get("Z1", 0) + zone_km.get("Z2", 0)
        easy_pct = easy_km / total_km * 100
        if easy_pct < 70:
            suggestions.append({
                "type": "danger",
                "title": f"Too much hard running ({easy_pct:.0f}% easy)",
                "text": f"Target is 80% easy. You're running too fast on easy days. Slow down to {CONFIG['easy_pace']}/km pace. Your ego is writing checks your tendons can't cash.",
            })
        elif easy_pct < 80:
            suggestions.append({
                "type": "warning",
                "title": f"Intensity slightly high ({easy_pct:.0f}% easy)",
                "text": "Getting close to the 80/20 target but still a bit hot. Back off the pace on easy runs.",
            })

    # HRV warning
    if hrv_records:
        recent = [float(r["value"]) for r in hrv_records
                  if parse_date(r["startDate"]) and parse_date(r["startDate"]) >= now - timedelta(days=7)]
        if recent and sum(recent) / len(recent) < 35:
            suggestions.append({
                "type": "danger",
                "title": "HRV critically low",
                "text": "Your HRV has dropped below 35ms. Take an extra rest day, prioritize sleep, and check stress levels.",
            })

    # RHR spike
    if rhr_records:
        recent = [float(r["value"]) for r in rhr_records
                  if parse_date(r["startDate"]) and parse_date(r["startDate"]) >= now - timedelta(days=3)]
        baseline = [float(r["value"]) for r in rhr_records
                    if parse_date(r["startDate"]) and now - timedelta(days=30) <= parse_date(r["startDate"]) <= now - timedelta(days=7)]
        if recent and baseline:
            r_avg = sum(recent) / len(recent)
            b_avg = sum(baseline) / len(baseline)
            if r_avg > b_avg + 5:
                suggestions.append({
                    "type": "danger",
                    "title": f"Resting HR spike: {r_avg:.0f} vs baseline {b_avg:.0f} bpm",
                    "text": "Elevated RHR can signal overtraining, illness, or poor recovery. Consider an easy day.",
                })

    # Weight tracking
    if weights:
        last = parse_date(weights[-1]["startDate"])
        if last and (now - last).days > 14:
            suggestions.append({
                "type": "warning",
                "title": "Weight not tracked recently",
                "text": f"Last recorded {(now - last).days} days ago. Weigh yourself to stay on target for {CONFIG['weight_target_kg']:.0f} kg.",
            })
        elif weights:
            current_w = float(weights[-1]["value"])
            target_w = CONFIG["weight_target_kg"]
            if current_w > target_w + 3:
                weeks_left = max(1, (HALF_MARATHON_DATE - now).days / 7)
                to_lose = current_w - target_w
                rate = to_lose / weeks_left
                suggestions.append({
                    "type": "info",
                    "title": f"Weight: {current_w:.1f} kg (target {target_w:.0f} kg)",
                    "text": f"Need to lose ~{to_lose:.1f} kg over {weeks_left:.0f} weeks ({rate:.2f} kg/week). Keep the 300-400 kcal deficit, don't crash diet.",
                })

    # Next week preview
    if current_week + 1 in WEEKLY_TARGETS:
        nw = WEEKLY_TARGETS[current_week + 1]
        is_deload = current_week + 1 in (4, 8, 12)
        suggestions.append({
            "type": "info",
            "title": f"Next week: {nw['phase']} ({nw['km']} km)",
            "text": f"Long run: {nw['long']} km." + (" This is a DELOAD week — reduce volume, let your body recover!" if is_deload else ""),
        })

    if not suggestions:
        suggestions.append({
            "type": "success",
            "title": "Looking good!",
            "text": "No issues detected. Keep consistent and trust the process.",
        })

    return suggestions


def load_or_parse(export_path):
    """Load cached data or parse the export if cache is stale."""
    os.makedirs(CACHE_DIR, exist_ok=True)

    # Check if cache exists and is newer than the export
    if os.path.exists(CACHE_FILE) and os.path.exists(export_path):
        cache_mtime = os.path.getmtime(CACHE_FILE)
        export_mtime = os.path.getmtime(export_path)
        # Also check if env config changed
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "env")
        env_mtime = os.path.getmtime(env_path) if os.path.exists(env_path) else 0
        if cache_mtime > export_mtime and cache_mtime > env_mtime:
            print("Loading cached data...")
            with open(CACHE_FILE) as f:
                return json.load(f)

    if not os.path.exists(export_path):
        print(f"Export file not found: {export_path}")
        print("Export your Apple Health data and place it in apple_health_export/")
        return None

    workouts, records, summaries = parse_export(export_path)
    data = build_dashboard_data(workouts, records, summaries)

    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)
    print(f"Cached to {CACHE_FILE}")

    return data


# ─── Routes ───

@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/data")
def api_data():
    if not DATA:
        return jsonify({"error": "No data loaded"}), 500
    return jsonify(DATA)


@app.route("/api/calendar.ics")
def api_calendar():
    """Download .ics file for the training plan."""
    from_date = request.args.get("from")
    to_date = request.args.get("to")

    start = parse_date_simple(from_date) if from_date else None
    end = parse_date_simple(to_date) if to_date else None

    # Default: current week through 4 weeks ahead
    if not start:
        now = datetime.now()
        start = now - timedelta(days=now.weekday())  # Monday of current week
    if not end:
        end = start + timedelta(weeks=4)

    ics_content = generate_ics(PLAN, CONFIG, start, end)
    response = app.response_class(ics_content, mimetype="text/calendar")
    response.headers["Content-Disposition"] = "attachment; filename=training_plan.ics"
    return response


@app.route("/api/calendar-full.ics")
def api_calendar_full():
    """Download .ics file for the entire training plan."""
    ics_content = generate_ics(PLAN, CONFIG)
    response = app.response_class(ics_content, mimetype="text/calendar")
    response.headers["Content-Disposition"] = "attachment; filename=training_plan_full.ics"
    return response


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    global DATA
    export_path = app.config.get("EXPORT_PATH", "apple_health_export/export.xml")
    # Force re-parse by removing cache
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
    DATA = load_or_parse(export_path) or {}
    return jsonify({"status": "ok", "generated_at": DATA.get("generated_at")})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Training dashboard")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--export", default="apple_health_export/export.xml")
    args = parser.parse_args()

    app.config["EXPORT_PATH"] = args.export
    DATA = load_or_parse(args.export) or {}

    if not DATA:
        print("\nNo data to display. Export from Apple Health first.")
        print("Starting server anyway — refresh after adding data.\n")
        DATA = {}

    print(f"\n  Dashboard: http://localhost:{args.port}\n")
    app.run(host="0.0.0.0", port=args.port, debug=False)
