"""
Apple Health Export analyzer for half-marathon training assessment.
Extracts: workouts (running focus), resting HR, VO2max, weight, HRV, sleep, activity summaries.
"""
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta
import json

from config import CONFIG

MAX_HR = CONFIG["max_hr"]

EXPORT_PATH = "apple_health_export/export.xml"

print("Parsing Apple Health export (this may take a minute)...")

workouts = []
records_by_type = defaultdict(list)

RECORD_TYPES_OF_INTEREST = {
    "HKQuantityTypeIdentifierBodyMass",
    "HKQuantityTypeIdentifierHeight",
    "HKQuantityTypeIdentifierRestingHeartRate",
    "HKQuantityTypeIdentifierVO2Max",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
    "HKQuantityTypeIdentifierBodyFatPercentage",
    "HKQuantityTypeIdentifierBodyMassIndex",
    "HKQuantityTypeIdentifierStepCount",
    "HKCategoryTypeIdentifierSleepAnalysis",
}

activity_summaries = []
profile = {}

context = ET.iterparse(EXPORT_PATH, events=("end",))
count = 0
for event, elem in context:
    tag = elem.tag

    if tag == "Me":
        profile = dict(elem.attrib)
        elem.clear()
    elif tag == "Record":
        rtype = elem.get("type")
        if rtype in RECORD_TYPES_OF_INTEREST:
            records_by_type[rtype].append({
                "value": elem.get("value"),
                "unit": elem.get("unit"),
                "startDate": elem.get("startDate"),
                "endDate": elem.get("endDate"),
                "sourceName": elem.get("sourceName"),
            })
        elem.clear()

    elif tag == "Workout":
        w = {
            "type": elem.get("workoutActivityType"),
            "duration": elem.get("duration"),
            "durationUnit": elem.get("durationUnit"),
            "sourceName": elem.get("sourceName"),
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
            "appleStandHours": elem.get("appleStandHours"),
        })
        elem.clear()
    else:
        if tag in ("Correlation", "ClinicalRecord", "Audiogram"):
            elem.clear()

    count += 1
    if count % 500000 == 0:
        print(f"  ...processed {count:,} elements")

print(f"Done. Processed {count:,} elements total.\n")


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


def get_stat(w, stat_type, field="sum"):
    """Get a workout statistic value."""
    s = w["stats"].get(stat_type, {})
    val = s.get(field)
    return float(val) if val else None


# ═══════════════════════════════════════════════
# 1. PROFILE
# ═══════════════════════════════════════════════
print("=" * 70)
print("PROFILE")
print("=" * 70)
dob = profile.get("HKCharacteristicTypeIdentifierDateOfBirth")
sex = (profile.get("HKCharacteristicTypeIdentifierBiologicalSex") or "").replace("HKBiologicalSex", "")
if dob:
    born = datetime.strptime(dob, "%Y-%m-%d")
    today = datetime.now()
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    print(f"DOB: {dob} | Age: {age} | Sex: {sex or 'unknown'}")
else:
    print("Profile: not in export")

weights = records_by_type.get("HKQuantityTypeIdentifierBodyMass", [])
if weights:
    recent_w = sorted(weights, key=lambda x: x["startDate"] or "")
    latest_weight = recent_w[-1]
    print(f"Latest weight: {float(latest_weight['value']):.1f} {latest_weight['unit']} (recorded {latest_weight['startDate'][:10]})")
    weight_by_month = defaultdict(list)
    for w in recent_w:
        d = parse_date(w["startDate"])
        if d and d.year >= 2025:
            weight_by_month[d.strftime("%Y-%m")].append(float(w["value"]))
    if weight_by_month:
        print("\nWeight trend (monthly avg):")
        for month in sorted(weight_by_month):
            vals = weight_by_month[month]
            print(f"  {month}: {sum(vals)/len(vals):.1f} kg (n={len(vals)})")

heights = records_by_type.get("HKQuantityTypeIdentifierHeight", [])
if heights:
    latest_h = sorted(heights, key=lambda x: x["startDate"] or "")[-1]
    h_cm = float(latest_h['value'])
    print(f"Height: {h_cm:.0f} {latest_h['unit']}")
    if weights:
        w_kg = float(latest_weight['value'])
        bmi = w_kg / ((h_cm/100) ** 2)
        print(f"BMI: {bmi:.1f}")

