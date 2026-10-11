"""
Printing through CUPS (`lp` / `lpstat` / `ipptool`) — the same on Linux and
macOS. No driver work: CUPS already knows the network printer (on the home
GX10 a driverless queue that cups-browsed created for the Brother
HL-L3270CDW), and it converts PDF / images / text itself. Office files are
turned into PDF with LibreOffice first.

    list_printers()            queues CUPS can print to
    default_printer()          Settings → printer_name, else CUPS default,
                               else the only printer there is
    printer_status(name)       state, alerts and toner levels, asked of the
                               printer itself over IPP when it can be found
    print_file(path, ...)      → {"job_id", "printer", ...}

Everything here is blocking (subprocess); call it through asyncio.to_thread.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from config import config

logger = logging.getLogger(__name__)

DIRECT_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff",
              ".txt", ".text", ".log", ".md", ".csv"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff"}
OFFICE_EXT = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
              ".odt", ".ods", ".odp", ".rtf", ".html", ".htm"}
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_SHEETS = 500                     # pages × copies, when the page count is known

def _env() -> Dict[str, str]:
    """The current environment with a C locale, so lpstat output is
    untranslated and parseable. Built per call, not at import."""
    return {**os.environ, "LC_ALL": "C", "LANG": "C"}
_CJK = re.compile(r"[㐀-鿿]")


class PrintError(Exception):
    """A print request that can't go through, with a message for the user."""


@dataclass
class PrintOptions:
    copies: int = 1
    duplex: Optional[bool] = None    # True two-sided, False one-sided, None = printer default
    color: Optional[bool] = None     # False = black and white
    pages: Optional[str] = None      # "1-3,5"

    def describe(self, zh: bool) -> str:
        bits = []
        if self.copies > 1:
            bits.append(f"{self.copies} 份" if zh else f"{self.copies} copies")
        if self.duplex is True:
            bits.append("雙面" if zh else "two-sided")
        elif self.duplex is False:
            bits.append("單面" if zh else "one-sided")
        if self.color is False:
            bits.append("黑白" if zh else "black and white")
        elif self.color is True:
            bits.append("彩色" if zh else "color")
        if self.pages:
            bits.append(f"第 {self.pages} 頁" if zh else f"pages {self.pages}")
        return "，".join(bits) if zh else ", ".join(bits)


def is_zh(text: str) -> bool:
    return bool(_CJK.search(text or ""))


def _run(args: List[str], timeout: float = 20.0) -> subprocess.CompletedProcess:
    if not shutil.which(args[0]):
        raise PrintError(
            f"這台機器沒有 {args[0]}（CUPS）。Linux 請安裝 cups，macOS 內建。"
        )
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=_env())


# ── Printers ─────────────────────────────────────────────────────────────────

def list_printers() -> List[Dict[str, Any]]:
    """[{name, state, default}] — configured queues plus ones CUPS can
    reach through discovery (lpstat -e)."""
    names: List[str] = []
    r = _run(["lpstat", "-e"])
    names = [n.strip() for n in r.stdout.splitlines() if n.strip()]
    states: Dict[str, str] = {}
    r = _run(["lpstat", "-p"])
    for line in r.stdout.splitlines():
        m = re.match(r"printer (\S+) (?:is |now )?(idle|printing|disabled)", line)
        if m:
            states[m.group(1)] = m.group(2)
            if m.group(1) not in names:
                names.append(m.group(1))
    default = _cups_default()
    return [{"name": n, "state": states.get(n, "unknown"), "default": n == default} for n in names]


def _cups_default() -> str:
    r = _run(["lpstat", "-d"])
    m = re.search(r"system default destination:\s*(\S+)", r.stdout)
    return m.group(1) if m else ""


def default_printer() -> str:
    configured = (getattr(config, "printer_name", "") or "").strip()
    if configured:
        return configured
    default = _cups_default()
    if default:
        return default
    printers = list_printers()
    if len(printers) == 1:
        return printers[0]["name"]
    if not printers:
        raise PrintError("找不到印表機：它可能關機或還在睡眠。打開印表機，等一分鐘讓這台機器重新找到它再試。"
                         "如果一直找不到，確認兩台在同一個網路。")
    names = "、".join(p["name"] for p in printers)
    raise PrintError(f"有多台印表機（{names}），請在 Settings → 印表機 選一台當預設。")


def display_name(name: str) -> str:
    return name.replace("_", " ")


# ── Status (state, alerts, toner) ────────────────────────────────────────────

