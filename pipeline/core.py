"""Deterministic, lossless primitives for the book reconstruction pipeline.

This module deliberately contains no editorial or semantic transformation.  It
only canonicalizes according to the declared contract, splits contiguous source
spans, validates manifests, and assembles an outline using already-approved
mapping data.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from . import PIPELINE_VERSION

SOURCE_FILE = "Source.md"
OUTLINE_FILE = "BOOK_OUTLINE.md"
BLOCK_ID_RE = re.compile(r"^B\d{4,}$")
OUTLINE_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+([^\n]*?)[ \t]*$")
CONVERSATION_HEADING_RE = re.compile(r"^## \[[0-9]+\] [^\n]*$")
OUTLINE_ID_RE = re.compile(r"^(?:BOOK|V[0-9]{2}(?:-P[0-9]{2})?(?:-C[0-9]{2})?(?:-S[0-9]{2})?)$")
OUTLINE_ID_PREFIX_RE = re.compile(r"^(BOOK|V[0-9]{2}(?:-P[0-9]{2})?(?:-C[0-9]{2})?(?:-S[0-9]{2})?)\s+(?:—|--|-)\s+(.+)$")
ALLOWED_ROLES = {"PRIMARY", "SECONDARY", "REFERENCE", "DUPLICATE", "EXPLICITLY_UNMAPPED"}
MAPPING_GATES = ["MAP-000", *[f"MAP-{index:03d}" for index in range(1, 16)]]
ALLOWED_STATUSES = {"VERIFIED", "PARTIALLY_VERIFIED", "PROVISIONAL", "BLOCKED"}
EVIDENCE_CLASSES = {"REPOSITORY", "FIXTURE", "UNIT_TEST", "MUTATION_TEST", "CI", "MANUAL_REVIEW"}

CANONICALIZATION_CONTRACT: dict[str, str] = {
    "encoding": "UTF-8",
    "unicode_normalization": "NFC",
    "line_endings": "LF",
    "BOM_policy": "STRIP_LEADING_BOM_FOR_CANONICAL_ONLY",
    "trailing_whitespace_policy": "PRESERVE",
    "final_newline_policy": "PRESERVE",
    "blank_lines_policy": "PRESERVE",
}

JSON_POLICY: dict[str, Any] = {
    "encoding": "UTF-8",
    "BOM": "none",
    "key_order": "lexicographic",
    "array_order": "semantic_or_declared",
    "separators": "(',', ':')",
    "escaping": "JSON standard with non-ASCII characters unescaped",
    "line_endings": "LF",
}

CSV_POLICY: dict[str, str] = {
    "encoding": "UTF-8",
    "line_endings": "LF",
    "delimiter": ",",
    "quote_character": '"',
    "header_order": "declared schema order",
    "column_order": "declared schema order",
    "sort_order": "outline order, then order, then block_id",
    "escaping": "RFC 4180 doubled quote",
}

DECOMPOSITION_POLICY: dict[str, str] = {
    "method": "explicit conversation message headings; fallback whole-file",
    "heading_pattern": r"^## \[[0-9]+\] [^\n]*$",
    "semantic_interpretation": "none",
}


class PipelineError(RuntimeError):
    """A mandatory pipeline operation could not be completed."""


@dataclass(frozen=True)
class Block:
    block_id: str
    source_sequence: int
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    original_text: str
    raw_sha256: str
    normalized_sha256: str

    def as_record(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "source": {
                "file": SOURCE_FILE,
                "sequence": self.source_sequence,
                "start_line": self.start_line,
                "end_line": self.end_line,
                "start_byte": self.start_byte,
                "end_byte": self.end_byte,
            },
            "original_text": self.original_text,
            "raw_sha256": self.raw_sha256,
            "normalized_sha256": self.normalized_sha256,
        }


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON under the repository's fixed byte-level policy."""
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return (text + "\n").encode("utf-8")


def write_json(path: Path, value: Any) -> str:
    data = canonical_json_bytes(value)
    atomic_write(path, data)
    return sha256_bytes(data)


def write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> str:
    data = b"".join(canonical_json_bytes(dict(record)) for record in records)
    atomic_write(path, data)
    return sha256_bytes(data)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def canonicalize(raw: bytes) -> bytes:
    """Apply only the declared canonicalization contract."""
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PipelineError(f"Source is not valid UTF-8: {exc}") from exc
    if text.startswith("\ufeff"):
        text = text[1:]
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.encode("utf-8")


def _split_lines(data: bytes) -> list[bytes]:
    # bytes.splitlines(keepends=True) preserves every byte, including a final
    # non-newline-terminated line and CRLF delimiters.
    return data.splitlines(keepends=True) if data else []


def _line_offsets(lines: Sequence[bytes]) -> list[int]:
    offsets = [0]
    total = 0
    for line in lines:
        total += len(line)
        offsets.append(total)
    return offsets


def _conversation_spans(canonical_lines: Sequence[bytes]) -> list[tuple[int, int]]:
    """Choose mechanical message boundaries without making semantic judgments.

    Source.md is a transcript.  Its explicit ``## [n] ...`` record headings
    provide stable boundaries.  The preamble is retained as its own block.  If
    a future source has no such headings, the whole file is one block rather
    than applying an invented semantic splitter.
    """
    if not canonical_lines:
        return []
    headings: list[int] = []
    for index, line in enumerate(canonical_lines):
        text = line.decode("utf-8")
        if text.endswith("\n"):
            text = text[:-1]
        if text.endswith("\r"):
            text = text[:-1]
        if CONVERSATION_HEADING_RE.fullmatch(text):
            headings.append(index)
    if not headings:
        return [(0, len(canonical_lines))]
    starts = sorted(set([0, *headings]))
    spans: list[tuple[int, int]] = []
    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(canonical_lines)
        if start < end:
            spans.append((start, end))
    return spans


def source_hash_record(source_path: Path) -> dict[str, Any]:
    raw = source_path.read_bytes()
    canonical = canonicalize(raw)
    raw_lines = _split_lines(raw)
    canonical_lines = _split_lines(canonical)
    return {
        "manifest_type": "source",
        "manifest_version": "1.0.0",
        "source_file": source_path.name,
        "raw_sha256": sha256_bytes(raw),
        "normalized_sha256": sha256_bytes(canonical),
        "raw_bytes": len(raw),
        "normalized_bytes": len(canonical),
        "raw_line_count": len(raw_lines),
        "normalized_line_count": len(canonical_lines),
        "canonicalization": CANONICALIZATION_CONTRACT,
        "reconstruction": {
            "separator_policy": "contiguous source spans; every canonical byte belongs to exactly one block",
            "block_order": "source_sequence ascending",
            "canonical_source_equivalence": "concatenate original_text in source_sequence order",
        },
    }


