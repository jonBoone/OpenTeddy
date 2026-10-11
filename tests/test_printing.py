"""
Printing: "upload + 我要列印" → printer, without paper.

Fake lp / lpstat / ipptool / avahi-browse / soffice / cancel executables go
first on PATH, so everything runs the real code paths and records what
would have been sent to CUPS.

  A. print_intent: which messages are print commands, options, manifest
  B. printing: printers, default, status + toner warnings, lp arguments,
     Office → PDF, guards (workspace only, type, copies, sheets, lp error)
  C. print_tool: tool results
  D. orchestrator: run() routes a print command before any model call;
     _direct_print reply, earlier upload, nothing to print, failure

    .venv/bin/python tests/test_printing.py
"""
from __future__ import annotations

import asyncio, json, logging, os, stat, sys, tempfile, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)

TMP = tempfile.mkdtemp(prefix="ot-print-test-")
BIN, WS, LOG = os.path.join(TMP, "bin"), os.path.join(TMP, "ws"), os.path.join(TMP, "calls.jsonl")
os.makedirs(BIN); os.makedirs(os.path.join(WS, "uploads"))

FAKES = {
    "lpstat": r'''
case "$1" in
  -e) echo Brother_HL_L3270CDW_series ;;
  -p) echo "printer Brother_HL_L3270CDW_series is idle.  enabled since Sat Oct 10" ;;
  -d) echo "no system default destination" ;;
  -v) echo "device for $2: implicitclass://$2/" ;;
esac''',
    "lp": r'''
python3 - "$@" <<'EOF'
import json, os, sys
open(os.environ["FAKE_LOG"], "a").write(json.dumps({"cmd": "lp", "args": sys.argv[1:]}) + "\n")
EOF
if [ -n "$FAKE_LP_FAIL" ]; then echo "lp: The printer or class does not exist." >&2; exit 1; fi
echo "request id is Brother_HL_L3270CDW_series-42 (1 file(s))"''',
    "ipptool": r'''
cat <<'EOF'
        printer-make-and-model (textWithoutLanguage) = Brother HL-L3270CDW series
        printer-state (enum) = idle
        printer-state-reasons (keyword) = toner-low-warning
        printer-alert-description (1setOf textWithLanguage) = 碳粉不足 (BK)[zh]
        marker-levels (1setOf integer) = 10,100,100,90
        marker-low-levels (1setOf integer) = 10,10,10,10
        marker-names (1setOf nameWithoutLanguage) = BK,C,M,Y
EOF''',
    "avahi-browse": r'''
echo '=;eth0;IPv6;Brother\032HL-L3270CDW\032series;_ipp._tcp;local;BRW.local;fe80::1;631;"rp=ipp/print"'
echo '=;eth0;IPv4;Brother\032HL-L3270CDW\032series;_ipp._tcp;local;BRW.local;192.168.50.160;631;"txtvers=1" "rp=ipp/print"' ''',
    "soffice": r'''
outdir=""; file=""
while [ $# -gt 0 ]; do case "$1" in --outdir) outdir="$2"; shift ;; -*) ;; *) file="$1" ;; esac; shift; done
base=$(basename "$file"); printf '%%PDF-1.4\n' > "$outdir/${base%.*}.pdf"
python3 -c "import json,os,sys; open(os.environ['FAKE_LOG'],'a').write(json.dumps({'cmd':'soffice','file':sys.argv[1]})+'\n')" "$file"''',
    "cancel": "exit 0",
}
for name, body in FAKES.items():
    p = os.path.join(BIN, name)
    open(p, "w").write("#!/bin/bash\n" + body.strip() + "\n")
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
os.environ["PATH"] = BIN + os.pathsep + os.environ["PATH"]
os.environ["FAKE_LOG"] = LOG

from config import config, set_session_workspace          # noqa: E402
config.agent_workspace_dir = WS
config.printer_name = ""
config.print_max_copies = 20
set_session_workspace(WS)

import printing                                            # noqa: E402
import print_intent as PI                                  # noqa: E402