_uri_cache: Dict[str, Tuple[float, Optional[str]]] = {}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def printer_uri(name: str) -> Optional[str]:
    """The printer's own IPP address, for toner levels a driverless CUPS
    queue doesn't pass through. From the queue's device URI when it is a
    plain ipp:// one, otherwise found on the network (ippfind / avahi)."""
    hit = _uri_cache.get(name)
    if hit and time.monotonic() - hit[0] < 600:
        return hit[1]
    uri: Optional[str] = None
    try:
        r = _run(["lpstat", "-v", name])
        m = re.search(r"device for \S+:\s*(\S+)", r.stdout)
        if m and m.group(1).startswith(("ipp://", "ipps://")):
            uri = m.group(1)
        want = _norm(name)
        if not uri and shutil.which("avahi-browse"):
            r = subprocess.run(["avahi-browse", "-rtp", "_ipp._tcp"], capture_output=True,
                               text=True, timeout=6, env=_env())
            for line in r.stdout.splitlines():
                f = line.split(";")
                if len(f) >= 10 and f[0] == "=" and _norm(f[3].replace("\\032", " ")) == want:
                    rp = re.search(r'"rp=([^"]+)"', f[9])
                    host = f"[{f[7]}]" if ":" in f[7] else f[7]
                    uri = f"ipp://{host}:{f[8]}/{rp.group(1) if rp else 'ipp/print'}"
                    if ":" not in f[7]:
                        break                     # prefer IPv4
        if not uri and shutil.which("ippfind"):
            r = subprocess.run(["ippfind", "-T", "3", "_ipp._tcp", "--exec", "echo", "{service_name}\t{}", ";"],
                               capture_output=True, text=True, timeout=6, env=_env())
            for line in r.stdout.splitlines():
                sname, _, u = line.partition("\t")
                if _norm(sname) == want and u:
                    uri = u
                    break
    except Exception as exc:  # noqa: BLE001
        logger.debug("printer_uri(%s) failed: %s", name, exc)
    _uri_cache[name] = (time.monotonic(), uri)
    return uri


def _ipp_attrs(uri: str) -> Dict[str, str]:
    r = _run(["ipptool", "-T", "5", "-tv", uri, "get-printer-attributes.test"], timeout=10)
    attrs: Dict[str, str] = {}
    for line in r.stdout.splitlines():
        m = re.match(r"\s+([a-z0-9-]+) \([^)]*\) = (.*)$", line)
        if m:
            attrs[m.group(1)] = m.group(2).strip()
    return attrs


def printer_status(name: Optional[str] = None) -> Dict[str, Any]:
    """{name, model, state, reasons, alerts, markers:[{name, level, low}], uri}"""
    name = name or default_printer()
    uri = printer_uri(name) or f"ipp://localhost/printers/{name}"
    attrs = _ipp_attrs(uri) if shutil.which("ipptool") else {}
    names = [x for x in attrs.get("marker-names", "").split(",") if x]
    levels = [x for x in attrs.get("marker-levels", "").split(",") if x]
    lows = [x for x in attrs.get("marker-low-levels", "").split(",") if x]
    markers = []
    for i, n in enumerate(names):
        try:
            markers.append({"name": n, "level": int(levels[i]),
                            "low": int(lows[i]) if i < len(lows) else 10})
        except (IndexError, ValueError):
            continue
    reasons = [x for x in attrs.get("printer-state-reasons", "none").split(",") if x and x != "none"]
    return {
        "name": name,
        "model": attrs.get("printer-make-and-model", "") or display_name(name),
        "state": attrs.get("printer-state", "unknown"),
        "reasons": reasons,
        "alerts": re.sub(r"\[[a-z]{2}\]", "", attrs.get("printer-alert-description", "")).strip(),
        "markers": markers,
        "uri": uri,
    }


_TONER = {"BK": ("黑色", "black"), "K": ("黑色", "black"), "C": ("青色", "cyan"),
          "M": ("洋紅色", "magenta"), "Y": ("黃色", "yellow")}
_REASONS = {
    "media-empty": ("紙匣沒紙了", "out of paper"),
    "media-needed": ("紙匣沒紙了", "out of paper"),
    "media-jam": ("卡紙", "paper jam"),
    "door-open": ("印表機的蓋子沒關", "a cover is open"),
    "cover-open": ("印表機的蓋子沒關", "a cover is open"),
    "offline": ("印表機離線", "the printer is offline"),
    "shutdown": ("印表機關機了", "the printer is off"),
    "toner-empty": ("碳粉用完了", "toner is empty"),
}


def status_warnings(status: Dict[str, Any], zh: bool = True) -> List[str]:
    """Plain-language warnings worth telling the person who just printed."""
    out: List[str] = []
    for r in status.get("reasons", []):
        base = re.sub(r"-(warning|error|report)$", "", r)
        if base in _REASONS:
            out.append(_REASONS[base][0 if zh else 1])
    for m in status.get("markers", []):
        if m["level"] >= 0 and m["level"] <= max(m["low"], 10):
            color = _TONER.get(m["name"].upper(), (m["name"], m["name"]))
            out.append(f"{color[0]}碳粉剩 {m['level']}%，快用完了" if zh
                       else f"{color[1]} toner at {m['level']}%, nearly empty")
    return list(dict.fromkeys(out))


# ── Printing ─────────────────────────────────────────────────────────────────

