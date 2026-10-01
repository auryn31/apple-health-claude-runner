---
name: retune-plan
description: Change the training plan safely by editing `env`, then verify the regenerated plan is still coherent. Use when the race date moves, the weekly schedule changes, volume targets need adjusting, paces or HR zones change, or the user asks to retune/rebuild/adjust the plan or says the plan looks wrong.
---

# Retune the plan

The plan is generated, never stored. Editing one value in `env` regenerates all of it — which
is the point, and also the risk: a small edit can silently produce an incoherent plan.

## 1. Edit `env` — not `env.example`

```bash
grep -E "RACE_DATE|PLAN_START" env
```

`env.example` is the committed template; `env` is the real (gitignored) config. Editing the
template changes nothing and is the most common mistake here.

## 2. Know what each change ripples into

| Change | Ripples into |
|---|---|
| `RACE_DATE` / `PLAN_START` | **Plan length.** Week count = whole weeks between them, rounded up so race day lands in the final week. Every phase boundary (deload, sharpening, taper) shifts with it. |
| `BASE_WEEKLY_KM` / `PEAK_WEEKLY_KM` | All weekly volumes, every long run, and the taper block (scaled off peak) |
| `RUN_DAYS` / `GYM_DAYS` / `LONG_RUN_DAY` | Day-by-day sessions, calendar events. Validated — see below. |
| `MAX_HR` / `HR_ZONE_PCTS` | HR zones, which re-buckets **historical** runs and changes the easy/hard split retroactively |
| `EASY_PACE` / `RACE_PACE` | Session targets and calendar event durations |

`config.py` asserts three things on load, so these fail loudly rather than silently:
`LONG_RUN_DAY` must be in `RUN_DAYS`; `RUN_DAYS` and `GYM_DAYS` must not overlap; the plan must
be at least 4 weeks.

## 3. Verify the regenerated plan

```bash
source venv/bin/activate && python test_plan.py
```

Five invariants, each one a bug that shipped at least once: every week has a target, race day
falls in the final week, `get_training_week` boundaries hold, consecutive build weeks respect
the 10% rule, and no long run exceeds ~50% of its week. It prints the week count and the full
volume list so you can eyeball the result.

One thing the tests can't judge — **peak vs reality.** If `PEAK_WEEKLY_KM` is far above what the
athlete has actually hit, the taper weeks scale off a fiction and will prescribe more than their
real peak. Compare the printed volumes against actual weekly km before trusting the taper.

## 4. Reload

`_cache/health_data.json` holds the generated `plan_schedule` as well as the parsed health data,
and it is invalidated by the mtimes of `export.xml` and `env` **only**. So:

- Changed `env` → restart `webapp.py` (mtime changed, cache rebuilds itself)
- Changed `config.py` → **restarting is not enough.** The stale plan is served from cache.
  Force a rebuild with `curl -X POST http://localhost:5050/api/refresh`, or `rm -rf _cache`.
- `weekly_checkin.py` and `test_plan.py` recompute on every run, so they will show a fixed plan
  while the dashboard still serves the broken one. Trust the dashboard last.

Confirm the change landed before reporting success:

```bash
curl -sf http://localhost:5050/api/data | python -c "
import json,sys; d=json.load(sys.stdin)
print('race', d['race_date'], '| week', d['current_week'], 'of', len(d['weekly']), '| days out', d['days_to_race'])"
```

## 5. Don't re-plan what has already happened

Changing `PLAN_START` retroactively relabels every past week, making historical compliance
meaningless. If the goal is "I fell behind, fix the plan", adjust `PEAK_WEEKLY_KM` down to
something reachable instead — never move the start date to flatter the numbers.
