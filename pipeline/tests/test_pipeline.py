from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.core import (
    CANONICALIZATION_CONTRACT,
    canonical_json_bytes,
    canonicalize,
    decompose,
    outline_preflight,
    parse_outline,
    reconstruct,
    sha256_bytes,
    validate_mapping_admission,
    validate_outline_proposal,
    verify_outline_review,
    write_json,
)


class CanonicalizationTests(unittest.TestCase):
    def test_raw_and_normalized_hashes_are_distinct_when_line_endings_change(self) -> None:
        lf = "café\n".encode("utf-8")
        crlf = "café\r\n".encode("utf-8")
        self.assertNotEqual(hashlib.sha256(lf).hexdigest(), hashlib.sha256(crlf).hexdigest())
        self.assertEqual(canonicalize(lf), canonicalize(crlf))
        self.assertEqual(sha256_bytes(canonicalize(lf)), sha256_bytes(canonicalize(crlf)))

    def test_nfc_is_the_only_unicode_normalization(self) -> None:
        composed = "é\n".encode("utf-8")
        decomposed = "e\u0301\n".encode("utf-8")
        self.assertNotEqual(composed, decomposed)
        self.assertEqual(canonicalize(composed), canonicalize(decomposed))

    def test_whitespace_and_blank_lines_are_preserved(self) -> None:
        data = b"a  \n\n\n b\n"
        self.assertEqual(canonicalize(data), data)
        self.assertEqual(CANONICALIZATION_CONTRACT["trailing_whitespace_policy"], "PRESERVE")


