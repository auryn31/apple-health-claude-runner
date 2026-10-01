"""Plan invariants. Run after changing `env` or the generator in config.py.

    python test_plan.py

No framework on purpose: plain asserts, so this runs anywhere the project does.
Each check corresponds to a bug that actually shipped once.
"""
from datetime import timedelta

from config import CONFIG, WEEKLY_TARGETS, PLAN_START, HALF_MARATHON_DATE
from weekly_checkin import get_training_week


def test_every_week_has_a_target():
    """Plan length isn't snapped to a multiple of 4, so block-based generation
    used to leave the remainder weeks with no target at all."""
    n = CONFIG["plan_weeks"]
    missing = sorted(set(range(1, n + 1)) - set(WEEKLY_TARGETS))
    assert not missing, f"weeks with no target: {missing}"


def test_race_day_is_in_the_final_week():
    """Snapping the week count (either direction) moved race week off the
    actual race date -- 3 weeks early when rounding down, 3 late when up."""
    n = CONFIG["plan_weeks"]
    assert PLAN_START + timedelta(weeks=n - 1) <= HALF_MARATHON_DATE \
        < PLAN_START + timedelta(weeks=n), \
        f"race {HALF_MARATHON_DATE:%Y-%m-%d} not inside week {n}"


def test_training_week_boundaries():
    """get_training_week once hardcoded a 16-week limit, so any longer plan
    reported 'Pre-plan' while mid-training."""
    n = CONFIG["plan_weeks"]
    assert get_training_week(PLAN_START - timedelta(days=1)) is None
    assert get_training_week(PLAN_START) == 1
    assert get_training_week(PLAN_START + timedelta(days=6)) == 1
    assert get_training_week(PLAN_START + timedelta(days=7)) == 2
    assert get_training_week(PLAN_START + timedelta(weeks=n - 1)) == n
    assert get_training_week(PLAN_START + timedelta(weeks=n)) is None
    assert get_training_week(HALF_MARATHON_DATE) == n


def test_build_weeks_respect_the_10_percent_rule():
    """Consecutive build weeks only. Deload weeks dip and the week after
    resumes the pre-deload level, so neither is a 10% violation."""
    n = CONFIG["plan_weeks"]
    taper_start = n - 3
    build = [w for w in range(1, taper_start) if w % 4 != 0]
    for prev, cur in zip(build, build[1:]):
        a, b = WEEKLY_TARGETS[prev]["km"], WEEKLY_TARGETS[cur]["km"]
        assert b <= a * 1.10 + 0.5, \
            f"week {cur} jumps {(b / a - 1) * 100:.0f}% from week {prev} ({a} -> {b} km)"


def test_long_run_is_never_too_big_a_share():
    """Long run over ~50% of weekly volume is an injury-risk bug, not a plan."""
    for wk, t in WEEKLY_TARGETS.items():
        if t["km"]:
            share = t["long"] / t["km"]
            assert share <= 0.52, f"week {wk}: long run is {share:.0%} of weekly volume"


def test_race_day_is_scheduled_on_the_race_date():
    """RACE DAY used to be pinned to LONG_RUN_DAY, so a Sunday race with
    RUN_DAYS=mon,wed,sat put the race on Saturday and rest on race day."""
    from config import PLAN_SCHEDULE
    key = HALF_MARATHON_DATE.strftime("%Y-%m-%d")
    s = PLAN_SCHEDULE.get(key)
    assert s, f"no schedule entry for race day {key}"
    assert s["session_type"] == "race", \
        f"race day {key} ({HALF_MARATHON_DATE:%a}) is '{s['description']}'"


def test_nothing_loading_in_the_48h_before_the_race():
    """No run and no strength work the day before, and no gym two days out,
    regardless of what RUN_DAYS / GYM_DAYS say."""
    from config import PLAN_SCHEDULE
    for offset, allowed in ((1, {"rest"}), (2, {"rest", "run"})):
        day = HALF_MARATHON_DATE - timedelta(days=offset)
        s = PLAN_SCHEDULE.get(day.strftime("%Y-%m-%d"))
        assert s, f"no schedule entry for {day:%Y-%m-%d}"
        assert s["type"] in allowed, \
            f"{day:%Y-%m-%d} ({day:%a}), {offset}d before race: {s['type']} - {s['description']}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    n = CONFIG["plan_weeks"]
    print(f"\n{n} weeks, race {HALF_MARATHON_DATE:%Y-%m-%d} in week {n}")
    print("volumes:", [WEEKLY_TARGETS[w]["km"] for w in range(1, n + 1)])