# ═══════════════════════════════════════════════
# 2. ALL WORKOUTS OVERVIEW
# ═══════════════════════════════════════════════
print("\n" + "=" * 70)
print("WORKOUT OVERVIEW (ALL TYPES)")
print("=" * 70)

workout_type_counts = defaultdict(int)
workout_type_time = defaultdict(float)
for w in workouts:
    wtype = w["type"].replace("HKWorkoutActivityType", "")
    workout_type_counts[wtype] += 1
    if w["duration"]:
        workout_type_time[wtype] += float(w["duration"])

for wtype in sorted(workout_type_counts, key=lambda x: -workout_type_counts[x]):
    hrs = workout_type_time[wtype] / 60
    print(f"  {wtype:30s}  {workout_type_counts[wtype]:4d} sessions  ({hrs:6.1f} hrs total)")

# ═══════════════════════════════════════════════
# 3. RUNNING DEEP DIVE
# ═══════════════════════════════════════════════
print("\n" + "=" * 70)
print("RUNNING DEEP DIVE")
print("=" * 70)

runs = [w for w in workouts if "Running" in w["type"]]
runs.sort(key=lambda x: x["startDate"] or "")

if not runs:
    print("No running workouts found!")
else:
    print(f"Total running workouts: {len(runs)}")
    first_run = parse_date(runs[0]["startDate"])
    last_run = parse_date(runs[-1]["startDate"])
    if first_run and last_run:
        print(f"Period: {first_run.strftime('%Y-%m-%d')} to {last_run.strftime('%Y-%m-%d')}")

    # Header
    print(f"\n{'Date':12s} {'Dist(km)':>9s} {'Duration':>10s} {'Pace':>9s} {'AvgSpd':>8s} {'AvgHR':>6s} {'MaxHR':>6s} {'Cal':>6s} {'Stride':>7s} {'GCT':>5s} {'VO':>5s} {'Power':>6s} {'Indoor':>7s}")
    print("-" * 120)

    total_dist = 0
    total_time = 0
    weekly_runs = defaultdict(list)

    for r in runs:
        dt = parse_date(r["startDate"])
        dur_min = float(r["duration"]) if r["duration"] else 0

        # Distance from WorkoutStatistics
        dist_km = get_stat(r, "HKQuantityTypeIdentifierDistanceWalkingRunning") or 0
        # Some stats store in km already based on unit
        dist_stat = r["stats"].get("HKQuantityTypeIdentifierDistanceWalkingRunning", {})
        dist_unit = dist_stat.get("unit", "km")
        if dist_unit == "m" and dist_km > 0:
            dist_km /= 1000

        total_dist += dist_km
        total_time += dur_min

        # Pace
        if dist_km > 0 and dur_min > 0:
            pace_sec = (dur_min * 60) / dist_km
            pace_str = f"{int(pace_sec//60)}:{int(pace_sec%60):02d}/km"
        else:
            pace_str = "N/A"

        # Avg speed
        avg_spd = get_stat(r, "HKQuantityTypeIdentifierRunningSpeed", "average")
        avg_spd_str = f"{avg_spd:.1f}km/h" if avg_spd else ""

        # HR
        avg_hr = get_stat(r, "HKQuantityTypeIdentifierHeartRate", "average")
        max_hr = get_stat(r, "HKQuantityTypeIdentifierHeartRate", "maximum")
        avg_hr_str = f"{avg_hr:.0f}" if avg_hr else ""
        max_hr_str = f"{max_hr:.0f}" if max_hr else ""

        # Calories
        cal = get_stat(r, "HKQuantityTypeIdentifierActiveEnergyBurned") or 0

        # Running metrics
        stride = get_stat(r, "HKQuantityTypeIdentifierRunningStrideLength", "average")
        stride_str = f"{stride:.2f}m" if stride else ""

        gct = get_stat(r, "HKQuantityTypeIdentifierRunningGroundContactTime", "average")
        gct_str = f"{gct:.0f}" if gct else ""

        vo = get_stat(r, "HKQuantityTypeIdentifierRunningVerticalOscillation", "average")
        vo_str = f"{vo:.1f}" if vo else ""

        power = get_stat(r, "HKQuantityTypeIdentifierRunningPower", "average")
        power_str = f"{power:.0f}W" if power else ""

        indoor = "Yes" if r["metadata"].get("HKIndoorWorkout") == "1" else "No"

        date_str = dt.strftime("%Y-%m-%d") if dt else "?"
        dur_str = f"{int(dur_min)}:{int((dur_min%1)*60):02d}"

        print(f"{date_str:12s} {dist_km:9.2f} {dur_str:>10s} {pace_str:>9s} {avg_spd_str:>8s} {avg_hr_str:>6s} {max_hr_str:>6s} {cal:6.0f} {stride_str:>7s} {gct_str:>5s} {vo_str:>5s} {power_str:>6s} {indoor:>7s}")

        if dt:
            week_start = dt - timedelta(days=dt.weekday())
            week_key = week_start.strftime("%Y-%m-%d")
            weekly_runs[week_key].append({"dist": dist_km, "dur": dur_min, "date": dt, "avg_hr": avg_hr, "indoor": indoor})

    print("-" * 120)
    print(f"{'TOTALS':12s} {total_dist:9.2f} {int(total_time)}min")

    # Weekly summary
    print(f"\n{'Week of':15s} {'Runs':>5s} {'Dist(km)':>9s} {'Time(min)':>10s} {'Avg Pace':>9s}")
    print("-" * 55)
    for wk in sorted(weekly_runs):
        wr = weekly_runs[wk]
        wdist = sum(x["dist"] for x in wr)
        wtime = sum(x["dur"] for x in wr)
        if wdist > 0:
            wpace_sec = (wtime * 60) / wdist
            wpace = f"{int(wpace_sec//60)}:{int(wpace_sec%60):02d}/km"
        else:
            wpace = "N/A"
        print(f"{wk:15s} {len(wr):5d} {wdist:9.2f} {wtime:10.1f} {wpace:>9s}")

    # Longest run
    longest = max(runs, key=lambda r: get_stat(r, "HKQuantityTypeIdentifierDistanceWalkingRunning") or 0)
    longest_km = get_stat(longest, "HKQuantityTypeIdentifierDistanceWalkingRunning") or 0
    print(f"\nLongest run: {longest_km:.2f} km on {longest['startDate'][:10]}")

    # Recent trend (last 4 weeks)
    four_weeks_ago = datetime.now() - timedelta(days=28)
    recent_runs = [r for r in runs if parse_date(r["startDate"]) and parse_date(r["startDate"]) >= four_weeks_ago]
    if recent_runs:
        recent_dist = sum((get_stat(r, "HKQuantityTypeIdentifierDistanceWalkingRunning") or 0) for r in recent_runs)
        print(f"Last 4 weeks: {len(recent_runs)} runs, {recent_dist:.1f} km total, avg {recent_dist/4:.1f} km/week")

    # Pace progression for recent outdoor runs
    outdoor_runs = [r for r in runs if r["metadata"].get("HKIndoorWorkout") != "1" and (get_stat(r, "HKQuantityTypeIdentifierDistanceWalkingRunning") or 0) > 2]
    if outdoor_runs:
        print(f"\nPACE PROGRESSION (outdoor runs > 2km):")
        print(f"{'Date':12s} {'Dist':>7s} {'Pace':>9s} {'AvgHR':>6s} {'Notes':>20s}")
        print("-" * 60)
        for r in outdoor_runs:
            dt = parse_date(r["startDate"])
            dist = get_stat(r, "HKQuantityTypeIdentifierDistanceWalkingRunning") or 0
            dur = float(r["duration"] or 0)
            avg_hr = get_stat(r, "HKQuantityTypeIdentifierHeartRate", "average")
            if dist > 0 and dur > 0:
                pace_sec = (dur * 60) / dist
                pace_str = f"{int(pace_sec//60)}:{int(pace_sec%60):02d}/km"
            else:
                pace_str = "N/A"
            # Categorize effort
            if avg_hr:
                max_hr_est = MAX_HR
                hr_pct = (avg_hr / max_hr_est) * 100
                if hr_pct < 70:
                    zone = "Easy"
                elif hr_pct < 80:
                    zone = "Aerobic"
                elif hr_pct < 90:
                    zone = "Tempo"
                else:
                    zone = "Hard"
            else:
                zone = ""
            print(f"{dt.strftime('%Y-%m-%d'):12s} {dist:7.2f} {pace_str:>9s} {avg_hr or 0:6.0f} {zone:>20s}")

