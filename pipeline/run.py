#!/usr/bin/env python3
"""Command line entry point for the provenance-preserving pipeline."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import PipelineError, clean_generated, invalidate_downstream, outline_preflight, run_pipeline, validate_mapping_admission, verify_outline_review, write_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Lossless Source.md reconstruction pipeline; no editorial rewriting is performed."
    )
    parser.add_argument("command", choices=["run", "outline-preflight", "review-verify", "mapping-validate", "clean"], help="execute a gated pipeline stage or remove generated artifacts")
    parser.add_argument("--root", default=None, help="repository root (default: parent of pipeline package)")
    parser.add_argument("--source", default="Source.md", help="authoritative source filename")
    parser.add_argument("--outline", default="BOOK_OUTLINE.md", help="authoritative outline filename")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    try:
        if args.command == "clean":
            clean_generated(root)
            print("Generated artifacts removed; Source.md and pipeline contracts were not touched.")
            return 0
        if args.command == "outline-preflight":
            result, _ = outline_preflight(root / args.outline)
            write_json(root / "artifacts" / "analysis" / "OUTLINE_PREFLIGHT.json", result)
            print(f"PIPELINE={result['status']}")
            print("STAGE=outline preflight")
            return 0 if result["status"] == "VERIFIED" else 2
        if args.command == "review-verify":
            invalidate_downstream(root)
            preflight, outline = outline_preflight(root / args.outline)
            write_json(root / "artifacts" / "analysis" / "OUTLINE_PREFLIGHT.json", preflight)
            if preflight["status"] != "VERIFIED" or outline is None:
                result = verify_outline_review(root, outline or {}, "REPOSITORY", root / args.outline)
                print("PIPELINE=BLOCKED")
                print("STAGE=outline preflight")
                return 2
            result = verify_outline_review(root, outline, "REPOSITORY", root / args.outline)
            print(f"PIPELINE={result['status']}")
            print("STAGE=outline review")
            return 0 if result["status"] in {"VERIFIED", "AUTHORIZED"} else 2
        if args.command == "mapping-validate":
            result = validate_mapping_admission(root, args.source, args.outline)
            print(f"PIPELINE={result['status']}")
            print(f"STAGE={result.get('stage', 'mapping validation')}")
            return 0 if result["status"] in {"VERIFIED", "AUTHORIZED"} else 2
        result = run_pipeline(root, args.source, args.outline)
        print(f"PIPELINE={result.get('status', 'BLOCKED')}")
        if result.get("stage"):
            print(f"STAGE={result['stage']}")
        return 0 if result.get("status") in {"VERIFIED", "AUTHORIZED"} else 2
    except PipelineError as exc:
        print(f"PIPELINE=BLOCKED\nERROR={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
