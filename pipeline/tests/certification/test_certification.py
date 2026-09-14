from __future__ import annotations

import copy
import unittest
from pathlib import Path

from pipeline.certification import _verify_once, certify_candidate, verify_candidate_independently, verify_certification_certificate
from pipeline.core import load_json, load_jsonl, write_json, write_jsonl


class CertificationMutationTests(unittest.TestCase):
    """Revision 1.8 CERT-M01..CERT-M15 and certificate mutation evidence."""

    def setUp(self) -> None:
        from pipeline.tests.assembly.test_assembly import AssemblyMutationTests
        self.fixture = AssemblyMutationTests("test_baseline_candidate_is_verified_and_no_finalization_occurs")
        self.fixture.setUp()
        self.root = self.fixture.root

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def assert_mutation(self, mutate, expected_gates: set[str]) -> None:
        mutate(self.root)
        result = _verify_once(self.root, "FIXTURE")
        self.assertEqual(result["status"], "BLOCKED")
        failed = {item["gate"] for item in result["checks"] if item["status"] != "PASS"}
        self.assertTrue(expected_gates & failed, (expected_gates, failed))
        self.assertFalse((self.root / "BOOK_FINAL.md").exists())

    def test_candidate_verification_and_certification_stop_before_finalization(self) -> None:
        verification = verify_candidate_independently(self.root, "FIXTURE")
        self.assertEqual(verification["status"], "VERIFIED")
        certification = certify_candidate(self.root, "FIXTURE")
        self.assertEqual(certification["status"], "CERTIFIED")
        self.assertTrue((self.root / "artifacts/verification/RELEASE_CERTIFICATE.json").exists())
        self.assertFalse((self.root / "BOOK_FINAL.md").exists())
        self.assertFalse((self.root / "release/BOOK_FINAL.md").exists())

    def test_CERT_M01_candidate_byte_mutation(self) -> None:
        self.assert_mutation(lambda root: (root / "artifacts/assembly/BOOK_FINAL_CANDIDATE.md").write_bytes((root / "artifacts/assembly/BOOK_FINAL_CANDIDATE.md").read_bytes() + b"mutated"), {"CERT-006", "CERT-011", "CERT-012"})

    def test_CERT_M02_candidate_hash_mutation(self) -> None:
        def mutate(root):
            path = root / "pipeline/manifests/assembly.manifest.json"; data = load_json(path); data["candidate_sha256"] = "0" * 64; write_json(path, data)
        self.assert_mutation(mutate, {"CERT-006", "CERT-014"})

    def test_CERT_M03_source_mutation(self) -> None:
        self.assert_mutation(lambda root: (root / "Source.md").write_bytes((root / "Source.md").read_bytes() + b"source mutation\n"), {"CERT-001", "CERT-002", "CERT-005"})

    def test_CERT_M04_block_manifest_mutation(self) -> None:
        def mutate(root):
            path = root / "pipeline/manifests/blocks.manifest.json"; data = load_json(path); data["blocks_artifact_sha256"] = "0" * 64; write_json(path, data)
        self.assert_mutation(mutate, {"CERT-002", "CERT-005"})

    def test_CERT_M05_outline_mutation(self) -> None:
        self.assert_mutation(lambda root: (root / "BOOK_OUTLINE.md").write_bytes((root / "BOOK_OUTLINE.md").read_bytes().replace(b"Fixture", b"Changed", 1)), {"CERT-003", "CERT-004", "CERT-005"})

    def test_CERT_M06_review_mutation(self) -> None:
        def mutate(root):
            path = root / "artifacts/review/OUTLINE_REVIEW.json"; data = load_json(path); data["decision"] = "REJECT"; write_json(path, data)
        self.assert_mutation(mutate, {"CERT-004", "CERT-005"})

    def test_CERT_M07_mapping_mutation(self) -> None:
        def mutate(root):
            path = root / "artifacts/mapping/mapping.input.json"; data = load_json(path); data["entries"][0]["placement"] = 999; write_json(path, data)
        self.assert_mutation(mutate, {"CERT-005"})

    def test_CERT_M08_assembly_manifest_mutation(self) -> None:
        def mutate(root):
            path = root / "pipeline/manifests/assembly.manifest.json"; data = load_json(path); data["ordered_block_sequence"] = list(reversed(data["ordered_block_sequence"])); write_json(path, data)
        self.assert_mutation(mutate, {"CERT-010", "CERT-014"})

    def test_CERT_M09_dependency_manifest_mutation(self) -> None:
        def mutate(root):
            path = root / "artifacts/assembly/assembly.dependencies.json"; data = load_json(path); data["mapping_sha256"] = "0" * 64; write_json(path, data)
        self.assert_mutation(mutate, {"CERT-015"})

    def test_CERT_M10_block_deletion(self) -> None:
        def mutate(root):
            path = root / "artifacts/decomposition/CONTENT_BLOCKS.jsonl"; records = load_jsonl(path); write_jsonl(path, records[:-1])
        self.assert_mutation(mutate, {"CERT-002", "CERT-007"})

    def test_CERT_M11_unauthorized_block_insertion(self) -> None:
        def mutate(root):
            path = root / "artifacts/assembly/BOOK_FINAL_CANDIDATE.md"; path.write_bytes(path.read_bytes() + b"<!-- BLOCK: B9999 -->\nunauthorized\n<!-- END BLOCK: B9999 -->\n")
        self.assert_mutation(mutate, {"CERT-009", "CERT-011", "CERT-012"})

    def test_CERT_M12_block_reorder(self) -> None:
        def mutate(root):
            path = root / "artifacts/assembly/BOOK_ASSEMBLY.jsonl"; records = load_jsonl(path); records[1], records[2] = records[2], records[1]; write_jsonl(path, records)
        self.assert_mutation(mutate, {"CERT-007", "CERT-009", "CERT-010"})

    def test_CERT_M13_payload_substitution(self) -> None:
        def mutate(root):
            path = root / "artifacts/assembly/BOOK_FINAL_CANDIDATE.md"; path.write_bytes(path.read_bytes().replace(b"One.", b"Changed.", 1))
        self.assert_mutation(mutate, {"CERT-006", "CERT-008", "CERT-011"})

    def test_CERT_M14_structural_envelope_injection(self) -> None:
        def mutate(root):
            path = root / "artifacts/assembly/BOOK_FINAL_CANDIDATE.md"; path.write_bytes(path.read_bytes() + b"\nInvented substantive prose.\n")
        self.assert_mutation(mutate, {"CERT-006", "CERT-011", "CERT-012"})

    def test_CERT_M15_duplicate_collapse(self) -> None:
        def mutate(root):
            path = root / "artifacts/assembly/BOOK_ASSEMBLY.jsonl"; records = load_jsonl(path); write_jsonl(path, [records[0], *records[1:-1]])
        self.assert_mutation(mutate, {"CERT-007", "CERT-009", "CERT-010"})

    def test_certificate_mutations_CM01_to_CM09_are_blocked(self) -> None:
        self.assertEqual(certify_candidate(self.root, "FIXTURE")["status"], "CERTIFIED")
        certificate_path = self.root / "artifacts/verification/RELEASE_CERTIFICATE.json"
        original = load_json(certificate_path)
        fields = [
            "candidate_sha256", "source_manifest_hash", "outline_raw_sha256", "outline_review_hash",
            "mapping_sha256", "assembly_manifest_hash", "verification_manifest_hash",
        ]
        for field in fields:
            mutated = copy.deepcopy(original); mutated["bindings"][field] = "0" * 64; write_json(certificate_path, mutated)
            self.assertEqual(verify_certification_certificate(self.root)["status"], "BLOCKED", field)
        mutated = copy.deepcopy(original); mutated["status"] = "BLOCKED"; write_json(certificate_path, mutated)
        self.assertEqual(verify_certification_certificate(self.root)["status"], "BLOCKED", "status")
        mutated = copy.deepcopy(original); mutated["bindings"]["unexpected_dependency"] = "mutation"; write_json(certificate_path, mutated)
        self.assertEqual(verify_certification_certificate(self.root)["status"], "BLOCKED", "dependency")


if __name__ == "__main__":
    unittest.main()