def calls(cmd=None):
    if not os.path.exists(LOG):
        return []
    rows = [json.loads(l) for l in open(LOG)]
    return [r for r in rows if cmd is None or r["cmd"] == cmd]


def reset():
    if os.path.exists(LOG):
        os.remove(LOG)
    printing._uri_cache.clear()
    os.environ.pop("FAKE_LP_FAIL", None)


MANIFEST = ("[Attachments in workspace — use these paths directly, do not re-upload:\n"
            "  - uploads/考卷 v2.pdf (1.2 MB, application/pdf)\n"
            "  - uploads/notes.docx (812 B)\n]\n\n")


# ── A ────────────────────────────────────────────────────────────────────────

def test_intent() -> None:
    yes = {"我要列印": (1, None, None, None, True), "幫我印 2 份 雙面": (2, True, None, None, True),
           "印第 1 到 3 頁 黑白": (1, None, False, "1-3", False), "print this": (1, None, None, None, True),
           "可以幫我列印嗎": (1, None, None, None, True), "請印 三份 單面": (3, False, None, None, False),
           "只印第 2 頁": (1, None, None, "2", False), "print 2 copies double-sided": (2, True, None, None, True)}
    for text, (copies, duplex, color, pages, explicit) in yes.items():
        c = PI.parse_print_command(MANIFEST + text)
        assert c, text
        o = c.options
        assert (o.copies, o.duplex, o.color, o.pages, c.explicit) == (copies, duplex, color, pages, explicit), (text, o, c.explicit)
        assert c.attachments == ["uploads/考卷 v2.pdf", "uploads/notes.docx"], c.attachments
    print("  ✓ print commands + copies / duplex / black-and-white / page range; paths with spaces")

    for text in ("列印狀態", "碳粉還有嗎", "printer status", "印表機在哪", "印象中是這樣", "印度的首都",
                 "把題庫整理好再印", "幫我總結這份", "翻譯後列印", "hello",
                 "請根據這份考卷的內容和解答，重新出一份有變化的題庫，然後幫我列印出來給學生"):
        assert PI.parse_print_command(MANIFEST + text) is None, text
    assert PI.parse_print_command("") is None
    print("  ✓ questions about the printer, 印-words and bigger jobs are not print commands")

    paths, rest = PI.split_manifest(MANIFEST + "我要列印")
    assert rest == "我要列印" and len(paths) == 2
    assert PI.split_manifest("沒有附件") == ([], "沒有附件")
    print("  ✓ manifest split (web UI B / KB / MB sizes, optional content type)")


# ── B ────────────────────────────────────────────────────────────────────────

