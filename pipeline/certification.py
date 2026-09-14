"""Revision 1.8 independent candidate verification and certification.

This module deliberately does not call the legacy release/finalization path.  It
recomputes the dependency chain and stops at a hash-bound certificate.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import PIPELINE_VERSION
from .core import (
    ASSEMBLY_SCHEMA_VERSION,
    EVIDENCE_CLASSES,
    PipelineError,
    assembly_dependency_payload,
    assembly_envelope_sha256,
    assemble,
    canonical_json_bytes,
    canonical_mapping_value,
    decompose,
    load_json,
    load_jsonl,
    mapping_sha256,
    outline_preflight,
    parse_outline,
    pipeline_bundle_hash,
    report,
    sha256_bytes,
    sha256_file,
    validate_mapping,
    write_json,
    write_text,
)

CERTIFICATION_VERSION = "1.8.0"
CERT_GATES = [f"CERT-{index:03d}" for index in range(21)]
BLOCK_RE = re.compile(rb"<!-- BLOCK: (B[0-9]{4,}) -->")
END_BLOCK_RE = re.compile(rb"<!-- END BLOCK: (B[0-9]{4,}) -->")


def _check(gate: str, status: str, expected: str, observed: str, artifacts: Sequence[str] = (), blocks: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "gate": gate,
        "status": status,
        "expected": expected,
        "observed": observed,
        "affected_artifacts": list(artifacts),
        "affected_blocks": list(blocks),
    }


def _status(checks: Sequence[Mapping[str, Any]]) -> str:
    return "VERIFIED" if all(item.get("status") == "PASS" for item in checks) else "BLOCKED"


def _read(path: Path) -> Any | None:
    try:
        return load_json(path)
    except (OSError, ValueError, PipelineError):
        return None


def _readl(path: Path) -> list[dict[str, Any]] | None:
    try:
        return load_jsonl(path)
    except (OSError, ValueError, PipelineError):
        return None


def _review_binding(root: Path, outline: Mapping[str, Any] | None, evidence_class: str) -> tuple[bool, str | None, dict[str, Any] | None]:
    review_path = root / "artifacts/review/OUTLINE_REVIEW.json"
    if outline is None or not review_path.exists():
        return False, None, None
    review = _read(review_path)
    if not isinstance(review, dict):
        return False, None, None
    review_hash = sha256_file(review_path)
    valid = (
        review.get("evidence_class") == "MANUAL_REVIEW"
        and review.get("decision") == "ACCEPT"
        and review.get("review_status") == "REVIEWED"
        and review.get("outline_raw_sha256") == outline.get("raw_sha256")
        and review.get("outline_normalized_sha256") == outline.get("normalized_sha256")
        and outline.get("outline_status") == "REVIEWED"
    )
    return valid, review_hash, review


def _independent_order(mapping: Mapping[str, Any], outline: Mapping[str, Any], blocks: Sequence[Mapping[str, Any]]) -> list[str]:
    outline_position = {node["outline_id"]: index for index, node in enumerate(outline.get("nodes", []))}
    source_position = {block["block_id"]: block["source"]["sequence"] for block in blocks}
    primary = [entry for entry in mapping.get("entries", []) if entry.get("role") == "PRIMARY"]
    primary.sort(key=lambda entry: (
        outline_position.get(entry.get("target_id"), 10**9),
        entry.get("placement", 10**9),
        source_position.get(entry.get("block_id"), 10**9),
        entry.get("block_id", ""),
    ))
    return [entry["block_id"] for entry in primary]


def _mapping_dispositions(mapping: Mapping[str, Any], blocks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_id = {block["block_id"]: block for block in blocks}
    result = []
    for entry in sorted(mapping.get("entries", []), key=lambda item: (item.get("target_id") or "", item.get("placement", 0), item.get("block_id", ""), item.get("role", ""))):
        block = by_id.get(entry.get("block_id"))
        if block is None:
            continue
        result.append({
            "block_id": entry["block_id"],
            "target_id": entry.get("target_id"),
            "role": entry.get("role"),
            "placement": entry.get("placement"),
            "source_span": block["source"],
            "source_raw_sha256": block["raw_sha256"],
            "source_normalized_sha256": block["normalized_sha256"],
            "payload_contributed": entry.get("role") == "PRIMARY",
        })
    return result


def _expected_envelope(outline: Mapping[str, Any], mapping: Mapping[str, Any], blocks: Sequence[Mapping[str, Any]]) -> bytes:
    """Reconstruct only the authorized structural envelope with payload tokens."""
    by_target: dict[str, list[dict[str, Any]]] = {}
    source_position = {block["block_id"]: block["source"]["sequence"] for block in blocks}
    for entry in mapping.get("entries", []):
        if entry.get("role") == "EXPLICITLY_UNMAPPED":
            continue
        by_target.setdefault(entry.get("target_id"), []).append(entry)
    for rows in by_target.values():
        rows.sort(key=lambda row: (row.get("role") != "PRIMARY", row.get("placement", 0), source_position.get(row.get("block_id"), 10**9), row.get("block_id", "")))
    output = bytearray()
    nodes = outline.get("nodes", [])
    for index, node in enumerate(nodes):
        outline_id = node["outline_id"]
        output.extend(f"{'#' * node['level']} {node['title']}\n".encode("utf-8"))
        output.extend(f"<!-- OUTLINE: {outline_id} -->\n".encode("utf-8"))
        rows = by_target.get(outline_id, [])
        primary = [row for row in rows if row.get("role") == "PRIMARY"]
        has_child = index + 1 < len(nodes) and nodes[index + 1]["level"] > node["level"]
        if not primary and not has_child:
            output.extend(f"[GAP: {outline_id}]\n".encode("utf-8"))
        for row in primary:
            block_id = row["block_id"]
            output.extend(f"<!-- BLOCK: {block_id} -->\n".encode("utf-8"))
            output.extend(f"<SOURCE_PAYLOAD:{block_id}>".encode("utf-8"))
            output.extend(f"<!-- END BLOCK: {block_id} -->\n".encode("utf-8"))
        for row in rows:
            if row.get("role") != "PRIMARY":
                output.extend(f"[{row['role']}: {row['block_id']}]\n".encode("utf-8"))
    return bytes(output)


def _outside_payload(candidate: bytes, placements: Sequence[Mapping[str, Any]]) -> bytes | None:
    output = bytearray()
    cursor = 0
    for item in sorted(placements, key=lambda row: row.get("start_offset", -1)):
        start, end = item.get("start_offset"), item.get("end_offset")
        if not isinstance(start, int) or not isinstance(end, int) or start < cursor or end < start or end > len(candidate):
            return None
        output.extend(candidate[cursor:start])
        cursor = end
    output.extend(candidate[cursor:])
    return bytes(output)


def _candidate_template(candidate: bytes, placements: Sequence[Mapping[str, Any]]) -> bytes | None:
    output = bytearray()
    cursor = 0
    for item in sorted(placements, key=lambda row: row.get("start_offset", -1)):
        start, end = item.get("start_offset"), item.get("end_offset")
        if not isinstance(start, int) or not isinstance(end, int) or start < cursor or end < start or end > len(candidate):
            return None
        output.extend(candidate[cursor:start])
        output.extend(f"<SOURCE_PAYLOAD:{item.get('block_id')}>".encode("utf-8"))
        cursor = end
    output.extend(candidate[cursor:])
    return bytes(output)


def _normalized_sidecar(records: Sequence[Mapping[str, Any]]) -> bytes:
    copied = [dict(item) for item in records]
    for item in copied:
        if "book_path" in item:
            item["book_path"] = "BOOK_FINAL_CANDIDATE.md"
    return b"".join(canonical_json_bytes(item) + b"\n" for item in copied)


def _required_paths(root: Path) -> dict[str, Path]:
    return {
        "source": root / "Source.md",
        "source_manifest": root / "pipeline/manifests/source.manifest.json",
        "blocks_manifest": root / "pipeline/manifests/blocks.manifest.json",
        "blocks": root / "artifacts/decomposition/CONTENT_BLOCKS.jsonl",
        "outline": root / "BOOK_OUTLINE.md",
        "review": root / "artifacts/review/OUTLINE_REVIEW.json",
        "mapping_input": root / "artifacts/mapping/mapping.input.json",
        "mapping_manifest": root / "pipeline/manifests/mapping.manifest.json",
        "mapping": root / "artifacts/mapping/mapping.canonical.json",
        "mapping_validation": root / "artifacts/mapping/mapping.validation.json",
        "map000": root / "artifacts/mapping/MAP_000_ADMISSION.json",
        "pipeline_manifest": root / "pipeline/manifests/pipeline.manifest.json",
        "assembly_contract": root / "pipeline/contracts/assembly.md",
        "assembly_schema": root / "pipeline/schemas/assembly.schema.json",
        "candidate": root / "artifacts/assembly/BOOK_FINAL_CANDIDATE.md",
        "assembly": root / "artifacts/assembly/BOOK_ASSEMBLY.jsonl",
        "assembly_dependencies": root / "artifacts/assembly/assembly.dependencies.json",
        "assembly_manifest": root / "pipeline/manifests/assembly.manifest.json",
        "roundtrip": root / "artifacts/assembly/candidate.roundtrip.json",
        "provenance": root / "artifacts/assembly/provenance.reconstruction.json",
    }


def _verify_once(root: Path, evidence_class: str) -> dict[str, Any]:
    paths = _required_paths(root)
    checks: list[dict[str, Any]] = []
    missing = [name for name, path in paths.items() if not path.exists()]
    checks.append(_check("CERT-000", "PASS" if not missing else "BLOCKED", "complete candidate dependency chain exists", "all present" if not missing else f"missing: {', '.join(missing)}", [str(paths[name]) for name in missing]))

    source_manifest = _read(paths["source_manifest"])
    blocks_manifest = _read(paths["blocks_manifest"])
    stored_blocks = _readl(paths["blocks"])
    try:
        current_source_manifest, fresh_blocks = decompose(paths["source"])
        source_ok = isinstance(source_manifest, dict) and source_manifest == current_source_manifest
    except (OSError, ValueError, PipelineError):
        current_source_manifest, fresh_blocks, source_ok = None, [], False
    source_file_hash = sha256_file(paths["source_manifest"]) if paths["source_manifest"].exists() else None
    checks.append(_check("CERT-001", "PASS" if source_ok else "FAIL", "current Source.md recomputes to the stored source manifest", "match" if source_ok else "mismatch", [str(paths["source"]), str(paths["source_manifest"])]))
    block_ok = (
        isinstance(stored_blocks, list)
        and stored_blocks == fresh_blocks
        and isinstance(blocks_manifest, dict)
        and source_file_hash is not None
        and blocks_manifest.get("source_manifest_sha256") == source_file_hash
        and blocks_manifest.get("blocks_artifact_sha256") == sha256_file(paths["blocks"])
        and blocks_manifest.get("block_count") == len(stored_blocks)
    )
    checks.append(_check("CERT-002", "PASS" if block_ok else "FAIL", "block manifest and stored blocks match a fresh source decomposition", "match" if block_ok else "mismatch", [str(paths["blocks_manifest"]), str(paths["blocks"])]))

    try:
        preflight, outline = outline_preflight(paths["outline"], evidence_class)
        outline_ok = preflight.get("status") == "VERIFIED" and outline.get("outline_status") == "REVIEWED"
    except (OSError, ValueError, PipelineError):
        preflight, outline, outline_ok = {}, None, False
    checks.append(_check("CERT-003", "PASS" if outline_ok else "FAIL", "current reviewed outline is structurally valid", "reviewed" if outline_ok else "invalid or provisional", [str(paths["outline"])]))
    review_ok, review_hash, review = _review_binding(root, outline, evidence_class)
    checks.append(_check("CERT-004", "PASS" if review_ok else "FAIL", "MANUAL_REVIEW ACCEPT is bound to current raw and normalized outline hashes", "bound" if review_ok else "unbound or unauthorized", [str(paths["review"])]))

    mapping_manifest = _read(paths["mapping_manifest"])
    mapping_input = _read(paths["mapping_input"])
    canonical_mapping = _read(paths["mapping"])
    mapping_validation = _read(paths["mapping_validation"])
    map000 = _read(paths["map000"])
    mapping_ok = False
    mapping_errors: list[str] = []
    mapping_file_hash = sha256_file(paths["mapping_manifest"]) if paths["mapping_manifest"].exists() else None
    if isinstance(mapping_input, dict) and isinstance(canonical_mapping, dict) and isinstance(mapping_manifest, dict) and isinstance(outline, dict) and isinstance(stored_blocks, list) and source_file_hash and sha256_file(paths["blocks_manifest"]) is not None:
        canonical_ok = canonical_mapping == canonical_mapping_value(mapping_input) and mapping_sha256(canonical_mapping) == mapping_manifest.get("mapping_sha256")
        mapping_errors = validate_mapping(mapping_input, stored_blocks, outline, source_file_hash, sha256_file(paths["blocks_manifest"]), review_hash)
        mapping_ok = (
            canonical_ok
            and not mapping_errors
            and mapping_manifest.get("status") == "AUTHORIZED"
            and mapping_manifest.get("authorization") == "AUTHORIZED"
            and mapping_manifest.get("mapping_input_sha256") == sha256_file(paths["mapping_input"])
            and mapping_manifest.get("source_manifest_hash") == source_file_hash
            and mapping_manifest.get("block_manifest_hash") == sha256_file(paths["blocks_manifest"])
            and mapping_manifest.get("outline_raw_sha256") == outline.get("raw_sha256")
            and mapping_manifest.get("outline_normalized_sha256") == outline.get("normalized_sha256")
            and mapping_manifest.get("outline_review_hash") == review_hash
            and mapping_manifest.get("review_status") == "REVIEWED"
            and isinstance(mapping_validation, dict)
            and mapping_validation.get("status") == "AUTHORIZED"
            and all(item.get("status") == "PASS" for item in mapping_validation.get("checks", []))
            and mapping_manifest.get("mapping_validation_sha256") == sha256_file(paths["mapping_validation"])
            and isinstance(map000, dict)
            and map000.get("status") == "PASS"
            and map000.get("source_manifest_hash") == source_file_hash
            and map000.get("block_manifest_hash") == sha256_file(paths["blocks_manifest"])
            and map000.get("outline_raw_sha256") == outline.get("raw_sha256")
            and map000.get("outline_normalized_sha256") == outline.get("normalized_sha256")
            and map000.get("outline_review_hash") == review_hash
            and map000.get("mapping_sha256") == mapping_manifest.get("mapping_sha256")
        )
    checks.append(_check("CERT-005", "PASS" if mapping_ok else "FAIL", "canonical mapping and all mapping relationships bind current authoritative inputs", "independently valid" if mapping_ok else ("; ".join(mapping_errors[:4]) or "stale, absent, or invalid"), [str(paths["mapping_input"]), str(paths["mapping"]), str(paths["mapping_manifest"])]))

    candidate = paths["candidate"].read_bytes() if paths["candidate"].exists() else None
    assembly_records = _readl(paths["assembly"])
    assembly_manifest = _read(paths["assembly_manifest"])
    dependency = _read(paths["assembly_dependencies"])
    placements = assembly_records[1:] if isinstance(assembly_records, list) and assembly_records else []
    header = assembly_records[0] if isinstance(assembly_records, list) and assembly_records else {}
    candidate_hash = sha256_bytes(candidate) if candidate is not None else None
    candidate_hash_ok = candidate is not None and isinstance(assembly_manifest, dict) and isinstance(header, dict) and candidate_hash == assembly_manifest.get("candidate_sha256") == header.get("candidate_sha256")
    checks.append(_check("CERT-006", "PASS" if candidate_hash_ok else "FAIL", "SHA256(exact candidate bytes) equals recorded candidate hash", "match" if candidate_hash_ok else "mismatch or absent", [str(paths["candidate"]), str(paths["assembly_manifest"])]))

    by_id = {block["block_id"]: block for block in stored_blocks or []}
    expected_order = _independent_order(mapping_input or {}, outline or {}, stored_blocks or []) if mapping_ok else []
    expected_entries = [entry for entry in (mapping_input or {}).get("entries", []) if entry.get("role") == "PRIMARY"]
    expected_tuples = []
    for block_id in expected_order:
        entry = next((row for row in expected_entries if row.get("block_id") == block_id), None)
        if entry:
            expected_tuples.append((block_id, entry.get("target_id"), entry.get("placement"), "PRIMARY"))
    actual_tuples = [(item.get("block_id"), item.get("target_id"), item.get("placement"), item.get("role")) for item in placements]
    completeness_ok = candidate is not None and mapping_ok and actual_tuples == expected_tuples and len(actual_tuples) == len(set(actual_tuples))
    checks.append(_check("CERT-007", "PASS" if completeness_ok else "FAIL", "actual candidate identity tuples equal all authorized PRIMARY tuples exactly once", "complete" if completeness_ok else "missing, duplicate, or unexpected tuple", [str(paths["candidate"]), str(paths["assembly"])]))

    verbatim_failures: list[str] = []
    provenance_failures: list[str] = []
    for item in placements:
        block_id = item.get("block_id")
        block = by_id.get(block_id)
        start, end = item.get("start_offset"), item.get("end_offset")
        if block is None or not isinstance(start, int) or not isinstance(end, int) or candidate is None or not (0 <= start <= end <= len(candidate)):
            provenance_failures.append(str(block_id))
            continue
        payload = candidate[start:end]
        expected_payload = block["original_text"].encode("utf-8")
        if payload != expected_payload or sha256_bytes(payload) != block["normalized_sha256"] or item.get("source_normalized_sha256") != block["normalized_sha256"]:
            verbatim_failures.append(str(block_id))
        if item.get("payload_sha256") != sha256_bytes(payload) or item.get("source_span") != block.get("source") or item.get("source_raw_sha256") != block.get("raw_sha256"):
            provenance_failures.append(str(block_id))
    checks.append(_check("CERT-008", "PASS" if not verbatim_failures else "FAIL", "every payload equals the explicitly contracted NORMALIZED source representation", "NORMALIZED exact" if not verbatim_failures else f"{len(verbatim_failures)} mismatch(es)", [str(paths["candidate"]), str(paths["assembly"])], verbatim_failures))

    envelope_bytes = _outside_payload(candidate or b"", placements)
    marker_ids = [value.decode("ascii") for value in BLOCK_RE.findall(envelope_bytes or b"")]
    end_ids = [value.decode("ascii") for value in END_BLOCK_RE.findall(envelope_bytes or b"")]
    provenance_ok = (
        candidate is not None
        and not provenance_failures
        and marker_ids == [item.get("block_id") for item in placements]
        and end_ids == [item.get("block_id") for item in placements]
        and all(item.get("role") == "PRIMARY" and item.get("placement_kind") == "PRIMARY" and item.get("target_id") in {node.get("outline_id") for node in (outline or {}).get("nodes", [])} for item in placements)
    )
    checks.append(_check("CERT-009", "PASS" if provenance_ok else "FAIL", "candidate -> block_id -> source span -> Source.md is mechanically traversable", "linked" if provenance_ok else "anonymous, unresolvable, or inconsistent", [str(paths["candidate"]), str(paths["assembly"])], provenance_failures))

    actual_order = [item.get("block_id") for item in placements]
    checks.append(_check("CERT-010", "PASS" if candidate is not None and mapping_ok and actual_order == expected_order else "FAIL", "expected order is independently reconstructed from outline and mapping", "equal" if candidate is not None and mapping_ok and actual_order == expected_order else "different or unavailable", [str(paths["assembly"])]))

    template = _candidate_template(candidate or b"", placements) if candidate is not None else None
    envelope_expected = _expected_envelope(outline or {}, mapping_input or {}, stored_blocks or []) if outline and mapping_ok else b""
    envelope_ok = template is not None and template == envelope_expected
    checks.append(_check("CERT-011", "PASS" if envelope_ok else "FAIL", "candidate structural envelope equals authorized outline/mapping envelope", "equal" if envelope_ok else "injected, missing, or altered structure", [str(paths["candidate"])]))
    checks.append(_check("CERT-012", "PASS" if envelope_ok else "FAIL", "all candidate bytes are source payload or authorized structural envelope", "no unexplained content" if envelope_ok else "unexplained substantive content", [str(paths["candidate"])]))

    dispositions_ok = isinstance(assembly_manifest, dict) and assembly_manifest.get("mapping_dispositions") == _mapping_dispositions(mapping_input or {}, stored_blocks or [])
    duplicate_ok = len({item.get("block_id") for item in placements}) == len(placements) and len(expected_order) == len(set(expected_order))
    checks.append(_check("CERT-013", "PASS" if duplicate_ok else "FAIL", "duplicate-content blocks retain distinct canonical identities", "identities preserved" if duplicate_ok else "identity collapse or duplicate placement", [str(paths["assembly_manifest"])]))
    assembly_ok = (
        isinstance(assembly_manifest, dict)
        and assembly_manifest.get("manifest_type") == "assembly"
        and assembly_manifest.get("status") == "VERIFIED"
        and assembly_manifest.get("mapping_status") == "AUTHORIZED"
        and assembly_manifest.get("placements") == placements
        and assembly_manifest.get("ordered_block_sequence") == expected_order
        and dispositions_ok
        and isinstance(header, dict)
        and assembly_manifest.get("assembly_sidecar_sha256") == sha256_file(paths["assembly"])
    )
    checks.append(_check("CERT-014", "PASS" if assembly_ok else "FAIL", "assembly manifest is reproducible from candidate and authoritative inputs", "reproducible" if assembly_ok else "contradictory or stale", [str(paths["assembly_manifest"])]))

    contract_hash = sha256_file(paths["assembly_contract"]) if paths["assembly_contract"].exists() else None
    expected_dependency = assembly_dependency_payload(source_file_hash, sha256_file(paths["blocks_manifest"]) if paths["blocks_manifest"].exists() else None, outline or {}, review_hash, mapping_manifest.get("mapping_sha256") if isinstance(mapping_manifest, dict) else None, mapping_file_hash, contract_hash, candidate_hash)
    dependency_ok = isinstance(dependency, dict) and dependency == expected_dependency and isinstance(assembly_manifest, dict) and assembly_manifest.get("dependencies") == expected_dependency and assembly_manifest.get("assembly_schema_version") == ASSEMBLY_SCHEMA_VERSION and assembly_manifest.get("pipeline_version") == PIPELINE_VERSION and assembly_manifest.get("dependency_sha256") == sha256_bytes(canonical_json_bytes(expected_dependency))
    checks.append(_check("CERT-015", "PASS" if dependency_ok else "FAIL", "all source, block, outline, review, mapping, assembly, pipeline, and schema dependencies hash-match", "exact" if dependency_ok else "mismatch or absent", [str(paths["assembly_dependencies"]), str(paths["assembly_manifest"])]))

    expected_roundtrip = [{"block_id": item.get("block_id"), "payload_sha256": item.get("payload_sha256"), "source_normalized_sha256": (by_id.get(item.get("block_id")) or {}).get("normalized_sha256"), "start_offset": item.get("start_offset"), "end_offset": item.get("end_offset")} for item in placements]
    roundtrip = _read(paths["roundtrip"])
    roundtrip_ok = isinstance(roundtrip, dict) and roundtrip.get("candidate_sha256") == candidate_hash and roundtrip.get("ordered_block_sequence") == expected_order and roundtrip.get("extracted_payloads") == expected_roundtrip
    checks.append(_check("CERT-016", "PASS" if roundtrip_ok else "FAIL", "candidate extraction roundtrip equals expected authorized payload sequence", "PASS" if roundtrip_ok else "FAIL", [str(paths["roundtrip"]), str(paths["candidate"])]))

    pipeline_manifest = _read(paths["pipeline_manifest"])
    manifest_projection = {
        "manifest_type": "assembly",
        "manifest_version": "1.7.0",
        "pipeline_version": PIPELINE_VERSION,
        "assembly_schema_version": ASSEMBLY_SCHEMA_VERSION,
        "source_representation": "NORMALIZED_SOURCE",
        "dependencies": expected_dependency,
        "candidate_sha256": candidate_hash,
        "structural_envelope_sha256": assembly_envelope_sha256(candidate or b"", placements),
        "ordered_block_sequence": expected_order,
        "placements": placements,
        "mapping_dispositions": _mapping_dispositions(mapping_input or {}, stored_blocks or []),
        "mapping_status": "AUTHORIZED",
    }
    manifest_projection_ok = isinstance(assembly_manifest, dict) and all(assembly_manifest.get(key) == value for key, value in manifest_projection.items())
    determinism_ok = False
    if mapping_ok and outline and stored_blocks and candidate is not None:
        try:
            with tempfile.TemporaryDirectory(prefix="cert-determinism-") as directory:
                temp = Path(directory)
                book1, side1 = temp / "BOOK_FINAL_CANDIDATE.md", temp / "BOOK_ASSEMBLY.jsonl"
                book2, side2 = temp / "BOOK_FINAL_CANDIDATE_2.md", temp / "BOOK_ASSEMBLY_2.jsonl"
                assemble(book1, side1, stored_blocks, outline, mapping_input)
                assemble(book2, side2, stored_blocks, outline, mapping_input)
                determinism_ok = manifest_projection_ok and book1.read_bytes() == book2.read_bytes() and _normalized_sidecar(load_jsonl(side1)) == _normalized_sidecar(load_jsonl(side2))
        except (OSError, ValueError, KeyError):
            determinism_ok = False
    checks.append(_check("CERT-017", "PASS" if determinism_ok else "FAIL", "independent assembly runs produce byte-identical candidate and normalized sidecar", "identical" if determinism_ok else "different or unavailable", [str(paths["candidate"]), str(paths["assembly"])]))

    return {
        "checks": checks,
        "status": _status(checks),
        "candidate_sha256": candidate_hash,
        "source_manifest_hash": source_file_hash,
        "block_manifest_hash": sha256_file(paths["blocks_manifest"]) if paths["blocks_manifest"].exists() else None,
        "outline_raw_sha256": (outline or {}).get("raw_sha256"),
        "outline_normalized_sha256": (outline or {}).get("normalized_sha256"),
        "outline_review_hash": review_hash,
        "mapping_sha256": mapping_manifest.get("mapping_sha256") if isinstance(mapping_manifest, dict) else None,
        "assembly_manifest_hash": sha256_file(paths["assembly_manifest"]) if paths["assembly_manifest"].exists() else None,
        "pipeline_version": PIPELINE_VERSION,
        "pipeline_manifest": pipeline_manifest,
        "paths": paths,
    }


def _mutation_resistance(root: Path, evidence_class: str) -> bool:
    """Run CERT-M01..CERT-M15 against isolated copies without repairing them."""
    mutations = []
    def candidate_byte(copy_root: Path) -> None:
        path = copy_root / "artifacts/assembly/BOOK_FINAL_CANDIDATE.md"
        path.write_bytes(path.read_bytes() + b"\nCERT-MUTATION\n")
    def candidate_hash(copy_root: Path) -> None:
        path = copy_root / "pipeline/manifests/assembly.manifest.json"
        data = load_json(path); data["candidate_sha256"] = "0" * 64; write_json(path, data)
    def source_mutation(copy_root: Path) -> None:
        path = copy_root / "Source.md"; path.write_bytes(path.read_bytes() + b"\nsource mutation\n")
    def block_manifest_mutation(copy_root: Path) -> None:
        path = copy_root / "pipeline/manifests/blocks.manifest.json"; data = load_json(path); data["blocks_artifact_sha256"] = "0" * 64; write_json(path, data)
    def outline_mutation(copy_root: Path) -> None:
        path = copy_root / "BOOK_OUTLINE.md"; path.write_bytes(path.read_bytes().replace(b"Fixture", b"Mutated", 1))
    def review_mutation(copy_root: Path) -> None:
        path = copy_root / "artifacts/review/OUTLINE_REVIEW.json"; data = load_json(path); data["decision"] = "REJECT"; write_json(path, data)
    def mapping_mutation(copy_root: Path) -> None:
        path = copy_root / "artifacts/mapping/mapping.input.json"; data = load_json(path); data["entries"][0]["placement"] = 999; write_json(path, data)
    def assembly_manifest_mutation(copy_root: Path) -> None:
        path = copy_root / "pipeline/manifests/assembly.manifest.json"; data = load_json(path); data["ordered_block_sequence"] = list(reversed(data["ordered_block_sequence"])); write_json(path, data)
    def dependency_mutation(copy_root: Path) -> None:
        path = copy_root / "artifacts/assembly/assembly.dependencies.json"; data = load_json(path); data["mapping_sha256"] = "0" * 64; write_json(path, data)
    def block_delete(copy_root: Path) -> None:
        path = copy_root / "artifacts/decomposition/CONTENT_BLOCKS.jsonl"; records = load_jsonl(path); path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in records[:-1]), encoding="utf-8")
    def unauthorized_insert(copy_root: Path) -> None:
        path = copy_root / "artifacts/assembly/BOOK_FINAL_CANDIDATE.md"; path.write_bytes(path.read_bytes() + b"<!-- BLOCK: B9999 -->\nnot source\n<!-- END BLOCK: B9999 -->\n")
    def reorder(copy_root: Path) -> None:
        path = copy_root / "artifacts/assembly/BOOK_ASSEMBLY.jsonl"; records = load_jsonl(path); records[1], records[2] = records[2], records[1]; path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in records), encoding="utf-8")
    def payload_substitution(copy_root: Path) -> None:
        path = copy_root / "artifacts/assembly/BOOK_FINAL_CANDIDATE.md"; path.write_bytes(path.read_bytes().replace(b"One.", b"Changed.", 1))
    def envelope_injection(copy_root: Path) -> None:
        path = copy_root / "artifacts/assembly/BOOK_FINAL_CANDIDATE.md"; path.write_bytes(path.read_bytes() + b"\nInvented substantive prose.\n")
    def duplicate_collapse(copy_root: Path) -> None:
        path = copy_root / "artifacts/assembly/BOOK_ASSEMBLY.jsonl"; records = load_jsonl(path); path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in [records[0], *records[1:-1]]), encoding="utf-8")
    mutations.extend([candidate_byte, candidate_hash, source_mutation, block_manifest_mutation, outline_mutation, review_mutation, mapping_mutation, assembly_manifest_mutation, dependency_mutation, block_delete, unauthorized_insert, reorder, payload_substitution, envelope_injection, duplicate_collapse])
    with tempfile.TemporaryDirectory(prefix="cert-mutations-") as directory:
        base = Path(directory) / "base"
        shutil.copytree(root, base, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
        for mutate in mutations:
            case = Path(directory) / f"case-{mutations.index(mutate):02d}"
            shutil.copytree(base, case)
            try:
                mutate(case)
                if _verify_once(case, evidence_class).get("status") != "BLOCKED":
                    return False
            except (OSError, ValueError, KeyError, IndexError):
                return False
    return True


def verify_candidate_independently(root: Path, evidence_class: str = "REPOSITORY") -> dict[str, Any]:
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(evidence_class)
    first = _verify_once(root, evidence_class)
    second = _verify_once(root, evidence_class)
    checks = list(first["checks"])
    stable_first = [(item.get("gate"), item.get("status"), item.get("observed")) for item in first["checks"]]
    stable_second = [(item.get("gate"), item.get("status"), item.get("observed")) for item in second["checks"]]
    checks.append(_check("CERT-018", "PASS" if stable_first == stable_second else "FAIL", "repeated verification has equivalent canonical results", "equivalent" if stable_first == stable_second else "different", ["artifacts/verification/CANDIDATE_VERIFICATION.json"]))
    mutation_ok = first["status"] != "VERIFIED" or _mutation_resistance(root, evidence_class)
    checks.append(_check("CERT-019", "PASS" if mutation_ok else "FAIL", "CERT-M01 through CERT-M15 mutations are detected without repair", "blocked as expected" if mutation_ok else "mutation escaped verifier", ["pipeline/tests/certification/test_certification.py"]))
    scope_ok = evidence_class in EVIDENCE_CLASSES and (not isinstance(first.get("pipeline_manifest"), dict) or first["pipeline_manifest"].get("evidence_class") in {None, evidence_class, "REPOSITORY"})
    checks.append(_check("CERT-020", "PASS" if scope_ok else "FAIL", "evidence class is retained and no local run claims CI", evidence_class, ["pipeline/manifests/pipeline.manifest.json"]))
    result = dict(first)
    result.pop("paths", None)
    result["checks"] = checks
    result["status"] = _status(checks)
    result["artifact_type"] = "candidate_verification"
    result["artifact_version"] = CERTIFICATION_VERSION
    result["evidence_class"] = evidence_class
    result["runtime_timestamps_excluded"] = True
    verification_path = root / "artifacts/verification/CANDIDATE_VERIFICATION.json"
    verification_md = root / "artifacts/verification/CANDIDATE_VERIFICATION.md"
    write_json(verification_path, result)
    write_text(verification_md, report("CANDIDATE_VERIFICATION", result["status"], checks, "Independent candidate verification only; certification and finalization are separate boundaries.", evidence_class=evidence_class))
    manifest = {
        "manifest_type": "verification",
        "manifest_version": CERTIFICATION_VERSION,
        "evidence_class": evidence_class,
        "status": result["status"],
        "report_artifact": verification_path.relative_to(root).as_posix(),
        "report_sha256": sha256_file(verification_path),
        "candidate_sha256": result.get("candidate_sha256"),
        "assembly_manifest_hash": result.get("assembly_manifest_hash"),
        "pipeline_version": PIPELINE_VERSION,
        "checks": checks,
        "runtime_timestamps_excluded": True,
    }
    write_json(root / "pipeline/manifests/verification.manifest.json", manifest)
    result["verification_manifest_hash"] = sha256_file(root / "pipeline/manifests/verification.manifest.json")
    return result


def _certificate_bindings(root: Path, verification: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "candidate_sha256": verification.get("candidate_sha256"),
        "source_manifest_hash": verification.get("source_manifest_hash"),
        "block_manifest_hash": verification.get("block_manifest_hash"),
        "outline_raw_sha256": verification.get("outline_raw_sha256"),
        "outline_normalized_sha256": verification.get("outline_normalized_sha256"),
        "outline_review_hash": verification.get("outline_review_hash"),
        "mapping_sha256": verification.get("mapping_sha256"),
        "assembly_manifest_hash": verification.get("assembly_manifest_hash"),
        "verification_manifest_hash": sha256_file(root / "pipeline/manifests/verification.manifest.json"),
        "pipeline_version": PIPELINE_VERSION,
    }


def verify_certification_certificate(root: Path, certificate_path: Path | None = None) -> dict[str, Any]:
    certificate_path = certificate_path or (root / "artifacts/verification/RELEASE_CERTIFICATE.json")
    certificate = _read(certificate_path)
    if not isinstance(certificate, dict):
        return {"status": "BLOCKED", "checks": [_check("CERTIFICATE", "FAIL", "certificate exists and is JSON", "absent or invalid", [str(certificate_path)])]}
    evidence_class = certificate.get("evidence_class", "REPOSITORY")
    verification = _verify_once(root, evidence_class) if evidence_class in EVIDENCE_CLASSES else {"status": "BLOCKED", "checks": []}
    expected = _certificate_bindings(root, verification) if verification.get("status") == "VERIFIED" else {}
    actual = certificate.get("bindings", {})
    checks = [_check("CERTIFICATE-BINDINGS", "PASS" if actual.get(key) == expected.get(key) else "FAIL", f"certificate binding {key} equals current recomputation", "match" if actual.get(key) == expected.get(key) else "mismatch", [str(certificate_path)]) for key in sorted(set(expected) | set(actual))]
    checks.append(_check("CERTIFICATE-STATUS", "PASS" if certificate.get("status") == "CERTIFIED" and certificate.get("certification_status") == "CERTIFIED" else "FAIL", "certificate status remains CERTIFIED", str(certificate.get("status")), [str(certificate_path)]))
    schema_ok = certificate.get("artifact_type") == "release_certificate" and certificate.get("certificate_version") == CERTIFICATION_VERSION and certificate.get("finalization") == "FORBIDDEN_IN_REVISION_1_8" and certificate.get("pipeline_version") == PIPELINE_VERSION
    checks.append(_check("CERTIFICATE-SCHEMA", "PASS" if schema_ok else "FAIL", "certificate schema and finalization prohibition remain exact", "valid" if schema_ok else "mutated", [str(certificate_path)]))
    checks.append(_check("CERTIFICATE-EVIDENCE", "PASS" if certificate.get("evidence_class") == evidence_class else "FAIL", "certificate evidence class is retained", str(certificate.get("evidence_class")), [str(certificate_path)]))
    return {"status": "VERIFIED" if verification.get("status") == "VERIFIED" and all(item["status"] == "PASS" for item in checks) else "BLOCKED", "evidence_class": evidence_class, "checks": checks}


def certify_candidate(root: Path, evidence_class: str = "REPOSITORY") -> dict[str, Any]:
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(evidence_class)
    verification = verify_candidate_independently(root, evidence_class)
    if verification.get("status") != "VERIFIED":
        return {"status": "BLOCKED", "stage": "candidate verification", "verification": verification}
    certificate_path = root / "artifacts/verification/RELEASE_CERTIFICATE.json"
    record = {
        "artifact_type": "release_certificate",
        "certificate_version": CERTIFICATION_VERSION,
        "evidence_class": evidence_class,
        "status": "CERTIFIED",
        "certification_status": "CERTIFIED",
        "pipeline_version": PIPELINE_VERSION,
        "bindings": _certificate_bindings(root, verification),
        "finalization": "FORBIDDEN_IN_REVISION_1_8",
    }
    write_json(certificate_path, record)
    certificate_check = verify_certification_certificate(root, certificate_path)
    if certificate_check["status"] != "VERIFIED":
        return {"status": "BLOCKED", "stage": "certificate verification", "verification": verification, "certificate_verification": certificate_check}
    return {"status": "CERTIFIED", "stage": "candidate certification", "verification": verification, "certificate": record, "certificate_verification": certificate_check, "certificate_artifact": certificate_path.relative_to(root).as_posix()}