# ═══════════════════════════════════════════════
# 4. CARDIO FITNESS
# ═══════════════════════════════════════════════
print("\n" + "=" * 70)
print("CARDIO FITNESS MARKERS")
print("=" * 70)

vo2_records = records_by_type.get("HKQuantityTypeIdentifierVO2Max", [])
if vo2_records:
    vo2_sorted = sorted(vo2_records, key=lambda x: x["startDate"] or "")
    print(f"\nVO2max readings (showing last 20 of {len(vo2_sorted)} total):")
    for r in vo2_sorted[-20:]:
        print(f"  {r['startDate'][:10]}  {float(r['value']):.1f} {r['unit']}")
    if len(vo2_sorted) >= 2:
        first_val = float(vo2_sorted[0]["value"])
        last_val = float(vo2_sorted[-1]["value"])
        print(f"\n  All-time trend: {first_val:.1f} -> {last_val:.1f} ({last_val - first_val:+.1f})")
    # Last 6 months trend
    six_mo_ago = datetime.now() - timedelta(days=180)
    recent_vo2 = [r for r in vo2_sorted if parse_date(r["startDate"]) and parse_date(r["startDate"]) >= six_mo_ago]
    if len(recent_vo2) >= 2:
        print(f"  6-month trend: {float(recent_vo2[0]['value']):.1f} -> {float(recent_vo2[-1]['value']):.1f} ({float(recent_vo2[-1]['value']) - float(recent_vo2[0]['value']):+.1f})")

