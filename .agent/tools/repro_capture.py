#!/usr/bin/env python3
"""Capture controlled-runner command evidence. Never makes a historical claim."""

from __future__ import annotations
import datetime as dt
import json
import os
import platform
import subprocess
import sys
import time

def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()

def run(command: str) -> dict:
    started = time.monotonic()
    p = subprocess.run(command, shell=True, text=True, capture_output=True)
    return {
        "command": command,
        "exit_code": p.returncode,
        "stdout": p.stdout,
        "stderr": p.stderr,
        "duration_sec": round(time.monotonic() - started, 6),
        "termination_reason": "normal_exit" if p.returncode >= 0 else "signal",
        "timestamp": now(),
    }

def main() -> int:
    if len(sys.argv) < 2:
        print("usage: repro_capture.py 'command' ['command' ...]", file=sys.stderr)
        return 2

    record = {
        "record_id": "repr-local-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "timestamp": now(),
        "runner": {
            "os": platform.platform(),
            "arch": platform.machine(),
            "shell": os.environ.get("SHELL", ""),
            "python_version": sys.version,
            "path": os.environ.get("PATH", ""),
            "working_directory": os.getcwd(),
        },
        "execution": [],
        "authority": "controlled-runner",
        "derivation": "DIRECT",
    }

    for command in sys.argv[1:]:
        result = run(command)
        record["execution"].append(result)
        if result["exit_code"] != 0:
            break

    print(json.dumps(record, indent=2, sort_keys=True))
    return 0 if all(x["exit_code"] == 0 for x in record["execution"]) else 1

if __name__ == "__main__":
    raise SystemExit(main())
