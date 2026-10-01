# Running Training Dashboard

A local web dashboard for half-marathon training. Ingests Apple Health exports, generates a configurable training plan, tracks compliance, visualizes health metrics, and exports to calendar.

## What You Get

- **Interactive dashboard** at `localhost:5050` with charts for weekly volume, pace trends, health markers (VO2max, RHR, HRV, weight, sleep, SpO2, respiratory rate), running form (stride, ground contact, power), and intensity distribution
- **Configurable training plan** generated from a simple `env` file — set your race date, run days, gym days, and the algorithm builds a periodized plan with deloads and taper
- **Calendar export** (.ics) with real event times and durations — import into Apple/Google Calendar, re-import weekly without duplicates
- **Coaching suggestions** — alerts for overtraining, intensity balance, missed tracking, and volume compliance
- **Gym tracking** — strength sessions from Apple Health shown alongside running data

## How It Works

Everything flows from two inputs: your Apple Health export (what you actually did) and the `env`
file (what you intended to do). The project exists to compare them.

```
  env  ──────────────►  config.py  ──────────────►  plan (weeks, sessions, paces)
  (your intent)         generates the plan              │
                                                        ▼
  apple_health_export/export.xml  ──►  webapp.py  ──►  compare  ──►  dashboard / CLI / .ics
  (what you did)                       parses XML       plan vs actual
                                       caches to
                                       _cache/
```

1. **`env` is the single source of truth.** Race date, run and gym days, base and peak volume, paces.
   Nothing about the plan is hardcoded — change a value here and the whole plan regenerates.
2. **`config.py` generates the plan algorithmically** from those values: week count, weekly volumes,
   deloads, taper, and a day-by-day session schedule. There is no stored plan file to drift out of date.
3. **`webapp.py` parses the Health export** — a single XML file that is routinely over 1 GB and a few
   million elements. It streams it with `iterparse` and caches the extracted summary to
   `_cache/health_data.json`, so the ~20–60 s parse happens once per export, not once per page load.
4. **The comparison is the product.** Planned km vs actual km per week, planned long run vs longest
   run, and the intensity split (your HR zones from `MAX_HR` and `HR_ZONE_PCTS`) against the 80/20
   target. That's what drives the compliance table and the coaching alerts.
5. **Three ways to read it:** the web dashboard (charts), `weekly_checkin.py` (terminal), and `.ics`
   calendar export (the upcoming sessions land in your actual calendar with real durations).

Nothing leaves your machine. There's no account, no server, no telemetry — it's a local Flask app
reading a local file.

## Quick Start

```bash
# 1. Clone and install
git clone <repo-url>
cd training
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt

# 2. Create your config
cp env.example env
# Edit 'env' with your details (race date, schedule, etc.)

# 3. Export Apple Health data
# On iPhone: Health app → Profile icon → Export All Health Data
# Unzip and place the folder as: apple_health_export/

# 4. Run
python webapp.py
# Open http://localhost:5050
```

The first load parses the XML export (~1 min for large files). Subsequent loads are instant (cached).

## Configuration

All config lives in the `env` file. Copy `env.example` to get started.

### Race & Plan

```ini
RACE_DATE=2026-09-20
PLAN_START=2026-05-04
```

Plan weeks are auto-calculated from these two dates, rounded **up** to whole weeks so race day always falls inside the final (race) week. May 4 to September 20 = 20 weeks. The count is deliberately *not* snapped to a multiple of 4 — snapping would move race week off your actual race date.

### Schedule

```ini
RUN_DAYS=mon,wed,sat
GYM_DAYS=tue,thu,fri
LONG_RUN_DAY=sat
```

Use 3-letter day abbreviations. `LONG_RUN_DAY` must be one of `RUN_DAYS`. Days not listed are rest days. Run and gym days must not overlap.

### Plan Progression

```ini
BASE_WEEKLY_KM=20      # Starting weekly volume
PEAK_WEEKLY_KM=40      # Maximum weekly volume
RACE_WEEK_KM=10        # Race week volume
DELOAD_REDUCTION_PCT=35
QUALITY_START_WEEK=5   # Week when tempo sessions begin
RACE_PACE_START_WEEK=9 # Week when race-pace sessions begin
```

The algorithm builds volume linearly from base to peak, respecting the 10% rule (no build week increases more than 10% from the previous). Every 4th week is a deload. The last 4 weeks are sharpening + taper + race.

### Paces & Heart Rate

```ini
EASY_PACE=6:30-7:00    # Easy run pace range (min:sec per km)
RACE_PACE=5:40         # Target race pace
MAX_HR=185             # Maximum heart rate
HR_ZONE_PCTS=70,80,88,95  # Zone boundaries as % of max HR
```

### Calendar Events

```ini
EVENT_START_HOUR=18    # Events start at 6pm (24h format)
GYM_DURATION_MIN=90    # Gym session length in minutes
```

Run event durations are calculated from distance and pace, plus a 5-minute warmup.

## Usage

### Web Dashboard

```bash
python webapp.py                    # Default: port 5050
python webapp.py --port 8080        # Custom port
python webapp.py --export /path/to/export.xml  # Custom export path
```

Open `http://localhost:5050`. The dashboard has tabs for:

| Tab | What it shows |
|-----|---------------|
| Overview | Summary cards (weight, VO2max, RHR, HRV, sleep) + coaching suggestions |
| Training | Weekly volume chart, compliance table with gym tracking, zone distribution, pace trend |
| Calendar | 4-week plan preview grid, .ics download links |
| Health | Weight, VO2max, RHR, HRV, walking HR, HR recovery, respiratory rate, SpO2 |
| Form | Stride length, ground contact time, vertical oscillation, running power |
| Runs | Filterable run log with pace color-coding |
| Sleep | Nightly sleep trend |