rhr_records = records_by_type.get("HKQuantityTypeIdentifierRestingHeartRate", [])
if rhr_records:
    rhr_sorted = sorted(rhr_records, key=lambda x: x["startDate"] or "")
    print(f"\nResting Heart Rate (last 30 readings):")
    for r in rhr_sorted[-30:]:
        print(f"  {r['startDate'][:10]}  {float(r['value']):.0f} bpm")
    rhr_by_month = defaultdict(list)
    for r in rhr_sorted:
        d = parse_date(r["startDate"])
        if d and d.year >= 2025:
            rhr_by_month[d.strftime("%Y-%m")].append(float(r["value"]))
    if rhr_by_month:
        print("\n  Monthly avg RHR:")
        for m in sorted(rhr_by_month):
            vals = rhr_by_month[m]
            print(f"    {m}: {sum(vals)/len(vals):.0f} bpm (range: {min(vals):.0f}-{max(vals):.0f})")

hrv_records = records_by_type.get("HKQuantityTypeIdentifierHeartRateVariabilitySDNN", [])
if hrv_records:
    hrv_sorted = sorted(hrv_records, key=lambda x: x["startDate"] or "")
    hrv_by_month = defaultdict(list)
    for r in hrv_sorted:
        d = parse_date(r["startDate"])
        if d and d.year >= 2025:
            hrv_by_month[d.strftime("%Y-%m")].append(float(r["value"]))
    if hrv_by_month:
        print(f"\n  Monthly avg HRV (SDNN):")
        for m in sorted(hrv_by_month):
            vals = hrv_by_month[m]
            print(f"    {m}: {sum(vals)/len(vals):.0f} ms (range: {min(vals):.0f}-{max(vals):.0f})")

# ═══════════════════════════════════════════════
# 5. SLEEP
# ═══════════════════════════════════════════════
print("\n" + "=" * 70)
print("SLEEP ANALYSIS")
print("=" * 70)

