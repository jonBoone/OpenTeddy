"""
Typhoon questions, and "asked again" meaning the last answer missed.

  A. parse: typhoon questions vs history / reports; weather no longer takes 颱風
  B. describe: moving away at 700 km is not a warning, approaching into
     800 km is, anything within 300 km is; 北東 → 東北; English compass
  C. active_storms parses JMA's JSON (fake client)
  D. orchestrator: typhoon answered directly; the same question again
     within 10 minutes skips the shortcuts and reaches the planner with a
     "don't repeat yourself" note; a repeated device command still runs

    .venv/bin/python tests/test_typhoon.py
"""
from __future__ import annotations

import asyncio, logging, os, sys, types
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)

import typhoon as T
import weather as W


def storm(name, dist_now, forecast_kms, course="北東", wind=20.0):
    return {"number": "2630", "name": name, "wind": wind, "pressure": "996", "course": course, "speed": "20",
            "pos": (29.0, 124.5), "distance": dist_now,
            "forecast": [(12 * (i + 1), (0, 0), km) for i, km in enumerate(forecast_kms)],
            "issued": "2026-10-25T00:45:00Z"}


def test_parse() -> None:
    assert T.parse_typhoon_question("最近有颱風靠近台灣嗎") is True
    assert T.parse_typhoon_question("is there a typhoon near Taiwan") is False
    for t in ("幫我整理今年的颱風歷史", "颱風來的時候提醒我", "今天天氣"):
        assert T.parse_typhoon_question(t) is None, t
    assert W.parse_weather_question("最近有颱風靠近台灣嗎") is None, "weather must not answer typhoon questions"
    assert W.parse_weather_question("颱風天會下雨嗎") is None
    print("  ✓ typhoon questions recognised; weather no longer answers them")


def test_describe() -> None:
    away = T.describe([storm("Champi", 758, [820, 900])])
    assert "往東北移動" in away and "正在遠離台灣" in away and "⚠️" not in away and "✅ 目前沒有颱風會靠近台灣" in away, away
    near = T.describe([storm("Krathon", 1200, [900, 500], course="北西", wind=45)])
    assert "（中度颱風）" in near and "往西北移動" in near and "正在靠近台灣，預報最近約 500 公里" in near
    assert "⚠️ Krathon 的路徑接近台灣" in near
    close_now = T.describe([storm("Close", 250, [400], wind=55)])
    assert "強烈颱風" in close_now and "⚠️" in close_now
    far = T.describe([storm("In-fa", 4847, [4000, 3159], course="北北西", wind=30)])
    assert "逐漸接近，但預報最近仍有約 3,159 公里" in far and "⚠️" not in far
    assert "目前沒有追蹤中的颱風" in T.describe([])
    en = T.describe([storm("Champi", 758, [820])], zh=False)
    assert "heading NE" in en and "moving away" in en and "tropical storm" in en
    print("  ✓ warnings only for storms coming close; 北東→東北; strengths on Taiwan's scale; English")


class _Resp:
    def __init__(self, p): self._p = p
    def raise_for_status(self): pass
    def json(self): return self._p


SPEC = [
    {"part": "title", "issue": {"UTC": "2026-10-25T00:45:00Z"}, "typhoonNumber": "2630", "name": {"en": "Champi"}},
    {"part": {"en": "Analysis"}, "maximumWind": {"sustained": {"m/s": "20"}}, "position": {"deg": [29.0, 124.5]},
     "course": "北東", "speed": {"km/h": "20"}, "pressure": "996"},
    {"part": {"en": "Forecast"}, "advancedHours": 12, "position": {"deg": [30.5, 126.1]}},
]


class _Client:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, url):
        return _Resp([{"tropicalCyclone": "TC2636", "typhoonNumber": "2630"}] if url.endswith("targetTc.json") else SPEC)


