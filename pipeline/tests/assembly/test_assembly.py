from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.core import (
    ASSEMBLY_SCHEMA_VERSION,
    assemble_authorized,
    canonical_json_bytes,
    decompose,
    load_json,
    load_jsonl,
    parse_outline,
    sha256_bytes,
    validate_mapping_admission,
    verify_assembly_candidate,
    write_json,
    write_jsonl,
)


class AssemblyMutationTests(unittest.TestCase):
    """ASM-M01..ASM-M15 are fixture evidence and never repository release evidence."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "pipeline/contracts").mkdir(parents=True)
        (self.root / "pipeline/contracts/mapping.md").write_text("fixture mapping contract\n", encoding="utf-8")
        (self.root / "pipeline/contracts/assembly.md").write_text("fixture assembly contract\n", encoding="utf-8")
        (self.root / "pipeline/schemas").mkdir(parents=True)
        (self.root / "pipeline/schemas/assembly.schema.json").write_text("fixture assembly schema\n", encoding="utf-8")
        write_json(self.root / "pipeline/manifests/pipeline.manifest.json", {"pipeline_version": "1.7.0", "fixture": True})
        self.source_path = self.root / "Source.md"
        self.source_path.write_text("alpha\n\n## [1] USER\n\nOne.\n\n## [2] CHATGPT\n\nTwo.\n", encoding="utf-8")
        self.outline_path = self.root / "BOOK_OUTLINE.md"
        outline_bytes = "<!-- outline_status: REVIEWED -->\n# BOOK — Fixture\n".encode("utf-8")
        self.outline_path.write_bytes(outline_bytes)
        self.outline = parse_outline(self.outline_path)
        source_manifest, self.blocks = decompose(self.source_path)
        self.review_path = self.root / "artifacts/review/OUTLINE_REVIEW.json"
        write_json(self.review_path, {
            "evidence_class": "MANUAL_REVIEW",
            "decision": "ACCEPT",
            "review_status": "REVIEWED",
            "outline_status_before": "PROVISIONAL",
            "outline_status_after": "REVIEWED",
            "reviewer": "fixture-reviewer",
            "reviewed_at_utc": "2026-09-14T00:00:00Z",
            "outline_raw_sha256": sha256_bytes(outline_bytes),
            "outline_normalized_sha256": self.outline["normalized_sha256"],
        })
        validate_mapping_admission(self.root, evidence_class="UNIT_TEST")
        block_manifest_hash = sha256_bytes((self.root / "pipeline/manifests/blocks.manifest.json").read_bytes())
        mapping = {
            "mapping_version": "1.0",
            "mapping_schema_version": "1.6.0",
            "pipeline_version": "1.8.0",
            "source_manifest_hash": sha256_bytes(canonical_json_bytes(source_manifest)),
            "block_manifest_hash": block_manifest_hash,
            "outline_raw_sha256": self.outline["raw_sha256"],
            "outline_normalized_sha256": self.outline["normalized_sha256"],
            "outline_review_hash": sha256_bytes(self.review_path.read_bytes()),
            "review_status": "REVIEWED",
            "entries": [
                {"block_id": block["block_id"], "target_id": "BOOK", "role": "PRIMARY", "placement": block["source"]["sequence"]}
                for block in self.blocks
            ],
            "unmapped_block_ids": [],
        }
        self.mapping_path = self.root / "artifacts/mapping/mapping.input.json"
        write_json(self.mapping_path, mapping)
        mapping_result = validate_mapping_admission(self.root, evidence_class="UNIT_TEST")
        self.assertEqual(mapping_result["status"], "AUTHORIZED")
        assembly_result = assemble_authorized(self.root, evidence_class="FIXTURE")
        self.assertEqual(assembly_result["status"], "VERIFIED")
        self.book_path = self.root / "artifacts/assembly/BOOK_FINAL_CANDIDATE.md"
        self.assembly_path = self.root / "artifacts/assembly/BOOK_ASSEMBLY.jsonl"
        self.assembly_manifest_path = self.root / "pipeline/manifests/assembly.manifest.json"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def verify_current(self) -> dict:
        source_manifest, blocks = decompose(self.source_path)
        return verify_assembly_candidate(
            self.root,
            self.source_path,
            source_manifest,
            blocks,
            parse_outline(self.outline_path),
            load_json(self.root / "artifacts/mapping/mapping.canonical.json"),
            self.book_path,
            self.assembly_path,
            self.assembly_manifest_path,
            "FIXTURE",
        )

    def assert_blocked_assembly(self, result: dict) -> None:
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse((self.root / "release/BOOK_FINAL.md").exists())

    def test_baseline_candidate_is_verified_and_no_finalization_occurs(self) -> None:
        result = self.verify_current()
        self.assertEqual(result["status"], "VERIFIED")
        manifest = load_json(self.assembly_manifest_path)
        self.assertEqual(manifest["assembly_schema_version"], ASSEMBLY_SCHEMA_VERSION)
        self.assertFalse((self.root / "release/RELEASE_CERTIFICATE.json").exists())
        self.assertFalse((self.root / "release/BOOK_FINAL.md").exists())

    def test_candidate_replay_is_byte_deterministic_and_idempotent(self) -> None:
        first = {path: Path(path).read_bytes() for path in (self.book_path, self.assembly_manifest_path, self.assembly_path)}
        result = assemble_authorized(self.root, evidence_class="FIXTURE")
        self.assertEqual(result["status"], "VERIFIED")
        for path, data in first.items():
            self.assertEqual(Path(path).read_bytes(), data, str(path))

    def test_explicitly_unmapped_is_auditable_but_has_no_candidate_payload(self) -> None:
        mapping = load_json(self.mapping_path)
        mapping["entries"][0]["role"] = "EXPLICITLY_UNMAPPED"
        mapping["entries"][0]["target_id"] = None
        mapping["unmapped_block_ids"] = [mapping["entries"][0]["block_id"]]
        write_json(self.mapping_path, mapping)
        self.assertEqual(validate_mapping_admission(self.root, evidence_class="UNIT_TEST")["status"], "AUTHORIZED")
        result = assemble_authorized(self.root, evidence_class="FIXTURE")
        self.assertEqual(result["status"], "VERIFIED")
        candidate = self.book_path.read_bytes()
        unmapped_id = mapping["entries"][0]["block_id"]
        self.assertNotIn(unmapped_id.encode("ascii"), candidate)
        manifest = load_json(self.assembly_manifest_path)
        self.assertIn(unmapped_id, manifest["unmapped_block_ids"])
        disposition = next(item for item in manifest["mapping_dispositions"] if item["block_id"] == unmapped_id)
        self.assertFalse(disposition["payload_contributed"])

    def test_ASM_M01_unauthorized_mapping(self) -> None:
        (self.root / "pipeline/manifests/mapping.manifest.json").unlink()
        self.assert_blocked_assembly(assemble_authorized(self.root, evidence_class="FIXTURE"))

    def test_ASM_M02_stale_mapping(self) -> None:
        mapping = load_json(self.mapping_path)
        mapping["entries"][0]["placement"] = 99
        write_json(self.mapping_path, mapping)
        self.assert_blocked_assembly(assemble_authorized(self.root, evidence_class="FIXTURE"))

    def test_ASM_M03_stale_review(self) -> None:
        review = load_json(self.review_path)
        review["reviewer"] = "changed-reviewer"
        write_json(self.review_path, review)
        self.assert_blocked_assembly(assemble_authorized(self.root, evidence_class="FIXTURE"))

    def test_ASM_M04_source_mutation(self) -> None:
        self.source_path.write_text(self.source_path.read_text(encoding="utf-8").replace("One.", "Changed."), encoding="utf-8")
        self.assert_blocked_assembly(assemble_authorized(self.root, evidence_class="FIXTURE"))

    def test_ASM_M05_block_manifest_mutation(self) -> None:
        manifest = load_json(self.root / "pipeline/manifests/blocks.manifest.json")
        manifest["blocks_artifact_sha256"] = "0" * 64
        write_json(self.root / "pipeline/manifests/blocks.manifest.json", manifest)
        self.assert_blocked_assembly(assemble_authorized(self.root, evidence_class="FIXTURE"))

    def test_ASM_M06_outline_mutation(self) -> None:
        self.outline_path.write_text(self.outline_path.read_text(encoding="utf-8").replace("Fixture", "Changed"), encoding="utf-8")
        self.assert_blocked_assembly(assemble_authorized(self.root, evidence_class="FIXTURE"))

    def test_ASM_M07_mapping_placement_mutation(self) -> None:
        mapping = load_json(self.mapping_path)
        mapping["entries"][0]["placement"] = 77
        write_json(self.mapping_path, mapping)
        self.assert_blocked_assembly(assemble_authorized(self.root, evidence_class="FIXTURE"))

    def test_ASM_M08_block_payload_mutation(self) -> None:
        blocks = load_jsonl(self.root / "artifacts/decomposition/CONTENT_BLOCKS.jsonl")
        blocks[0]["original_text"] = "tampered\n"
        write_jsonl(self.root / "artifacts/decomposition/CONTENT_BLOCKS.jsonl", blocks)
        self.assert_blocked_assembly(assemble_authorized(self.root, evidence_class="FIXTURE"))

    def test_ASM_M09_deleted_mapped_block(self) -> None:
        blocks = load_jsonl(self.root / "artifacts/decomposition/CONTENT_BLOCKS.jsonl")
        write_jsonl(self.root / "artifacts/decomposition/CONTENT_BLOCKS.jsonl", blocks[:-1])
        self.assert_blocked_assembly(assemble_authorized(self.root, evidence_class="FIXTURE"))

    def test_ASM_M10_injected_unauthorized_block(self) -> None:
        self.book_path.write_bytes(self.book_path.read_bytes() + b"<!-- BLOCK: B9999 -->\nunauthorized\n<!-- END BLOCK: B9999 -->\n")
        self.assert_blocked_assembly(self.verify_current())

    def test_ASM_M11_reordered_candidate_blocks(self) -> None:
        records = load_jsonl(self.assembly_path)
        records[1], records[2] = records[2], records[1]
        write_jsonl(self.assembly_path, records)
        self.assert_blocked_assembly(self.verify_current())

    def test_ASM_M12_candidate_payload_mutation(self) -> None:
        self.book_path.write_bytes(self.book_path.read_bytes().replace(b"One.", b"Changed.", 1))
        self.assert_blocked_assembly(self.verify_current())

    def test_ASM_M13_candidate_manifest_mutation(self) -> None:
        manifest = load_json(self.assembly_manifest_path)
        manifest["candidate_sha256"] = "0" * 64
        write_json(self.assembly_manifest_path, manifest)
        self.assert_blocked_assembly(self.verify_current())

    def test_ASM_M14_structural_envelope_mutation(self) -> None:
        self.book_path.write_bytes(self.book_path.read_bytes().replace(b"# BOOK", b"# CHANGED", 1))
        result = self.verify_current()
        self.assert_blocked_assembly(result)

    def test_ASM_M15_duplicate_collapse(self) -> None:
        records = load_jsonl(self.assembly_path)
        write_jsonl(self.assembly_path, [records[0], records[1]])
        self.assert_blocked_assembly(self.verify_current())


if __name__ == "__main__":
    unittest.main()
