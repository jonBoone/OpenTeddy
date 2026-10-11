"""
OpenTeddy Home Tools — the house through Home Assistant.

Short commands ("關客廳燈") never reach these: the orchestrator sends them
straight to HA Assist (home_intent.py). These are for the planner when a
request needs looking things up or several steps — "我出門了，把該關的都關掉",
"溫度超過 28 度就開冷氣", "用小愛跟大家說晚餐好了".

All low risk at the tool level; the real guard is in home_assistant.py:
locks, alarms, valves, sirens, water heaters and garage / gate / door
covers are held until the person replies "確認 <code>" themselves.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import home_assistant as HA
from tool_registry import RiskLevel, make_result


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


async def _wrap(coro) -> Dict[str, Any]:
    start = time.monotonic()
    try:
        return make_result(True, result=await coro, duration_ms=_ms(start))
    except HA.HAError as exc:
        return make_result(False, error=str(exc), duration_ms=_ms(start))
    except Exception as exc:  # noqa: BLE001
        return make_result(False, error=f"{type(exc).__name__}: {exc}", duration_ms=_ms(start))


async def home_devices(query: Optional[str] = None, domain: Optional[str] = None) -> Dict[str, Any]:
    return await _wrap(HA.list_entities(query, domain))


async def home_state(entity_id: str) -> Dict[str, Any]:
    return await _wrap(HA.get_state(entity_id))


async def home_control(entity_id: str, action: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return await _wrap(HA.control(entity_id, action, data or {}))


async def home_assist(text: str) -> Dict[str, Any]:
    return await _wrap(HA.assist(text))


async def xiaoai(text: str, mode: str = "say") -> Dict[str, Any]:
    return await _wrap(HA.xiaoai(text, execute=(mode == "command")))


def _fn(name: str, desc: str, props: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required}}}


_SCHEMAS = {
    "home_devices": _fn(
        "home_devices",
        "List smart-home devices from Home Assistant (lights, air conditioners, TV, "
        "plugs, curtains, sensors…) with their entity_id and current state. Filter by "
        "a name fragment (e.g. '客廳', '冷氣') or a domain (light, climate, media_player, "
        "switch, cover, fan, sensor). Call this first to find the entity_id.",
        {"query": {"type": "string", "description": "Name or entity_id fragment, e.g. 客廳"},
         "domain": {"type": "string", "description": "Domain such as light, climate, switch"}}, []),
    "home_state": _fn(
        "home_state", "Current state and attributes of one device (e.g. temperature, on/off).",
        {"entity_id": {"type": "string"}}, ["entity_id"]),
    "home_control": _fn(
        "home_control",
        "Control one device through Home Assistant. action is the service name: "
        "turn_on, turn_off, toggle, set_temperature (data: {temperature: 26}), "
        "set_hvac_mode (data: {hvac_mode: cool}), open_cover, close_cover, "
        "volume_set (data: {volume_level: 0.3}), media_play_pause… Locks, alarms, "
        "valves and garage doors come back with needs_confirmation and a code: tell the "
        "user to reply '確認 <code>' themselves — never claim it was done.",
        {"entity_id": {"type": "string", "description": "e.g. light.living_room"},
         "action": {"type": "string", "description": "e.g. turn_off, set_temperature"},
         "data": {"type": "object", "description": "Extra service data, e.g. {\"temperature\": 26}"}},
        ["entity_id", "action"]),
    "home_assist": _fn(
        "home_assist",
        "Send a short natural-language command to Home Assistant's own assistant, "
        "e.g. '關客廳燈' or '冷氣調到 26 度'. Fast when device names match; returns "
        "ok=false when it didn't understand — then use home_devices + home_control.",
        {"text": {"type": "string"}}, ["text"]),
    "xiaoai": _fn(
        "xiaoai",
        "Use the 小愛音箱 (Xiao AI speaker). mode='say' speaks the text aloud "
        "(announcements). mode='command' runs the text as if someone said it to 小愛, "
        "e.g. '打開冷氣' — this reaches infrared devices (air-con, TV) that only 小愛 controls.",
        {"text": {"type": "string"},
         "mode": {"type": "string", "enum": ["say", "command"], "description": "say (default) or command"}},
        ["text"]),
}

HOME_TOOLS: List[Tuple[Any, Dict[str, Any], RiskLevel]] = [
    (home_devices, _SCHEMAS["home_devices"], "low"),
    (home_state, _SCHEMAS["home_state"], "low"),
    (home_control, _SCHEMAS["home_control"], "low"),
    (home_assist, _SCHEMAS["home_assist"], "low"),
    (xiaoai, _SCHEMAS["xiaoai"], "low"),
]
