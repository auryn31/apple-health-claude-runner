# Running Training Dashboard

## Project Overview

Half-marathon training system with an interactive local web dashboard. Parses Apple Health exports, generates a configurable training plan, tracks compliance, and exports to calendar.

All training config lives in the `env` file — race date, schedule, paces, progression. The plan is generated algorithmically by `config.py`.

## Weekly Check-in Workflow

When the user asks for a weekly check-in or progress update:

1. Run `python weekly_checkin.py` to get the latest stats from the Apple Health export
2. Analyze the output and provide a brutally honest assessment covering:
   - **Volume compliance**: Did they hit the weekly km target? Long run target?
   - **Intensity distribution**: Are they doing 80% easy / 20% hard? Call out if too much tempo/threshold running.
   - **Health markers**: VO2max trend, RHR trend, HRV. Flag any warning signs.
   - **Weight progress**: Are they trending toward target weight? (from `env`: ATHLETE_WEIGHT_TARGET_KG)
   - **Sleep**: If data available, are they getting 7+ hours?
   - **Injury risk**: Any signs of overtraining (HR spikes, HRV drops, volume spikes)?
   - **Gym compliance**: Are they hitting their configured gym days?
3. Give specific, actionable advice for the coming week based on the generated plan
4. If they're ahead of plan, tell them to bank the fitness not add more volume
5. If they're behind, suggest how to adjust without panic

## Key Context

- Training for half marathon mid-August 2026, target sub-2:00:00
- Training for full marathon end of 2026 (plan to be created after half)
- User tends to run too fast — remind them to slow down on easy days
- User is a beginner runner with strong cycling/strength background
- Injury prevention is priority #1 given limited running history
- Apple Health export must be re-exported from iPhone before running scripts

## Architecture

- `env` — Single source of truth for all training config (race date, schedule, paces, progression)
- `config.py` — Reads `env`, generates the full training plan algorithmically, provides calendar export
- `webapp.py` — Flask backend: parses Apple Health XML, serves dashboard API + calendar .ics
- `templates/dashboard.html` — Single-page frontend with Chart.js
- `weekly_checkin.py` — CLI progress report (imports config from `config.py`)
- `test_plan.py` — Plan invariants (`python test_plan.py`); run after any `env` or `config.py` change
- `analyze_health.py` — Deep health data analysis (standalone, run occasionally)

## Running

```bash
# Activate venv
source venv/bin/activate

# Web dashboard
python webapp.py                     # http://localhost:5050

# Weekly check-in (CLI)
python weekly_checkin.py             # Last 4 weeks
python weekly_checkin.py --weeks 8   # Last 8 weeks

# Full deep-dive analysis
python analyze_health.py
```

The user needs to re-export Apple Health data from their iPhone (Health app → Profile → Export All Health Data) and place the unzipped folder as `apple_health_export/` before running.

## Config Changes

Edit the `env` file to change anything about the plan. The cache auto-invalidates when `env` changes. Key settings:

- `RUN_DAYS`, `GYM_DAYS`, `LONG_RUN_DAY` — weekly schedule
- `BASE_WEEKLY_KM`, `PEAK_WEEKLY_KM` — volume progression
- `EASY_PACE`, `RACE_PACE` — pace targets
- `EVENT_START_HOUR`, `GYM_DURATION_MIN` — calendar event timing

See `env.example` for all options with descriptions.