def test_printing() -> None:
    reset()
    ps = printing.list_printers()
    assert ps == [{"name": "Brother_HL_L3270CDW_series", "state": "idle", "default": False}], ps
    assert printing.default_printer() == "Brother_HL_L3270CDW_series"
    config.printer_name = "Office_Printer"
    assert printing.default_printer() == "Office_Printer"
    config.printer_name = ""
    print("  ✓ printers listed; default = Settings, else CUPS default, else the only printer")

    assert printing.printer_uri("Brother_HL_L3270CDW_series") == "ipp://192.168.50.160:631/ipp/print"
    st = printing.printer_status()
    assert st["model"] == "Brother HL-L3270CDW series" and st["reasons"] == ["toner-low-warning"]
    assert st["markers"][0] == {"name": "BK", "level": 10, "low": 10}
    assert printing.status_warnings(st) == ["黑色碳粉剩 10%，快用完了"], printing.status_warnings(st)
    assert printing.status_warnings(st, zh=False) == ["black toner at 10%, nearly empty"]
    st2 = {"reasons": ["media-empty-error", "media-jam"], "markers": [{"name": "C", "level": 90, "low": 10}]}
    assert printing.status_warnings(st2) == ["紙匣沒紙了", "卡紙"]
    print("  ✓ printer found on the network (IPv4 preferred); toner / paper warnings in plain words")

    pdf = os.path.join(WS, "uploads", "考卷 v2.pdf")
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(3):
        w.add_blank_page(width=595, height=842)
    w.write(pdf)
    job = printing.print_file("uploads/考卷 v2.pdf", opts=printing.PrintOptions(copies=2, duplex=True, color=False, pages="1-3"))
    assert job == {"job_id": "Brother_HL_L3270CDW_series-42", "printer": "Brother_HL_L3270CDW_series",
                   "file": "考卷 v2.pdf", "pages": 3, "copies": 2}, job
    args = calls("lp")[-1]["args"]
    assert args[:6] == ["-d", "Brother_HL_L3270CDW_series", "-n", "2", "-t", "考卷 v2.pdf"], args
    for opt in ("sides=two-sided-long-edge", "print-color-mode=monochrome", "page-ranges=1-3"):
        assert opt in args, (opt, args)
    assert args[-1] == os.path.realpath(pdf) and "-H" not in args
    print("  ✓ lp gets printer, copies, title, duplex, black and white, page range")

    open(os.path.join(WS, "uploads", "photo.jpg"), "wb").write(b"\xff\xd8\xff")
    printing.print_file("uploads/photo.jpg", hold=True)
    args = calls("lp")[-1]["args"]
    assert "fit-to-page" in args and args[args.index("-H") + 1] == "hold"
    open(os.path.join(WS, "uploads", "notes.docx"), "wb").write(b"PK")
    printing.print_file("uploads/notes.docx")
    assert calls("soffice")[-1]["file"].endswith("notes.docx")
    assert calls("lp")[-1]["args"][-1].endswith("/notes.pdf")
    print("  ✓ images fit the page; hold queues without printing; Word → PDF via LibreOffice first")

    def refused(path, **kw):
        try:
            printing.print_file(path, **kw)
        except printing.PrintError as exc:
            return str(exc)
        raise AssertionError(f"{path} should be refused")

    n = len(calls("lp"))
    secret = os.path.join(TMP, "secret.txt"); open(secret, "w").write("x")
    assert "工作區" in refused(secret) and "工作區" in refused("../secret.txt")
    assert "找不到" in refused("uploads/missing.pdf")
    open(os.path.join(WS, "app.exe"), "wb").write(b"MZ")
    assert "不支援" in refused("app.exe")
    assert "份數" in refused("uploads/考卷 v2.pdf", opts=printing.PrintOptions(copies=50))
    big = os.path.join(WS, "big.pdf")
    w = PdfWriter()
    for _ in range(260):
        w.add_blank_page(width=595, height=842)
    w.write(big)
    assert "上限" in refused("big.pdf", opts=printing.PrintOptions(copies=2))
    assert len(calls("lp")) == n, "refused jobs never reach lp"
    os.environ["FAKE_LP_FAIL"] = "1"
    assert "沒有接受" in refused("uploads/考卷 v2.pdf")
    os.environ.pop("FAKE_LP_FAIL")
    print("  ✓ refused: outside the workspace, missing, unsupported type, too many copies / sheets, lp error")


# ── C ────────────────────────────────────────────────────────────────────────

async def test_tool() -> None:
    from tools import print_tool
    r = await print_tool.print_file("uploads/考卷 v2.pdf", copies=1, two_sided=False, black_and_white=True)
    assert r["success"] and r["result"]["job_id"].endswith("-42"), r
    assert r["result"]["warnings"] == ["黑色碳粉剩 10%，快用完了"]
    args = calls("lp")[-1]["args"]
    assert "sides=one-sided" in args and "print-color-mode=monochrome" in args
    r = await print_tool.print_file("nope.pdf")
    assert not r["success"] and "找不到" in r["error"]
    r = await print_tool.printer_status()
    assert r["success"] and r["result"]["printers"][0]["name"].startswith("Brother") and r["result"]["warnings"]
    from tool_registry import tool_registry
    tool_registry.auto_register_all()
    assert tool_registry.risk_of("print_file") == "low" and tool_registry.risk_of("printer_status") == "low"
    print("  ✓ print_file / printer_status tools: results, warnings, errors; registered")


# ── D ────────────────────────────────────────────────────────────────────────

