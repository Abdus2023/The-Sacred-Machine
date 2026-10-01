#!/usr/bin/env python3
"""Compute SHA-256 fingerprints for evidence inputs."""

from __future__ import annotations
import hashlib
import json
import pathlib
import sys

def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()

def main() -> int:
    if len(sys.argv) < 2:
        print("usage: hash_evidence.py FILE [FILE ...]", file=sys.stderr)
        return 2
    out = {str(pathlib.Path(p)): sha256_file(pathlib.Path(p)) for p in sys.argv[1:]}
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
