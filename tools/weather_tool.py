"""
OpenTeddy Weather Tool — forecasts from Open-Meteo (free, no API key).

Short questions ("桃園今天天氣") are answered before planning
(weather.parse_weather_question). This tool is for weather as one step of
a bigger job: "看明天天氣決定要不要開除濕機", a morning briefing, a trip plan.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple

import weather as W
from tool_registry import RiskLevel, make_result


async def weather_forecast(location: str = "", days: int = 1, start: int = 0,
                           language: str = "zh") -> Dict[str, Any]:
    t0 = time.monotonic()
    q = W.WeatherQuery(place=location or "", start=max(0, min(int(start or 0), 14)),
                       days=max(1, min(int(days or 1), 14)), zh=(language or "zh").lower().startswith("zh"))
    try:
        text = await W.forecast(q)
        return make_result(True, result=text, duration_ms=int((time.monotonic() - t0) * 1000))
    except Exception as exc:  # noqa: BLE001
        return make_result(False, error=f"{type(exc).__name__}: {exc}", duration_ms=int((time.monotonic() - t0) * 1000))


async def typhoon_status(language: str = "zh") -> Dict[str, Any]:
    t0 = time.monotonic()
    try:
        import typhoon
        text = await typhoon.report(zh=(language or "zh").lower().startswith("zh"))
        return make_result(True, result=text, duration_ms=int((time.monotonic() - t0) * 1000))
    except Exception as exc:  # noqa: BLE001
        return make_result(False, error=f"{type(exc).__name__}: {exc}", duration_ms=int((time.monotonic() - t0) * 1000))


_SCHEMA_TY: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "typhoon_status",
        "description": (
            "Active typhoons / tropical storms in the Northwest Pacific right now (Japan "
            "Meteorological Agency): strength, distance from Taiwan, direction, whether each "
            "is approaching and its forecast closest approach. Use for any typhoon question "
            "instead of searching or scraping."
        ),
        "parameters": {"type": "object", "properties": {
            "language": {"type": "string", "description": "zh (default) or en"}}},
    },
}

_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "weather_forecast",
        "description": (
            "Current weather and the daily forecast for a place (Open-Meteo; no API key). "
            "Use this for any weather question instead of searching or scraping websites. "
            "Taiwanese place names in Chinese work (桃園, 台北, 高雄…), as do world cities. "
            "Empty location = the home location."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "City or place, e.g. 桃園, 東京, Paris. Empty for home."},
                "days": {"type": "integer", "description": "How many days to report (1–14). Default 1."},
                "start": {"type": "integer", "description": "0 today, 1 tomorrow, 2 the day after. Default 0."},
                "language": {"type": "string", "description": "zh (default) or en"},
            },
        },
    },
}

WEATHER_TOOLS: List[Tuple[Any, Dict[str, Any], RiskLevel]] = [
    (weather_forecast, _SCHEMA, "low"),
    (typhoon_status, _SCHEMA_TY, "low"),
]