def _workspace_roots() -> List[str]:
    roots = []
    try:
        from config import effective_workspace_dir
        roots.append(os.path.realpath(effective_workspace_dir()))
    except Exception:  # noqa: BLE001
        pass
    roots.append(os.path.realpath(getattr(config, "agent_workspace_dir", "") or os.getcwd()))
    return roots


def resolve_path(path: str) -> str:
    """Workspace-relative paths (uploads/x.pdf) resolve against the session
    workspace. Only files inside a workspace can be printed: the agent
    must not be able to put arbitrary files from this machine on paper."""
    p = os.path.expanduser((path or "").strip())
    roots = _workspace_roots()
    full = os.path.realpath(p if os.path.isabs(p) else os.path.join(roots[0], p))
    if not any(full == r or full.startswith(r + os.sep) for r in roots):
        raise PrintError(f"只能列印工作區裡的檔案：{path}")
    if not os.path.isfile(full):
        raise PrintError(f"找不到檔案：{path}")
    return full


def _to_pdf(path: str, outdir: str) -> str:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise PrintError("這台機器沒有 LibreOffice，Office 檔案沒辦法直接列印。請先轉成 PDF，或安裝 libreoffice。")
    profile = os.path.join(tempfile.gettempdir(), "openteddy-lo-profile")
    r = subprocess.run([soffice, f"-env:UserInstallation=file://{profile}", "--headless",
                        "--convert-to", "pdf", "--outdir", outdir, path],
                       capture_output=True, text=True, timeout=180, env=_env())
    pdf = os.path.join(outdir, os.path.splitext(os.path.basename(path))[0] + ".pdf")
    if r.returncode != 0 or not os.path.isfile(pdf):
        raise PrintError(f"轉成 PDF 失敗：{(r.stderr or r.stdout).strip()[:200]}")
    return pdf


def _pdf_pages(path: str) -> Optional[int]:
    try:
        from pypdf import PdfReader
        return len(PdfReader(path).pages)
    except Exception:  # noqa: BLE001
        return None


def print_file(path: str, printer: Optional[str] = None,
               opts: Optional[PrintOptions] = None, hold: bool = False) -> Dict[str, Any]:
    """Send one file to the printer. `hold` queues it without printing
    (used to test the queue without spending paper)."""
    opts = opts or PrintOptions()
    full = resolve_path(path)
    ext = os.path.splitext(full)[1].lower()
    if ext not in DIRECT_EXT and ext not in OFFICE_EXT:
        raise PrintError(f"不支援列印 {ext or '這種'} 檔案。可以列印 PDF、圖片、文字檔和 Word / Excel / PowerPoint。")
    if os.path.getsize(full) > MAX_FILE_BYTES:
        raise PrintError("檔案超過 100 MB，太大了。")
    max_copies = int(getattr(config, "print_max_copies", 20) or 20)
    if not 1 <= opts.copies <= max_copies:
        raise PrintError(f"份數要在 1 到 {max_copies} 之間（Settings 可以調整上限）。")
    if opts.pages and not re.fullmatch(r"\d+(-\d+)?(,\d+(-\d+)?)*", opts.pages):
        raise PrintError(f"看不懂頁數範圍：{opts.pages}")
    printer = printer or default_printer()

    with tempfile.TemporaryDirectory(prefix="openteddy-print-") as tmp:
        target = _to_pdf(full, tmp) if ext in OFFICE_EXT else full
        pages = _pdf_pages(target) if target.lower().endswith(".pdf") else None
        if pages and not opts.pages and pages * opts.copies > MAX_SHEETS:
            raise PrintError(f"這份有 {pages} 頁 × {opts.copies} 份，超過一次 {MAX_SHEETS} 頁的上限。可以指定頁數範圍。")
        args = ["lp", "-d", printer, "-n", str(opts.copies), "-t", os.path.basename(full)]
        if opts.duplex is True:
            args += ["-o", "sides=two-sided-long-edge"]
        elif opts.duplex is False:
            args += ["-o", "sides=one-sided"]
        if opts.color is False:
            args += ["-o", "print-color-mode=monochrome"]
        elif opts.color is True:
            args += ["-o", "print-color-mode=color"]
        if opts.pages:
            args += ["-o", f"page-ranges={opts.pages}"]
        if ext in IMAGE_EXT:
            args += ["-o", "fit-to-page"]
        if hold:
            args += ["-H", "hold"]
        args.append(target)
        r = _run(args, timeout=60)
    if r.returncode != 0:
        raise PrintError(f"印表機沒有接受這份工作：{(r.stderr or r.stdout).strip()[:200]}")
    m = re.search(r"request id is (\S+)", r.stdout)
    job = m.group(1) if m else ""
    logger.info("Printed %s on %s (job %s, %s)", full, printer, job, opts)
    return {"job_id": job, "printer": printer, "file": os.path.basename(full),
            "pages": pages, "copies": opts.copies}


def cancel_job(job_id: str) -> bool:
    return _run(["cancel", job_id]).returncode == 0
