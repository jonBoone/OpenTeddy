"""
OpenTeddy Print Tool — put a workspace file on paper, check the printer.

"上傳 + 我要列印" never reaches these: the orchestrator prints directly
(print_intent.py). These are for the planner when printing is one step of
a larger job — "整理成題庫再印出來", "print the report you just made".
Low risk: it spends paper, never data; copies and pages are capped in
printing.py, and only files inside the workspace can be printed.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

import printing
from tool_registry import RiskLevel, make_result


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


async def print_file(path: str, copies: int = 1, two_sided: Optional[bool] = None,
                     black_and_white: Optional[bool] = None, pages: Optional[str] = None,
                     printer: Optional[str] = None) -> Dict[str, Any]:
    start = time.monotonic()
    opts = printing.PrintOptions(
        copies=int(copies or 1), duplex=two_sided,
        color=None if black_and_white is None else (not black_and_white),
        pages=(pages or None),
    )
    try:
        job = await asyncio.to_thread(printing.print_file, path, printer or None, opts)
    except printing.PrintError as exc:
        return make_result(False, error=str(exc), duration_ms=_ms(start))
    try:
        st = await asyncio.wait_for(asyncio.to_thread(printing.printer_status, job["printer"]), 8)
        job["warnings"] = printing.status_warnings(st, zh=True)
    except Exception:  # noqa: BLE001 — status is a courtesy, the job is already queued
        job["warnings"] = []
    return make_result(True, result=job, duration_ms=_ms(start))


async def printer_status(printer: Optional[str] = None) -> Dict[str, Any]:
    start = time.monotonic()
    try:
        printers = await asyncio.to_thread(printing.list_printers)
        st = await asyncio.to_thread(printing.printer_status, printer or None)
        st["warnings"] = printing.status_warnings(st, zh=True)
        st["printers"] = printers
        return make_result(True, result=st, duration_ms=_ms(start))
    except printing.PrintError as exc:
        return make_result(False, error=str(exc), duration_ms=_ms(start))
    except Exception as exc:  # noqa: BLE001
        return make_result(False, error=f"{type(exc).__name__}: {exc}", duration_ms=_ms(start))


_SCHEMA_PRINT: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "print_file",
        "description": (
            "Print a file from the workspace on the office printer (the "
            "default printer in Settings). PDF, images, text, Word, Excel "
            "and PowerPoint. Use when the user asks to print something you "
            "have, or something you just produced (write it to a file first). "
            "Returns the print job id and any printer warnings (e.g. low toner) "
            "— tell the user both."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "Workspace-relative path, e.g. uploads/exam.pdf or report.docx"},
                "copies": {"type": "integer", "description": "Number of copies (default 1)."},
                "two_sided": {"type": "boolean", "description": "true = double-sided, false = single-sided; omit for the printer default."},
                "black_and_white": {"type": "boolean", "description": "true to print in black and white."},
                "pages": {"type": "string", "description": "Page range such as '1-3' or '2,5'. Omit for all pages."},
                "printer": {"type": "string", "description": "Printer name; omit for the default printer."},
            },
            "required": ["path"],
        },
    },
}

_SCHEMA_STATUS: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "printer_status",
        "description": (
            "The printer's state, alerts and toner levels, plus the list of "
            "printers this machine can use. For questions like '印表機還有碳粉嗎'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "printer": {"type": "string", "description": "Printer name; omit for the default printer."},
            },
        },
    },
}

PRINT_TOOLS: List[Tuple[Any, Dict[str, Any], RiskLevel]] = [
    (print_file, _SCHEMA_PRINT, "low"),
    (printer_status, _SCHEMA_STATUS, "low"),
]
