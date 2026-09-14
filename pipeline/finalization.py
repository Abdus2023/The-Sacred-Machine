"""Revision 1.9 certified-candidate finalization.

Finalization is intentionally boring: it revalidates the certificate and
copies candidate bytes in binary mode.  It never invokes assembly or changes
book content.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import PIPELINE_VERSION
from .core import (
    EVIDENCE_CLASSES,
    PipelineError,
    assembly_dependency_payload,
    assembly_order,
    canonical_json_bytes,
    load_json,
    load_jsonl,
    mapping_sha256,
    parse_outline,
    pipeline_bundle_hash,
    report,
    sha256_bytes,
    sha256_file,
    source_hash_record,
    write_json,
    write_text,
)

FINALIZATION_SCHEMA_VERSION = "1.9.0"


def _check(gate: str, status: str, expected: str, observed: str, artifacts: Sequence[str] = (), blocks: Sequence[str] = ()) -> dict[str, Any]:
    return {"gate": gate, "status": status, "expected": expected, "observed": observed, "affected_artifacts": list(artifacts), "affected_blocks": list(blocks)}


def _paths(root: Path) -> dict[str, Path]:
    return {
        "source": root / "Source.md",
        "candidate": root / "artifacts/assembly/BOOK_FINAL_CANDIDATE.md",
        "certificate": root / "artifacts/verification/RELEASE_CERTIFICATE.json",
        "verification": root / "pipeline/manifests/verification.manifest.json",
        "source_manifest": root / "pipeline/manifests/source.manifest.json",
        "blocks_manifest": root / "pipeline/manifests/blocks.manifest.json",
        "blocks": root / "artifacts/decomposition/CONTENT_BLOCKS.jsonl",
        "outline": root / "BOOK_OUTLINE.md",
        "review": root / "artifacts/review/OUTLINE_REVIEW.json",
        "mapping_manifest": root / "pipeline/manifests/mapping.manifest.json",
        "mapping_input": root / "artifacts/mapping/mapping.input.json",
        "mapping": root / "artifacts/mapping/mapping.canonical.json",
        "mapping_validation": root / "artifacts/mapping/mapping.validation.json",
        "map000": root / "artifacts/mapping/MAP_000_ADMISSION.json",
        "assembly_manifest": root / "pipeline/manifests/assembly.manifest.json",
        "assembly_dependencies": root / "artifacts/assembly/assembly.dependencies.json",
        "assembly": root / "artifacts/assembly/BOOK_ASSEMBLY.jsonl",
        "pipeline_manifest": root / "pipeline/manifests/pipeline.manifest.json",
        "final": root / "BOOK_FINAL.md",
        "final_manifest": root / "pipeline/manifests/final.manifest.json",
    }


def _read_json(path: Path) -> Any | None:
    try:
        return load_json(path)
    except (OSError, ValueError, PipelineError):
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]] | None:
    try:
        return load_jsonl(path)
    except (OSError, ValueError, PipelineError):
        return None


def _certificate_binding(certificate: Mapping[str, Any], key: str) -> Any:
    bindings = certificate.get("bindings")
    return bindings.get(key) if isinstance(bindings, Mapping) else None


def _final_payload_check(root: Path, candidate: bytes, final: bytes) -> tuple[bool, bool, bool, list[str]]:
    """Compare final payload ranges with the certified candidate and mapping."""
    paths = _paths(root)
    records = _read_jsonl(paths["assembly"])
    blocks = _read_jsonl(paths["blocks"])
    outline = _read_json(root / "artifacts/analysis/outline.manifest.json")
    mapping = _read_json(paths["mapping"])
    if not records or not blocks or not isinstance(outline, dict) or not isinstance(mapping, dict):
        return False, False, False, ["final provenance inputs are absent"]
    placements = records[1:]
    by_id = {block.get("block_id"): block for block in blocks}
    provenance_failures: list[str] = []
    candidate_payloads: list[tuple[str, bytes]] = []
    final_payloads: list[tuple[str, bytes]] = []
    for item in placements:
        block_id = item.get("block_id")
        start, end = item.get("start_offset"), item.get("end_offset")
        if not isinstance(start, int) or not isinstance(end, int) or not (0 <= start <= end <= len(candidate)) or end > len(final):
            provenance_failures.append(str(block_id))
            continue
        candidate_payloads.append((str(block_id), candidate[start:end]))
        final_payloads.append((str(block_id), final[start:end]))
        block = by_id.get(block_id)
        if block is None or candidate[start:end] != block.get("original_text", "").encode("utf-8") or sha256_bytes(candidate[start:end]) != block.get("normalized_sha256"):
            provenance_failures.append(str(block_id))
    expected_order = assembly_order(mapping, outline, blocks)
    actual_order = [item.get("block_id") for item in placements]
    expected_primary = [entry.get("block_id") for entry in mapping.get("entries", []) if entry.get("role") == "PRIMARY"]
    # assembly_order is the authoritative deterministic sequence, while the
    # set check independently ensures no payload identity was lost.
    completeness = actual_order == expected_order and set(actual_order) == set(expected_primary) and len(actual_order) == len(set(actual_order))
    roundtrip = candidate_payloads == final_payloads and not provenance_failures
    return roundtrip, not provenance_failures, completeness, provenance_failures


def _revalidate_certificate_without_assembly(root: Path, evidence_class: str) -> dict[str, Any]:
    """Revalidate certificate edges without invoking decomposition or assembly."""
    paths = _paths(root)
    checks: list[dict[str, Any]] = []
    certificate = _read_json(paths["certificate"])
    bindings = certificate.get("bindings", {}) if isinstance(certificate, Mapping) else {}
    schema_ok = isinstance(certificate, dict) and certificate.get("artifact_type") == "release_certificate" and certificate.get("certificate_version") == "1.8.0" and certificate.get("status") == "CERTIFIED" and certificate.get("certification_status") == "CERTIFIED" and certificate.get("evidence_class") == evidence_class and certificate.get("finalization") == "FORBIDDEN_IN_REVISION_1_8"
    checks.append(_check("FINAL-000", "PASS" if schema_ok else "FAIL", "certificate schema, status, and evidence class are exact", "valid" if schema_ok else "invalid", [str(paths["certificate"])]))
    candidate_hash = sha256_file(paths["candidate"]) if paths["candidate"].exists() else None
    checks.append(_check("FINAL-000", "PASS" if candidate_hash is not None and bindings.get("candidate_sha256") == candidate_hash else "FAIL", "certificate candidate hash equals current candidate bytes", "match" if candidate_hash and bindings.get("candidate_sha256") == candidate_hash else "mismatch", [str(paths["candidate"])]))
    source_manifest = _read_json(paths["source_manifest"])
    try:
        current_source = source_hash_record(paths["source"])
        source_ok = isinstance(source_manifest, dict) and source_manifest.get("raw_sha256") == current_source.get("raw_sha256") and source_manifest.get("normalized_sha256") == current_source.get("normalized_sha256")
    except (OSError, ValueError, PipelineError):
        source_ok = False
    source_manifest_hash = sha256_file(paths["source_manifest"]) if paths["source_manifest"].exists() else None
    checks.append(_check("FINAL-000", "PASS" if source_ok and bindings.get("source_manifest_hash") == source_manifest_hash else "FAIL", "current Source.md identity and source manifest hash remain certified", "current" if source_ok and bindings.get("source_manifest_hash") == source_manifest_hash else "stale", [str(paths["source"]), str(paths["source_manifest"])]))
    blocks_manifest = _read_json(paths["blocks_manifest"])
    blocks_hash = sha256_file(paths["blocks_manifest"]) if paths["blocks_manifest"].exists() else None
    blocks_ok = isinstance(blocks_manifest, dict) and blocks_manifest.get("source_manifest_sha256") == source_manifest_hash and blocks_hash == bindings.get("block_manifest_hash") and paths["blocks"].exists() and blocks_manifest.get("blocks_artifact_sha256") == sha256_file(paths["blocks"])
    checks.append(_check("FINAL-000", "PASS" if blocks_ok else "FAIL", "block manifest remains source-bound and hash-current", "current" if blocks_ok else "stale", [str(paths["blocks_manifest"]), str(paths["blocks"])]))
    try:
        outline = parse_outline(paths["outline"])
        outline_ok = outline.get("outline_status") == "REVIEWED" and outline.get("raw_sha256") == bindings.get("outline_raw_sha256") and outline.get("normalized_sha256") == bindings.get("outline_normalized_sha256")
    except (OSError, ValueError, PipelineError):
        outline, outline_ok = None, False
    checks.append(_check("FINAL-000", "PASS" if outline_ok else "FAIL", "current outline raw/normalized hashes remain reviewed and certified", "current" if outline_ok else "stale", [str(paths["outline"])]))
    review = _read_json(paths["review"])
    review_hash = sha256_file(paths["review"]) if paths["review"].exists() else None
    review_ok = isinstance(review, dict) and outline is not None and review.get("evidence_class") == "MANUAL_REVIEW" and review.get("decision") == "ACCEPT" and review.get("review_status") == "REVIEWED" and review.get("outline_raw_sha256") == outline.get("raw_sha256") and review.get("outline_normalized_sha256") == outline.get("normalized_sha256") and review_hash == bindings.get("outline_review_hash")
    checks.append(_check("FINAL-000", "PASS" if review_ok else "FAIL", "current MANUAL_REVIEW ACCEPT record remains bound", "current" if review_ok else "stale or revoked", [str(paths["review"])]))
    mapping = _read_json(paths["mapping"])
    mapping_manifest = _read_json(paths["mapping_manifest"])
    mapping_value_hash = mapping_sha256(mapping) if isinstance(mapping, dict) else None
    mapping_ok = isinstance(mapping_manifest, dict) and paths["mapping_input"].exists() and mapping_value_hash == bindings.get("mapping_sha256") and mapping_manifest.get("mapping_sha256") == mapping_value_hash and mapping_manifest.get("mapping_input_sha256") == sha256_file(paths["mapping_input"]) and mapping_manifest.get("status") == "AUTHORIZED" and mapping_manifest.get("authorization") == "AUTHORIZED" and mapping_manifest.get("block_manifest_hash") == blocks_hash and mapping_manifest.get("outline_review_hash") == review_hash and mapping_manifest.get("mapping_validation_sha256") == (sha256_file(paths["mapping_validation"]) if paths["mapping_validation"].exists() else None)
    checks.append(_check("FINAL-000", "PASS" if mapping_ok else "FAIL", "authorized canonical mapping remains current", "current" if mapping_ok else "stale or invalid", [str(paths["mapping"]), str(paths["mapping_manifest"])]))
    map000 = _read_json(paths["map000"])
    map000_ok = isinstance(map000, dict) and map000.get("status") == "PASS" and map000.get("mapping_sha256") == mapping_value_hash
    checks.append(_check("FINAL-000", "PASS" if map000_ok else "FAIL", "MAP-000 remains a current PASS admission", "PASS" if map000_ok else "invalid", [str(paths["map000"])]))
    assembly_manifest = _read_json(paths["assembly_manifest"])
    assembly_manifest_hash = sha256_file(paths["assembly_manifest"]) if paths["assembly_manifest"].exists() else None
    verification_manifest = _read_json(paths["verification"])
    verification_manifest_hash = sha256_file(paths["verification"]) if paths["verification"].exists() else None
    pipeline_manifest = _read_json(paths["pipeline_manifest"])
    pipeline_ok = isinstance(pipeline_manifest, dict) and pipeline_manifest.get("pipeline_version") == PIPELINE_VERSION and pipeline_manifest.get("pipeline_bundle_sha256") == pipeline_bundle_hash(root) and bindings.get("pipeline_version") == PIPELINE_VERSION
    checks.append(_check("FINAL-000", "PASS" if pipeline_ok else "FAIL", "pipeline version and bundle remain compatible", "current" if pipeline_ok else "stale", [str(paths["pipeline_manifest"])]))
    contract_hash = sha256_file(root / "pipeline/contracts/assembly.md") if (root / "pipeline/contracts/assembly.md").exists() else None
    assembly_dependencies = _read_json(paths["assembly_dependencies"])
    expected_dependency = assembly_dependency_payload(source_manifest_hash, blocks_hash, outline or {}, review_hash, mapping_value_hash, sha256_file(paths["mapping_manifest"]) if paths["mapping_manifest"].exists() else None, contract_hash, candidate_hash)
    assembly_ok = isinstance(assembly_manifest, dict) and assembly_manifest_hash == bindings.get("assembly_manifest_hash") and assembly_manifest.get("candidate_sha256") == candidate_hash and assembly_manifest.get("dependencies") == expected_dependency and assembly_dependencies == expected_dependency and assembly_manifest.get("dependency_sha256") == sha256_bytes(canonical_json_bytes(expected_dependency))
    checks.append(_check("FINAL-000", "PASS" if assembly_ok else "FAIL", "assembly manifest and dependency record remain current", "current" if assembly_ok else "stale or invalid", [str(paths["assembly_manifest"]), str(paths["assembly_dependencies"])]))
    verification_ok = isinstance(verification_manifest, dict) and verification_manifest_hash == bindings.get("verification_manifest_hash") and verification_manifest.get("status") == "VERIFIED" and verification_manifest.get("pipeline_version") == PIPELINE_VERSION
    checks.append(_check("FINAL-000", "PASS" if verification_ok else "FAIL", "verification manifest remains current and VERIFIED", "current" if verification_ok else "stale or invalid", [str(paths["verification"])]))
    status = "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL"
    return {"status": status, "checks": checks, "certificate": certificate, "candidate_sha256": candidate_hash, "release_certificate_hash": sha256_file(paths["certificate"]) if paths["certificate"].exists() else None}


def finalization_admission(root: Path, evidence_class: str = "REPOSITORY", allow_existing_final: bool = False) -> dict[str, Any]:
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(evidence_class)
    paths = _paths(root)
    checks: list[dict[str, Any]] = []
    missing = [name for name, path in paths.items() if name not in {"final", "final_manifest"} and not path.exists()]
    checks.append(_check("FINAL-000", "PASS" if not missing else "BLOCKED", "certified candidate finalization dependency chain exists", "all prerequisites present" if not missing else f"missing: {', '.join(missing)}", [str(paths[name]) for name in missing]))
    certificate = _read_json(paths["certificate"])
    cert_verification = _revalidate_certificate_without_assembly(root, evidence_class)
    cert_schema_ok = isinstance(certificate, dict) and certificate.get("artifact_type") == "release_certificate" and certificate.get("certificate_version") == "1.8.0" and certificate.get("status") == "CERTIFIED" and certificate.get("certification_status") == "CERTIFIED" and certificate.get("finalization") == "FORBIDDEN_IN_REVISION_1_8"
    checks.append(_check("FINAL-000", "PASS" if cert_schema_ok and cert_verification.get("status") == "PASS" else "FAIL", "current hash-bound CERTIFIED certificate independently revalidates", "valid" if cert_schema_ok and cert_verification.get("status") == "PASS" else "stale, invalid, or absent", [str(paths["certificate"])]))
    candidate = paths["candidate"].read_bytes() if paths["candidate"].exists() else None
    candidate_hash = sha256_bytes(candidate) if candidate is not None else None
    candidate_ok = candidate is not None and candidate_hash == _certificate_binding(certificate or {}, "candidate_sha256")
    checks.append(_check("FINAL-000", "PASS" if candidate_ok else "FAIL", "certificate candidate hash matches current candidate bytes", "match" if candidate_ok else "mismatch or absent", [str(paths["candidate"]), str(paths["certificate"])]))
    dependency_names = ["source_manifest_hash", "block_manifest_hash", "outline_raw_sha256", "outline_normalized_sha256", "outline_review_hash", "mapping_sha256", "assembly_manifest_hash", "verification_manifest_hash", "pipeline_version"]
    dependency_ok = all(_certificate_binding(certificate or {}, key) is not None for key in dependency_names)
    current_dependency_paths = {
        "source_manifest_hash": paths["source_manifest"],
        "block_manifest_hash": paths["blocks_manifest"],
        "mapping_sha256": paths["mapping_manifest"],
        "assembly_manifest_hash": paths["assembly_manifest"],
        "verification_manifest_hash": paths["verification"],
    }
    for key, path in current_dependency_paths.items():
        if not path.exists():
            dependency_ok = False
        elif key == "mapping_sha256":
            mapping_value = _read_json(paths["mapping"])
            if not isinstance(mapping_value, dict) or _certificate_binding(certificate or {}, key) != mapping_sha256(mapping_value):
                dependency_ok = False
        elif _certificate_binding(certificate or {}, key) != sha256_file(path):
            dependency_ok = False
    pipeline_manifest = _read_json(paths["pipeline_manifest"])
    pipeline_ok = isinstance(pipeline_manifest, dict) and pipeline_manifest.get("pipeline_version") == PIPELINE_VERSION and pipeline_manifest.get("pipeline_bundle_sha256") == pipeline_bundle_hash(root) and _certificate_binding(certificate or {}, "pipeline_version") == PIPELINE_VERSION
    dependency_ok = dependency_ok and pipeline_ok
    checks.append(_check("FINAL-000", "PASS" if dependency_ok else "FAIL", "all certificate dependency hashes and current pipeline bundle remain valid", "current" if dependency_ok else "stale or unavailable", [str(path) for path in current_dependency_paths.values()] + [str(paths["pipeline_manifest"])]))
    existing_ok = allow_existing_final or not paths["final"].exists()
    checks.append(_check("FINAL-000", "PASS" if existing_ok else "BLOCKED", "no preexisting final artifact is overwritten", "absent or verification-only" if existing_ok else "preexisting BOOK_FINAL.md", [str(paths["final"])]))
    status = "AUTHORIZED" if all(item["status"] == "PASS" for item in checks) else "BLOCKED"
    result = {
        "artifact_type": "finalization_admission",
        "artifact_version": FINALIZATION_SCHEMA_VERSION,
        "evidence_class": evidence_class,
        "status": status,
        "finalization_status": "AUTHORIZED" if status == "AUTHORIZED" else "BLOCKED",
        "checks": checks,
        "candidate_sha256": candidate_hash,
        "release_certificate_hash": sha256_file(paths["certificate"]) if paths["certificate"].exists() else None,
    }
    write_json(root / "artifacts/verification/FINAL_000_ADMISSION.json", result)
    write_text(root / "artifacts/verification/FINAL_000_ADMISSION.md", report("FINAL-000_CERTIFIED_CANDIDATE_ADMISSION", "VERIFIED" if status == "AUTHORIZED" else "BLOCKED", checks, "Finalization only materializes an existing certified candidate; it never assembles or rewrites content.", evidence_class=evidence_class))
    result["certificate"] = certificate
    result["paths"] = paths
    return result


def _atomic_binary_copy(candidate_path: Path, final_path: Path) -> tuple[bool, str | None]:
    candidate_bytes = candidate_path.read_bytes()
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".BOOK_FINAL.", suffix=".tmp", dir=final_path.parent, delete=False) as handle:
            temporary_path = Path(handle.name)
            handle.write(candidate_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        if temporary_path.read_bytes() != candidate_bytes or sha256_file(temporary_path) != sha256_bytes(candidate_bytes):
            return False, None
        os.replace(temporary_path, final_path)
        temporary_path = None
        try:
            directory_fd = os.open(final_path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
        return True, sha256_file(final_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _final_manifest(root: Path, admission: Mapping[str, Any], final_hash: str, roundtrip: bool, provenance: bool, completeness: bool, evidence_class: str) -> dict[str, Any]:
    certificate = admission.get("certificate") or {}
    bindings = certificate.get("bindings", {}) if isinstance(certificate, Mapping) else {}
    paths = _paths(root)
    candidate_hash = admission.get("candidate_sha256")
    certificate_hash = admission.get("release_certificate_hash")
    return {
        "manifest_type": "final",
        "manifest_version": FINALIZATION_SCHEMA_VERSION,
        "evidence_class": evidence_class,
        "status": "FINALIZED" if final_hash == candidate_hash and roundtrip and provenance and completeness else "INVALID",
        "finalization_status": "FINALIZED" if final_hash == candidate_hash and roundtrip and provenance and completeness else "INVALID",
        "release_status": "READY" if final_hash == candidate_hash and roundtrip and provenance and completeness else "BLOCKED",
        "candidate_sha256": candidate_hash,
        "final_sha256": final_hash,
        "release_certificate_hash": certificate_hash,
        "source_manifest_hash": bindings.get("source_manifest_hash"),
        "block_manifest_hash": bindings.get("block_manifest_hash"),
        "outline_raw_sha256": bindings.get("outline_raw_sha256"),
        "outline_normalized_sha256": bindings.get("outline_normalized_sha256"),
        "outline_review_hash": bindings.get("outline_review_hash"),
        "mapping_sha256": bindings.get("mapping_sha256"),
        "assembly_manifest_hash": bindings.get("assembly_manifest_hash"),
        "verification_manifest_hash": bindings.get("verification_manifest_hash"),
        "pipeline_version": PIPELINE_VERSION,
        "finalization_schema_version": FINALIZATION_SCHEMA_VERSION,
        "final_artifact": paths["final"].relative_to(root).as_posix(),
        "candidate_artifact": paths["candidate"].relative_to(root).as_posix(),
        "operation": "binary-safe exact byte copy; no assembly, parsing, normalization, or rewriting",
        "final_roundtrip": "PASS" if roundtrip else "FAIL",
        "final_provenance": "PASS" if provenance else "FAIL",
        "final_completeness": "PASS" if completeness else "FAIL",
    }


def _write_final_verification(root: Path, status: str, checks: Sequence[Mapping[str, Any]], evidence_class: str) -> dict[str, Any]:
    result = {
        "artifact_type": "final_verification",
        "artifact_version": FINALIZATION_SCHEMA_VERSION,
        "evidence_class": evidence_class,
        "status": status,
        "checks": list(checks),
        "runtime_timestamps_excluded": True,
    }
    write_json(root / "artifacts/verification/FINAL_VERIFICATION.json", result)
    report_status = "VERIFIED" if status == "FINALIZED" else "BLOCKED" if status == "INVALID" else status
    write_text(root / "artifacts/verification/FINAL_VERIFICATION.md", report("FINAL_VERIFICATION", report_status, checks, "Final artifact verification only; no finalization repair is performed.", evidence_class=evidence_class))
    return result


def verify_final(root: Path, evidence_class: str = "REPOSITORY") -> dict[str, Any]:
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(evidence_class)
    admission = finalization_admission(root, evidence_class, allow_existing_final=True)
    paths = _paths(root)
    checks = list(admission["checks"])
    candidate = paths["candidate"].read_bytes() if paths["candidate"].exists() else None
    final = paths["final"].read_bytes() if paths["final"].exists() else None
    byte_identity = candidate is not None and final is not None and candidate == final
    checks.append(_check("FINAL-001", "PASS" if byte_identity else "FAIL", "BOOK_FINAL.md bytes equal certified candidate bytes", "byte-identical" if byte_identity else "missing or different", [str(paths["candidate"]), str(paths["final"])]))
    candidate_hash = sha256_bytes(candidate) if candidate is not None else None
    final_hash = sha256_bytes(final) if final is not None else None
    hash_ok = final_hash is not None and final_hash == candidate_hash and final_hash == _certificate_binding(admission.get("certificate") or {}, "candidate_sha256")
    checks.append(_check("FINAL-001", "PASS" if hash_ok else "FAIL", "final SHA256 equals candidate and certificate candidate hash", "match" if hash_ok else "mismatch", [str(paths["final"]), str(paths["certificate"])]))
    roundtrip, provenance, completeness, affected = _final_payload_check(root, candidate or b"", final or b"")
    checks.append(_check("FINAL-ROUNDTRIP", "PASS" if roundtrip else "FAIL", "final payload sequence equals certified candidate payload sequence", "PASS" if roundtrip else "FAIL", [str(paths["final"])]))
    checks.append(_check("FINAL-PROVENANCE", "PASS" if provenance else "FAIL", "final payload provenance remains traversable through candidate and source blocks", "PASS" if provenance else "FAIL", [str(paths["final"]), str(paths["assembly_manifest"])], affected))
    checks.append(_check("FINAL-COMPLETE", "PASS" if completeness else "FAIL", "final payload identity set equals certified candidate identity set", "PASS" if completeness else "FAIL", [str(paths["final"]), str(paths["mapping_manifest"])]))
    manifest = _read_json(paths["final_manifest"])
    expected_manifest = _final_manifest(root, admission, final_hash or "", roundtrip, provenance, completeness, evidence_class)
    manifest_ok = isinstance(manifest, dict) and manifest == expected_manifest
    checks.append(_check("FINAL-RELEASE", "PASS" if manifest_ok and byte_identity and hash_ok and roundtrip and provenance and completeness else "FAIL", "final manifest, certificate, hashes, provenance, completeness, and roundtrip are valid", "valid" if manifest_ok and byte_identity and hash_ok and roundtrip and provenance and completeness else "invalid", [str(paths["final_manifest"])]))
    status = "FINALIZED" if all(item.get("status") == "PASS" for item in checks) else "INVALID" if paths["final"].exists() else "BLOCKED"
    verification = _write_final_verification(root, status, checks, evidence_class)
    return {"status": status, "checks": checks, "final_manifest": manifest, "verification": verification}


def finalize_candidate(root: Path, evidence_class: str = "REPOSITORY") -> dict[str, Any]:
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(evidence_class)
    admission = finalization_admission(root, evidence_class, allow_existing_final=False)
    if admission["status"] != "AUTHORIZED":
        return {"status": "BLOCKED", "stage": "finalization admission", "admission": admission}
    paths = admission["paths"]
    final_path = paths["final"]
    final_path.parent.mkdir(parents=True, exist_ok=True)
    copied, final_hash = _atomic_binary_copy(paths["candidate"], final_path)
    candidate_bytes = paths["candidate"].read_bytes()
    final_bytes = final_path.read_bytes() if final_path.exists() else b""
    byte_identity = copied and candidate_bytes == final_bytes
    roundtrip, provenance, completeness, affected = _final_payload_check(root, candidate_bytes, final_bytes)
    checks = list(admission["checks"])
    checks.append(_check("FINAL-001", "PASS" if byte_identity and final_hash == admission.get("candidate_sha256") else "FAIL", "binary finalization preserves exact candidate bytes and hash", "byte-identical" if byte_identity and final_hash == admission.get("candidate_sha256") else "mismatch", [str(paths["candidate"]), str(final_path)]))
    checks.append(_check("FINAL-ROUNDTRIP", "PASS" if roundtrip else "FAIL", "final payload roundtrip equals certified candidate payload sequence", "PASS" if roundtrip else "FAIL", [str(final_path)]))
    checks.append(_check("FINAL-PROVENANCE", "PASS" if provenance else "FAIL", "final provenance remains traversable", "PASS" if provenance else "FAIL", [str(final_path), str(paths["assembly_manifest"])], affected))
    checks.append(_check("FINAL-COMPLETE", "PASS" if completeness else "FAIL", "final completeness equals certified candidate completeness", "PASS" if completeness else "FAIL", [str(final_path)]))
    if not byte_identity or not roundtrip or not provenance or not completeness:
        if final_path.exists():
            final_path.unlink()
        _write_final_verification(root, "INVALID", checks, evidence_class)
        return {"status": "INVALID", "stage": "finalization verification", "admission": admission, "checks": checks}
    manifest = _final_manifest(root, admission, final_hash or "", roundtrip, provenance, completeness, evidence_class)
    write_json(paths["final_manifest"], manifest)
    checks.append(_check("FINAL-RELEASE", "PASS" if manifest.get("status") == "FINALIZED" and manifest.get("candidate_sha256") == manifest.get("final_sha256") else "FAIL", "final manifest binds exact candidate and final hashes", "valid" if manifest.get("status") == "FINALIZED" else "invalid", [str(paths["final_manifest"])]))
    verification = _write_final_verification(root, "FINALIZED" if all(item["status"] == "PASS" for item in checks) else "INVALID", checks, evidence_class)
    if verification["status"] != "FINALIZED":
        return {"status": "INVALID", "stage": "finalization verification", "admission": admission, "checks": checks, "final_manifest": manifest}
    return {"status": "FINALIZED", "stage": "finalization", "admission": admission, "checks": checks, "final_manifest": manifest, "final": final_path.relative_to(root).as_posix()}
