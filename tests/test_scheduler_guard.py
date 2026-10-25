"""
A missing APScheduler is said, not papered over.

  A. scheduler.available / _validate_cron raise SchedulerUnavailable with
     the fix, instead of a cryptic "Invalid cron expression"
  B. Telegram: a schedule request on such a machine gets that message and
     never reaches the agent (which once faked a schedule with a script)
  C. scripts/check_requirements.py finds a missing package

    .venv/bin/python tests/test_scheduler_guard.py
"""
from __future__ import annotations

import asyncio, logging, os, subprocess, sys, tempfile, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)

import scheduler


def without_apscheduler():
    saved = {k: v for k, v in sys.modules.items() if k.startswith("apscheduler")}
    for k in list(saved):
        del sys.modules[k]
    for k in ("apscheduler", "apscheduler.triggers", "apscheduler.triggers.cron",
              "apscheduler.schedulers", "apscheduler.schedulers.asyncio"):
        sys.modules[k] = None          # import raises ImportError
    return saved


def restore(saved):
    for k in [k for k in sys.modules if k.startswith("apscheduler")]:
        del sys.modules[k]
    sys.modules.update(saved)


def test_scheduler() -> None:
    assert scheduler.available()
    scheduler._validate_cron("40 7 * * 1-5")
    saved = without_apscheduler()
    try:
        assert not scheduler.available()
        try:
            scheduler._validate_cron("40 7 * * 1-5"); raise AssertionError
        except scheduler.SchedulerUnavailable as exc:
            assert "APScheduler" in str(exc) and "pip install -r requirements.txt" in str(exc)
    finally:
        restore(saved)
    try:
        scheduler._validate_cron("not a cron"); raise AssertionError
    except ValueError as exc:
        assert not isinstance(exc, scheduler.SchedulerUnavailable)
    print("  ✓ missing APScheduler → SchedulerUnavailable with the fix; a bad cron is still a ValueError")


async def test_telegram() -> None:
    import telegram_bridge as TB
    import scheduling_intent
    replies, ran = [], []

    async def send(chat_id, text, **k): replies.append(text)
    async def resolve(chat_id, goal): return "sess-1"
    async def detect(text):
        return types.SimpleNamespace(cron="40 7 * * 1-5", task_goal="請小愛音箱提醒出門",
                                     summary="週一到週五 07:40", notify_when="")
    async def add(**k): raise scheduler.SchedulerUnavailable(scheduler.UNAVAILABLE_MESSAGE)
    async def run(req): ran.append(req); raise AssertionError("the agent must not run")

    TB._send_reply = send
    TB._resolve_or_create_session = resolve
    TB._orchestrator = types.SimpleNamespace(run=run)
    scheduling_intent.detect_scheduling_intent = detect
    scheduler.add_schedule = add
    TB._running_chats["42"] = None
    await TB._run_goal_for_chat("42", "請小愛音箱每天週一到週五早上7：40分提醒出門")
    assert replies == [scheduler.UNAVAILABLE_MESSAGE] and not ran, (replies, ran)
    assert "42" not in TB._running_chats
    print("  ✓ Telegram: scheduling unavailable → told plainly, the agent never improvises a schedule")


def test_weekdays() -> None:
    """Cron counts Sunday = 0; APScheduler 3 counts Monday = 0. "1-5" must
    mean Monday–Friday — it ran Tuesday–Saturday until the names fix."""
    from datetime import datetime, timedelta
    def fires(cron, n=6):
        t = scheduler._cron_to_trigger(cron)
        cur = t.get_next_fire_time(None, datetime(2026, 10, 24, 12, 0, tzinfo=t.timezone))   # Saturday noon
        out = []
        for _ in range(n):
            out.append(cur.strftime("%a")); cur = t.get_next_fire_time(cur, cur + timedelta(seconds=1))
        return out
    assert fires("40 7 * * 1-5") == ["Mon", "Tue", "Wed", "Thu", "Fri", "Mon"]
    assert set(fires("0 9 * * 1")) == {"Mon"}
    assert set(fires("0 9 * * 0")) == set(fires("0 9 * * 7")) == {"Sun"}
    assert set(fires("0 9 * * 6,0")) == {"Sat", "Sun"}
    assert fires("0 9 * * mon-fri") == fires("0 9 * * 1-5")
    for bad in ("40 7 * *", "61 7 * * *"):
        try:
            scheduler._validate_cron(bad); raise AssertionError(bad)
        except ValueError:
            pass
    print("  ✓ weekdays follow cron (1-5 = Mon–Fri, 0/7 = Sun); malformed rules still rejected")


def test_requirements() -> None:
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "check_requirements.py")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("# comment\nhttpx>=0.20\nAPScheduler>=3.10\ndefinitely-not-installed-pkg>=1.0  # x\n"
                "-r other.txt\nwinonly-pkg>=1; sys_platform == 'win32'\n")
        p = f.name
    r = subprocess.run([sys.executable, script, p], capture_output=True, text=True)
    os.unlink(p)
    assert r.returncode == 1 and r.stdout.split() == ["definitely-not-installed-pkg>=1.0"], r
    r = subprocess.run([sys.executable, script], capture_output=True, text=True)
    assert r.returncode == 0, f"this venv is missing: {r.stdout}"
    print("  ✓ check_requirements: lists what's missing (markers respected), clean on this venv")


async def main() -> None:
    print("A. scheduler"); test_scheduler()
    print("B. telegram"); await test_telegram()
    print("B′. weekdays"); test_weekdays()
    print("C. requirements"); test_requirements()
    print("\nALL SCHEDULER GUARD TESTS PASS")


asyncio.run(main())
