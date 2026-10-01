"""
Weekly check-in script for half-marathon & marathon training progress.
Run after each new Apple Health export to track progress against targets.

Usage: python weekly_checkin.py [--weeks N] [--export PATH]
  --weeks N     Number of recent weeks to show (default: 4)
  --export PATH Path to Apple Health export.xml (default: apple_health_export/export.xml)
"""
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta
import argparse
import json
import os

from config import CONFIG, WEEKLY_TARGETS, PLAN_START, HALF_MARATHON_DATE

# HR zones with descriptive names for CLI output
ZONE_BOUNDARIES = {}
for name, (lo, hi) in CONFIG["zone_boundaries"].items():
    labels = {"Z1": "Z1 (Recovery)", "Z2": "Z2 (Easy)", "Z3": "Z3 (Tempo)", "Z4": "Z4 (Threshold)", "Z5": "Z5 (VO2max)"}
    ZONE_BOUNDARIES[labels.get(name, name)] = (lo, hi)


def parse_args():
    parser = argparse.ArgumentParser(description="Weekly training check-in")
    parser.add_argument("--weeks", type=int, default=4, help="Recent weeks to display")
    parser.add_argument("--export", default="apple_health_export/export.xml", help="Path to export.xml")
    return parser.parse_args()


def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d")
        except:
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


def parse_export(path):
    print("Parsing Apple Health export...")
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

    print(f"Done ({count:,} elements).\n")
    return workouts, records_by_type, activity_summaries


def _pace_to_sec(pace_str):
    """'5:40' or '6:30-7:00' -> seconds per km (midpoint for a range)."""
    parts = [p.strip() for p in pace_str.split("-")]
    secs = [int(p.split(":")[0]) * 60 + int(p.split(":")[1]) for p in parts]
    return sum(secs) / len(secs)


def get_training_week(dt):
    """Return which training plan week number a date falls in (1-indexed), or None."""
    if dt < PLAN_START:
        return None
    delta = (dt - PLAN_START).days
    week = delta // 7 + 1
    return week if week <= max(WEEKLY_TARGETS) else None