async def test_jma() -> None:
    T.httpx = types.SimpleNamespace(AsyncClient=_Client)  # type: ignore[assignment]
    T._cache = (0.0, None)
    s = (await T.active_storms())[0]
    assert s["name"] == "Champi" and s["wind"] == 20.0 and s["course"] == "北東" and s["speed"] == "20"
    assert 600 < s["distance"] < 800 and s["forecast"][0][0] == 12 and s["forecast"][0][2] > s["distance"]
    print("  ✓ JMA storm list and specifications parsed; distances to Taiwan computed")


async def test_orchestrator() -> None:
    from orchestrator import Orchestrator
    from models import TaskRequest, TaskStatus
    history = []
    seen = {}

    class _Tracker:
        async def get_session(self, sid): return {"id": sid, "mode": "code"}
        async def create_task(self, req): pass
        async def update_task_status(self, *a, **k): pass
        async def create_subtask(self, st): pass
        async def update_subtask(self, st): pass
        async def list_tasks(self, limit=50, session_id=None): return list(history)

    class _Reached(Exception):
        pass

    async def no_event(*a, **k): pass
    async def classify(*a, **k): seen["classifier"] = True; return None
    async def plan(req, *a, **k): seen["planner_ctx"] = dict(req.context); raise _Reached()

    def fake():
        o = types.SimpleNamespace(tracker=_Tracker(), _emit_task_event=no_event, _plan=plan,
                                  _classify_intent=classify, _fast_chat_response=plan)
        for n in ("_finish_direct", "_typhoon", "_weather", "_asked_again", "_self_review",
                  "_home_fast", "_home_xiaoai", "_home_overview", "_home_confirm"):
            setattr(o, n, (lambda name: lambda *a, **k: getattr(Orchestrator, name)(o, *a, **k))(n))
        return o

    async def run(goal):
        seen.clear()
        try:
            return await Orchestrator.run(fake(), TaskRequest(goal=goal, session_id="s1"))
        except _Reached:
            return "planner"
        except Exception as exc:  # noqa: BLE001
            return f"other:{type(exc).__name__}"

    res = await run("最近有颱風靠近台灣嗎")
    assert res.status == TaskStatus.COMPLETED and res.summary.startswith("🌀"), res
    print("  ✓ 最近有颱風靠近台灣嗎 answered from JMA, no planning")

    history[:] = [{"id": "old", "goal": "最近有颱風靠近台灣嗎", "status": "completed",
                   "created_at": (datetime.utcnow() - timedelta(minutes=2)).isoformat()}]
    res = await run("最近有颱風靠近台灣嗎")
    assert res == "planner" and seen["planner_ctx"].get("asked_again") and "classifier" not in seen, (res, seen)
    history[0]["created_at"] = (datetime.utcnow() - timedelta(minutes=30)).isoformat()
    res = await run("最近有颱風靠近台灣嗎")
    assert res != "planner" and res.summary.startswith("🌀"), "half an hour later it's a new question"
    print("  ✓ asked again within 10 min → planner with tools and the asked_again note; later → shortcut again")

    import home_assistant as HA
    from config import config
    config.ha_url, config.ha_token = "http://127.0.0.1:8123", "tok"
    async def assist(text, language=None):
        return {"ok": True, "kind": "action_done", "speech": "已關閉客廳燈", "targets": ["客廳燈"], "failed": [], "code": None}
    HA.assist = assist
    history[:] = [{"id": "old", "goal": "關客廳燈", "status": "completed",
                   "created_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat()}]
    res = await run("關客廳燈")
    assert res != "planner" and res.summary == "🏠 已關閉客廳燈", res
    config.ha_token = ""
    print("  ✓ a repeated device command still runs directly")


async def main() -> None:
    print("A. parse"); test_parse()
    print("B. describe"); test_describe()
    print("C. JMA"); await test_jma()
    print("D. orchestrator"); await test_orchestrator()
    print("\nALL TYPHOON TESTS PASS")


asyncio.run(main())