class DecompositionTests(unittest.TestCase):
    def test_decomposition_is_lossless_and_message_spans_are_contiguous(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "Source.md"
            source.write_bytes("# Preamble\n\n## [1] USER\n\nOne\n\n## [2] CHATGPT\n\nTwo\n".encode())
            manifest, blocks = decompose(source)
            self.assertEqual(len(blocks), 3)
            self.assertEqual(reconstruct(blocks), canonicalize(source.read_bytes()))
            self.assertEqual(blocks[0]["source"]["start_byte"], 0)
            self.assertEqual(blocks[-1]["source"]["end_byte"], source.stat().st_size)
            for left, right in zip(blocks, blocks[1:]):
                self.assertEqual(left["source"]["end_byte"], right["source"]["start_byte"])

    def test_fallback_without_message_headings_is_one_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "Source.md"
            source.write_bytes(b"arbitrary\ntext\n")
            manifest, blocks = decompose(source)
            self.assertEqual(len(blocks), 1)
            self.assertEqual(blocks[0]["original_text"], "arbitrary\ntext\n")


class OutlineTests(unittest.TestCase):
    def test_outline_ids_and_hierarchy_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outline_path = Path(directory) / "BOOK_OUTLINE.md"
            outline_path.write_text("# Volume I\n## Chapter I\n### Section A\n### Section B\n", encoding="utf-8")
            outline = parse_outline(outline_path)
            self.assertEqual([node["outline_id"] for node in outline["nodes"]], ["O0001", "O0002", "O0003", "O0004"])
            self.assertEqual(outline["nodes"][2]["path"], ["Volume I", "Chapter I", "Section A"])
            self.assertEqual(outline["nodes"][3]["chapter"], "Chapter I")
            baseline_hash = outline["normalized_sha256"]
            # OM-01, OM-03, OM-07, OM-09, and OM-10: authoritative structural mutations.
            outline_path.write_text("# Volume I\n## Chapter I\n### Renamed\n### Section B\n", encoding="utf-8")
            mutated, _ = outline_preflight(outline_path)
            self.assertNotEqual(mutated["normalized_sha256"], baseline_hash)
            self.assertEqual(mutated["status"], "VERIFIED")
            outline_path.write_text("# Volume I\n## V01 — Chapter I\n### V01-P01-C99 — Section A\n", encoding="utf-8")
            id_mutation, _ = outline_preflight(outline_path)
            self.assertNotEqual(id_mutation["normalized_sha256"], baseline_hash)
            outline_path.write_text("# Volume I\n### Chapter I\n", encoding="utf-8")
            hierarchy_mutation, _ = outline_preflight(outline_path)
            self.assertEqual(next(item for item in hierarchy_mutation["checks"] if item["gate_id"] == "OP-006")["status"], "FAIL")
            outline_path.write_text("# BOOK — Test\n## V01 — One\n## V01 — Duplicate\n", encoding="utf-8")
            duplicate_id_mutation, _ = outline_preflight(outline_path)
            self.assertEqual(next(item for item in duplicate_id_mutation["checks"] if item["gate_id"] == "OP-007")["status"], "FAIL")
            proposal = {"nodes": [{"outline_id": "V01", "parent_id": "MISSING", "source_block_ids": ["B9999"], "support_type": "DIRECT", "confidence": "HIGH"}], "source_coverage": {"unmapped_block_ids": []}}
            support_errors = validate_outline_proposal(proposal, ["B0001"], parse_outline(outline_path))
            self.assertTrue(any("unknown blocks" in error for error in support_errors))
            self.assertTrue(any("unresolved parent" in error for error in support_errors))
            deleted_support = {"nodes": [{"outline_id": "V01", "parent_id": None, "source_block_ids": [], "support_type": "DIRECT", "confidence": "HIGH"}], "source_coverage": {"unmapped_block_ids": []}}
            self.assertTrue(any("no source support" in error for error in validate_outline_proposal(deleted_support, ["B0001"], parse_outline(outline_path))))
            reordered = outline_path.read_text(encoding="utf-8").replace("### Chapter I", "### Chapter II\n### Chapter I")
            outline_path.write_text(reordered, encoding="utf-8")
            reordered_result, _ = outline_preflight(outline_path)
            self.assertNotEqual(reordered_result["normalized_sha256"], baseline_hash)


class OutlineReviewMutationTests(unittest.TestCase):
    """OR-01..OR-10 are fixture-boundary tests, never repository approval."""

    def _fixture(self, directory: str, status: str = "REVIEWED") -> tuple[Path, Path, dict[str, object], bytes]:
        root = Path(directory)
        outline_path = root / "BOOK_OUTLINE.md"
        outline_bytes = f"<!-- outline_status: {status} -->\n<!-- outline_version: 1.5 -->\n# BOOK — Fixture\n## V01 — Fixture chapter\n".encode("utf-8")
        outline_path.write_bytes(outline_bytes)
        outline = parse_outline(outline_path)
        review = {
            "evidence_class": "MANUAL_REVIEW",
            "decision": "ACCEPT",
            "outline_status_before": "PROVISIONAL",
            "outline_status_after": "REVIEWED",
            "reviewer": "fixture-reviewer",
            "reviewed_at_utc": "2026-09-14T00:00:00Z",
            "reviewed_outline_raw_sha256": sha256_bytes(outline_bytes),
            "reviewed_outline_normalized_sha256": outline["normalized_sha256"],
        }
        write_json(root / "artifacts" / "review" / "OUTLINE_REVIEW.json", review)
        return outline_path, root, review, outline_bytes

    def _verify(self, root: Path, outline_path: Path) -> dict[str, object]:
        outline = parse_outline(outline_path)
        return verify_outline_review(root, outline, "UNIT_TEST", outline_path)

    def test_OR_01_missing_review_record_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outline_path = Path(directory) / "BOOK_OUTLINE.md"
            outline_path.write_text("<!-- outline_status: REVIEWED -->\n# BOOK — Fixture\n", encoding="utf-8")
            result = self._verify(Path(directory), outline_path)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["checks"][0]["gate_id"], "REVIEW-001")

    def test_OR_02_manual_review_evidence_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outline_path, root, review, _ = self._fixture(directory)
            review["evidence_class"] = "REPOSITORY"
            write_json(root / "artifacts/review/OUTLINE_REVIEW.json", review)
            self.assertEqual(self._verify(root, outline_path)["status"], "BLOCKED")

    def test_OR_03_accept_with_changes_requires_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outline_path, root, review, _ = self._fixture(directory)
            review["decision"] = "ACCEPT_WITH_CHANGES"
            write_json(root / "artifacts/review/OUTLINE_REVIEW.json", review)
            result = self._verify(root, outline_path)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(next(check for check in result["checks"] if check["gate_id"] == "REVIEW-003")["status"], "FAIL")

    def test_OR_04_reject_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outline_path, root, review, _ = self._fixture(directory)
            review["decision"] = "REJECT"
            write_json(root / "artifacts/review/OUTLINE_REVIEW.json", review)
            self.assertEqual(self._verify(root, outline_path)["status"], "BLOCKED")

    def test_OR_05_reviewer_metadata_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outline_path, root, review, _ = self._fixture(directory)
            review["reviewer"] = ""
            write_json(root / "artifacts/review/OUTLINE_REVIEW.json", review)
            self.assertEqual(self._verify(root, outline_path)["status"], "BLOCKED")

    def test_OR_06_review_timestamp_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outline_path, root, review, _ = self._fixture(directory)
            review["reviewed_at_utc"] = ""
            write_json(root / "artifacts/review/OUTLINE_REVIEW.json", review)
            self.assertEqual(self._verify(root, outline_path)["status"], "BLOCKED")

    def test_OR_07_raw_outline_hash_mutation_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outline_path, root, review, _ = self._fixture(directory)
            review["reviewed_outline_raw_sha256"] = "0" * 64
            write_json(root / "artifacts/review/OUTLINE_REVIEW.json", review)
            self.assertEqual(self._verify(root, outline_path)["status"], "BLOCKED")

    def test_OR_08_normalized_outline_hash_mutation_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outline_path, root, review, _ = self._fixture(directory)
            review["reviewed_outline_normalized_sha256"] = "0" * 64
            write_json(root / "artifacts/review/OUTLINE_REVIEW.json", review)
            self.assertEqual(self._verify(root, outline_path)["status"], "BLOCKED")

    def test_OR_09_current_outline_must_be_reviewed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outline_path, root, review, _ = self._fixture(directory, status="PROVISIONAL")
            self.assertEqual(self._verify(root, outline_path)["status"], "BLOCKED")

    def test_OR_10_outline_bytes_changed_after_review_are_revoked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outline_path, root, _, original = self._fixture(directory)
            outline_path.write_bytes(original + b"\n")
            result = self._verify(root, outline_path)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(next(check for check in result["checks"] if check["gate_id"] == "REVIEW-006")["status"], "FAIL")

    def test_review_replay_passes_then_revocation_and_exact_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outline_path, root, _, original = self._fixture(directory)
            self.assertEqual(self._verify(root, outline_path)["status"], "VERIFIED")
            outline_path.write_bytes(original.replace(b"Fixture chapter", b"Changed chapter"))
            self.assertEqual(self._verify(root, outline_path)["status"], "BLOCKED")
            outline_path.write_bytes(original)
            self.assertEqual(self._verify(root, outline_path)["status"], "VERIFIED")

    def test_MAP_000_precedes_mapping_gates_and_does_not_assemble(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "Source.md"
            source_path.write_text("source\n", encoding="utf-8")
            outline_path = root / "BOOK_OUTLINE.md"
            outline_bytes = "<!-- outline_status: REVIEWED -->\n# BOOK — Fixture\n".encode("utf-8")
            outline_path.write_bytes(outline_bytes)
            outline = parse_outline(outline_path)
            _, blocks = decompose(source_path)
            source_manifest, _ = decompose(source_path)
            review = {
                "evidence_class": "MANUAL_REVIEW",
                "decision": "ACCEPT",
                "outline_status_before": "PROVISIONAL",
                "outline_status_after": "REVIEWED",
                "reviewer": "fixture-reviewer",
                "reviewed_at_utc": "2026-09-14T00:00:00Z",
                "reviewed_outline_raw_sha256": sha256_bytes(outline_bytes),
                "reviewed_outline_normalized_sha256": outline["normalized_sha256"],
            }
            write_json(root / "artifacts/review/OUTLINE_REVIEW.json", review)
            mapping = {
                "mapping_version": "1.0",
                "source_manifest_hash": sha256_bytes(canonical_json_bytes(source_manifest)),
                "outline_hash": outline["normalized_sha256"],
                "outline_status": "REVIEWED",
                "review_status": "REVIEWED",
                "entries": [{"block_id": blocks[0]["block_id"], "role": "PRIMARY", "target": "BOOK", "placement": 1}],
            }
            write_json(root / "artifacts/mapping/mapping.input.json", mapping)
            result = validate_mapping_admission(root, evidence_class="UNIT_TEST")
            self.assertEqual(result["status"], "VERIFIED")
            self.assertEqual(result["mapping_validation"]["checks"][0]["gate_id"], "MAP-000")
            self.assertTrue(all(check["status"] == "PASS" for check in result["mapping_validation"]["checks"]))
            self.assertFalse((root / "artifacts/assembly/BOOK_FINAL_CANDIDATE.md").exists())


if __name__ == "__main__":
    unittest.main()
