"""
schedule_runs.py — plan and fire the day's pipeline runs around first pitch.

What it does, all in Eastern time:

  1. From 08:00 ET, pull the day's schedule once and write a plan naming two
     run times — one hour before the FIRST game and one hour before the LAST
     game. The plan is a file, so it survives reboots and is safe to re-read.
  2. On every invocation, fire any planned run whose time has arrived and that
     has not run yet, then record the result back into the plan.

Why one cron entry every few minutes instead of a job at 08:00 that schedules
two more: `at` is disabled by default on macOS, and anything that sleeps until
a target time dies when the machine sleeps or reboots. Polling a plan file is
the version that still works when the laptop was shut when 08:00 came round —
it plans as soon as it wakes, and a run whose time passed during sleep fires on
the next tick rather than being lost.

Cron also runs in the machine's local timezone, which is not necessarily
Eastern. Every decision here is made against America/New_York explicitly, so
the schedule is correct regardless of where the machine is or how cron is set,
and DST is handled by the zone database rather than by hand.

Usage:
    python schedule_runs.py                 # plan if due, fire what's due
    python schedule_runs.py --status        # show today's plan, change nothing
    python schedule_runs.py --dry-run       # plan/report but never run pipeline
    python schedule_runs.py --replan        # rebuild today's plan from scratch
    python schedule_runs.py --print-cron    # emit the crontab line to install
"""

import argparse
import json
import os
import subprocess
import sys
import traceback
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo


REPO = Path(__file__).resolve().parent
DATA_DIR = REPO / "data"
LOG_DIR = REPO / "logs"
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

ET = ZoneInfo("America/New_York")

# Don't build the day's plan before this hour: probable starters and start times
# firm up over the morning, and a plan built at 03:00 would be read off a
# half-populated schedule.
PLAN_HOUR_ET = 8

# How long before first pitch each run fires.
LEAD = dt.timedelta(hours=1)

# A run whose time passed while the machine was off still fires, but only if it
# is this fresh. Later than that and the slate has moved on — firing "one hour
# before the first game" at midnight predicts games that already finished.
MAX_LATE = dt.timedelta(hours=6)

LOCK_PATH = DATA_DIR / "scheduler.lock"
# A pipeline run takes minutes; anything holding the lock longer than this is a
# crashed process, not a live one.
LOCK_STALE = dt.timedelta(hours=2)


def now_et() -> dt.datetime:
    return dt.datetime.now(ET)


def log(msg: str) -> None:
    stamp = now_et().strftime("%Y-%m-%dT%H:%M:%S%z")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    try:
        with (LOG_DIR / f"scheduler_{now_et().date().isoformat()}.log").open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def plan_path(date: str) -> Path:
    return DATA_DIR / f"run_plan_{date}.json"


# ---------------------------------------------------------------------------
# lock


def _read_lock() -> dict | None:
    try:
        return json.loads(LOCK_PATH.read_text())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_lock() -> bool:
    """Stop two cron ticks running the pipeline at once."""
    held = _read_lock()
    if held:
        try:
            since = dt.datetime.fromisoformat(held["since"])
        except (KeyError, ValueError):
            since = None
        alive = _pid_alive(int(held.get("pid", -1)))
        fresh = since is not None and (now_et() - since) < LOCK_STALE
        if alive and fresh:
            log(f"another run is in progress (pid {held.get('pid')}, since "
                f"{held.get('since')}) — skipping this tick")
            return False
        log(f"clearing stale lock (pid {held.get('pid')}, since {held.get('since')})")
    LOCK_PATH.write_text(json.dumps({"pid": os.getpid(),
                                     "since": now_et().isoformat()}))
    return True


def release_lock() -> None:
    try:
        LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# planning


def _first_pitches(date: str) -> list[dt.datetime]:
    """Every game's first pitch for `date`, in ET, ascending."""
    import fetch
    slate = fetch.get_slate(date)
    times = []
    for g in slate:
        iso = g.get("game_datetime")
        if not iso:
            continue
        try:
            t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        except ValueError:
            continue
        times.append(t.astimezone(ET))
    return sorted(times)


def build_plan(date: str) -> dict:
    times = _first_pitches(date)
    plan = {
        "date": date,
        "timezone": "America/New_York",
        "planned_at": now_et().isoformat(),
        "n_games": len(times),
        "first_pitch": times[0].isoformat() if times else None,
        "last_pitch": times[-1].isoformat() if times else None,
        "runs": [],
    }
    if not times:
        log(f"no games scheduled for {date}; nothing to run")
        return plan

    slots = [("pre_first", times[0] - LEAD)]
    # A single-game day, or a slate where the last game starts within an hour of
    # the first, would otherwise queue the same run twice.
    if (times[-1] - LEAD) - (times[0] - LEAD) >= dt.timedelta(minutes=30):
        slots.append(("pre_last", times[-1] - LEAD))

    for name, at in slots:
        plan["runs"].append({
            "name": name,
            "at": at.isoformat(),
            "status": "pending",
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
        })

    log(f"planned {date}: {len(times)} games, first pitch "
        f"{times[0].strftime('%-I:%M %p')} ET, last {times[-1].strftime('%-I:%M %p')} ET")
    for r in plan["runs"]:
        log(f"  {r['name']}: run at "
            f"{dt.datetime.fromisoformat(r['at']).strftime('%-I:%M %p')} ET")
    return plan


