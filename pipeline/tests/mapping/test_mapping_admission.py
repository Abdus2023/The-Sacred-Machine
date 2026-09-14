from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.core import (
    PIPELINE_VERSION,
    canonical_json_bytes,
    canonical_mapping_bytes,
    decompose,
    mapping_sha256,
    parse_outline,
    sha256_bytes,
    validate_mapping_admission,
    write_json,
)


class MappingAdmissionMutationTests(unittest.TestCase):
    """MA-01..MA-15 are fixture-boundary tests and never authorize the repository."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "pipeline/contracts").mkdir(parents=True)
        (self.root / "pipeline/contracts/mapping.md").write_text("fixture mapping contract\n", encoding="utf-8")
        self.source_path = self.root / "Source.md"
        self.source_path.write_text("source one\nsource two\n", encoding="utf-8")
        self.outline_path = self.root / "BOOK_OUTLINE.md"
        outline_bytes = "<!-- outline_status: REVIEWED -->\n# BOOK — Fixture\n".encode("utf-8")
        self.outline_path.write_bytes(outline_bytes)
        self.outline = parse_outline(self.outline_path)
        source_manifest, self.blocks = decompose(self.source_path)
        self.source_manifest_hash = sha256_bytes(canonical_json_bytes(source_manifest))
        review = {
            "evidence_class": "MANUAL_REVIEW",
            "decision": "ACCEPT",
            "review_status": "REVIEWED",
            "outline_status_before": "PROVISIONAL",
            "outline_status_after": "REVIEWED",
            "reviewer": "fixture-reviewer",
            "reviewed_at_utc": "2026-09-14T00:00:00Z",
            "outline_raw_sha256": sha256_bytes(outline_bytes),
            "outline_normalized_sha256": self.outline["normalized_sha256"],
        }
        self.review_path = self.root / "artifacts/review/OUTLINE_REVIEW.json"
        write_json(self.review_path, review)
        # A first gated run creates only the mechanical source/block manifests;
        # it cannot invent mapping input.
        validate_mapping_admission(self.root, evidence_class="UNIT_TEST")
        self.block_manifest_hash = sha256_bytes((self.root / "pipeline/manifests/blocks.manifest.json").read_bytes())
        self.mapping_path = self.root / "artifacts/mapping/mapping.input.json"
        self.mapping = {
            "mapping_version": "1.0",
            "mapping_schema_version": "1.6.0",
            "pipeline_version": PIPELINE_VERSION,
            "source_manifest_hash": self.source_manifest_hash,
            "block_manifest_hash": self.block_manifest_hash,
            "outline_raw_sha256": self.outline["raw_sha256"],
            "outline_normalized_sha256": self.outline["normalized_sha256"],
            "outline_review_hash": sha256_bytes(self.review_path.read_bytes()),
            "review_status": "REVIEWED",
            "entries": [
                {
                    "block_id": block["block_id"],
                    "target_id": "BOOK",
                    "role": "PRIMARY",
                    "placement": block["source"]["sequence"],
                }
                for block in self.blocks
            ],
            "unmapped_block_ids": [],
        }

    def tearDown(self) -> None:
        self.directory.cleanup()

    def run_mapping(self, mutation: dict | None = None) -> dict:
        write_json(self.mapping_path, mutation or self.mapping)
        return validate_mapping_admission(self.root, evidence_class="UNIT_TEST")

    def assert_blocked(self, mutation: dict) -> dict:
        result = self.run_mapping(mutation)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertNotEqual(result.get("mapping_validation", {}).get("status"), "AUTHORIZED")
        self.assertFalse((self.root / "artifacts/assembly/BOOK_FINAL_CANDIDATE.md").exists())
        self.assertFalse((self.root / "release/BOOK_FINAL.md").exists())
        return result

    def test_baseline_authorizes_mapping_only(self) -> None:
        result = self.run_mapping()
        self.assertEqual(result["status"], "AUTHORIZED")
        self.assertEqual(result["mapping_validation"]["status"], "AUTHORIZED")
        self.assertEqual(result["mapping_authorization"]["mapping_sha256"], mapping_sha256(self.mapping))
        self.assertEqual((self.root / "artifacts/mapping/mapping.canonical.json").read_bytes(), canonical_mapping_bytes(self.mapping))
        self.assertFalse((self.root / "artifacts/assembly/BOOK_FINAL_CANDIDATE.md").exists())
        self.assertFalse((self.root / "release/BOOK_FINAL.md").exists())

    def test_MA_01_source_hash_mutation(self) -> None:
        mutation = copy.deepcopy(self.mapping)
        mutation["source_manifest_hash"] = "0" * 64
        self.assert_blocked(mutation)

    def test_MA_02_outline_hash_mutation(self) -> None:
        mutation = copy.deepcopy(self.mapping)
        mutation["outline_normalized_sha256"] = "0" * 64
        self.assert_blocked(mutation)

    def test_MA_03_review_hash_mutation(self) -> None:
        mutation = copy.deepcopy(self.mapping)
        mutation["outline_review_hash"] = "0" * 64
        self.assert_blocked(mutation)

    def test_MA_04_review_status_mutation(self) -> None:
        mutation = copy.deepcopy(self.mapping)
        mutation["review_status"] = "DRAFT"
        self.assert_blocked(mutation)

    def test_MA_05_unsupported_mapping_version(self) -> None:
        mutation = copy.deepcopy(self.mapping)
        mutation["mapping_version"] = "9.9"
        self.assert_blocked(mutation)

    def test_MA_06_unknown_block_id(self) -> None:
        mutation = copy.deepcopy(self.mapping)
        mutation["entries"][0]["block_id"] = "B9999"
        self.assert_blocked(mutation)

    def test_MA_07_unknown_target_id(self) -> None:
        mutation = copy.deepcopy(self.mapping)
        mutation["entries"][0]["target_id"] = "O9999"
        self.assert_blocked(mutation)

    def test_MA_08_duplicate_primary(self) -> None:
        mutation = copy.deepcopy(self.mapping)
        mutation["entries"].append(copy.deepcopy(mutation["entries"][0]))
        self.assert_blocked(mutation)

    def test_MA_09_invalid_placement(self) -> None:
        mutation = copy.deepcopy(self.mapping)
        mutation["entries"][0]["placement"] = 0
        self.assert_blocked(mutation)

    def test_MA_10_omitted_block(self) -> None:
        mutation = copy.deepcopy(self.mapping)
        mutation["entries"] = mutation["entries"][:-1]
        self.assert_blocked(mutation)

    def test_MA_11_undeclared_unmapped_block(self) -> None:
        mutation = copy.deepcopy(self.mapping)
        mutation["unmapped_block_ids"] = ["B9999"]
        self.assert_blocked(mutation)

    def test_MA_12_duplicate_mapping_entry(self) -> None:
        mutation = copy.deepcopy(self.mapping)
        mutation["entries"].append(copy.deepcopy(mutation["entries"][0]))
        mutation["entries"][-1]["placement"] = 99
        self.assert_blocked(mutation)

    def test_MA_13_unsupported_canonical_order_mutation(self) -> None:
        mutation = copy.deepcopy(self.mapping)
        mutation["canonical_order"] = "filesystem-order"
        self.assert_blocked(mutation)

    def test_MA_14_canonical_hash_mutation(self) -> None:
        mutation = copy.deepcopy(self.mapping)
        mutation["mapping_sha256"] = "0" * 64
        self.assert_blocked(mutation)

    def test_MA_15_stale_review_mutation(self) -> None:
        result = self.run_mapping()
        self.assertEqual(result["status"], "AUTHORIZED")
        review = copy.deepcopy(json.loads(self.review_path.read_text(encoding="utf-8")))
        review["reviewer"] = "different-reviewer"
        write_json(self.review_path, review)
        result = validate_mapping_admission(self.root, evidence_class="UNIT_TEST")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse((self.root / "artifacts/assembly/BOOK_FINAL_CANDIDATE.md").exists())

    def test_mapping_replay_is_byte_deterministic(self) -> None:
        first = self.run_mapping()
        self.assertEqual(first["status"], "AUTHORIZED")
        artifacts = {
            name: (self.root / name).read_bytes()
            for name in (
                "artifacts/mapping/mapping.canonical.json",
                "artifacts/mapping/mapping.validation.json",
                "pipeline/manifests/mapping.manifest.json",
            )
        }
        second = self.run_mapping()
        self.assertEqual(second["status"], "AUTHORIZED")
        for name, data in artifacts.items():
            self.assertEqual((self.root / name).read_bytes(), data, name)


if __name__ == "__main__":
    unittest.main()