sleep_records = records_by_type.get("HKCategoryTypeIdentifierSleepAnalysis", [])
if sleep_records:
    sleep_by_night = defaultdict(list)
    for r in sleep_records:
        d = parse_date(r["startDate"])
        if d:
            night_date = d.date() if d.hour >= 18 else (d - timedelta(days=1)).date()
            end = parse_date(r["endDate"])
            if end:
                duration_hrs = (end - d).total_seconds() / 3600
                sleep_by_night[night_date].append({
                    "value": r["value"],
                    "duration": duration_hrs,
                    "source": r["sourceName"],
                })

    sorted_nights = sorted(sleep_by_night.keys())

    # Monthly sleep averages
    sleep_by_month = defaultdict(list)
    for night in sorted_nights:
        entries = sleep_by_night[night]
        total = sum(e["duration"] for e in entries if 0 < e["duration"] < 15)
        if total > 0:
            sleep_by_month[night.strftime("%Y-%m")].append(total)

    print(f"Total nights tracked: {len(sorted_nights)}")
    print("\nMonthly avg sleep (2025+):")
    for m in sorted(sleep_by_month):
        if m >= "2025":
            vals = sleep_by_month[m]
            avg = sum(vals) / len(vals)
            under_7 = sum(1 for v in vals if v < 7)
            print(f"  {m}: {int(avg)}h{int((avg%1)*60):02d}m avg | {under_7}/{len(vals)} nights under 7h")

    # Last 14 nights detail
    recent_nights = sorted_nights[-14:]
    print(f"\nLast {len(recent_nights)} tracked nights:")
    recent_totals = []
    for night in recent_nights:
        entries = sleep_by_night[night]
        total = sum(e["duration"] for e in entries if 0 < e["duration"] < 15)
        if total > 0:
            hrs = int(total)
            mins = int((total - hrs) * 60)
            flag = " <<<" if total < 7 else ""
            print(f"  {night}  {hrs}h{mins:02d}m{flag}")
            recent_totals.append(total)
    if recent_totals:
        avg = sum(recent_totals) / len(recent_totals)
        print(f"\n  Recent avg: {int(avg)}h{int((avg%1)*60):02d}m")

# ═══════════════════════════════════════════════
# 6. DAILY ACTIVITY (Last 30 days)
# ═══════════════════════════════════════════════
print("\n" + "=" * 70)
print("DAILY ACTIVITY (Last 30 days)")
print("=" * 70)

if activity_summaries:
    sorted_activities = sorted(activity_summaries, key=lambda x: x["date"] or "")
    recent_activities = sorted_activities[-30:]
    total_exercise = 0
    total_cal = 0
    n = 0
    for a in recent_activities:
        ex = int(a["appleExerciseTime"] or 0)
        cal = float(a["activeEnergyBurned"] or 0)
        stand = int(a["appleStandHours"] or 0)
        total_exercise += ex
        total_cal += cal
        n += 1
    if n:
        print(f"  Avg daily exercise: {total_exercise/n:.0f} min")
        print(f"  Avg daily active calories: {total_cal/n:.0f}")
        rest_days = sum(1 for a in recent_activities if int(a["appleExerciseTime"] or 0) < 10)
        print(f"  Rest days (< 10min exercise): {rest_days}/{n}")

# ═══════════════════════════════════════════════
# 7. STEP COUNT
# ═══════════════════════════════════════════════
print("\n" + "=" * 70)
print("STEP COUNT (monthly avg, 2025+)")
print("=" * 70)

steps = records_by_type.get("HKQuantityTypeIdentifierStepCount", [])
if steps:
    steps_by_day = defaultdict(float)
    for s in steps:
        d = parse_date(s["startDate"])
        if d and s["value"]:
            steps_by_day[d.date()] += float(s["value"])

    steps_by_month = defaultdict(list)
    for day, count in steps_by_day.items():
        if day.year >= 2025:
            steps_by_month[day.strftime("%Y-%m")].append(count)

    for m in sorted(steps_by_month):
        vals = steps_by_month[m]
        print(f"  {m}: avg {sum(vals)/len(vals):,.0f} steps/day")

# ═══════════════════════════════════════════════
# 8. CROSS-TRAINING
# ═══════════════════════════════════════════════
print("\n" + "=" * 70)
print("CROSS-TRAINING (last 8 weeks)")
print("=" * 70)

eight_weeks_ago = datetime.now() - timedelta(days=56)
other_workouts = [w for w in workouts if "Running" not in w["type"] and parse_date(w["startDate"]) and parse_date(w["startDate"]) >= eight_weeks_ago]

cross_summary = defaultdict(lambda: {"count": 0, "time": 0})
for w in other_workouts:
    wtype = w["type"].replace("HKWorkoutActivityType", "")
    cross_summary[wtype]["count"] += 1
    cross_summary[wtype]["time"] += float(w["duration"] or 0)

for wtype in sorted(cross_summary, key=lambda x: -cross_summary[x]["count"]):
    s = cross_summary[wtype]
    print(f"  {wtype:30s}  {s['count']:3d} sessions  ({s['time']/60:.1f} hrs)")

print("\n" + "=" * 70)
print("ANALYSIS COMPLETE")
print("=" * 70)