def load_plan(date: str) -> dict | None:
    p = plan_path(date)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except ValueError:
        log(f"plan file {p.name} is unreadable — rebuilding")
        return None


def save_plan(plan: dict) -> None:
    plan_path(plan["date"]).write_text(json.dumps(plan, indent=2))


# ---------------------------------------------------------------------------
# firing


def run_pipeline(date: str, dry_run: bool = False) -> int:
    cmd = ["bash", str(REPO / "pipeline.sh"), date]
    if dry_run:
        log(f"DRY RUN — would exec: {' '.join(cmd)}")
        return 0
    log(f"exec: {' '.join(cmd)}")
    env = dict(os.environ)
    # cron's PATH rarely includes the interpreter this script is running under,
    # and pipeline.sh calls `python`. Hand it the one we know exists.
    env.setdefault("PYTHON", sys.executable)
    proc = subprocess.run(cmd, cwd=str(REPO), env=env)
    log(f"pipeline exited {proc.returncode}")
    return proc.returncode


def fire_due(plan: dict, dry_run: bool = False) -> bool:
    """Run any slot that is due. Returns True if anything ran."""
    now = now_et()
    ran = False
    for r in plan["runs"]:
        if r["status"] != "pending":
            continue
        at = dt.datetime.fromisoformat(r["at"])
        if now < at:
            continue
        late = now - at
        if late > MAX_LATE:
            r["status"] = "missed"
            r["finished_at"] = now.isoformat()
            log(f"{r['name']} was due {late} ago (limit {MAX_LATE}) — marking "
                f"missed rather than predicting games that have already started")
            save_plan(plan)
            continue

        if not acquire_lock():
            return ran
        try:
            r["status"] = "running"
            r["started_at"] = now.isoformat()
            save_plan(plan)
            log(f"firing {r['name']} (due {at.strftime('%-I:%M %p')} ET, "
                f"{int(late.total_seconds() // 60)} min late)")
            code = run_pipeline(plan["date"], dry_run=dry_run)
            r["exit_code"] = code
            r["status"] = "done" if code == 0 else "failed"
            r["finished_at"] = now_et().isoformat()
            save_plan(plan)
            ran = True
        finally:
            release_lock()
    return ran


# ---------------------------------------------------------------------------


def describe(plan: dict | None) -> None:
    if not plan:
        print("no plan for today yet")
        return
    print(f"date        {plan['date']}  ({plan['n_games']} games)")
    fp, lp = plan.get("first_pitch"), plan.get("last_pitch")
    fmt = lambda s: dt.datetime.fromisoformat(s).strftime("%-I:%M %p ET") if s else "—"
    print(f"first pitch {fmt(fp)}")
    print(f"last pitch  {fmt(lp)}")
    print(f"planned at  {fmt(plan.get('planned_at'))}")
    print("runs:")
    for r in plan["runs"]:
        extra = "" if r["exit_code"] is None else f"  exit={r['exit_code']}"
        print(f"  {r['name']:<10} {fmt(r['at']):<12} {r['status']}{extra}")


CRON_COMMENT = "# orb-ai pipeline scheduler (plans at 08:00 ET, runs 1h before first and last game)"


def print_cron() -> None:
    py = sys.executable
    script = REPO / "schedule_runs.py"
    print(CRON_COMMENT)
    print(f"*/5 * * * * cd {REPO} && {py} {script} >> {LOG_DIR}/cron.log 2>&1")
    print()
    print("Install with:")
    print(f"  (crontab -l 2>/dev/null | grep -v 'schedule_runs.py'; "
          f"{py} {script} --print-cron | grep -v '^Install\\|^ \\|^$') | crontab -")
    print()
    print("Runs every 5 minutes and does nothing until there is something to do:")
    print(f"  - builds today's plan on the first tick at/after {PLAN_HOUR_ET}:00 ET")
    print("  - fires the pipeline when a planned time arrives")
    print("A tick with nothing due exits in well under a second.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="YYYY-MM-DD (default: today in ET)")
    ap.add_argument("--status", action="store_true", help="show the plan, change nothing")
    ap.add_argument("--dry-run", action="store_true", help="never exec pipeline.sh")
    ap.add_argument("--replan", action="store_true", help="rebuild today's plan")
    ap.add_argument("--print-cron", action="store_true", help="emit the crontab line")
    args = ap.parse_args(argv)

    if args.print_cron:
        print_cron()
        return 0

    date = args.date or now_et().date().isoformat()
    plan = load_plan(date)

    if args.status:
        describe(plan)
        return 0

    if plan is None or args.replan:
        # Before the planning hour there is nothing to do; the slate is not
        # settled and firing off a schedule read at 04:00 would be worse than
        # waiting. Exit quietly so cron output stays empty.
        if not args.replan and now_et().hour < PLAN_HOUR_ET:
            return 0
        plan = build_plan(date)
        save_plan(plan)

    fire_due(plan, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        log("scheduler failed — see traceback above")
        sys.exit(1)