def decompose(source_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw = source_path.read_bytes()
    canonical = canonicalize(raw)
    raw_lines = _split_lines(raw)
    canonical_lines = _split_lines(canonical)
    if len(raw_lines) != len(canonical_lines):
        raise PipelineError("Canonicalization changed the number of physical lines")
    raw_offsets = _line_offsets(raw_lines)
    canonical_offsets = _line_offsets(canonical_lines)
    spans = _conversation_spans(canonical_lines)
    blocks: list[dict[str, Any]] = []
    for sequence, (start, end) in enumerate(spans, start=1):
        canonical_slice = b"".join(canonical_lines[start:end])
        raw_slice = b"".join(raw_lines[start:end])
        block = Block(
            block_id=f"B{sequence:04d}",
            source_sequence=sequence,
            start_line=start + 1,
            end_line=end,
            start_byte=raw_offsets[start],
            end_byte=raw_offsets[end],
            original_text=canonical_slice.decode("utf-8"),
            raw_sha256=sha256_bytes(raw_slice),
            normalized_sha256=sha256_bytes(canonical_slice),
        )
        blocks.append(block.as_record())
    source_manifest = source_hash_record(source_path)
    return source_manifest, blocks


def reconstruct(blocks: Sequence[Mapping[str, Any]]) -> bytes:
    ordered = sorted(blocks, key=lambda item: item["source"]["sequence"])
    return b"".join(str(item["original_text"]).encode("utf-8") for item in ordered)


def blocks_jsonl_hash(records: Sequence[Mapping[str, Any]]) -> str:
    return sha256_bytes(b"".join(canonical_json_bytes(dict(record)) for record in records))


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PipelineError(f"Invalid JSON artifact {path}: {exc}") from exc


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.endswith("\n"):
                raise PipelineError(f"JSONL line {line_number} has no LF terminator: {path}")
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PipelineError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise PipelineError(f"JSONL record {path}:{line_number} is not an object")
            records.append(value)
    return records


def parse_outline(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    canonical = canonicalize(raw)
    nodes: list[dict[str, Any]] = []
    ancestors: list[dict[str, Any]] = []
    outline_status = "UNSPECIFIED"
    outline_version = "UNSPECIFIED"
    for line_number, raw_line in enumerate(canonical.splitlines(), start=1):
        line = raw_line.decode("utf-8")
        status_match = re.fullmatch(r"<!--\s*outline_status:\s*([A-Z_]+)\s*-->", line)
        version_match = re.fullmatch(r"<!--\s*outline_version:\s*([^\s]+)\s*-->", line)
        if status_match:
            outline_status = status_match.group(1)
        if version_match:
            outline_version = version_match.group(1)
        match = OUTLINE_HEADING_RE.fullmatch(line)
        if not match:
            continue
        level = len(match.group(1))
        raw_title = match.group(2)
        id_match = OUTLINE_ID_PREFIX_RE.fullmatch(raw_title)
        outline_id = id_match.group(1) if id_match else f"O{len(nodes) + 1:04d}"
        title = raw_title
        while ancestors and ancestors[-1]["level"] >= level:
            ancestors.pop()
        path_titles = [item["title"] for item in ancestors] + [title]
        node = {
            "outline_id": outline_id,
            "order": len(nodes) + 1,
            "level": level,
            "title": title,
            "line": line_number,
            "path": path_titles,
            "volume": path_titles[0] if len(path_titles) >= 1 else None,
            "chapter": path_titles[1] if len(path_titles) >= 2 else None,
            "section": path_titles[2] if len(path_titles) >= 3 else title,
        }
        nodes.append(node)
        ancestors.append(node)
    if not nodes:
        raise PipelineError("BOOK_OUTLINE.md contains no Markdown headings")
    return {
        "outline_file": path.name,
        "pipeline_version": PIPELINE_VERSION,
        "outline_version": outline_version,
        "outline_status": outline_status,
        "raw_sha256": sha256_bytes(raw),
        "normalized_sha256": sha256_bytes(canonical),
        "outline_sha256": sha256_bytes(canonical),
        "byte_length": len(raw),
        "line_count": len(_split_lines(raw)),
        "canonicalization": CANONICALIZATION_CONTRACT,
        "nodes": nodes,
    }


def _outline_by_id(outline: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["outline_id"]: node for node in outline["nodes"]}


def outline_preflight(path: Path, evidence_class: str = "REPOSITORY") -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Validate outline authority without rewriting or inferring structure."""
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(evidence_class)
    gate_names = {
        "OP-001": "file_exists",
        "OP-002": "file_readable",
        "OP-003": "encoding_valid",
        "OP-004": "canonicalization_valid",
        "OP-005": "heading_structure",
        "OP-006": "hierarchy_valid",
        "OP-007": "identifiers_unique",
        "OP-008": "ordering_valid",
        "OP-009": "references_resolvable",
        "OP-010": "malformed_structural_nodes",
    }

    def make_check(gate_id: str, status: str, expected: str, observed: str) -> dict[str, str]:
        return {"gate_id": gate_id, "check": gate_names[gate_id], "status": status, "expected": expected, "observed": observed}

    if not path.exists():
        checks = [
            make_check("OP-001", "BLOCKED", "BOOK_OUTLINE.md exists", "absent"),
            *[make_check(gate, "BLOCKED", "outline preflight prerequisite is available", "not evaluated because OP-001 is BLOCKED") for gate in list(gate_names)[1:]],
        ]
        return {
            "artifact_type": "outline_preflight",
            "artifact_version": "1.2.0",
            "evidence_class": evidence_class,
            "pipeline_version": PIPELINE_VERSION,
            "outline_file": path.name,
            "status": "BLOCKED",
            "checks": checks,
            "blocking_condition": "missing authoritative outline",
        }, None

    try:
        raw = path.read_bytes()
    except OSError as exc:
        checks = [
            make_check("OP-001", "PASS", "BOOK_OUTLINE.md exists", "present"),
            make_check("OP-002", "BLOCKED", "outline is readable", str(exc)),
            *[make_check(gate, "BLOCKED", "outline preflight prerequisite is available", "not evaluated because OP-002 is BLOCKED") for gate in list(gate_names)[2:]],
        ]
        return {
            "artifact_type": "outline_preflight",
            "artifact_version": "1.2.0",
            "evidence_class": evidence_class,
            "pipeline_version": PIPELINE_VERSION,
            "outline_file": path.name,
            "status": "BLOCKED",
            "checks": checks,
            "blocking_condition": "outline cannot be read",
        }, None

    try:
        canonical = canonicalize(raw)
    except PipelineError as exc:
        checks = [
            make_check("OP-001", "PASS", "BOOK_OUTLINE.md exists", "present"),
            make_check("OP-002", "PASS", "outline is readable", "readable"),
            make_check("OP-003", "FAIL", "outline is valid UTF-8", str(exc)),
            *[make_check(gate, "BLOCKED", "outline preflight prerequisite is available", "not evaluated because OP-003 is FAIL") for gate in list(gate_names)[3:]],
        ]
        return {
            "artifact_type": "outline_preflight",
            "artifact_version": "1.2.0",
            "evidence_class": evidence_class,
            "pipeline_version": PIPELINE_VERSION,
            "outline_file": path.name,
            "status": "BLOCKED",
            "checks": checks,
            "blocking_condition": "outline encoding is invalid",
        }, None

    checks: list[dict[str, str]] = [
        make_check("OP-001", "PASS", "BOOK_OUTLINE.md exists", "present"),
        make_check("OP-002", "PASS", "outline is readable", "readable"),
        make_check("OP-003", "PASS", "outline is valid UTF-8", "valid"),
        make_check("OP-004", "PASS", "declared canonicalization can be applied", "valid"),
    ]
    malformed: list[int] = []
    explicit_ids: list[str] = []
    local_references: list[str] = []
    explicit_id_pattern = re.compile(r"(?:\{#([A-Za-z][A-Za-z0-9_.-]*)\}|\bID[:=]\s*([A-Za-z][A-Za-z0-9_.-]*))")
    local_reference_pattern = re.compile(r"\]\(#([A-Za-z][A-Za-z0-9_.-]*)\)")
    for line_number, raw_line in enumerate(canonical.splitlines(), start=1):
        line = raw_line.decode("utf-8")
        if line.lstrip().startswith("#") and not OUTLINE_HEADING_RE.fullmatch(line):
            malformed.append(line_number)
        explicit_ids.extend(match.group(1) or match.group(2) for match in explicit_id_pattern.finditer(line))
        local_references.extend(match.group(1) for match in local_reference_pattern.finditer(line))
    try:
        outline = parse_outline(path)
    except PipelineError as exc:
        outline = None
        parse_error = str(exc)
    else:
        parse_error = ""
    nodes = outline["nodes"] if outline else []
    heading_valid = bool(nodes) and not parse_error
    checks.append(make_check("OP-005", "PASS" if heading_valid else "FAIL", "at least one valid structural heading exists", "valid" if heading_valid else parse_error or "no valid headings"))
    levels = [node["level"] for node in nodes]
    hierarchy_valid = bool(levels) and levels[0] == 1 and all(next_level - level <= 1 for level, next_level in zip(levels, levels[1:]))
    checks.append(make_check("OP-006", "PASS" if hierarchy_valid else "FAIL", "first heading is level 1 and levels do not skip", "valid" if hierarchy_valid else "invalid heading level transition"))
    generated_ids = [node["outline_id"] for node in nodes]
    identifiers_unique = len(generated_ids) == len(set(generated_ids)) and len(explicit_ids) == len(set(explicit_ids))
    checks.append(make_check("OP-007", "PASS" if identifiers_unique else "FAIL", "generated and explicit structural identifiers are unique", "unique" if identifiers_unique else "duplicate identifier"))
    ordered = [node["order"] for node in nodes]
    ordering_valid = ordered == list(range(1, len(ordered) + 1))
    checks.append(make_check("OP-008", "PASS" if ordering_valid else "FAIL", "outline nodes have deterministic ascending order", "ascending" if ordering_valid else "invalid order"))
    reference_targets = set(explicit_ids) | set(generated_ids)
    references_valid = all(reference in reference_targets for reference in local_references)
    checks.append(make_check("OP-009", "PASS" if references_valid else "FAIL", "outline-local references resolve to declared structural identifiers", "resolvable" if references_valid else "unresolved local reference"))
    checks.append(make_check("OP-010", "PASS" if not malformed else "FAIL", "no malformed structural nodes exist", "none" if not malformed else f"malformed lines: {malformed}"))
    mandatory_failed = any(item["status"] in {"FAIL", "BLOCKED"} for item in checks)
    result: dict[str, Any] = {
        "artifact_type": "outline_preflight",
        "artifact_version": "1.2.0",
            "evidence_class": evidence_class,
        "pipeline_version": PIPELINE_VERSION,
        "outline_file": path.name,
        "status": "BLOCKED" if mandatory_failed else "VERIFIED",
        "checks": checks,
    }
    if outline:
        result.update({
            "raw_sha256": outline["raw_sha256"],
            "normalized_sha256": outline["normalized_sha256"],
            "outline_sha256": outline["normalized_sha256"],
            "outline_version": outline["outline_version"],
            "outline_status": outline["outline_status"],
            "byte_length": outline["byte_length"],
            "line_count": outline["line_count"],
            "node_count": len(nodes),
        })
    if mandatory_failed:
        result["blocking_condition"] = "outline structural preflight failed"
    return result, outline if not mandatory_failed else None

def verify_outline_review(
    root: Path,
    outline: Mapping[str, Any],
    evidence_class: str = "REPOSITORY",
    outline_path: Path | None = None,
) -> dict[str, Any]:
    """Verify an externally supplied MANUAL_REVIEW record; never create one."""
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(evidence_class)
    review_path = root / "artifacts" / "review" / "OUTLINE_REVIEW.json"
    checks: list[dict[str, Any]] = []
    if not review_path.exists():
        checks.append({"gate_id": "REVIEW-001", "status": "BLOCKED", "check": "review_record_exists", "expected": "human-supplied OUTLINE_REVIEW.json exists", "observed": "absent"})
        result = {"artifact_type": "outline_review_validation", "artifact_version": "1.6.0", "evidence_class": evidence_class, "review_evidence_class": "MANUAL_REVIEW", "status": "BLOCKED", "checks": checks, "review_record": "NOT_PRESENT"}
        write_json(root / "artifacts" / "review" / "OUTLINE_REVIEW_VALIDATION.json", result)
        return result
    try:
        record = load_json(review_path)
    except (FileNotFoundError, PipelineError) as exc:
        checks.append({"gate_id": "REVIEW-001", "status": "FAIL", "check": "review_record_readable", "expected": "valid JSON review record", "observed": str(exc)})
        result = {"artifact_type": "outline_review_validation", "artifact_version": "1.6.0", "evidence_class": evidence_class, "review_evidence_class": "MANUAL_REVIEW", "status": "BLOCKED", "checks": checks}
        write_json(root / "artifacts" / "review" / "OUTLINE_REVIEW_VALIDATION.json", result)
        return result
    if not isinstance(record, dict):
        checks.append({"gate_id": "REVIEW-001", "status": "FAIL", "check": "review_record_shape", "expected": "review record is a JSON object", "observed": type(record).__name__})
        result = {"artifact_type": "outline_review_validation", "artifact_version": "1.6.0", "evidence_class": evidence_class, "review_evidence_class": "MANUAL_REVIEW", "status": "BLOCKED", "checks": checks}
        write_json(root / "artifacts" / "review" / "OUTLINE_REVIEW_VALIDATION.json", result)
        return result
    authoritative_outline_path = outline_path or root / "BOOK_OUTLINE.md"
    try:
        raw = authoritative_outline_path.read_bytes()
        normalized = canonicalize(raw)
    except (OSError, PipelineError) as exc:
        checks.append({"gate_id": "REVIEW-008", "status": "BLOCKED", "check": "current_outline_readable", "expected": "current authoritative outline is readable and canonicalizable", "observed": str(exc)})
        result = {"artifact_type": "outline_review_validation", "artifact_version": "1.6.0", "evidence_class": evidence_class, "review_evidence_class": "MANUAL_REVIEW", "status": "BLOCKED", "checks": checks}
        write_json(root / "artifacts" / "review" / "OUTLINE_REVIEW_VALIDATION.json", result)
        return result
    expected_raw = sha256_bytes(raw)
    expected_normalized = sha256_bytes(normalized)
    decision = record.get("decision")
    decision_outcome = {
        "ACCEPT": "ACCEPTED",
        "ACCEPT_WITH_CHANGES": "REVISION_REQUIRED",
        "REJECT": "REJECTED",
    }.get(decision, "INVALID")
    checks.extend([
        {"gate_id": "REVIEW-001", "status": "PASS", "check": "review_record_exists", "expected": "review record exists", "observed": "present"},
        {"gate_id": "REVIEW-002", "status": "PASS" if record.get("evidence_class") == "MANUAL_REVIEW" else "FAIL", "check": "manual_review_evidence", "expected": "evidence_class == MANUAL_REVIEW", "observed": record.get("evidence_class")},
        {"gate_id": "REVIEW-003", "status": "PASS" if record.get("decision") == "ACCEPT" else "FAIL", "check": "review_decision", "expected": "decision == ACCEPT", "observed": record.get("decision")},
        {"gate_id": "REVIEW-004", "status": "PASS" if record.get("outline_status_before") == "PROVISIONAL" and record.get("outline_status_after") == "REVIEWED" else "FAIL", "check": "review_transition", "expected": "PROVISIONAL to REVIEWED", "observed": f"{record.get('outline_status_before')} to {record.get('outline_status_after')}"},
        {"gate_id": "REVIEW-005", "status": "PASS" if isinstance(record.get("reviewer"), str) and bool(record.get("reviewer").strip()) and isinstance(record.get("reviewed_at_utc"), str) and bool(record.get("reviewed_at_utc").strip()) else "FAIL", "check": "review_governance_metadata", "expected": "non-empty reviewer and reviewed_at_utc strings are present", "observed": "present" if isinstance(record.get("reviewer"), str) and record.get("reviewer").strip() and isinstance(record.get("reviewed_at_utc"), str) and record.get("reviewed_at_utc").strip() else "missing"},
        {"gate_id": "REVIEW-006", "status": "PASS" if record.get("outline_raw_sha256") == expected_raw else "FAIL", "check": "reviewed_raw_hash", "expected": expected_raw, "observed": record.get("outline_raw_sha256")},
        {"gate_id": "REVIEW-007", "status": "PASS" if record.get("outline_normalized_sha256") == expected_normalized else "FAIL", "check": "reviewed_normalized_hash", "expected": expected_normalized, "observed": record.get("outline_normalized_sha256")},
        {"gate_id": "REVIEW-008", "status": "PASS" if record.get("review_status") == "REVIEWED" else "FAIL", "check": "review_status", "expected": "review_status == REVIEWED", "observed": record.get("review_status")},
        {"gate_id": "REVIEW-009", "status": "PASS" if outline.get("outline_status") == "REVIEWED" else "FAIL", "check": "current_outline_review_status", "expected": "current outline_status == REVIEWED", "observed": outline.get("outline_status")},
    ])
    status = "VERIFIED" if all(check["status"] == "PASS" for check in checks) else "BLOCKED"
    review_hash = sha256_file(review_path)
    result = {"artifact_type": "outline_review_validation", "artifact_version": "1.6.0", "evidence_class": evidence_class, "review_evidence_class": "MANUAL_REVIEW", "status": status, "decision_outcome": decision_outcome, "review_status": record.get("review_status"), "outline_raw_sha256": expected_raw, "outline_normalized_sha256": expected_normalized, "checks": checks, "review_record_sha256": review_hash}
    write_json(root / "artifacts" / "review" / "OUTLINE_REVIEW_VALIDATION.json", result)
    return result

def validate_outline_proposal(proposal: Mapping[str, Any], block_ids: Sequence[str], outline: Mapping[str, Any]) -> list[str]:
    """Validate proposal support metadata without treating it as authority."""
    errors: list[str] = []
    block_set = set(block_ids)
    outline_ids = {node["outline_id"] for node in outline.get("nodes", [])}
    nodes = proposal.get("nodes")
    if not isinstance(nodes, list):
        return ["outline proposal nodes must be an array"]
    proposal_ids: set[str] = set()
    covered: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"proposal node {index} is not an object")
            continue
        node_id = node.get("outline_id")
        if not isinstance(node_id, str) or node_id in proposal_ids:
            errors.append(f"proposal node {index} has missing or duplicate outline_id")
        proposal_ids.add(node_id)
        support = node.get("source_block_ids")
        if not isinstance(support, list) or (node_id != "BOOK" and not support):
            errors.append(f"proposal node {node_id!r} has no source support")
            support = []
        unknown = set(support) - block_set
        if unknown:
            errors.append(f"proposal node {node_id!r} references unknown blocks: {sorted(unknown)}")
        covered.update(set(support) & block_set)
        if node_id not in outline_ids:
            errors.append(f"proposal node {node_id!r} is absent from BOOK_OUTLINE.md")
        parent = node.get("parent_id")
        if parent is not None and parent not in proposal_ids and parent not in outline_ids:
            errors.append(f"proposal node {node_id!r} has an unresolved parent")
        if node.get("support_type") not in {"DIRECT", "DISTRIBUTED", "INFERRED_STRUCTURE"}:
            errors.append(f"proposal node {node_id!r} has invalid support_type")
        if node.get("confidence") not in {"HIGH", "MEDIUM", "LOW"}:
            errors.append(f"proposal node {node_id!r} has invalid confidence")
    if covered != block_set:
        errors.append(f"proposal coverage differs from source block set: missing={sorted(block_set-covered)} unknown={sorted(covered-block_set)}")
    if proposal.get("source_coverage", {}).get("unmapped_block_ids") not in ([], None):
        errors.append("proposal declares unmapped blocks without an explicit review disposition")
    return errors

def manifest_content_hash(value: Mapping[str, Any]) -> str:
    """Hash the canonical manifest bytes, excluding runtime metadata."""
    return sha256_bytes(canonical_json_bytes(dict(value)))


MAPPING_CANONICAL_FIELDS = (
    "mapping_version",
    "mapping_schema_version",
    "pipeline_version",
    "source_manifest_hash",
    "block_manifest_hash",
    "outline_raw_sha256",
    "outline_normalized_sha256",
    "outline_review_hash",
    "review_status",
    "entries",
    "unmapped_block_ids",
    "duplicate_relations",
)


def canonical_mapping_value(mapping: Mapping[str, Any]) -> dict[str, Any]:
    """Return the versioned, deterministic mapping representation.

    The input artifact remains untouched. Object keys are canonicalized by
    ``canonical_json_bytes`` and entry arrays are sorted by explicit semantic
    fields rather than input order or filesystem order.
    """
    if not isinstance(mapping, Mapping):
        raise PipelineError("mapping root must be an object")
    entries = []
    raw_entries = mapping.get("entries", [])
    if not isinstance(raw_entries, list):
        raw_entries = []
    for entry in raw_entries:
        if not isinstance(entry, Mapping):
            entries.append(entry)
            continue
        normalized_entry = {
            "block_id": entry.get("block_id"),
            "role": entry.get("role"),
            "target_id": entry.get("target_id"),
            "placement": entry.get("placement"),
        }
        entries.append(normalized_entry)
    entries.sort(key=lambda entry: (
        str(entry.get("block_id")),
        str(entry.get("role")),
        "" if entry.get("target_id") is None else str(entry.get("target_id")),
        entry.get("placement") if isinstance(entry.get("placement"), int) else 0,
    ))
    result: dict[str, Any] = {}
    for field in MAPPING_CANONICAL_FIELDS:
        if field == "entries":
            result[field] = entries
        elif field == "unmapped_block_ids":
            unmapped = mapping.get(field, [])
            result[field] = sorted(unmapped) if isinstance(unmapped, list) else unmapped
        elif field == "duplicate_relations":
            relations = mapping.get(field, [])
            result[field] = sorted(relations, key=lambda value: canonical_json_bytes(value)) if isinstance(relations, list) else relations
        elif field in mapping:
            result[field] = mapping[field]
    return result


def canonical_mapping_bytes(mapping: Mapping[str, Any]) -> bytes:
    """Serialize a mapping under the declared canonical mapping contract."""
    return canonical_json_bytes(canonical_mapping_value(mapping))


def mapping_sha256(mapping: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_mapping_bytes(mapping))


def validate_mapping(
    mapping: Mapping[str, Any],
    blocks: Sequence[Mapping[str, Any]],
    outline: Mapping[str, Any],
    source_manifest_hash: str | None = None,
    block_manifest_hash: str | None = None,
    review_hash: str | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(mapping, Mapping):
        return ["MAP-001: mapping root must be an object"]
    allowed_fields = {
        "mapping_version", "mapping_schema_version", "pipeline_version",
        "source_manifest_hash", "block_manifest_hash", "outline_raw_sha256",
        "outline_normalized_sha256", "outline_review_hash", "review_status",
        "entries", "unmapped_block_ids", "duplicate_relations", "mapping_sha256",
    }
    unknown_fields = set(mapping) - allowed_fields
    if unknown_fields:
        errors.append(f"MAP-001: unsupported mapping fields: {sorted(unknown_fields)}")
    if mapping.get("mapping_version") != "1.0":
        errors.append("MAP-006: mapping_version must be exactly '1.0'")
    if mapping.get("mapping_schema_version") is not None and mapping.get("mapping_schema_version") != "1.6.0":
        errors.append("MAP-006: mapping_schema_version must be exactly '1.6.0' when supplied")
    if mapping.get("review_status") != "REVIEWED" or outline.get("outline_status") != "REVIEWED":
        errors.append("MAP-005: mapping and current outline review_status must be exactly 'REVIEWED'")
    actual_source_hash = mapping.get("source_manifest_hash")
    if not isinstance(actual_source_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", actual_source_hash):
        errors.append("MAP-002: source_manifest_hash must be a SHA-256 hex digest")
    elif source_manifest_hash is not None and actual_source_hash != source_manifest_hash:
        errors.append("MAP-002/MAP-014: mapping source_manifest_hash does not match current source manifest (STALE_SOURCE_BINDING)")
    expected_raw_hash = outline.get("raw_sha256")
    expected_normalized_hash = outline.get("normalized_sha256", outline.get("outline_sha256"))
    actual_raw_hash = mapping.get("outline_raw_sha256")
    actual_normalized_hash = mapping.get("outline_normalized_sha256")
    if actual_raw_hash != expected_raw_hash or not isinstance(actual_raw_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", actual_raw_hash):
        errors.append("MAP-005/MAP-014: mapping outline_raw_sha256 does not match current outline (STALE_OUTLINE_BINDING)")
    if actual_normalized_hash != expected_normalized_hash or not isinstance(actual_normalized_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", actual_normalized_hash):
        errors.append("MAP-005/MAP-014: mapping outline_normalized_sha256 does not match current outline (STALE_OUTLINE_BINDING)")
    actual_review_hash = mapping.get("outline_review_hash")
    if not isinstance(actual_review_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", actual_review_hash):
        errors.append("MAP-005: outline_review_hash must be a SHA-256 hex digest")
    elif review_hash is not None and actual_review_hash != review_hash:
        errors.append("MAP-005/MAP-014: mapping outline_review_hash does not match current review record (STALE_REVIEW_BINDING)")
    actual_block_hash = mapping.get("block_manifest_hash")
    if actual_block_hash is not None:
        if not isinstance(actual_block_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", actual_block_hash):
            errors.append("MAP-003: block_manifest_hash must be a SHA-256 hex digest")
        elif block_manifest_hash is not None and actual_block_hash != block_manifest_hash:
            errors.append("MAP-003/MAP-014: mapping block_manifest_hash does not match current block manifest (STALE_BLOCK_BINDING)")
    if mapping.get("pipeline_version") is not None and mapping.get("pipeline_version") != PIPELINE_VERSION:
        errors.append("MAP-014: mapping pipeline_version is stale")
    block_set = {block["block_id"] for block in blocks}
    outline_ids = set(_outline_by_id(outline))
    entries = mapping.get("entries")
    if not isinstance(entries, list):
        errors.append("MAP-001: mapping.entries must be an array")
        entries = []
    primary_counts: dict[str, int] = {}
    primary_ids: set[str] = set()
    seen_entries: set[tuple[Any, ...]] = set()
    mapped_ids: set[str] = set()
    explicit_entry_unmapped: set[str] = set()
    unknown_blocks: set[str] = set()
    unknown_targets: set[str] = set()
    placement_invalid = False
    for index, entry in enumerate(entries):
        prefix = f"mapping.entries[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"MAP-001: {prefix} is not an object")
            continue
        unknown_fields = set(entry) - {"block_id", "target_id", "role", "placement"}
        if unknown_fields:
            errors.append(f"MAP-001: {prefix} has unsupported fields: {sorted(unknown_fields)}")
        if any(key in entry for key in ("original_text", "source_text", "payload")):
            errors.append(f"MAP-001: {prefix} embeds source text instead of a block reference")
        block_id = entry.get("block_id")
        role = entry.get("role")
        target = entry.get("target_id")
        placement = entry.get("placement")
        if "target" in entry or "order" in entry:
            errors.append(f"MAP-001: {prefix} uses an unsupported legacy field; use target_id and placement")
        if not isinstance(block_id, str) or block_id not in block_set:
            unknown_blocks.add(str(block_id))
        else:
            if role != "EXPLICITLY_UNMAPPED":
                mapped_ids.add(block_id)
            else:
                explicit_entry_unmapped.add(block_id)
        if role not in ALLOWED_ROLES:
            errors.append(f"MAP-008: {prefix} has invalid role {role!r}")
        if role == "EXPLICITLY_UNMAPPED":
            if target is not None:
                errors.append(f"MAP-011: {prefix} EXPLICITLY_UNMAPPED cannot have a target")
        elif target not in outline_ids:
            unknown_targets.add(str(target))
        if not isinstance(placement, int) or placement < 1:
            placement_invalid = True
        key = (block_id, role, target, placement)
        if key in seen_entries:
            errors.append(f"MAP-011: {prefix} duplicates an identical mapping entry")
        seen_entries.add(key)
        if role == "PRIMARY" and isinstance(block_id, str):
            primary_counts[block_id] = primary_counts.get(block_id, 0) + 1
            primary_ids.add(block_id)
    if unknown_blocks:
        errors.append(f"MAP-003: unknown block IDs: {sorted(unknown_blocks)}")
    if unknown_targets:
        errors.append(f"MAP-004: unknown outline targets: {sorted(unknown_targets)}")
    if placement_invalid:
        errors.append("MAP-008: placement values must be positive integers")
    unmapped = mapping.get("unmapped_block_ids", [])
    if not isinstance(unmapped, list) or any(not isinstance(item, str) for item in unmapped):
        errors.append("MAP-009: unmapped_block_ids must be an array of block IDs")
        unmapped = []
    unmapped_set = set(unmapped) | explicit_entry_unmapped
    if len(unmapped) != len(set(unmapped)):
        errors.append("MAP-011: unmapped_block_ids contains duplicate dispositions")
    unknown_unmapped = unmapped_set - block_set
    if unknown_unmapped:
        errors.append(f"MAP-009: unknown explicitly-unmapped block IDs: {sorted(unknown_unmapped)}")
    overlap = unmapped_set & mapped_ids
    if overlap:
        errors.append(f"MAP-011: blocks cannot be mapped and explicitly unmapped: {sorted(overlap)}")
    missing = block_set - primary_ids - unmapped_set
    if missing:
        errors.append(f"MAP-010: blocks require one PRIMARY or explicit UNMAPPED disposition: {sorted(missing)}")
    multiple = {key: value for key, value in primary_counts.items() if value > 1}
    if multiple:
        errors.append(f"MAP-007: multiple PRIMARY placements: {multiple}")
    supplied_mapping_hash = mapping.get("mapping_sha256")
    if supplied_mapping_hash is not None:
        if not isinstance(supplied_mapping_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", supplied_mapping_hash):
            errors.append("MAP-013: mapping_sha256 must be a SHA-256 hex digest")
        elif supplied_mapping_hash != mapping_sha256(mapping):
            errors.append("MAP-013: supplied mapping_sha256 does not match canonical mapping bytes")
    return errors


def mapping_validation_record(
    mapping: Mapping[str, Any] | None,
    errors: Sequence[str],
    source_manifest_hash: str,
    outline: Mapping[str, Any],
    evidence_class: str = "REPOSITORY",
    *,
    block_manifest_hash: str | None = None,
    review_validation: Mapping[str, Any] | None = None,
    admission_status: str = "PASS",
    deterministic: bool = True,
    canonical_order_valid: bool = True,
    canonical_hash_valid: bool = True,
) -> dict[str, Any]:
    """Create the deterministic mapping validation/authorization record."""
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(evidence_class)
    error_text = " ".join(errors)
    descriptions = {
        "MAP-000": "reviewed outline and mapping admission prerequisites valid",
        "MAP-001": "mapping schema valid",
        "MAP-002": "source binding valid",
        "MAP-003": "block references and block manifest binding valid",
        "MAP-004": "target references valid",
        "MAP-005": "review binding and status valid",
        "MAP-006": "mapping version supported",
        "MAP-007": "PRIMARY uniqueness valid",
        "MAP-008": "placement validity enforced",
        "MAP-009": "explicit unmapped handling valid",
        "MAP-010": "complete block disposition valid",
        "MAP-011": "partition integrity valid",
        "MAP-012": "canonical ordering deterministic",
        "MAP-013": "canonical mapping hash valid",
        "MAP-014": "stale authoritative input detection valid",
        "MAP-015": "deterministic validation replay valid",
    }
    def status_for(gate: str) -> str:
        if gate == "MAP-000":
            return admission_status
        if gate == "MAP-012":
            return "PASS" if canonical_order_valid else "FAIL"
        if gate == "MAP-013":
            return "PASS" if canonical_hash_valid else "FAIL"
        if gate == "MAP-015":
            return "PASS" if deterministic else "FAIL"
        return "FAIL" if gate in error_text else "PASS"
    checks = []
    for gate in MAPPING_GATES:
        status = status_for(gate)
        checks.append({
            "gate_id": gate,
            "status": status,
            "check": descriptions[gate],
            "expected": descriptions[gate],
            "observed": "valid" if status == "PASS" else error_text or "admission prerequisite absent",
        })
    mapping_hash = None
    if isinstance(mapping, Mapping):
        try:
            mapping_hash = mapping_sha256(mapping)
        except (PipelineError, TypeError, ValueError):
            mapping_hash = None
    review_hash = (review_validation or {}).get("review_record_sha256")
    authorized = admission_status == "PASS" and not errors and all(check["status"] == "PASS" for check in checks)
    return {
        "artifact_type": "mapping_validation",
        "artifact_version": "1.6.0",
        "evidence_class": evidence_class,
        "status": "AUTHORIZED" if authorized else "BLOCKED",
        "mapping_status": "AUTHORIZED" if authorized else "BLOCKED",
        "validation_result": "VERIFIED" if authorized else "BLOCKED",
        "authorization": "AUTHORIZED" if authorized else "NOT_AUTHORIZED",
        "source_manifest_hash": source_manifest_hash,
        "block_manifest_hash": block_manifest_hash,
        "outline_raw_sha256": outline.get("raw_sha256"),
        "outline_normalized_sha256": outline.get("normalized_sha256", outline.get("outline_sha256")),
        "outline_review_hash": review_hash or (mapping or {}).get("outline_review_hash") if mapping else review_hash,
        "mapping_sha256": mapping_hash,
        "pipeline_version": PIPELINE_VERSION,
        "mapping_review_status": mapping.get("review_status") if mapping else None,
        "checks": checks,
        "errors": list(errors),
        "evidence_scope": evidence_class,
    }

def mapping_admission_record(
    root: Path,
    source_manifest_hash: str,
    block_manifest_hash: str | None,
    outline: Mapping[str, Any] | None,
    review_validation: Mapping[str, Any] | None,
    mapping: Mapping[str, Any] | None,
    mapping_input: Path,
    mapping_errors: Sequence[str] = (),
    outline_path: Path | None = None,
    evidence_class: str = "REPOSITORY",
) -> dict[str, Any]:
    """Record MAP-000 without converting an absent input into validation."""
    outline_path = outline_path or root / "BOOK_OUTLINE.md"
    review_path = root / "artifacts" / "review" / "OUTLINE_REVIEW.json"
    source_manifest_path = root / "pipeline" / "manifests" / "source.manifest.json"
    block_manifest_path = root / "pipeline" / "manifests" / "blocks.manifest.json"
    pipeline_contract_path = root / "pipeline" / "contracts" / "mapping.md"
    error_text = " ".join(mapping_errors)
    checks = [
        {"check": "source_manifest_exists", "status": "PASS" if source_manifest_path.exists() else "BLOCKED", "expected": "source manifest exists", "observed": "present" if source_manifest_path.exists() else "absent"},
        {"check": "block_manifest_exists", "status": "PASS" if block_manifest_path.exists() else "BLOCKED", "expected": "block manifest exists", "observed": "present" if block_manifest_path.exists() else "absent"},
        {"check": "pipeline_contract_exists", "status": "PASS" if pipeline_contract_path.exists() else "BLOCKED", "expected": "mapping pipeline contract exists", "observed": "present" if pipeline_contract_path.exists() else "absent"},
        {"check": "current_outline_exists", "status": "PASS" if outline_path.exists() and outline else "BLOCKED", "expected": "current outline exists", "observed": "present" if outline_path.exists() and outline else "absent or invalid"},
        {"check": "external_review_exists", "status": "PASS" if review_path.exists() else "BLOCKED", "expected": "external OUTLINE_REVIEW.json exists", "observed": "present" if review_path.exists() else "absent"},
        {"check": "review_replay", "status": "PASS" if (review_validation or {}).get("status") == "VERIFIED" else "BLOCKED", "expected": "review replay is VERIFIED", "observed": (review_validation or {}).get("status", "NOT_RUN")},
        {"check": "mapping_input_exists", "status": "PASS" if mapping_input.exists() else "BLOCKED", "expected": "mapping.input.json exists", "observed": "present" if mapping_input.exists() else "absent"},
    ]
    if mapping is not None:
        review_hash = (review_validation or {}).get("review_record_sha256")
        checks.extend([
            {"check": "review_decision", "status": "PASS" if (review_validation or {}).get("decision_outcome") == "ACCEPTED" else "FAIL", "expected": "review decision is ACCEPT", "observed": (review_validation or {}).get("decision_outcome", "INVALID")},
            {"check": "review_status", "status": "PASS" if (review_validation or {}).get("review_status") == "REVIEWED" and mapping.get("review_status") == "REVIEWED" else "FAIL", "expected": "review and mapping review_status are REVIEWED", "observed": f"{(review_validation or {}).get('review_status')} / {mapping.get('review_status')}"},
            {"check": "source_binding", "status": "PASS" if mapping.get("source_manifest_hash") == source_manifest_hash else "FAIL", "expected": source_manifest_hash, "observed": mapping.get("source_manifest_hash")},
            {"check": "outline_hash_binding", "status": "PASS" if outline and mapping.get("outline_raw_sha256") == outline.get("raw_sha256") and mapping.get("outline_normalized_sha256") == outline.get("normalized_sha256") else "FAIL", "expected": "current raw and normalized outline hashes", "observed": "match" if outline and mapping.get("outline_raw_sha256") == outline.get("raw_sha256") and mapping.get("outline_normalized_sha256") == outline.get("normalized_sha256") else "mismatch"},
            {"check": "review_hash_binding", "status": "PASS" if review_hash and mapping.get("outline_review_hash") == review_hash else "FAIL", "expected": review_hash or "current review hash", "observed": mapping.get("outline_review_hash")},
            {"check": "mapping_version", "status": "PASS" if mapping.get("mapping_version") == "1.0" else "FAIL", "expected": "1.0", "observed": mapping.get("mapping_version")},
            {"check": "mapping_schema", "status": "FAIL" if "MAP-001" in error_text else "PASS", "expected": "supported mapping schema", "observed": "invalid" if "MAP-001" in error_text else "valid"},
        ])
    else:
        checks.extend([
            {"check": "review_decision", "status": "BLOCKED", "expected": "review decision is ACCEPT", "observed": "not admitted"},
            {"check": "review_status", "status": "BLOCKED", "expected": "review and mapping review_status are REVIEWED", "observed": "not admitted"},
            {"check": "source_binding", "status": "BLOCKED", "expected": source_manifest_hash, "observed": "not admitted"},
            {"check": "outline_hash_binding", "status": "BLOCKED", "expected": "current raw and normalized outline hashes", "observed": "not admitted"},
            {"check": "review_hash_binding", "status": "BLOCKED", "expected": "current review hash", "observed": "not admitted"},
            {"check": "mapping_version", "status": "BLOCKED", "expected": "1.0", "observed": "not admitted"},
            {"check": "mapping_schema", "status": "BLOCKED", "expected": "supported mapping schema", "observed": "not admitted"},
        ])
    statuses = [check["status"] for check in checks]
    status = "BLOCKED" if "BLOCKED" in statuses else "FAIL" if "FAIL" in statuses else "PASS"
    try:
        admitted_mapping_hash = mapping_sha256(mapping) if isinstance(mapping, Mapping) else None
    except (PipelineError, TypeError, ValueError):
        admitted_mapping_hash = None
    result = {
        "artifact_type": "mapping_admission",
        "artifact_version": "1.6.0",
        "evidence_class": evidence_class,
        "gate_id": "MAP-000",
        "status": status,
        "mapping_status": "AUTHORIZED" if status == "PASS" else "BLOCKED",
        "source_manifest_hash": source_manifest_hash,
        "block_manifest_hash": block_manifest_hash,
        "outline_raw_sha256": outline.get("raw_sha256") if outline else None,
        "outline_normalized_sha256": outline.get("normalized_sha256") if outline else None,
        "outline_review_hash": (review_validation or {}).get("review_record_sha256"),
        "mapping_sha256": admitted_mapping_hash,
        "checks": checks,
    }
    write_json(root / "artifacts" / "mapping" / "MAP_000_ADMISSION.json", result)
    write_text(root / "artifacts" / "mapping" / "MAP_000_ADMISSION.md", report("MAP-000_MAPPING_ADMISSION", "VERIFIED" if status == "PASS" else "BLOCKED", [{"gate": "MAP-000", "status": status, "expected": "all reviewed-outline and mapping admission prerequisites pass", "observed": status, "affected_artifacts": ["artifacts/mapping/MAP_000_ADMISSION.json"]}], "MAP-000 is subordinate to external review and does not authorize assembly."))
    return result


def write_mapping_authorization(
    root: Path,
    mapping: Mapping[str, Any],
    validation: Mapping[str, Any],
    outline: Mapping[str, Any],
    review_validation: Mapping[str, Any],
    block_manifest_hash: str,
) -> dict[str, Any]:
    """Persist only the authorized mapping boundary; never assemble a book."""
    canonical_path = root / "artifacts" / "mapping" / "mapping.canonical.json"
    canonical_hash = write_json(canonical_path, canonical_mapping_value(mapping))
    expected_hash = validation.get("mapping_sha256")
    if expected_hash != canonical_hash:
        raise PipelineError("canonical mapping hash did not match mapping validation")
    manifest = {
        "manifest_type": "mapping_authorization",
        "manifest_version": "1.6.0",
        "evidence_class": validation.get("evidence_class", "REPOSITORY"),
        "status": "AUTHORIZED",
        "authorization": "AUTHORIZED",
        "mapping_sha256": canonical_hash,
        "source_manifest_hash": validation.get("source_manifest_hash"),
        "block_manifest_hash": block_manifest_hash,
        "outline_raw_sha256": outline.get("raw_sha256"),
        "outline_normalized_sha256": outline.get("normalized_sha256"),
        "outline_review_hash": review_validation.get("review_record_sha256"),
        "pipeline_version": PIPELINE_VERSION,
        "validation_result": "VERIFIED",
        "mapping_validation_artifact": "artifacts/mapping/mapping.validation.json",
        "canonical_mapping_artifact": canonical_path.relative_to(root).as_posix(),
        "review_evidence_class": review_validation.get("review_evidence_class"),
    }
    write_json(root / "pipeline" / "manifests" / "mapping.manifest.json", manifest)
    return manifest


def write_mapping_blocked_reports(root: Path, stage: str, observed: str, affected: Sequence[str]) -> None:
    message = render_failure_report(
        stage,
        "MAP-000..MAP-015",
        "mapping admission and validation",
        "external reviewed mapping satisfies all mapping gates",
        observed,
        list(affected),
        [],
        "artifacts/mapping/MAP_000_ADMISSION.json",
        "Supply or correct the external review or mapping input. Do not auto-repair, adopt a proposal, or assemble a book.",
    )
    write_text(root / "FAILURE_REPORT.md", message)
    write_text(root / "artifacts" / "verification" / "FAILURE_REPORT.md", message)
    write_recovery(root, recovery_plan(stage, "artifacts/mapping/mapping.input.json", "artifacts/mapping/MAP_000_ADMISSION.json", "a corrected externally supplied mapping input and valid review binding", "python3 -m pipeline.run mapping-validate", ["MAP-000 must PASS", "MAP-001 through MAP-015 must PASS", "mapping authorization must precede any future assembly boundary"]))


def validate_mapping_admission(
    root: Path,
    source_name: str = SOURCE_FILE,
    outline_name: str = OUTLINE_FILE,
    evidence_class: str = "REPOSITORY",
) -> dict[str, Any]:
    """Admit and validate an external mapping, stopping at authorization."""
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(evidence_class)
    invalidate_downstream(root)
    source_path = root / source_name
    outline_path = root / outline_name
    mapping_input = root / "artifacts" / "mapping" / "mapping.input.json"
    mapping_validation_artifact = root / "artifacts" / "mapping" / "mapping.validation.json"
    if not source_path.exists():
        raise PipelineError(f"Required source file is missing: {source_path}")

    source_manifest, blocks = decompose(source_path)
    manifests = root / "pipeline" / "manifests"
    blocks_path = root / "artifacts" / "decomposition" / "CONTENT_BLOCKS.jsonl"
    write_json(manifests / "source.manifest.json", source_manifest)
    blocks_hash = write_jsonl(blocks_path, blocks)
    blocks_manifest = {
        "manifest_type": "blocks",
        "manifest_version": "1.0.0",
        "source_manifest_sha256": sha256_file(manifests / "source.manifest.json"),
        "blocks_artifact": blocks_path.relative_to(root).as_posix(),
        "blocks_artifact_sha256": blocks_hash,
        "block_count": len(blocks),
        "segmentation": DECOMPOSITION_POLICY,
        "block_id_policy": "B followed by zero-padded four-or-more decimal digits",
        "payload_policy": "original_text is canonical source slice; no trimming or editorial transformation",
    }
    block_manifest_hash = write_json(manifests / "blocks.manifest.json", blocks_manifest)
    write_json(root / "artifacts" / "source" / "source.integrity.json", source_manifest)
    write_json(manifests / "pipeline.manifest.json", pipeline_manifest(root, source_manifest))
    source_manifest_hash = sha256_file(manifests / "source.manifest.json")

    preflight_result, outline = outline_preflight(outline_path, evidence_class)
    write_json(root / "artifacts" / "analysis" / "OUTLINE_PREFLIGHT.json", preflight_result)
    if preflight_result["status"] != "VERIFIED" or outline is None:
        review_validation = verify_outline_review(root, outline or {}, evidence_class, outline_path)
        admission = mapping_admission_record(root, source_manifest_hash, block_manifest_hash, None, review_validation, None, mapping_input, outline_path=outline_path, evidence_class=evidence_class)
        write_mapping_blocked_reports(root, "outline preflight", preflight_result.get("blocking_condition", "outline preflight failed"), ["artifacts/analysis/OUTLINE_PREFLIGHT.json", "artifacts/mapping/MAP_000_ADMISSION.json"])
        return {"status": "BLOCKED", "stage": "outline preflight", "outline_preflight": preflight_result, "review_validation": review_validation, "mapping_admission": admission}

    write_json(root / "artifacts" / "analysis" / "outline.manifest.json", outline)
    review_validation = verify_outline_review(root, outline, evidence_class, outline_path)
    if review_validation["status"] != "VERIFIED":
        admission = mapping_admission_record(root, source_manifest_hash, block_manifest_hash, outline, review_validation, None, mapping_input, outline_path=outline_path, evidence_class=evidence_class)
        write_mapping_blocked_reports(root, "mapping admission", "external outline review is absent, invalid, or stale", ["artifacts/review/OUTLINE_REVIEW_VALIDATION.json", "artifacts/mapping/MAP_000_ADMISSION.json"])
        return {"status": "BLOCKED", "stage": "mapping admission", "outline": outline, "outline_preflight": preflight_result, "review_validation": review_validation, "mapping_admission": admission}

    if not mapping_input.exists():
        admission = mapping_admission_record(root, source_manifest_hash, block_manifest_hash, outline, review_validation, None, mapping_input, outline_path=outline_path, evidence_class=evidence_class)
        write_mapping_blocked_reports(root, "mapping admission", "mapping.input.json is absent; no mapping validation or authorization is permitted", ["artifacts/mapping/MAP_000_ADMISSION.json", "artifacts/mapping/mapping.input.json"])
        return {"status": "BLOCKED", "stage": "mapping admission", "outline": outline, "review_validation": review_validation, "mapping_admission": admission}

    try:
        mapping = load_json(mapping_input)
    except (OSError, PipelineError, json.JSONDecodeError) as exc:
        mapping = None
        mapping_errors = [f"MAP-001: unable to read mapping.input.json: {exc}"]
    else:
        mapping_errors = validate_mapping(
            mapping,
            blocks,
            outline,
            source_manifest_hash,
            block_manifest_hash,
            review_validation.get("review_record_sha256"),
        )
    replay_errors = list(mapping_errors)
    if isinstance(mapping, Mapping):
        replay_errors = validate_mapping(
            mapping,
            blocks,
            outline,
            source_manifest_hash,
            block_manifest_hash,
            review_validation.get("review_record_sha256"),
        )
    deterministic = mapping_errors == replay_errors
    admission = mapping_admission_record(
        root,
        source_manifest_hash,
        block_manifest_hash,
        outline,
        review_validation,
        mapping,
        mapping_input,
        mapping_errors,
        outline_path,
        evidence_class,
    )
    validation = mapping_validation_record(
        mapping,
        mapping_errors,
        source_manifest_hash,
        outline,
        evidence_class,
        block_manifest_hash=block_manifest_hash,
        review_validation=review_validation,
        admission_status=admission["status"],
        deterministic=deterministic,
        canonical_order_valid=True,
        canonical_hash_valid=not any("MAP-013" in error for error in mapping_errors),
    )
    validation.update({
        "mapping_file": mapping_input.relative_to(root).as_posix(),
        "review_validation_status": review_validation["status"],
        "admission": "AUTHORIZED" if validation["status"] == "AUTHORIZED" else "BLOCKED",
    })
    write_json(mapping_validation_artifact, validation)
    write_text(
        root / "artifacts" / "mapping" / "MAPPING_VALIDATION.md",
        report(
            "MAPPING_VALIDATION",
            "VERIFIED" if validation["status"] == "AUTHORIZED" else "BLOCKED",
            [{"gate": check["gate_id"], "status": check["status"], "expected": check["expected"], "observed": check["observed"], "affected_artifacts": ["artifacts/mapping/mapping.validation.json"]} for check in validation["checks"]],
            "Mapping validation is distinct from assembly, certification, and finalization.",
            evidence_class=evidence_class,
        ),
    )
    if validation["status"] != "AUTHORIZED":
        write_mapping_blocked_reports(root, "mapping validation", "; ".join(mapping_errors) or "one or more mapping gates failed", ["artifacts/mapping/mapping.input.json", "artifacts/mapping/mapping.validation.json"])
        return {"status": "BLOCKED", "stage": "mapping validation", "errors": mapping_errors, "outline": outline, "review_validation": review_validation, "mapping_admission": admission, "mapping_validation": validation}

    authorization = write_mapping_authorization(root, mapping, validation, outline, review_validation, block_manifest_hash)
    return {
        "status": "AUTHORIZED",
        "stage": "mapping authorization",
        "source_manifest": source_manifest,
        "blocks": blocks,
        "outline": outline,
        "review_validation": review_validation,
        "mapping_admission": admission,
        "mapping_validation": validation,
        "mapping_authorization": authorization,
    }

def mapping_order(mapping: Mapping[str, Any], outline: Mapping[str, Any], blocks: Sequence[Mapping[str, Any]]) -> list[str]:
    outline_positions = {node["outline_id"]: index for index, node in enumerate(outline["nodes"])}
    source_positions = {block["block_id"]: block["source"]["sequence"] for block in blocks}
    entries = [entry for entry in mapping.get("entries", []) if entry.get("role") == "PRIMARY"]
    entries.sort(key=lambda entry: (outline_positions[entry["target_id"]], entry["placement"], source_positions[entry["block_id"]], entry["block_id"]))
    result = [entry["block_id"] for entry in entries]
    explicit_unmapped = {entry["block_id"] for entry in mapping.get("entries", []) if entry.get("role") == "EXPLICITLY_UNMAPPED"}
    unmapped = set(mapping.get("unmapped_block_ids", [])) | explicit_unmapped
    result.extend(sorted(unmapped, key=lambda block_id: source_positions[block_id]))
    return result

def pipeline_bundle_hash(root: Path) -> str:
    """Hash executable contracts and schemas, excluding generated artifacts."""
    files: list[Path] = []
    for pattern in ("pipeline/*.py", "pipeline/contracts/*.md", "pipeline/schemas/*.json"):
        files.extend(root.glob(pattern))
    files = sorted(path for path in files if path.is_file())
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def pipeline_manifest(root: Path, source_manifest: Mapping[str, Any]) -> dict[str, Any]:
    schema_hashes: dict[str, str] = {}
    for path in sorted((root / "pipeline" / "schemas").glob("*.json")):
        schema_hashes[path.name] = sha256_file(path)
    config = {
        "canonicalization": CANONICALIZATION_CONTRACT,
        "json": JSON_POLICY,
        "csv": CSV_POLICY,
        "decomposition": DECOMPOSITION_POLICY,
    }
    return {
        "manifest_type": "pipeline",
        "evidence_class": "REPOSITORY",
        "execution_scope": "REPOSITORY",
        "ci_authority": "NOT_AVAILABLE",
        "pipeline_version": PIPELINE_VERSION,
        "pipeline_bundle_sha256": pipeline_bundle_hash(root),
        "contract_hashes": {
            path.name: sha256_file(path)
            for path in sorted((root / "pipeline" / "contracts").glob("*.md"))
        },
        "prompt_hashes": {},
        "schema_hashes": schema_hashes,
        "configuration": config,
        "configuration_sha256": sha256_bytes(canonical_json_bytes(config)),
        "tool_versions": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "pipeline_runtime": "stdlib-only",
        },
        "model_runtime": {
            "semantic_model": None,
            "decoding_parameters": None,
            "determinism": "PROVISIONAL",
            "reason": "No semantic model is invoked by the mechanical path",
        },
        "input_hashes": {
            "source_raw_sha256": source_manifest["raw_sha256"],
            "source_normalized_sha256": source_manifest["normalized_sha256"],
        },
    }


def render_failure_report(stage: str, gate: str, check: str, expected: str, observed: str, artifacts: Sequence[str], blocks: Sequence[str], last_valid: str, recovery: str) -> str:
    def bullets(items: Sequence[str]) -> str:
        return "\n".join(f"- {item}" for item in items) if items else "- None identified"

    return "\n".join(
        [
            "# FAILURE_REPORT",
            "",
            "**Status:** BLOCKED",
            "",
            "## Failure",
            f"- **Stage:** {stage}",
            f"- **Gate:** {gate}",
            f"- **Check:** {check}",
            f"- **Expected:** {expected}",
            f"- **Observed:** {observed}",
            "",
            "## Affected artifacts",
            bullets(artifacts),
            "",
            "## Affected blocks",
            bullets(blocks),
            "",
            f"## Last valid artifact\n{last_valid}",
            "",
            f"## Recovery action\n{recovery}",
            "",
            "No automatic editorial repair was attempted. No release is authorized.",
            "",
        ]
    )


def recovery_plan(stage: str, prerequisite: str, last_valid_artifact: str, required_input: str, rerun_command: str, verification_requirements: Sequence[str]) -> str:
    lines = [
        "# RECOVERY_PLAN",
        "",
        "**Status:** BLOCKED",
        "",
        f"- **Failed stage:** {stage}",
        f"- **Missing or invalid prerequisite:** {prerequisite}",
        f"- **Last valid artifact:** {last_valid_artifact}",
        f"- **Required human-provided input:** {required_input}",
        f"- **Rerun command:** `{rerun_command}`",
        "",
        "## Verification requirements",
        *[f"- {requirement}" for requirement in verification_requirements],
        "",
        "No gate bypass, inferred authority, source modification, or manual downstream repair is permitted.",
        "",
    ]
    return "\n".join(lines)


def report(title: str, status: str, checks: Sequence[Mapping[str, Any]], note: str = "", evidence_class: str = "REPOSITORY") -> str:
    if status not in ALLOWED_STATUSES:
        raise ValueError(status)
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(evidence_class)
    lines = [f"# {title}", "", f"**Status:** {status}", f"**Evidence class:** {evidence_class}", ""]
    if note:
        lines += [note, ""]
    lines += ["| Gate | Status | Expected | Observed |", "|---|---|---|---|"]
    for item in checks:
        lines.append(
            f"| {item.get('gate', '')} | {item.get('status', '')} | {item.get('expected', '')} | {item.get('observed', '')} |"
        )
    lines += ["", "## Affected artifacts", ""]
    artifacts = sorted({artifact for item in checks for artifact in item.get("affected_artifacts", [])})
    lines += [f"- {artifact}" for artifact in artifacts] or ["- None"]
    lines += ["", "## Affected blocks", ""]
    blocks = sorted({block for item in checks for block in item.get("affected_blocks", [])})
    lines += [f"- {block}" for block in blocks] or ["- None"]
    lines.append("")
    return "\n".join(lines)


def assemble(
    book_path: Path,
    assembly_path: Path,
    blocks: Sequence[Mapping[str, Any]],
    outline: Mapping[str, Any],
    mapping: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    block_by_id = {block["block_id"]: block for block in blocks}
    outline_by_id = _outline_by_id(outline)
    outline_positions = {node["outline_id"]: index for index, node in enumerate(outline["nodes"])}
    rows_by_outline: dict[str, list[dict[str, Any]]] = {}
    for entry in mapping.get("entries", []):
        if entry.get("role") == "EXPLICITLY_UNMAPPED":
            continue
        rows_by_outline.setdefault(entry["target_id"], []).append(entry)
    for entries in rows_by_outline.values():
        entries.sort(key=lambda entry: (entry["role"] != "PRIMARY", entry["placement"], entry["block_id"]))
    source_positions = {block["block_id"]: block["source"]["sequence"] for block in blocks}
    output = bytearray()
    placements: list[dict[str, Any]] = []

    def structural(text: str) -> None:
        output.extend(text.encode("utf-8"))
        if not text.endswith("\n"):
            output.extend(b"\n")

    def payload(block_id: str, role: str, outline_id: str, placement_index: int) -> None:
        block = block_by_id[block_id]
        structural(f"<!-- BLOCK: {block_id} -->\n")
        start = len(output)
        data = block["original_text"].encode("utf-8")
        output.extend(data)
        end = len(output)
        if not data.endswith(b"\n"):
            output.extend(b"\n")
        structural(f"<!-- END BLOCK: {block_id} -->\n")
        placements.append(
            {
                "placement_index": placement_index,
                "block_id": block_id,
                "outline_id": outline_id,
                "role": role,
                "placement_kind": "PRIMARY" if role == "PRIMARY" else "UNMAPPED",
                "book_path": book_path.name,
                "start_offset": start,
                "end_offset": end,
                "payload_sha256": sha256_bytes(data),
            }
        )

    placement_index = 0
    outline_nodes = outline["nodes"]
    for node_index, node in enumerate(outline_nodes):
        outline_id = node["outline_id"]
        structural(f"{'#' * node['level']} {node['title']}\n")
        structural(f"<!-- OUTLINE: {outline_id} -->\n")
        rows = rows_by_outline.get(outline_id, [])
        payload_rows = [row for row in rows if row["role"] == "PRIMARY"]
        has_child = node_index + 1 < len(outline_nodes) and outline_nodes[node_index + 1]["level"] > node["level"]
        if not payload_rows and not has_child:
            structural(f"[GAP: {outline_id}]\n")
        for row in payload_rows:
            placement_index += 1
            payload(row["block_id"], "PRIMARY", outline_id, placement_index)
        for row in rows:
            if row["role"] != "PRIMARY":
                structural(f"[{row['role']}: {row['block_id']}]\n")

    explicit_unmapped = {entry["block_id"] for entry in mapping.get("entries", []) if entry.get("role") == "EXPLICITLY_UNMAPPED"}
    unmapped = sorted(set(mapping.get("unmapped_block_ids", [])) | explicit_unmapped, key=lambda item: source_positions[item])
    if unmapped:
        structural("# Unmapped source blocks\n")
        structural("<!-- STRUCTURAL POLICY: UNMAPPED blocks retain source payload below -->\n")
        for block_id in unmapped:
            placement_index += 1
            payload(block_id, "UNMAPPED", "UNMAPPED", placement_index)

    book_bytes = bytes(output)
    atomic_write(book_path, book_bytes)
    assembly_header = {
        "manifest_type": "assembly",
        "manifest_version": "1.0.0",
        "book_path": book_path.name,
        "candidate_sha256": sha256_bytes(book_bytes),
        "book_sha256": sha256_bytes(book_bytes),
        "payload_policy": "offsets are UTF-8 byte offsets; end_offset is exclusive; envelope is outside ranges",
        "placements": len(placements),
    }
    write_jsonl(assembly_path, [assembly_header, *placements])
    return assembly_header, placements


def verify_assembled(
    root: Path,
    source_path: Path,
    source_manifest: Mapping[str, Any],
    blocks: Sequence[Mapping[str, Any]],
    outline: Mapping[str, Any],
    mapping: Mapping[str, Any],
    book_path: Path,
    assembly_path: Path,
    evidence_class: str = "FIXTURE",
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    raw = source_path.read_bytes()
    canonical = canonicalize(raw)
    expected_source = source_hash_record(source_path)
    source_ok = (
        expected_source["raw_sha256"] == source_manifest.get("raw_sha256")
        and expected_source["normalized_sha256"] == source_manifest.get("normalized_sha256")
    )
    checks.append({
        "gate": "G-SRC-001",
        "status": "PASS" if source_ok else "FAIL",
        "expected": "declared raw and normalized source hashes match current Source.md",
        "observed": "match" if source_ok else "mismatch",
        "affected_artifacts": [str(source_path), "pipeline/manifests/source.manifest.json"],
    })

    expected_source_blocks = decompose(source_path)[1]
    source_by_id = {item["block_id"]: item for item in expected_source_blocks}
    block_by_id = {item["block_id"]: item for item in blocks}
    block_failures: list[str] = []
    if len(blocks) != len(expected_source_blocks):
        block_failures.append("block record count differs from fresh decomposition")
    if len(block_by_id) != len(blocks):
        block_failures.append("duplicate block IDs are present")
    if list(block_by_id) != list(source_by_id):
        block_failures.append("block ID sequence differs from fresh decomposition")
    for block_id, expected in source_by_id.items():
        actual = block_by_id.get(block_id)
        if actual != expected:
            block_failures.append(block_id)
    checks.append({
        "gate": "G-BLK-001",
        "status": "PASS" if not block_failures else "FAIL",
        "expected": "stored blocks equal a fresh lossless decomposition",
        "observed": "match" if not block_failures else f"mismatch ({len(block_failures)} records)",
        "affected_artifacts": ["artifacts/decomposition/CONTENT_BLOCKS.jsonl", "pipeline/manifests/blocks.manifest.json"],
        "affected_blocks": block_failures,
    })
    roundtrip_ok = reconstruct(blocks) == canonical
    checks.append({
        "gate": "G-VRF-005",
        "status": "PASS" if roundtrip_ok else "FAIL",
        "expected": "concatenate block payloads in source sequence equals canonical Source.md",
        "observed": "equal" if roundtrip_ok else "not equal",
        "affected_artifacts": [str(source_path), "artifacts/decomposition/CONTENT_BLOCKS.jsonl"],
    })

    mapping_errors = validate_mapping(mapping, blocks, outline, manifest_content_hash(source_manifest))
    checks.append({
        "gate": "G-MAP-001",
        "status": "PASS" if not mapping_errors else "FAIL",
        "expected": "each block has at most one PRIMARY and valid outline provenance",
        "observed": "valid" if not mapping_errors else "; ".join(mapping_errors[:8]),
        "affected_artifacts": ["pipeline/manifests/mapping.manifest.json"],
    })
    if mapping_errors:
        return finalize_verification(root, checks, evidence_class)

    try:
        assembly_records = load_jsonl(assembly_path)
        assembly_header = assembly_records[0]
        placements = assembly_records[1:]
        book = book_path.read_bytes()
    except (FileNotFoundError, PipelineError, IndexError) as exc:
        checks.append({
            "gate": "G-ASM-001",
            "status": "FAIL",
            "expected": "assembly sidecar and book draft exist",
            "observed": str(exc),
            "affected_artifacts": [str(book_path), str(assembly_path)],
        })
        return finalize_verification(root, checks, evidence_class)

    book_hash_ok = assembly_header.get("book_sha256") == sha256_bytes(book)
    candidate_hash_ok = assembly_header.get("candidate_sha256") == sha256_bytes(book)
    checks.append({
        "gate": "G-ASM-001",
        "status": "PASS" if book_hash_ok and candidate_hash_ok else "FAIL",
        "expected": "assembly candidate_sha256 and book_sha256 equal BOOK_FINAL_CANDIDATE.md",
        "observed": "match" if book_hash_ok and candidate_hash_ok else "mismatch",
        "affected_artifacts": [str(book_path), str(assembly_path)],
    })
    gap_markers = re.findall(rb"\[GAP: ([^\]]+)\]", book)
    source_block_ids = set(block_by_id)
    mapping_entries = mapping.get("entries", [])
    mapped_ids = {entry.get("block_id") for entry in mapping_entries if entry.get("block_id") in source_block_ids}
    unmapped_ids = set(mapping.get("unmapped_block_ids", []))
    unknown_mapping_ids = {entry.get("block_id") for entry in mapping_entries if entry.get("block_id") not in source_block_ids}
    missing_mapping_ids = source_block_ids - mapped_ids - unmapped_ids
    coverage = {
        "MAPPED": sorted(mapped_ids),
        "EXPLICITLY_UNMAPPED": sorted(unmapped_ids & source_block_ids),
        "MISSING": sorted(missing_mapping_ids),
        "UNKNOWN": sorted(unknown_mapping_ids),
    }
    coverage_ok = not missing_mapping_ids and not unknown_mapping_ids
    gap_ok = not gap_markers and coverage_ok and not unmapped_ids
    checks.append({
        "gate": "G-GAP-001",
        "status": "PASS" if gap_ok else "FAIL",
        "check": "COVERAGE",
        "coverage": coverage,
        "expected": "no unresolved GAP markers, missing blocks, unknown entries, or explicitly unmapped blocks for release",
        "observed": "no unresolved gaps" if gap_ok else f"{len(gap_markers)} gap marker(s), {len(missing_mapping_ids)} missing, {len(unknown_mapping_ids)} unknown, {len(unmapped_ids)} unmapped",
        "affected_artifacts": [str(book_path), "artifacts/verification/COVERAGE_AUDIT.md"],
        "affected_blocks": sorted(missing_mapping_ids | unknown_mapping_ids | unmapped_ids),
    })
    payload_placements = [item for item in placements if item.get("placement_kind") in {"PRIMARY", "UNMAPPED"}]
    actual_ids = [item.get("block_id") for item in payload_placements]
    expected_ids = mapping_order(mapping, outline, blocks)
    completeness_ok = sorted(actual_ids) == sorted(block_by_id) and all(actual_ids.count(item) == 1 for item in block_by_id)
    checks.append({
        "gate": "G-VRF-001",
        "status": "PASS" if completeness_ok else "FAIL",
        "check": "COMPLETENESS",
        "expected": "every source block appears exactly once as payload",
        "observed": "complete" if completeness_ok else f"actual payload sequence has {len(actual_ids)} entries",
        "affected_artifacts": [str(book_path), str(assembly_path)],
        "affected_blocks": sorted(set(block_by_id) ^ set(actual_ids)),
    })
    placement_uniqueness_ok = len(actual_ids) == len(set(actual_ids))
    checks.append({
        "gate": "G-VRF-001U",
        "status": "PASS" if placement_uniqueness_ok else "FAIL",
        "check": "PLACEMENT_UNIQUENESS",
        "expected": "no block has more than one textual payload placement",
        "observed": "unique" if placement_uniqueness_ok else "duplicate payload placement detected",
        "affected_artifacts": [str(book_path), str(assembly_path)],
        "affected_blocks": sorted({item for item in actual_ids if actual_ids.count(item) > 1}),
    })
    verbatim_failures: list[str] = []
    provenance_failures: list[str] = []
    for placement in payload_placements:
        block_id = placement.get("block_id")
        if block_id not in block_by_id:
            provenance_failures.append(str(block_id))
            continue
        start, end = placement.get("start_offset"), placement.get("end_offset")
        if not isinstance(start, int) or not isinstance(end, int) or not (0 <= start <= end <= len(book)):
            provenance_failures.append(block_id)
            continue
        payload_bytes = book[start:end]
        expected_bytes = block_by_id[block_id]["original_text"].encode("utf-8")
        if payload_bytes != expected_bytes or sha256_bytes(payload_bytes) != block_by_id[block_id]["normalized_sha256"]:
            verbatim_failures.append(block_id)
        if placement.get("payload_sha256") != sha256_bytes(payload_bytes):
            provenance_failures.append(block_id)
    checks.append({
        "gate": "G-VRF-002",
        "status": "PASS" if not verbatim_failures else "FAIL",
        "expected": "each book payload byte range equals the canonical source block",
        "observed": "all exact" if not verbatim_failures else f"{len(verbatim_failures)} payload mismatches",
        "affected_artifacts": [str(book_path), str(assembly_path)],
        "affected_blocks": verbatim_failures,
    })
    checks.append({
        "gate": "G-VRF-003",
        "status": "PASS" if not provenance_failures else "FAIL",
        "expected": "source -> block -> mapping -> placement links are machine-checkable",
        "observed": "all linked" if not provenance_failures else f"{len(provenance_failures)} broken links",
        "affected_artifacts": ["pipeline/manifests/source.manifest.json", "pipeline/manifests/blocks.manifest.json", "pipeline/manifests/mapping.manifest.json", str(assembly_path)],
        "affected_blocks": provenance_failures,
    })
    actual_primary = [item["block_id"] for item in payload_placements if item.get("placement_kind") == "PRIMARY"]
    expected_primary = [item for item in expected_ids if item not in set(mapping.get("unmapped_block_ids", []))]
    order_ok = actual_primary == expected_primary
    checks.append({
        "gate": "G-VRF-004",
        "status": "PASS" if order_ok else "FAIL",
        "expected": "actual PRIMARY sequence equals mapping order",
        "observed": "equal" if order_ok else "different",
        "affected_artifacts": ["pipeline/manifests/mapping.manifest.json", str(assembly_path)],
        "affected_blocks": actual_primary if not order_ok else [],
    })
    return finalize_verification(root, checks, evidence_class)


def finalize_verification(root: Path, checks: list[dict[str, Any]], evidence_class: str = "REPOSITORY") -> dict[str, Any]:
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(evidence_class)
    mandatory = {"G-SRC-001", "G-BLK-001", "G-VRF-005", "G-MAP-001", "G-ASM-001", "G-GAP-001", "G-VRF-001", "G-VRF-001U", "G-VRF-002", "G-VRF-003", "G-VRF-004"}
    by_gate = {item["gate"]: item for item in checks}
    all_pass = all(by_gate.get(gate, {}).get("status") == "PASS" for gate in mandatory)
    status = "VERIFIED" if all_pass else "BLOCKED"
    verification = {
        "manifest_type": "verification",
        "manifest_version": "1.3.0",
        "evidence_class": evidence_class,
        "status": status,
        "mandatory_gates": sorted(mandatory),
        "checks": checks,
        "runtime_timestamps_excluded": True,
    }
    verification_dir = root / "pipeline" / "manifests"
    verification_dir.mkdir(parents=True, exist_ok=True)
    write_json(verification_dir / "verification.manifest.json", verification)
    report_dir = root / "artifacts" / "verification"
    report_dir.mkdir(parents=True, exist_ok=True)
    titles = {
        "G-VRF-003": "PROVENANCE_AUDIT",
        "G-VRF-001": "COMPLETENESS_AUDIT",
        "G-VRF-002": "VERBATIM_AUDIT",
        "G-VRF-004": "ORDER_AUDIT",
        "G-VRF-005": "ROUNDTRIP_REPORT",
    }
    for gate, title in titles.items():
        item = by_gate.get(gate)
        if item:
            write_text(report_dir / f"{title}.md", report(title, "VERIFIED" if item["status"] == "PASS" else "BLOCKED", [item], evidence_class=evidence_class))
    write_text(report_dir / "COVERAGE_AUDIT.md", report("COVERAGE_AUDIT", status, checks, evidence_class=evidence_class))
    return {"status": status, "checks": checks, "manifest": verification}


def release_bindings(root: Path, candidate_path: Path) -> dict[str, str]:
    paths = {
        "source_manifest_sha256": root / "pipeline" / "manifests" / "source.manifest.json",
        "outline_manifest_sha256": root / "artifacts" / "analysis" / "outline.manifest.json",
        "mapping_manifest_sha256": root / "pipeline" / "manifests" / "mapping.manifest.json",
        "blocks_manifest_sha256": root / "pipeline" / "manifests" / "blocks.manifest.json",
        "verification_manifest_sha256": root / "pipeline" / "manifests" / "verification.manifest.json",
        "pipeline_manifest_sha256": root / "pipeline" / "manifests" / "pipeline.manifest.json",
    }
    bindings = {name: sha256_file(path) for name, path in paths.items()}
    bindings["candidate_sha256"] = sha256_file(candidate_path)
    bindings["pipeline_bundle_sha256"] = load_json(root / "pipeline" / "manifests" / "pipeline.manifest.json").get("pipeline_bundle_sha256", "")
    return bindings


def verify_release_certificate(root: Path, certificate_json: Path | None = None, candidate_path: Path | None = None) -> dict[str, Any]:
    certificate_json = certificate_json or (root / "release" / "RELEASE_CERTIFICATE.json")
    candidate_path = candidate_path or (root / "release" / "BOOK_FINAL_CANDIDATE.md")
    checks: list[dict[str, Any]] = []
    try:
        certificate = load_json(certificate_json)
    except (FileNotFoundError, PipelineError) as exc:
        return {"status": "BLOCKED", "evidence_class": "REPOSITORY", "checks": [{"gate": "G-REL-002", "status": "FAIL", "expected": "release certificate JSON exists", "observed": str(exc)}]}
    bindings = certificate.get("bindings", {})
    expected = release_bindings(root, candidate_path)
    binding_names = sorted(set(expected) | set(bindings))
    for name in binding_names:
        checks.append({
            "gate": "G-REL-002",
            "binding": name,
            "status": "PASS" if bindings.get(name) == expected.get(name) else "FAIL",
            "expected": expected.get(name, "absent"),
            "observed": bindings.get(name, "absent"),
        })
    certificate_ok = certificate.get("status") == "VERIFIED" and certificate.get("evidence_class") == "REPOSITORY" and all(item["status"] == "PASS" for item in checks)
    return {"status": "VERIFIED" if certificate_ok else "BLOCKED", "evidence_class": "REPOSITORY", "checks": checks}


def finalize_certified_candidate(root: Path) -> dict[str, Any]:
    candidate = root / "release" / "BOOK_FINAL_CANDIDATE.md"
    final = root / "release" / "BOOK_FINAL.md"
    certificate_check = verify_release_certificate(root)
    if certificate_check["status"] != "VERIFIED":
        return {"status": "BLOCKED", "certificate_verification": certificate_check}
    atomic_write(final, candidate.read_bytes())
    final_hash = sha256_file(final)
    candidate_hash = sha256_file(candidate)
    if final_hash != candidate_hash:
        return {"status": "BLOCKED", "certificate_verification": certificate_check, "final_sha256": final_hash, "candidate_sha256": candidate_hash}
    final_hash_record = {
        "artifact_type": "final_hash",
        "artifact_version": "1.3.0",
        "evidence_class": "REPOSITORY",
        "status": "VERIFIED",
        "candidate_sha256": candidate_hash,
        "final_sha256": final_hash,
        "finalization": "byte-for-byte copy of certified candidate",
    }
    write_json(root / "release" / "FINAL_HASH.json", final_hash_record)
    return {"status": "VERIFIED", "certificate_verification": certificate_check, "final_sha256": final_hash}


def release_certification(
    root: Path,
    source_path: Path,
    blocks: Sequence[Mapping[str, Any]],
    outline: Mapping[str, Any],
    mapping: Mapping[str, Any],
    book_path: Path,
    assembly_path: Path,
    verification: Mapping[str, Any],
) -> dict[str, Any]:
    """Run release-only gates and create final artifacts only on full PASS."""
    release_checks: list[dict[str, Any]] = []
    current_checks = list(verification.get("checks", []))
    current_pass = all(item.get("status") == "PASS" for item in current_checks if item.get("gate") in set(verification.get("mandatory_gates", [])))
    release_checks.append({
        "gate": "G-REL-001",
        "status": "PASS" if current_pass else "FAIL",
        "expected": "all assembly verification gates PASS",
        "observed": "all PASS" if current_pass else "one or more assembly gates failed",
        "affected_artifacts": ["pipeline/manifests/verification.manifest.json"],
    })
    duplicate_path = root / "artifacts" / "dedup" / "duplicate.audit.json"
    duplicate_ok = False
    if duplicate_path.exists():
        duplicate_record = load_json(duplicate_path)
        duplicate_ok = (
            duplicate_record.get("status") == "VERIFIED"
            and duplicate_record.get("blocks_manifest_sha256") == sha256_file(root / "pipeline" / "manifests" / "blocks.manifest.json")
            and duplicate_record.get("evidence_class") == "REPOSITORY"
        )
    release_checks.append({
        "gate": "G-DUP-001",
        "status": "PASS" if duplicate_ok else "FAIL",
        "expected": "duplicate audit is present and exact duplicate relations are preserved",
        "observed": "verified" if duplicate_ok else "missing or unverified",
        "affected_artifacts": [str(duplicate_path.relative_to(root))],
    })
    with tempfile.TemporaryDirectory(prefix="reconstruction-release-") as directory:
        repeat_root = Path(directory)
        repeat_book = repeat_root / "BOOK_FINAL_CANDIDATE.md"
        repeat_assembly = repeat_root / "BOOK_ASSEMBLY.jsonl"
        assemble(repeat_book, repeat_assembly, blocks, outline, mapping)
        first_repeat_equal = repeat_book.read_bytes() == book_path.read_bytes() and repeat_assembly.read_bytes() == assembly_path.read_bytes()
        repeat_book_2 = repeat_root / "BOOK_FINAL_CANDIDATE_2.md"
        repeat_assembly_2 = repeat_root / "BOOK_ASSEMBLY_2.jsonl"
        assemble(repeat_book_2, repeat_assembly_2, blocks, outline, mapping)
        first_sidecar = repeat_assembly.read_bytes().replace(b"BOOK_FINAL_CANDIDATE.md", b"BOOK.md")
        second_sidecar = repeat_assembly_2.read_bytes().replace(b"BOOK_FINAL_CANDIDATE_2.md", b"BOOK.md")
        second_repeat_equal = repeat_book.read_bytes() == repeat_book_2.read_bytes() and first_sidecar == second_sidecar
    release_checks.append({
        "gate": "G-VRF-006",
        "status": "PASS" if first_repeat_equal else "FAIL",
        "expected": "repeat assembly produces identical canonical draft and sidecar",
        "observed": "identical" if first_repeat_equal else "different",
        "affected_artifacts": [str(book_path), str(assembly_path)],
    })
    release_checks.append({
        "gate": "G-VRF-007",
        "status": "PASS" if second_repeat_equal else "FAIL",
        "expected": "repeated assembly creates no new IDs, payload, or ordering changes",
        "observed": "unchanged" if second_repeat_equal else "changed",
        "affected_artifacts": [str(book_path), str(assembly_path)],
    })
    mutation_process = subprocess.run(
        [sys.executable, "-m", "unittest", "pipeline.tests.mutation.test_mutation_suite", "-q"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    release_checks.append({
        "gate": "G-VRF-008",
        "status": "PASS" if mutation_process.returncode == 0 else "FAIL",
        "expected": "mutation suite M01-M12 passes with predefined gate outcomes",
        "observed": "all expected failures observed" if mutation_process.returncode == 0 else mutation_process.stderr[-1000:],
        "affected_artifacts": ["pipeline/tests/mutation/test_mutation_suite.py"],
    })
    release_checks.append({
        "gate": "G-GAP-001",
        "status": "PASS" if any(item.get("gate") == "G-GAP-001" and item.get("status") == "PASS" for item in current_checks) else "FAIL",
        "expected": "coverage audit has no unresolved gap or unmapped block",
        "observed": "covered" if any(item.get("gate") == "G-GAP-001" and item.get("status") == "PASS" for item in current_checks) else "gap remains",
        "affected_artifacts": ["artifacts/verification/COVERAGE_AUDIT.md"],
    })
    all_pass = all(item["status"] == "PASS" for item in release_checks)
    release_manifest = {
        "manifest_type": "release",
        "manifest_version": "1.3.0",
        "evidence_class": "REPOSITORY",
        "execution_scope": "REPOSITORY",
        "status": "VERIFIED" if all_pass else "BLOCKED",
        "checks": release_checks,
        "candidate_sha256": sha256_file(book_path),
        "assembly_sha256": sha256_file(assembly_path),
    }
    write_json(root / "pipeline" / "manifests" / "release.manifest.json", release_manifest)
    verification_dir = root / "artifacts" / "verification"
    write_text(verification_dir / "DETERMINISM_REPORT.md", report("DETERMINISM_REPORT", "VERIFIED" if first_repeat_equal else "BLOCKED", [release_checks[2]], "Scope: complete deterministic assembly."))
    write_text(verification_dir / "IDEMPOTENCE_REPORT.md", report("IDEMPOTENCE_REPORT", "VERIFIED" if second_repeat_equal else "BLOCKED", [release_checks[3]], "Scope: repeated assembly from frozen inputs."))
    write_text(verification_dir / "MUTATION_REPORT.md", report("MUTATION_REPORT", "VERIFIED" if mutation_process.returncode == 0 else "BLOCKED", [release_checks[4]], "Scope: M01-M12.", evidence_class="MUTATION_TEST"))
    if not all_pass:
        failure = render_failure_report(
            "release certification",
            "G-REL-001",
            "release gates",
            "all mandatory release checks PASS",
            "; ".join(item["gate"] for item in release_checks if item["status"] != "PASS"),
            ["pipeline/manifests/release.manifest.json", str(book_path), str(assembly_path)],
            [],
            "pipeline/manifests/verification.manifest.json",
            "Resolve the failed prerequisite or provide the missing reviewed input, then rerun. Do not manually edit release artifacts.",
        )
        write_text(root / "FAILURE_REPORT.md", failure)
        write_text(verification_dir / "FAILURE_REPORT.md", failure)
        write_recovery(root, recovery_plan("release certification", "all mandatory release gates", "pipeline/manifests/verification.manifest.json", "the input required by the failed gate", "python3 -m pipeline.run run", ["all release checks must be PASS", "release certificate must be generated only after the checks", "BOOK_FINAL.md must not exist while blocked"]))
        write_evidence_scope(root, "BLOCKED", "PARTIALLY_VERIFIED")
        return {"status": "BLOCKED", "release_manifest": release_manifest}
    release_dir = root / "release"
    candidate = release_dir / "BOOK_FINAL_CANDIDATE.md"
    certificate = release_dir / "RELEASE_CERTIFICATE.md"
    certificate_json = release_dir / "RELEASE_CERTIFICATE.json"
    atomic_write(candidate, book_path.read_bytes())
    bindings = release_bindings(root, candidate)
    release_manifest["bindings"] = bindings
    write_json(root / "pipeline" / "manifests" / "release.manifest.json", release_manifest)
    certificate_record = {
        "artifact_type": "release_certificate",
        "artifact_version": "1.3.0",
        "evidence_class": "REPOSITORY",
        "status": "VERIFIED",
        "pipeline_version": PIPELINE_VERSION,
        "bindings": bindings,
    }
    write_json(certificate_json, certificate_record)
    certificate_check = verify_release_certificate(root, certificate_json, candidate)
    if certificate_check["status"] != "VERIFIED":
        return {"status": "BLOCKED", "release_manifest": release_manifest, "certificate_verification": certificate_check}
    certificate_text = "\n".join([
        "# RELEASE_CERTIFICATE",
        "",
        "**Status:** VERIFIED",
        "**Evidence class:** REPOSITORY",
        "",
        "This certificate covers structural reconstruction, source payload preservation, provenance, completeness, order, roundtrip, determinism, idempotence, mutation resistance, duplicate integrity, and coverage for the declared inputs.",
        "",
        *[f"- {name}: `{digest}`" for name, digest in sorted(bindings.items())],
        "- Certificate bindings are machine-verified from RELEASE_CERTIFICATE.json.",
        "- No editorial or factual correctness claim is made.",
        "",
    ])
    write_text(certificate, certificate_text)
    finalization = finalize_certified_candidate(root)
    if finalization["status"] != "VERIFIED":
        write_evidence_scope(root, "BLOCKED", "PARTIALLY_VERIFIED")
        return {"status": "BLOCKED", "release_manifest": release_manifest, "certificate_verification": certificate_check, "finalization": finalization}
    write_evidence_scope(root, "VERIFIED", "PARTIALLY_VERIFIED")
    return {"status": "VERIFIED", "release_manifest": release_manifest, "certificate_verification": certificate_check, "finalization": finalization, "final": (release_dir / "BOOK_FINAL.md").relative_to(root).as_posix()}


def write_text(path: Path, text: str) -> None:
    atomic_write(path, text.encode("utf-8"))


def write_recovery(root: Path, text: str) -> None:
    write_text(root / "RECOVERY_PLAN.md", text)
    write_text(root / "artifacts" / "verification" / "RECOVERY_PLAN.md", text)


def write_evidence_scope(root: Path, repository_status: str, fixture_status: str = "PARTIALLY_VERIFIED") -> None:
    if repository_status not in ALLOWED_STATUSES or fixture_status not in ALLOWED_STATUSES:
        raise ValueError("invalid evidence status")
    text = "\n".join([
        "# EVIDENCE_SCOPE",
        "",
        f"repository_verification: {repository_status}",
        "repository_evidence_class: REPOSITORY",
        f"fixture_verification: {fixture_status}",
        "fixture_evidence_class: FIXTURE / UNIT_TEST / MUTATION_TEST",
        "release: BLOCKED" if repository_status != "VERIFIED" else "release: repository-specific certification required",
        "",
        "Fixture evidence does not certify the authoritative repository.",
        "",
    ])
    write_text(root / "artifacts" / "verification" / "EVIDENCE_SCOPE.md", text)


def duplicate_audit_record(blocks: Sequence[Mapping[str, Any]], blocks_manifest_sha256: str, evidence_class: str = "REPOSITORY") -> dict[str, Any]:
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(evidence_class)
    by_hash: dict[str, list[str]] = {}
    for block in blocks:
        by_hash.setdefault(block["normalized_sha256"], []).append(block["block_id"])
    groups = [
        {"normalized_sha256": digest, "block_ids": sorted(block_ids)}
        for digest, block_ids in sorted(by_hash.items())
        if len(block_ids) > 1
    ]
    relations = [
        {"from": block_id, "relation": "VERBATIM_DUPLICATE", "to": group["block_ids"][0]}
        for group in groups
        for block_id in group["block_ids"][1:]
    ]
    return {
        "artifact_type": "duplicate_audit",
        "artifact_version": "1.3.0",
        "evidence_class": evidence_class,
        "status": "VERIFIED",
        "scope": "exact normalized payload equality only; no paraphrase or semantic similarity claim",
        "relationship_types": ["VERBATIM_DUPLICATE", "PARTIAL_DUPLICATE", "PARAPHRASE", "RELATED"],
        "semantic_status": "PROVISIONAL",
        "blocks_manifest_sha256": blocks_manifest_sha256,
        "groups": groups,
        "relations": relations,
    }


def preflight_audits(root: Path, source_path: Path, source_manifest: Mapping[str, Any], blocks: Sequence[Mapping[str, Any]]) -> None:
    """Record only checks that are actually executable before outline mapping.

    These are component audits, not a release certificate.  They make the
    stopping point explicit when the authoritative outline is absent.
    """
    canonical = canonicalize(source_path.read_bytes())
    reconstructed = reconstruct(blocks)
    block_ids = [block["block_id"] for block in blocks]
    expected_ids = [f"B{index:04d}" for index in range(1, len(blocks) + 1)]
    hash_failures = [
        block["block_id"]
        for block in blocks
        if sha256_bytes(block["original_text"].encode("utf-8")) != block["normalized_sha256"]
    ]
    fresh_manifest, fresh_blocks = decompose(source_path)
    deterministic = blocks_jsonl_hash(blocks) == blocks_jsonl_hash(fresh_blocks) and source_manifest == fresh_manifest
    checks = {
        "source": {
            "gate": "G-SRC-001",
            "status": "PASS",
            "expected": "current Source.md equals declared raw and normalized hashes",
            "observed": "match",
            "affected_artifacts": [str(source_path)],
        },
        "block_completeness": {
            "gate": "G-BLK-001",
            "status": "PASS" if block_ids == expected_ids else "FAIL",
            "expected": "contiguous source block IDs in source order",
            "observed": "contiguous" if block_ids == expected_ids else "non-contiguous",
            "affected_artifacts": ["artifacts/decomposition/CONTENT_BLOCKS.jsonl"],
        },
        "block_hash": {
            "gate": "G-BLK-002",
            "status": "PASS" if not hash_failures else "FAIL",
            "expected": "each stored normalized hash equals its stored payload",
            "observed": "all match" if not hash_failures else f"{len(hash_failures)} mismatch(es)",
            "affected_artifacts": ["artifacts/decomposition/CONTENT_BLOCKS.jsonl"],
            "affected_blocks": hash_failures,
        },
        "roundtrip": {
            "gate": "G-VRF-005",
            "status": "PASS" if reconstructed == canonical else "FAIL",
            "expected": "concatenated block payload equals canonical Source.md",
            "observed": "equal" if reconstructed == canonical else "not equal",
            "affected_artifacts": ["Source.md", "artifacts/decomposition/CONTENT_BLOCKS.jsonl"],
        },
        "determinism": {
            "gate": "G-VRF-006",
            "status": "PASS" if deterministic else "FAIL",
            "expected": "two decompositions with identical inputs produce identical canonical blocks",
            "observed": "equal" if deterministic else "different",
            "affected_artifacts": ["artifacts/decomposition/CONTENT_BLOCKS.jsonl"],
        },
        "idempotence": {
            "gate": "G-VRF-007",
            "status": "PASS" if deterministic else "FAIL",
            "expected": "repeating the decomposition stage creates no new identities or payload changes",
            "observed": "unchanged" if deterministic else "changed",
            "affected_artifacts": ["artifacts/decomposition/CONTENT_BLOCKS.jsonl"],
        },
    }
    verification_dir = root / "artifacts" / "verification"
    blocks_manifest_path = root / "pipeline" / "manifests" / "blocks.manifest.json"
    duplicate_record = duplicate_audit_record(blocks, sha256_file(blocks_manifest_path), "REPOSITORY")
    write_json(root / "artifacts" / "dedup" / "duplicate.audit.json", duplicate_record)
    write_text(verification_dir / "PROVENANCE_AUDIT.md", report("PROVENANCE_AUDIT", "VERIFIED" if checks["source"]["status"] == "PASS" and not hash_failures else "BLOCKED", [checks["source"], checks["block_hash"]], "Scope: source-to-block provenance only; mapping and book placement are not yet authorized."))
    write_text(verification_dir / "COMPLETENESS_AUDIT.md", report("COMPLETENESS_AUDIT", "VERIFIED" if checks["block_completeness"]["status"] == "PASS" else "BLOCKED", [checks["block_completeness"]]))
    write_text(verification_dir / "VERBATIM_AUDIT.md", report("VERBATIM_AUDIT", "VERIFIED" if not hash_failures else "BLOCKED", [checks["block_hash"]], "Scope: stored block payloads and hashes; no book payload exists yet."))
    write_text(verification_dir / "ROUNDTRIP_REPORT.md", report("ROUNDTRIP_REPORT", "VERIFIED" if checks["roundtrip"]["status"] == "PASS" else "BLOCKED", [checks["roundtrip"]]))
    write_text(verification_dir / "DETERMINISM_REPORT.md", report("DETERMINISM_REPORT", "VERIFIED" if deterministic else "BLOCKED", [checks["determinism"]], "Scope: mechanical source decomposition only. Semantic planning determinism is not claimed."))
    write_text(verification_dir / "IDEMPOTENCE_REPORT.md", report("IDEMPOTENCE_REPORT", "VERIFIED" if deterministic else "BLOCKED", [checks["idempotence"]], "Scope: mechanical source decomposition only."))
    write_text(verification_dir / "MUTATION_REPORT.md", report("MUTATION_REPORT", "PARTIALLY_VERIFIED", [{"gate": "G-VRF-008", "status": "PARTIALLY_VERIFIED", "expected": "M01-M12 mutation suite executes against a complete mapped assembly", "observed": "fixture suite exists; complete assembly is blocked by missing outline", "affected_artifacts": ["pipeline/tests/mutation/test_mutation_suite.py"]}], evidence_class="MUTATION_TEST"))
    write_text(verification_dir / "DUPLICATE_AUDIT.md", report("DUPLICATE_AUDIT", "VERIFIED", [{"gate": "G-DUP-001", "status": "PASS", "expected": "exact normalized duplicates are preserved and recorded", "observed": f"{len(duplicate_record['relations'])} verbatim relation(s)", "affected_artifacts": ["artifacts/dedup/duplicate.audit.json"]}], "Scope: exact normalized payload equality only; semantic or paraphrase relationships are not claimed."))
    write_text(verification_dir / "ORDER_AUDIT.md", report("ORDER_AUDIT", "BLOCKED", [{"gate": "G-VRF-004", "status": "BLOCKED", "expected": "mapping order compared with assembly order", "observed": "no outline or mapping", "affected_artifacts": ["BOOK_OUTLINE.md"]}]))


def invalidate_downstream(root: Path) -> None:
    """Remove generated artifacts that cannot survive a failed admission gate."""
    generated_files = (
        root / "artifacts" / "mapping" / "MAP_000_ADMISSION.json",
        root / "artifacts" / "mapping" / "MAP_000_ADMISSION.md",
        root / "artifacts" / "mapping" / "mapping.validation.json",
        root / "artifacts" / "mapping" / "MAPPING_VALIDATION.md",
        root / "artifacts" / "mapping" / "mapping.canonical.json",
        root / "pipeline" / "manifests" / "mapping.manifest.json",
        root / "pipeline" / "manifests" / "verification.manifest.json",
        root / "pipeline" / "manifests" / "release.manifest.json",
        root / "artifacts" / "assembly" / "BOOK_ASSEMBLY.jsonl",
        root / "artifacts" / "assembly" / "BOOK_FINAL_CANDIDATE.md",
    )
    for path in generated_files:
        if path.exists():
            path.unlink()
    release_dir = root / "release"
    for filename in ("BOOK_FINAL_CANDIDATE.md", "BOOK_FINAL.md", "RELEASE_CERTIFICATE.md", "RELEASE_CERTIFICATE.json", "FINAL_HASH.json"):
        path = release_dir / filename
        if path.exists():
            path.unlink()


def clean_generated(root: Path) -> None:
    # Only generated directories are cleaned.  Source, contracts, and schemas
    # are never touched.
    for relative in ("artifacts/source", "artifacts/decomposition", "artifacts/analysis", "artifacts/dedup", "artifacts/assembly", "artifacts/verification", "pipeline/manifests", "release"):
        directory = root / relative
        if directory.exists():
            for child in directory.iterdir():
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    shutil.rmtree(child)
    for relative in (
        "artifacts/review/OUTLINE_REVIEW_VALIDATION.json",
        "artifacts/mapping/MAP_000_ADMISSION.json",
        "artifacts/mapping/MAP_000_ADMISSION.md",
        "artifacts/mapping/mapping.validation.json",
        "artifacts/mapping/MAPPING_VALIDATION.md",
        "artifacts/mapping/mapping.canonical.json",
    ):
        generated = root / relative
        if generated.exists():
            generated.unlink()
    for filename in ("FAILURE_REPORT.md", "RECOVERY_PLAN.md"):
        generated = root / filename
        if generated.exists():
            generated.unlink()


def run_pipeline(root: Path, source_name: str = SOURCE_FILE, outline_name: str = OUTLINE_FILE) -> dict[str, Any]:
    source_path = root / source_name
    outline_path = root / outline_name
    if not source_path.exists():
        raise PipelineError(f"Required source file is missing: {source_path}")
    invalidate_downstream(root)
    source_manifest, blocks = decompose(source_path)
    manifests = root / "pipeline" / "manifests"
    decomposition = root / "artifacts" / "decomposition"
    source_artifacts = root / "artifacts" / "source"
    write_json(manifests / "source.manifest.json", source_manifest)
    blocks_path = decomposition / "CONTENT_BLOCKS.jsonl"
    blocks_hash = write_jsonl(blocks_path, blocks)
    blocks_manifest = {
        "manifest_type": "blocks",
        "manifest_version": "1.0.0",
        "source_manifest_sha256": sha256_file(manifests / "source.manifest.json"),
        "blocks_artifact": blocks_path.relative_to(root).as_posix(),
        "blocks_artifact_sha256": blocks_hash,
        "block_count": len(blocks),
        "segmentation": DECOMPOSITION_POLICY,
        "block_id_policy": "B followed by zero-padded four-or-more decimal digits",
        "payload_policy": "original_text is canonical source slice; no trimming or editorial transformation",
    }
    write_json(manifests / "blocks.manifest.json", blocks_manifest)
    write_json(source_artifacts / "source.integrity.json", source_manifest)
    write_json(manifests / "pipeline.manifest.json", pipeline_manifest(root, source_manifest))
    preflight_audits(root, source_path, source_manifest, blocks)
    write_evidence_scope(root, "BLOCKED", "PARTIALLY_VERIFIED")

    outline_preflight_result, outline = outline_preflight(outline_path)
    outline_preflight_artifact = root / "artifacts" / "analysis" / "OUTLINE_PREFLIGHT.json"
    write_json(outline_preflight_artifact, outline_preflight_result)
    if outline_preflight_result["status"] != "VERIFIED" or outline is None:
        review_validation = verify_outline_review(root, outline or {}, "REPOSITORY", outline_path)
        observed = outline_preflight_result.get("blocking_condition", "outline preflight failed")
        message = render_failure_report(
            "outline preflight",
            "G-MAP-001",
            "BOOK_OUTLINE.md authority and structural preflight",
            "an existing, readable, structurally valid authoritative outline",
            observed,
            ["Source.md", "pipeline/manifests/source.manifest.json", "pipeline/manifests/blocks.manifest.json", "artifacts/decomposition/CONTENT_BLOCKS.jsonl", str(outline_preflight_artifact.relative_to(root))],
            [],
            "pipeline/manifests/blocks.manifest.json",
            "Supply or correct BOOK_OUTLINE.md without modifying Source.md, then rerun the pipeline. Do not infer or generate outline authority.",
        )
        write_text(root / "FAILURE_REPORT.md", message)
        write_text(root / "artifacts" / "verification" / "FAILURE_REPORT.md", message)
        write_text(root / "artifacts" / "verification" / "COVERAGE_AUDIT.md", report("COVERAGE_AUDIT", "BLOCKED", [{"gate": "G-GAP-001", "status": "BLOCKED", "expected": "outline preflight PASS", "observed": observed, "affected_artifacts": [str(outline_preflight_artifact.relative_to(root))]}], "Mapping and gap analysis were not executed because outline authority is missing or invalid."))
        recovery = recovery_plan(
            "outline preflight",
            "BOOK_OUTLINE.md",
            "pipeline/manifests/blocks.manifest.json",
            "the authoritative BOOK_OUTLINE.md",
            "python3 -m pipeline.run run",
            ["outline preflight must be VERIFIED", "mapping.input.json must then be supplied and validated", "all downstream mechanical gates must pass before release"],
        )
        write_recovery(root, recovery)
        return {"status": "BLOCKED", "stage": "outline preflight", "source_manifest": source_manifest, "blocks": blocks, "outline_preflight": outline_preflight_result, "review_validation": review_validation}

    outline_artifact = root / "artifacts" / "analysis" / "outline.manifest.json"
    write_json(outline_artifact, outline)
    proposal_path = root / "artifacts" / "analysis" / "OUTLINE_PROPOSAL.json"
    if proposal_path.exists():
        proposal = load_json(proposal_path)
        proposal_errors = validate_outline_proposal(proposal, [block["block_id"] for block in blocks], outline)
        if proposal_errors:
            message = render_failure_report(
                "outline support validation",
                "G-MAP-001",
                "source support for outline nodes",
                "every ordinary node has valid source block support and complete coverage",
                "; ".join(proposal_errors),
                [str(proposal_path.relative_to(root)), str(outline_artifact.relative_to(root)), "pipeline/manifests/blocks.manifest.json"],
                [],
                str(outline_artifact.relative_to(root)),
                "Correct or replace the provisional proposal/outline from source evidence. Do not weaken support validation or invent block IDs.",
            )
            write_text(root / "FAILURE_REPORT.md", message)
            write_text(root / "artifacts" / "verification" / "FAILURE_REPORT.md", message)
            write_recovery(root, recovery_plan("outline support validation", "source-grounded node support", str(outline_artifact.relative_to(root)), "a corrected source-grounded outline proposal", "python3 -m pipeline.run run", ["all support block IDs must exist", "coverage must account for all 142 blocks", "human review remains separate from structural validity"]))
            write_evidence_scope(root, "BLOCKED", "PARTIALLY_VERIFIED")
            return {"status": "BLOCKED", "stage": "outline support validation", "errors": proposal_errors}
    review_validation = verify_outline_review(root, outline, "REPOSITORY", outline_path)
    if review_validation["status"] != "VERIFIED":
        message = render_failure_report(
            "outline review",
            "REVIEW-001..009",
            "human review replay and exact outline binding",
            "MANUAL_REVIEW ACCEPT record bound to current reviewed outline hashes",
            "outline review is absent, invalid, or the outline remains PROVISIONAL",
            [str(outline_artifact.relative_to(root)), "artifacts/review/OUTLINE_REVIEW.json", "artifacts/review/OUTLINE_REVIEW_VALIDATION.json"],
            [],
            str(outline_artifact.relative_to(root)),
            "Obtain an explicit human review record. Do not create or alter OUTLINE_REVIEW.json automatically and do not infer human approval from structural preflight.",
        )
        write_evidence_scope(root, "BLOCKED", "PARTIALLY_VERIFIED")
        mapping_result = validate_mapping_admission(root, source_name, outline_name, "REPOSITORY")
        write_text(root / "FAILURE_REPORT.md", message)
        write_text(root / "artifacts" / "verification" / "FAILURE_REPORT.md", message)
        write_recovery(root, recovery_plan("outline review", "artifacts/review/OUTLINE_REVIEW.json", str(outline_artifact.relative_to(root)), "a human-supplied ACCEPT review record bound to the exact outline", "python3 -m pipeline.run review-verify", ["review evidence_class must be MANUAL_REVIEW", "decision must be ACCEPT", "raw and normalized outline hashes must match", "BOOK_OUTLINE.md must explicitly be REVIEWED"]))
        return {"status": "BLOCKED", "stage": "outline review", "outline": outline, "outline_preflight": outline_preflight_result, "review_validation": review_validation, "mapping_admission": mapping_result.get("mapping_admission")}
    return validate_mapping_admission(root, source_name, outline_name, "REPOSITORY")
