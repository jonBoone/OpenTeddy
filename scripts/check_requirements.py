"""
List requirements.txt entries that aren't installed in this environment.

    .venv/bin/python scripts/check_requirements.py        # exit 1 + the missing lines, or exit 0

Used by `openteddy update` and deploy.sh on every update, not only when
requirements.txt changed in that pull: a venv that missed a package once
(APScheduler on the home GX10) stayed broken through every later update,
and every schedule on that machine silently never ran.

Checks presence, not versions — an installed package with an older
version is left for pip to sort out when requirements.txt changes.
"""
from __future__ import annotations

import os
import re
import sys
from importlib import metadata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def missing(path: str = os.path.join(ROOT, "requirements.txt")) -> list:
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        if ";" in line:                          # environment markers
            req, marker = line.split(";", 1)
            try:
                from packaging.markers import Marker
                if not Marker(marker.strip()).evaluate():
                    continue
            except Exception:  # noqa: BLE001
                pass
            line = req.strip()
        name = re.split(r"[<>=!~\[ ]", line, 1)[0]
        try:
            metadata.version(name)
        except metadata.PackageNotFoundError:
            out.append(line)
    return out


if __name__ == "__main__":
    m = missing(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "requirements.txt"))
    for line in m:
        print(line)
    sys.exit(1 if m else 0)
