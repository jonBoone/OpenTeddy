"""
"我要列印" + an attached file → print it now, no model call.

A short message whose point is printing ("列印", "幫我印 2 份 雙面",
"print this", "印第 1 到 3 頁 黑白") is answered directly by the
orchestrator: the files in the attachment manifest (web UI upload,
Telegram file) go to the printer and the reply says so in about a second.
Anything that also asks for work first — "把題庫整理好再印", "translate
and print" — is left to the planner, which has the print_file tool.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from printing import PrintOptions

_MANIFEST_RE = re.compile(r"^\[Attachments in workspace[^\n]*\n(.*?)\n\]\s*", re.S)
_ITEM_RE = re.compile(r"^\s*-\s+(.+?)\s+\(\d+(?:\.\d+)?\s*(?:B|KB|MB|GB)(?:,[^)]*)?\)\s*$")

_VERB = re.compile(r"(列印|打印|印出|印一下|印一份|印給我|幫我印|拿去印|再印|印\s*\d+\s*份|印[這那第]|只印|^\s*(?:請|麻煩)?\s*印(?![表象度尼章記證花鈔泥堂地])|\bprint\b)", re.I)
# Strong enough to reach back for a file uploaded earlier in the session.
_EXPLICIT = re.compile(r"(列印|打印|印出|幫我印|\bprint\b)", re.I)
# Questions about the printer itself are not print jobs.
_ABOUT_PRINTER = re.compile(r"(狀態|碳粉|墨水|墨盒|設定|status|toner|\bink\b|setting)", re.I)
_OTHER_WORK = re.compile(
    r"(總結|摘要|翻譯|整理|改寫|修改|出題|題庫|生成|產生|做成|做一|分析|計算|轉成|轉換|寫|"
    r"summar|translat|rewrite|generat|analy[sz]|convert|create|make|write)", re.I)
_CN_NUM = {"一": 1, "二": 2, "兩": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_MAX_TEXT = 40


@dataclass
class PrintCommand:
    attachments: List[str] = field(default_factory=list)
    options: PrintOptions = field(default_factory=PrintOptions)
    text: str = ""
    explicit: bool = False      # 列印 / 印出 / print — may use an earlier upload


def split_manifest(goal: str) -> Tuple[List[str], str]:
    """(attachment paths, the message without the manifest)."""
    goal = goal or ""
    m = _MANIFEST_RE.match(goal)
    if not m:
        return [], goal.strip()
    paths = [im.group(1) for line in m.group(1).splitlines() if (im := _ITEM_RE.match(line))]
    return paths, goal[m.end():].strip()


def _num(s: str) -> int:
    return int(s) if s.isdigit() else _CN_NUM.get(s, 1)


def parse_options(text: str) -> PrintOptions:
    o = PrintOptions()
    m = re.search(r"(\d+|[一二兩两三四五六七八九十])\s*(?:份|copies|copy)", text, re.I) \
        or re.search(r"[x×]\s*(\d+)\b", text, re.I)
    if m:
        o.copies = _num(m.group(1))
    if re.search(r"雙面|双面|double[- ]?sided|two[- ]?sided|duplex", text, re.I):
        o.duplex = True
    elif re.search(r"單面|单面|one[- ]?sided|single[- ]?sided", text, re.I):
        o.duplex = False
    if re.search(r"黑白|灰階|灰阶|單色|black\s*(?:and|&)\s*white|b&w|grayscale|greyscale|mono", text, re.I):
        o.color = False
    elif re.search(r"彩色|in colou?r", text, re.I):
        o.color = True
    m = re.search(r"第?\s*(\d+)\s*(?:-|~|～|到|至)\s*(\d+)\s*頁", text) \
        or re.search(r"pages?\s*(\d+)\s*(?:-|to)\s*(\d+)", text, re.I)
    if m:
        o.pages = f"{m.group(1)}-{m.group(2)}"
    else:
        m = re.search(r"第\s*(\d+)\s*頁", text) or re.search(r"page\s*(\d+)\b", text, re.I)
        if m:
            o.pages = m.group(1)
    return o


def parse_print_command(goal: str) -> Optional[PrintCommand]:
    """A PrintCommand when the message is just "print (this)", else None."""
    paths, text = split_manifest(goal)
    t = text.strip()
    if (not t or len(t) > _MAX_TEXT or not _VERB.search(t)
            or _OTHER_WORK.search(t) or _ABOUT_PRINTER.search(t)):
        return None
    return PrintCommand(attachments=paths, options=parse_options(t), text=t,
                        explicit=bool(_EXPLICIT.search(t)))