### CLI Check-in

```bash
python weekly_checkin.py             # Last 4 weeks
python weekly_checkin.py --weeks 8   # Last 8 weeks
```

Quick text-based progress report for terminal use.

### Calendar Export

Download from the dashboard or directly:

```bash
# Next 4 weeks
curl http://localhost:5050/api/calendar.ics -o training.ics

# Full plan
curl http://localhost:5050/api/calendar-full.ics -o training_full.ics

# Custom date range
curl "http://localhost:5050/api/calendar.ics?from=2026-06-01&to=2026-07-01" -o june.ics
```

Import the `.ics` file into Apple Calendar, Google Calendar, or any calendar app. Events use deterministic UIDs, so re-importing the same config overwrites existing events instead of creating duplicates.

### Refreshing Data

1. Re-export from iPhone (Health → Profile → Export All Health Data)
2. Replace the `apple_health_export/` directory
3. Either restart the server or click "Refresh Data" in the dashboard

The cache auto-invalidates when `export.xml` or `env` changes (compared by modification time).

Caveat: the cache holds the generated `plan_schedule` too, not just the parsed health data. Editing `config.py` changes neither watched mtime, so a restart will still serve the **stale plan**. Use "Refresh Data" in the dashboard (or `POST /api/refresh`, or `rm -rf _cache`) after any `config.py` change.

## How the Plan Algorithm Works

Given your config, the plan generator:

1. **Calculates plan length** from `PLAN_START` to `RACE_DATE`, rounded up to whole weeks (race day lands in the last week)
2. **Builds weekly volumes** linearly from `BASE_WEEKLY_KM` to `PEAK_WEEKLY_KM`, capped at 10% increase per build week
3. **Inserts deload weeks** every 4th week (reduced by `DELOAD_REDUCTION_PCT`)
4. **Creates a taper block** for the last 4 weeks: sharpening → taper → taper → race week
5. **Distributes daily sessions**: long run on `LONG_RUN_DAY`, one quality session (tempo or race-pace) on another run day, remaining km split across easy days
6. **Assigns gym and rest days** per config

Actual output for a 20-week plan (`BASE_WEEKLY_KM=20`, `PEAK_WEEKLY_KM=40`, `DELOAD_REDUCTION_PCT=35`):

```
W 1 |  20 km |  7 km long | Base Building
W 2 |  22 km |  8 km long | Base Building
W 3 |  24 km |  8 km long | Base Building
W 4 |  16 km |  5 km long | Base Building (Deload)
W 5 |  25 km |  9 km long | Aerobic Dev
W 6 |  27 km | 10 km long | Aerobic Dev
W 7 |  29 km | 11 km long | Aerobic Dev
W 8 |  19 km |  6 km long | Aerobic Dev (Deload)
W 9 |  31 km | 12 km long | Race-Specific
W10 |  33 km | 13 km long | Race-Specific
W11 |  35 km | 14 km long | Race-Specific
W12 |  23 km |  8 km long | Race-Specific (Deload)
W13 |  36 km | 13 km long | Race-Specific
W14 |  38 km | 13 km long | Race-Specific
W15 |  40 km | 14 km long | Race-Specific (Peak)
W16 |  26 km |  9 km long | Race-Specific (Deload)
W17 |  36 km | 18 km long | Sharpening
W18 |  31 km | 11 km long | Taper
W19 |  22 km |  8 km long | Taper
W20 |  10 km |  0 km long | Race Week
```

Deload weeks are every 4th week and are derived from the week before them, so a plan whose length isn't a multiple of 4 still gets a full, gap-free set of weekly targets.

## Files

| File | Purpose |
|------|---------|
| `webapp.py` | Flask backend — parses health data, serves API + dashboard |
| `config.py` | Reads `env`, generates training plan, calendar export |
| `weekly_checkin.py` | CLI progress report |
| `test_plan.py` | Plan invariants — run after changing `env` or the generator |
| `analyze_health.py` | Deep health data analysis (standalone) |
| `templates/dashboard.html` | Single-page dashboard frontend |
| `env.example` | Config template — copy to `env` |
| `.claude/skills/` | Claude Code skills (see below) |

## Claude Code Skills

`.claude/skills/` contains two skills, so an agent can drive this project without being re-taught
each time:

| Skill | What it does |
|-------|--------------|
| `apple-health-data` | Checks whether `export.xml` exists and how stale it is, walks you through the iPhone export, states exactly where the folder goes, handles venv and `env` setup, then runs the parsers |
| `half-marathon-coach` | Runs the check-in and assesses it in priority order — injury risk, volume, intensity split, health markers — then prescribes the coming week from the generated plan. Inside 3 weeks of the race it switches to race mode: finish-time projection, durability risk, and a pacing plan |
| `retune-plan` | Changes the plan safely: which `env` value ripples into what, then verifies the regenerated plan has no gaps and still puts race day in the final week |

They're plain Markdown with YAML frontmatter; read or edit them directly. The coach skill assumes
fresh data and defers to `apple-health-data` when the export is stale.

## Requirements

- Python 3.9+
- Flask (`pip install -r requirements.txt`)
- Apple Health export (from iPhone)
- A browser

No other dependencies. Charts use Chart.js from CDN.