def main():
    args = parse_args()
    workouts, records, activity_summaries = parse_export(args.export)

    now = datetime.now()
    current_week = get_training_week(now)

    # ─── Running workouts grouped by training week ───
    runs = [w for w in workouts if "Running" in w["type"]]
    runs.sort(key=lambda x: x["startDate"] or "")

    runs_by_week = defaultdict(list)
    for r in runs:
        dt = parse_date(r["startDate"])
        if not dt:
            continue
        tw = get_training_week(dt)
        if tw:
            dist = get_stat(r["stats"], "HKQuantityTypeIdentifierDistanceWalkingRunning") or 0
            dur = float(r["duration"] or 0)
            avg_hr = get_stat(r["stats"], "HKQuantityTypeIdentifierHeartRate", "average")
            max_hr = get_stat(r["stats"], "HKQuantityTypeIdentifierHeartRate", "maximum")
            avg_spd = get_stat(r["stats"], "HKQuantityTypeIdentifierRunningSpeed", "average")
            cal = get_stat(r["stats"], "HKQuantityTypeIdentifierActiveEnergyBurned") or 0
            indoor = r["metadata"].get("HKIndoorWorkout") == "1"

            if dist > 0 and dur > 0:
                pace_sec = (dur * 60) / dist
                pace_str = f"{int(pace_sec//60)}:{int(pace_sec%60):02d}/km"
            else:
                pace_str = "N/A"

            runs_by_week[tw].append({
                "date": dt,
                "dist": dist,
                "dur": dur,
                "pace": pace_str,
                "avg_hr": avg_hr,
                "max_hr": max_hr,
                "cal": cal,
                "zone": classify_zone(avg_hr),
                "indoor": indoor,
            })

    # ─── Header ───
    print("=" * 70)
    print("HALF MARATHON TRAINING CHECK-IN")
    week_label = current_week or ("Pre-plan" if now < PLAN_START else "Past plan end")
    print(f"Date: {now.strftime('%Y-%m-%d')} | Training week: {week_label}")
    days_to_race = (HALF_MARATHON_DATE - now).days
    goal_sec = _pace_to_sec(CONFIG["race_pace"]) * 21.0975
    goal = f"{int(goal_sec // 3600)}:{int(goal_sec % 3600 // 60):02d}:{int(goal_sec % 60):02d}"
    print(f"Days to race: {days_to_race} | Target: {goal} ({CONFIG['race_pace']}/km)")
    print("=" * 70)

    # ─── Key health markers (latest) ───
    print("\n--- HEALTH MARKERS ---")

    # Weight
    weights = sorted(records.get("HKQuantityTypeIdentifierBodyMass", []), key=lambda x: x["startDate"] or "")
    if weights:
        w = weights[-1]
        print(f"Weight: {float(w['value']):.1f} kg (as of {w['startDate'][:10]}) "
              f"| Target: {CONFIG['weight_target_kg']:.0f} kg by race day")
    else:
        print("Weight: NOT TRACKED — please weigh yourself weekly!")

    # VO2max
    vo2 = sorted(records.get("HKQuantityTypeIdentifierVO2Max", []), key=lambda x: x["startDate"] or "")
    if vo2:
        latest_vo2 = float(vo2[-1]["value"])
        print(f"VO2max: {latest_vo2:.1f} mL/min·kg (as of {vo2[-1]['startDate'][:10]})")
        # 4-week trend
        four_wk_ago = now - timedelta(days=28)
        recent_vo2 = [r for r in vo2 if parse_date(r["startDate"]) and parse_date(r["startDate"]) >= four_wk_ago]
        if len(recent_vo2) >= 2:
            delta = float(recent_vo2[-1]["value"]) - float(recent_vo2[0]["value"])
            print(f"  4-week change: {delta:+.1f}")

    # Resting HR
    rhr = sorted(records.get("HKQuantityTypeIdentifierRestingHeartRate", []), key=lambda x: x["startDate"] or "")
    if rhr:
        recent_rhr = [r for r in rhr if parse_date(r["startDate"]) and parse_date(r["startDate"]) >= now - timedelta(days=7)]
        if recent_rhr:
            avg_rhr = sum(float(r["value"]) for r in recent_rhr) / len(recent_rhr)
            print(f"Resting HR (7-day avg): {avg_rhr:.0f} bpm")

    # HRV
    hrv = sorted(records.get("HKQuantityTypeIdentifierHeartRateVariabilitySDNN", []), key=lambda x: x["startDate"] or "")
    if hrv:
        recent_hrv = [r for r in hrv if parse_date(r["startDate"]) and parse_date(r["startDate"]) >= now - timedelta(days=7)]
        if recent_hrv:
            avg_hrv = sum(float(r["value"]) for r in recent_hrv) / len(recent_hrv)
            print(f"HRV (7-day avg): {avg_hrv:.0f} ms", end="")
            if avg_hrv < 35:
                print(" ⚠️  LOW — consider extra rest")
            elif avg_hrv < 45:
                print(" — on the low side, monitor closely")
            else:
                print(" — healthy range")

    # ─── Cross-training: cycling + gym/strength (last 60 days) ───
    cutoff_60 = now - timedelta(days=60)
    cross = []
    for w in workouts:
        wtype = w["type"] or ""
        if "Cycling" in wtype:
            kind = "Bike"
        elif "Strength" in wtype:  # Traditional/Functional StrengthTraining
            kind = "Gym"
        elif any(k in wtype for k in ("CoreTraining", "HighIntensityIntervalTraining", "Elliptical", "Rowing")):
            kind = "Gym"
        else:
            continue
        dt = parse_date(w["startDate"])
        if not dt or dt < cutoff_60:
            continue
        cross.append({
            "date": dt,
            "kind": kind,
            "dur": float(w["duration"] or 0),
            "dist": get_stat(w["stats"], "HKQuantityTypeIdentifierDistanceCycling") or 0,
            "cal": get_stat(w["stats"], "HKQuantityTypeIdentifierActiveEnergyBurned") or 0,
            "avg_hr": get_stat(w["stats"], "HKQuantityTypeIdentifierHeartRate", "average"),
        })

    if cross:
        cross.sort(key=lambda x: x["date"])
        bikes = [c for c in cross if c["kind"] == "Bike"]
        gyms = [c for c in cross if c["kind"] == "Gym"]
        print("\n--- CROSS-TRAINING (last 60 days) ---")
        print(f"  Bike: {len(bikes)} rides, {sum(c['dist'] for c in bikes):.0f} km, {sum(c['dur'] for c in bikes)/60:.1f} h")
        print(f"  Gym:  {len(gyms)} sessions, {sum(c['dur'] for c in gyms)/60:.1f} h")
        print(f"\n  {'Date':12s} {'Type':>5s} {'Time':>8s} {'Dist':>7s} {'AvgHR':>6s} {'kcal':>6s}")
        print("  " + "-" * 50)
        for c in cross:
            dur_str = f"{int(c['dur'])}:{int((c['dur']%1)*60):02d}"
            dist_str = f"{c['dist']:.1f}k" if c["dist"] > 0 else "-"
            hr_str = f"{c['avg_hr']:.0f}" if c["avg_hr"] else "-"
            print(f"  {c['date'].strftime('%Y-%m-%d'):12s} {c['kind']:>5s} {dur_str:>8s} {dist_str:>7s} {hr_str:>6s} {c['cal']:>6.0f}")

    # ─── Weekly training summary ───
    if current_week is None:
        if now < PLAN_START:
            print(f"\n--- Plan starts {PLAN_START.strftime('%Y-%m-%d')} ({(PLAN_START - now).days} days away) ---")
            print("No training weeks to report yet. Pre-plan runs shown below.\n")
        else:
            # Past the end of the generated plan — check RACE_DATE/PLAN_START in `env`
            print(f"\n--- Plan ended after week {max(WEEKLY_TARGETS)} ({PLAN_START.strftime('%Y-%m-%d')} + "
                  f"{max(WEEKLY_TARGETS)} weeks) ---")
            print("Past the plan window. Recent runs shown below.\n")

        # Show any recent runs anyway
        recent_cutoff = now - timedelta(days=60)
        pre_plan_runs = [r for r in runs if parse_date(r["startDate"]) and parse_date(r["startDate"]) >= recent_cutoff]
        if pre_plan_runs:
            print(f"{'Date':12s} {'Dist':>7s} {'Time':>8s} {'Pace':>9s} {'AvgHR':>6s} {'Zone':>18s}")
            print("-" * 65)
            for r in pre_plan_runs:
                dt = parse_date(r["startDate"])
                dist = get_stat(r["stats"], "HKQuantityTypeIdentifierDistanceWalkingRunning") or 0
                dur = float(r["duration"] or 0)
                avg_hr = get_stat(r["stats"], "HKQuantityTypeIdentifierHeartRate", "average")
                if dist > 0 and dur > 0:
                    pace_sec = (dur * 60) / dist
                    pace_str = f"{int(pace_sec//60)}:{int(pace_sec%60):02d}/km"
                else:
                    pace_str = "N/A"
                hr_str = f"{avg_hr:.0f}" if avg_hr else "-"
                zone = classify_zone(avg_hr)
                dur_str = f"{int(dur)}:{int((dur%1)*60):02d}"
                print(f"{dt.strftime('%Y-%m-%d'):12s} {dist:6.2f}k {dur_str:>8s} {pace_str:>9s} {hr_str:>6s} {zone:>18s}")

        # Alerts for pre-plan
        print(f"\n--- ALERTS ---")
        if weights:
            last_weight_date = parse_date(weights[-1]["startDate"])
            if last_weight_date and (now - last_weight_date).days > 14:
                print(f"  ⚠️  Weight not tracked in {(now - last_weight_date).days} days — weigh yourself!")
        print(f"\n  Tip: This week, focus on easy running ({CONFIG['easy_pace']}/km pace) to be fresh for Week 1.")
        print("\n" + "=" * 70)
        print("END OF CHECK-IN")
        print("=" * 70)
        return

    start_week = max(1, current_week - args.weeks + 1)
    end_week = current_week

    print(f"\n--- WEEKLY PROGRESS (weeks {start_week}-{end_week}) ---")
    print(f"{'Week':>5s} {'Phase':>22s} {'Runs':>5s} {'Dist':>8s} {'Target':>8s} {'%':>5s} {'Long':>6s} {'Tgt':>5s} {'EasyPct':>8s} {'Status':>10s}")
    print("-" * 95)

    total_plan_km = 0
    total_actual_km = 0

    for wk in range(start_week, end_week + 1):
        target = WEEKLY_TARGETS.get(wk, {})
        target_km = target.get("km", 0)
        target_long = target.get("long", 0)
        phase = target.get("phase", "?")

        week_runs = runs_by_week.get(wk, [])
        actual_km = sum(r["dist"] for r in week_runs)
        longest = max((r["dist"] for r in week_runs), default=0)

        total_plan_km += target_km
        total_actual_km += actual_km

        pct = (actual_km / target_km * 100) if target_km > 0 else 0

        # Calculate % of easy running (Z1 + Z2)
        easy_runs = [r for r in week_runs if r["zone"] in ("Z1 (Recovery)", "Z2 (Easy)", "Unknown")]
        easy_km = sum(r["dist"] for r in easy_runs)
        easy_pct = (easy_km / actual_km * 100) if actual_km > 0 else 0

        # Status
        is_current = (wk == current_week)
        is_future = current_week is not None and wk > current_week
        if is_future:
            status = "upcoming"
        elif is_current:
            status = "in progress"
        elif pct >= 85:
            status = "DONE"
        elif pct >= 60:
            status = "partial"
        elif pct > 0:
            status = "LOW"
        else:
            status = "MISSED"

        easy_str = f"{easy_pct:.0f}%" if week_runs else "-"

        print(f"{wk:5d} {phase:>22s} {len(week_runs):5d} {actual_km:7.1f}k {target_km:7.1f}k {pct:4.0f}% {longest:5.1f}k {target_long:4.0f}k {easy_str:>8s} {status:>10s}")

    if total_plan_km > 0:
        overall_pct = total_actual_km / total_plan_km * 100
        print(f"\nOverall plan adherence: {total_actual_km:.0f}/{total_plan_km:.0f} km ({overall_pct:.0f}%)")

    # ─── Detailed run log for current week ───
    if current_week and current_week in runs_by_week:
        print(f"\n--- THIS WEEK'S RUNS (Week {current_week}) ---")
        print(f"{'Date':12s} {'Dist':>7s} {'Time':>8s} {'Pace':>9s} {'AvgHR':>6s} {'MaxHR':>6s} {'Zone':>18s} {'Indoor':>7s}")
        print("-" * 80)
        for r in sorted(runs_by_week[current_week], key=lambda x: x["date"]):
            dur_str = f"{int(r['dur'])}:{int((r['dur']%1)*60):02d}"
            hr_str = f"{r['avg_hr']:.0f}" if r['avg_hr'] else "-"
            max_str = f"{r['max_hr']:.0f}" if r['max_hr'] else "-"
            indoor_str = "Yes" if r["indoor"] else "No"
            print(f"{r['date'].strftime('%Y-%m-%d'):12s} {r['dist']:6.2f}k {dur_str:>8s} {r['pace']:>9s} {hr_str:>6s} {max_str:>6s} {r['zone']:>18s} {indoor_str:>7s}")

    # ─── Intensity distribution (all training weeks) ───
    all_training_runs = []
    for wk in range(1, (current_week or 0) + 1):
        all_training_runs.extend(runs_by_week.get(wk, []))

    if all_training_runs:
        print(f"\n--- INTENSITY DISTRIBUTION (since plan start) ---")
        zone_km = defaultdict(float)
        for r in all_training_runs:
            zone_km[r["zone"]] += r["dist"]
        total_km = sum(zone_km.values())
        target_easy = 80  # target 80% easy

        for zone in ["Z1 (Recovery)", "Z2 (Easy)", "Z3 (Tempo)", "Z4 (Threshold)", "Z5 (VO2max)", "Unknown"]:
            km = zone_km.get(zone, 0)
            pct = (km / total_km * 100) if total_km > 0 else 0
            bar = "#" * int(pct / 2)
            print(f"  {zone:>18s}: {km:6.1f} km ({pct:4.1f}%) {bar}")

        easy_total = zone_km.get("Z1 (Recovery)", 0) + zone_km.get("Z2 (Easy)", 0)
        easy_pct = (easy_total / total_km * 100) if total_km > 0 else 0
        print(f"\n  Easy running: {easy_pct:.0f}% (target: 80%)", end="")
        if easy_pct < 70:
            print(" ⚠️  TOO MUCH HARD RUNNING — slow down on easy days!")
        elif easy_pct < 80:
            print(" — getting there, a bit more easy running needed")
        else:
            print(" — great distribution!")

    # ─── Pace trend for comparable runs ───
    outdoor_runs = [r for wk_runs in runs_by_week.values() for r in wk_runs if not r["indoor"] and r["dist"] > 3]
    if len(outdoor_runs) >= 3:
        print(f"\n--- PACE TREND (outdoor runs > 3km) ---")
        outdoor_runs.sort(key=lambda x: x["date"])
        for r in outdoor_runs:
            print(f"  {r['date'].strftime('%Y-%m-%d')}  {r['dist']:5.1f}km  {r['pace']:>9s}  HR:{r['avg_hr']:.0f}" if r['avg_hr'] else f"  {r['date'].strftime('%Y-%m-%d')}  {r['dist']:5.1f}km  {r['pace']:>9s}")

    # ─── Warnings ───
    print(f"\n--- ALERTS ---")
    alerts = []

    # Check if weight is being tracked
    if weights:
        last_weight_date = parse_date(weights[-1]["startDate"])
        if last_weight_date and (now - last_weight_date).days > 14:
            alerts.append(f"Weight not tracked in {(now - last_weight_date).days} days — weigh yourself!")

    # Check HRV trend
    if hrv:
        recent_hrv_vals = [float(r["value"]) for r in hrv if parse_date(r["startDate"]) and parse_date(r["startDate"]) >= now - timedelta(days=7)]
        if recent_hrv_vals and sum(recent_hrv_vals)/len(recent_hrv_vals) < 35:
            alerts.append("HRV is critically low — take extra rest days, check sleep")

    # Check RHR spike
    if rhr:
        recent_rhr_vals = [float(r["value"]) for r in rhr if parse_date(r["startDate"]) and parse_date(r["startDate"]) >= now - timedelta(days=3)]
        baseline_rhr_vals = [float(r["value"]) for r in rhr if parse_date(r["startDate"]) and now - timedelta(days=30) <= parse_date(r["startDate"]) <= now - timedelta(days=7)]
        if recent_rhr_vals and baseline_rhr_vals:
            recent_avg = sum(recent_rhr_vals) / len(recent_rhr_vals)
            baseline_avg = sum(baseline_rhr_vals) / len(baseline_rhr_vals)
            if recent_avg > baseline_avg + 5:
                alerts.append(f"RHR spike: {recent_avg:.0f} bpm vs baseline {baseline_avg:.0f} bpm — possible overtraining or illness")

    # Check volume compliance
    if current_week and current_week in WEEKLY_TARGETS:
        target = WEEKLY_TARGETS[current_week]
        actual = sum(r["dist"] for r in runs_by_week.get(current_week, []))
        # Only alert if we're past Thursday and behind
        if now.weekday() >= 3 and actual < target["km"] * 0.4:
            alerts.append(f"Week {current_week}: only {actual:.0f}km of {target['km']}km target done and it's {now.strftime('%A')}")

    if not alerts:
        print("  No alerts — keep going!")
    else:
        for a in alerts:
            print(f"  ⚠️  {a}")

    # ─── Next week preview ───
    if current_week and current_week + 1 in WEEKLY_TARGETS:
        nw = WEEKLY_TARGETS[current_week + 1]
        print(f"\n--- NEXT WEEK (Week {current_week + 1}: {nw['phase']}) ---")
        print(f"  Target: {nw['km']} km | Long run: {nw['long']} km")
        if current_week + 1 in (4, 8, 12):
            print("  This is a DELOAD week — reduce volume and intensity, let your body recover!")

    print("\n" + "=" * 70)
    print("END OF CHECK-IN")
    print("=" * 70)


if __name__ == "__main__":
    main()
