"""Executable mutation checks for the mechanical verifier.

These fixtures intentionally remain tiny. They test gate behavior, not the
editorial meaning of the real Arabic source. Every mutation operates on a
copy of an artifact; the source fixture is never repaired to satisfy a gate.
"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.core import (
    assemble,
    canonical_json_bytes,
    canonicalize,
    decompose,
    load_jsonl,
    manifest_content_hash,
    parse_outline,
    release_bindings,
    sha256_bytes,
    verify_release_certificate,
    write_json,
    verify_assembled,
    write_jsonl,
)


class MutationSuite(unittest.TestCase):
    def fixture(self):
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        (root / "artifacts" / "assembly").mkdir(parents=True)
        (root / "pipeline" / "manifests").mkdir(parents=True)
        source_path = root / "Source.md"
        source_path.write_text("# Preamble\n\n## [1] USER\n\nOne.\n\n## [2] CHATGPT\n\nTwo.\n", encoding="utf-8")
        outline_path = root / "BOOK_OUTLINE.md"
        outline_path.write_text("<!-- outline_status: REVIEWED -->\n# Volume\n## Chapter\n### Section\n", encoding="utf-8")
        source_manifest, blocks = decompose(source_path)
        outline = parse_outline(outline_path)
        mapping = {
            "mapping_version": "1.0",
            "mapping_schema_version": "1.6.0",
            "source_manifest_hash": manifest_content_hash(source_manifest),
            "outline_raw_sha256": outline["raw_sha256"],
            "outline_normalized_sha256": outline["normalized_sha256"],
            "outline_review_hash": sha256_bytes(canonical_json_bytes({"fixture_review": True})),
            "review_status": "REVIEWED",
            "entries": [
                {
                    "block_id": block["block_id"],
                    "target_id": "O0003",
                    "placement": block["source"]["sequence"],
                    "role": "PRIMARY",
                }
                for block in blocks
            ],
            "unmapped_block_ids": [],
        }
        book = root / "artifacts" / "assembly" / "BOOK_FINAL_CANDIDATE.md"
        assembly = root / "artifacts" / "assembly" / "BOOK_ASSEMBLY.jsonl"
        assemble(book, assembly, blocks, outline, mapping)
        return directory, root, source_path, source_manifest, blocks, outline, mapping, book, assembly

    @staticmethod
    def gates(result):
        return {item["gate"]: item["status"] for item in result["checks"]}

    def test_baseline_passes(self):
        fixture = self.fixture()
        try:
            _, root, source, manifest, blocks, outline, mapping, book, assembly = fixture
            result = verify_assembled(root, source, manifest, blocks, outline, mapping, book, assembly)
            self.assertEqual(result["status"], "VERIFIED")
        finally:
            fixture[0].cleanup()

    def test_M01_change_one_word_verbatim_fail(self):
        self.assert_book_change_fails("One", "Uno", "G-VRF-002")

    def test_M02_remove_punctuation_verbatim_fail(self):
        self.assert_book_change_fails("One.", "One", "G-VRF-002")

    def test_M03_remove_space_verbatim_fail(self):
        self.assert_book_change_fails("# Preamble", "#Preamble", "G-VRF-002")

    def test_M04_delete_block_completeness_fail(self):
        fixture = self.fixture()
        try:
            _, root, source, manifest, blocks, outline, mapping, book, assembly = fixture
            records = load_jsonl(assembly)
            records = [records[0], *records[2:]]
            write_jsonl(assembly, records)
            result = verify_assembled(root, source, manifest, blocks, outline, mapping, book, assembly)
            self.assertEqual(self.gates(result)["G-VRF-001"], "FAIL")
        finally:
            fixture[0].cleanup()

    def test_M05_duplicate_block_placement_uniqueness_fail(self):
        fixture = self.fixture()
        try:
            _, root, source, manifest, blocks, outline, mapping, book, assembly = fixture
            data = book.read_bytes()
            block = blocks[0]["original_text"].encode("utf-8")
            start = len(data)
            extra = b"<!-- BLOCK: B0001 -->\n" + block
            end = start + len(extra) - len(b"<!-- BLOCK: B0001 -->\n")
            book.write_bytes(data + extra + b"<!-- END BLOCK: B0001 -->\n")
            records = load_jsonl(assembly)
            duplicate = dict(records[1])
            duplicate.update({"placement_index": 999, "start_offset": end - len(block), "end_offset": end})
            write_jsonl(assembly, [records[0], *records[1:], duplicate])
            result = verify_assembled(root, source, manifest, blocks, outline, mapping, book, assembly)
            self.assertEqual(self.gates(result)["G-VRF-001"], "FAIL")
            self.assertEqual(self.gates(result)["G-VRF-001U"], "FAIL")
        finally:
            fixture[0].cleanup()

    def test_M06_swap_two_blocks_order_fail(self):
        fixture = self.fixture()
        try:
            _, root, source, manifest, blocks, outline, mapping, book, assembly = fixture
            records = load_jsonl(assembly)
            payload_records = records[1:]
            payload_records[0], payload_records[1] = payload_records[1], payload_records[0]
            write_jsonl(assembly, [records[0], *payload_records])
            result = verify_assembled(root, source, manifest, blocks, outline, mapping, book, assembly)
            self.assertEqual(self.gates(result)["G-VRF-004"], "FAIL")
        finally:
            fixture[0].cleanup()

    def test_M07_alter_source_heading_source_fail(self):
        fixture = self.fixture()
        try:
            _, root, source, manifest, blocks, outline, mapping, book, assembly = fixture
            source.write_text(source.read_text(encoding="utf-8").replace("## [1] USER", "## [1] EDITED"), encoding="utf-8")
            result = verify_assembled(root, source, manifest, blocks, outline, mapping, book, assembly)
            self.assertEqual(self.gates(result)["G-SRC-001"], "FAIL")
        finally:
            fixture[0].cleanup()

    def test_M08_corrupt_hash_manifest_fail(self):
        fixture = self.fixture()
        try:
            _, root, source, manifest, blocks, outline, mapping, book, assembly = fixture
            corrupted = dict(manifest)
            corrupted["normalized_sha256"] = "0" * 64
            result = verify_assembled(root, source, corrupted, blocks, outline, mapping, book, assembly)
            self.assertEqual(self.gates(result)["G-SRC-001"], "FAIL")
        finally:
            fixture[0].cleanup()

    def test_M09_valid_syntax_block_id_provenance_fail(self):
        fixture = self.fixture()
        try:
            _, root, source, manifest, blocks, outline, mapping, book, assembly = fixture
            mutated = [dict(block) for block in blocks]
            mutated[0]["block_id"] = "B0999"
            result = verify_assembled(root, source, manifest, mutated, outline, mapping, book, assembly)
            gates = self.gates(result)
            self.assertEqual(gates["G-BLK-001"], "FAIL")
            self.assertEqual(gates["G-MAP-001"], "FAIL")
            deleted_block_result = verify_assembled(root, source, manifest, blocks[:-1], outline, mapping, book, assembly)
            self.assertEqual(self.gates(deleted_block_result)["G-BLK-001"], "FAIL")
            duplicated_block_result = verify_assembled(root, source, manifest, blocks + [copy.deepcopy(blocks[0])], outline, mapping, book, assembly)
            self.assertEqual(self.gates(duplicated_block_result)["G-BLK-001"], "FAIL")
            mutations = {
                "mapping_version": "9.9",
                "source_manifest_hash": "0" * 64,
                "outline_normalized_sha256": "0" * 64,
                "review_status": "DRAFT",
            }
            for key, value in mutations.items():
                mutated_mapping = copy.deepcopy(mapping)
                mutated_mapping[key] = value
                mutated_result = verify_assembled(root, source, manifest, blocks, outline, mutated_mapping, book, assembly)
                self.assertEqual(self.gates(mutated_result)["G-MAP-001"], "FAIL", key)
            for entry_key, value in (("block_id", "B0999"), ("target_id", "O9999"), ("role", "INVALID"), ("placement", 0)):
                mutated_mapping = copy.deepcopy(mapping)
                mutated_mapping["entries"][0][entry_key] = value
                mutated_result = verify_assembled(root, source, manifest, blocks, outline, mutated_mapping, book, assembly)
                self.assertEqual(self.gates(mutated_result)["G-MAP-001"], "FAIL", entry_key)
            duplicate_mapping = copy.deepcopy(mapping)
            duplicate_mapping["entries"].append(copy.deepcopy(duplicate_mapping["entries"][0]))
            duplicate_result = verify_assembled(root, source, manifest, blocks, outline, duplicate_mapping, book, assembly)
            self.assertEqual(self.gates(duplicate_result)["G-MAP-001"], "FAIL")
            mutated_outline = copy.deepcopy(outline)
            mutated_outline["normalized_sha256"] = "0" * 64
            outline_result = verify_assembled(root, source, manifest, blocks, mutated_outline, mapping, book, assembly)
            self.assertEqual(self.gates(outline_result)["G-MAP-001"], "FAIL")
        finally:
            fixture[0].cleanup()

    def test_M10_unicode_representation_raw_fails_normalized_matches(self):
        composed = "é\n".encode("utf-8")
        decomposed = "e\u0301\n".encode("utf-8")
        self.assertNotEqual(composed, decomposed)
        self.assertNotEqual(sha256_bytes(composed), sha256_bytes(decomposed))
        self.assertEqual(canonicalize(composed), canonicalize(decomposed))

    def test_M11_line_endings_raw_fails_normalized_matches(self):
        lf = b"line\n"
        crlf = b"line\r\n"
        self.assertNotEqual(sha256_bytes(lf), sha256_bytes(crlf))
        self.assertEqual(canonicalize(lf), canonicalize(crlf))

    def test_M12_paraphrase_verbatim_fail(self):
        self.assert_book_change_fails("Two.", "A different sentence.", "G-VRF-002")
        fixture = self.fixture()
        try:
            _, root, source, manifest, blocks, outline, mapping, book, assembly = fixture
            release_dir = root / "release"
            release_dir.mkdir(parents=True)
            candidate = release_dir / "BOOK_FINAL_CANDIDATE.md"
            candidate.write_bytes(book.read_bytes())
            for relative in (
                "pipeline/manifests/source.manifest.json",
                "pipeline/manifests/blocks.manifest.json",
                "pipeline/manifests/mapping.manifest.json",
                "pipeline/manifests/verification.manifest.json",
                "pipeline/manifests/pipeline.manifest.json",
                "artifacts/analysis/outline.manifest.json",
            ):
                write_json(root / relative, {"fixture": True, "path": relative, "pipeline_bundle_sha256": "fixture"} if relative.endswith("pipeline.manifest.json") else {"fixture": True, "path": relative})
            bindings = release_bindings(root, candidate)
            certificate = release_dir / "RELEASE_CERTIFICATE.json"
            write_json(certificate, {"status": "VERIFIED", "evidence_class": "REPOSITORY", "bindings": bindings})
            self.assertEqual(verify_release_certificate(root)["status"], "VERIFIED")
            mutated_certificate = dict(bindings)
            mutated_certificate["candidate_sha256"] = "0" * 64
            write_json(certificate, {"status": "VERIFIED", "evidence_class": "REPOSITORY", "bindings": mutated_certificate})
            self.assertEqual(verify_release_certificate(root)["status"], "BLOCKED")
            write_json(certificate, {"status": "VERIFIED", "evidence_class": "REPOSITORY", "bindings": bindings})
            candidate.write_bytes(candidate.read_bytes().replace(b"Two.", b"Altered."))
            self.assertEqual(verify_release_certificate(root)["status"], "BLOCKED")
        finally:
            fixture[0].cleanup()

    def assert_book_change_fails(self, old: str, new: str, gate: str):
        fixture = self.fixture()
        try:
            _, root, source, manifest, blocks, outline, mapping, book, assembly = fixture
            data = book.read_text(encoding="utf-8")
            self.assertIn(old, data)
            book.write_text(data.replace(old, new, 1), encoding="utf-8", newline="")
            result = verify_assembled(root, source, manifest, blocks, outline, mapping, book, assembly)
            self.assertEqual(self.gates(result)[gate], "FAIL")
        finally:
            fixture[0].cleanup()


if __name__ == "__main__":
    unittest.main()
