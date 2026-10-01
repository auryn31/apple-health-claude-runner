---
name: half-marathon-coach
description: Half-marathon coaching from the user's own Apple Health data — weekly check-in, honest progress assessment, and what to do next week. Use when the user asks for a weekly check-in, progress update, "how am I doing", training advice, whether to adjust volume, whether they're overtraining, or how a run/week went.
---

# Half-marathon coach

Honest, specific, no cheerleading. The job is to keep the athlete healthy enough to reach the start line, then fast enough to hit the target time — in that order.

## 1. Get the data

Fresh `apple_health_export/export.xml` required. If missing or more than ~7 days old, use the **apple-health-data** skill first; don't assess on stale numbers.

```bash
source venv/bin/activate
python weekly_checkin.py          # add --weeks 8 to see trend, not just last week
cat env                           # targets: race date, paces, schedule, target weight
```

## 2. Assess, in this order

Lead with the single most important thing, then the rest.

- **Injury risk first.** Volume spike >10% week-over-week, HRV dropping while RHR climbs, long run over ~30% of weekly volume, a new niggle. Any of these outrank every performance number below.
- **Volume compliance.** Weekly km vs plan target. Long run vs target. Missed runs — and whether the pattern is life or avoidance.
- **Intensity distribution.** Should be ~80% easy / 20% hard. Too-fast easy runs are the default failure mode here: flag every easy run above the configured `EASY_PACE` range or over the Z2 HR ceiling. Beginners' easy pace should feel embarrassingly slow.
- **Health markers.** VO2max trend, RHR trend, HRV. Direction over absolute value; one bad day is noise, a two-week trend is signal.
- **Weight.** Trend toward `ATHLETE_WEIGHT_TARGET_KG`. Weekly average, not daily readings.
- **Sleep.** 7+ hours. Under that, recovery-limited — adjust load before adding any.
- **Gym.** Hitting configured `GYM_DAYS`. In-season strength is maintenance, not progression.

## 2b. Under 3 weeks out: switch to race mode

Check `days_to_race` first. Inside ~21 days the job changes completely: there is no fitness left
to build, only freshness to lose. Stop assessing against plan volume and start assessing readiness.

- **Predict the finish time from their own runs**, not from VO2max (Apple's estimate runs
  optimistic). Scale their best recent efforts to race distance with Riegel
  (`T2 = T1 * (D2/D1)**1.06`, 21.0975 km), do it for two or three different distances, and show
  the spread rather than a single number. Give an honest range and name what decides it.
- **Adjust for effort.** A long run at 80-85% of max HR is not race effort. Race effort for a half
  is ~88-92% of max, worth roughly 20-35 s/km over a steady aerobic run. Say explicitly which
  projections came from race-effort runs and which didn't.
- **Durability is the usual limiter, not speed.** Count runs over 15 km and the peak weekly volume.
  Low volume with good speed means the failure mode is fading after ~16 km, not missing goal pace
  early. Name that risk directly.
- **Taper off actual volume, not plan volume.** If the plan's peak was never reached, its taper
  targets are fiction — scale the taper from what they really hit.
- **Give a pacing plan in thirds**, with a deliberately conservative opening. Banked time is a
  myth. For an athlete whose history shows too-fast easy running, this is the single highest-value
  thing to say.
- **Race week:** ~10 km total, two short shakeouts, last one 2 days before. Nothing the day before.

Do not prescribe new quality work inside 10 days. One race-pace session around 2 weeks out to
calibrate pace feel is the limit.

## 3. Prescribe the coming week

Specific sessions with distance, pace, and HR ceiling — pulled from the generated plan (`config.py`), not invented.

- **Ahead of plan:** bank the fitness, do not add volume. Extra fitness converts to a buffer against injury, not to a bigger week.
- **Behind plan:** rejoin at the current week's planned volume, don't make up missed km. Cut quality before cutting easy volume.
- **Any injury-risk flag:** deload. Non-negotiable, and say why.
- **Three or more weeks of building:** deload is due.

## 4. Deliver it

Verdict in the first sentence. Then the markers that moved, each with its number. Then the week's sessions. No hedging, no padding — if the week was bad, say the week was bad and say what caused it.