class _Tracker:
    def __init__(self, rows=None):
        self.rows, self.status = rows or [], None
    async def get_session(self, sid): return {"id": sid, "workspace_dir": WS, "mode": "code"}
    async def create_task(self, req): pass
    async def update_task_status(self, tid, status, summary=None): self.status = (status, summary)
    async def create_subtask(self, st): pass
    async def update_subtask(self, st): pass
    async def list_tasks(self, limit=50, session_id=None): return self.rows


async def test_orchestrator() -> None:
    from orchestrator import Orchestrator
    from models import TaskRequest, TaskStatus

    async def no_event(*a, **k): pass

    def fake(tracker):
        o = types.SimpleNamespace(tracker=tracker, _emit_task_event=no_event)
        o._recent_uploads = lambda req, **k: Orchestrator._recent_uploads(o, req, **k)
        o._direct_print = lambda req, cmd: Orchestrator._direct_print(o, req, cmd)
        return o

    reset()
    tr = _Tracker()
    res = await Orchestrator.run(fake(tr), TaskRequest(goal=MANIFEST + "我要列印 2 份", session_id="s1"))
    assert res.status == TaskStatus.COMPLETED, res
    s = res.summary
    assert s.startswith("印表機：Brother HL-L3270CDW series"), s
    assert "🖨️ 已送出列印：考卷 v2.pdf（3 頁，2 份），工作編號 42" in s, s
    assert "🖨️ 已送出列印：notes.docx（2 份），工作編號 42" in s, s
    assert "⚠️ 黑色碳粉剩 10%，快用完了" in s
    assert tr.status[0] == TaskStatus.COMPLETED and len(calls("lp")) == 2
    print("  ✓ run(): upload + 我要列印 → printed directly (no planner), job numbers, toner warning")

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    tr = _Tracker(rows=[{"id": "t9", "goal": "謝謝", "created_at": now},
                        {"id": "t8", "goal": MANIFEST + "幫我總結", "created_at": now}])
    res = await Orchestrator.run(fake(tr), TaskRequest(goal="把剛剛的檔案列印", session_id="s1"))
    assert res.status == TaskStatus.COMPLETED and "考卷 v2.pdf" in res.summary and "最近上傳" in res.summary, res.summary
    tr = _Tracker(rows=[{"id": "t8", "goal": MANIFEST + "幫我總結", "created_at": "2020-01-01T00:00:00"}])
    res = await Orchestrator.run(fake(tr), TaskRequest(goal="我要列印", session_id="s1"))
    assert res.summary.startswith("請先上傳"), res.summary
    tr = _Tracker(rows=[{"id": "t8", "goal": MANIFEST + "幫我總結", "created_at": now}])
    res = await Orchestrator.run(fake(tr), TaskRequest(goal="請印 3 份", session_id="s1"))
    assert res.summary.startswith("請先上傳"), "only an explicit print verb reaches back for an old upload"
    print("  ✓ no attachment: latest upload within 2 h with 列印/print; otherwise asks for the file")

    os.environ["FAKE_LP_FAIL"] = "1"
    res = await Orchestrator.run(fake(_Tracker()), TaskRequest(goal=MANIFEST + "print", session_id="s1"))
    os.environ.pop("FAKE_LP_FAIL")
    assert res.status == TaskStatus.FAILED and "❌" in res.summary, res.summary
    sched = TaskRequest(goal=MANIFEST + "我要列印", session_id="s1", context={"triggered_by": "schedule"})
    reached = {}
    o = fake(_Tracker())
    async def boom(*a, **k): reached["planner"] = True; raise RuntimeError("stop here")
    o._direct_print = boom
    try:
        await Orchestrator.run(o, sched)
    except Exception:  # noqa: BLE001 — the fake has no planner; we only care it wasn't the print path
        pass
    assert "planner" not in reached
    print("  ✓ printer refuses → task failed with the reason; scheduled runs never print on their own")


async def main() -> None:
    print("A. print intent"); test_intent()
    print("B. printing (CUPS)"); test_printing()
    print("C. tools"); await test_tool()
    print("D. orchestrator"); await test_orchestrator()
    print("\nALL PRINTING TESTS PASS")


asyncio.run(main())
