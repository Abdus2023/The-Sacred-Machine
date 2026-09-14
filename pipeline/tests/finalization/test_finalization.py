from __future__ import annotations

import copy
import unittest

from pipeline.certification import certify_candidate
from pipeline.core import finalize_certified_candidate, load_json, write_json
from pipeline.finalization import finalize_candidate, verify_final


class FinalizationMutationTests(unittest.TestCase):
    """Revision 1.9 FINAL-M01..FINAL-M15 evidence in isolated fixtures."""

    def setUp(self) -> None:
        from pipeline.tests.assembly.test_assembly import AssemblyMutationTests
        self.fixture = AssemblyMutationTests("test_baseline_candidate_is_verified_and_no_finalization_occurs")
        self.fixture.setUp()
        self.root = self.fixture.root
        self.assertEqual(certify_candidate(self.root, "FIXTURE")["status"], "CERTIFIED")

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def assert_blocked(self, mutate, expected_gate: str | None = None) -> None:
        mutate(self.root)
        result = finalize_candidate(self.root, "FIXTURE")
        self.assertEqual(result["status"], "BLOCKED")
        if expected_gate:
            failed = {item["gate"] for item in result["admission"]["checks"] if item["status"] != "PASS"}
            self.assertIn(expected_gate, failed)
        self.assertFalse((self.root / "BOOK_FINAL.md").exists())

    def test_finalization_baseline_is_byte_exact_and_idempotence_is_verification_only(self) -> None:
        result = finalize_candidate(self.root, "FIXTURE")
        self.assertEqual(result["status"], "FINALIZED")
        candidate = (self.root / "artifacts/assembly/BOOK_FINAL_CANDIDATE.md").read_bytes()
        final = (self.root / "BOOK_FINAL.md").read_bytes()
        self.assertEqual(final, candidate)
        self.assertEqual(verify_final(self.root, "FIXTURE")["status"], "FINALIZED")
        before = final
        self.assertEqual(finalize_candidate(self.root, "FIXTURE")["status"], "BLOCKED")
        self.assertEqual((self.root / "BOOK_FINAL.md").read_bytes(), before)
        self.assertFalse((self.root / "release/BOOK_FINAL.md").exists())

    def test_FINAL_M01_certificate_mutation(self) -> None:
        def mutate(root):
            path = root / "artifacts/verification/RELEASE_CERTIFICATE.json"; data = load_json(path); data["bindings"]["candidate_sha256"] = "0" * 64; write_json(path, data)
        self.assert_blocked(mutate, "FINAL-000")

    def test_FINAL_M02_candidate_mutation(self) -> None:
        self.assert_blocked(lambda root: (root / "artifacts/assembly/BOOK_FINAL_CANDIDATE.md").write_bytes((root / "artifacts/assembly/BOOK_FINAL_CANDIDATE.md").read_bytes() + b"mutation"), "FINAL-000")

    def test_FINAL_M03_source_mutation(self) -> None:
        self.assert_blocked(lambda root: (root / "Source.md").write_bytes((root / "Source.md").read_bytes() + b"source mutation\n"), "FINAL-000")

    def test_FINAL_M04_outline_mutation(self) -> None:
        self.assert_blocked(lambda root: (root / "BOOK_OUTLINE.md").write_bytes((root / "BOOK_OUTLINE.md").read_bytes().replace(b"Fixture", b"Changed", 1)), "FINAL-000")

    def test_FINAL_M05_review_mutation(self) -> None:
        def mutate(root):
            path = root / "artifacts/review/OUTLINE_REVIEW.json"; data = load_json(path); data["decision"] = "REJECT"; write_json(path, data)
        self.assert_blocked(mutate, "FINAL-000")

    def test_FINAL_M06_mapping_mutation(self) -> None:
        def mutate(root):
            path = root / "artifacts/mapping/mapping.input.json"; data = load_json(path); data["entries"][0]["placement"] = 999; write_json(path, data)
        self.assert_blocked(mutate, "FINAL-000")

    def test_FINAL_M07_assembly_manifest_mutation(self) -> None:
        def mutate(root):
            path = root / "pipeline/manifests/assembly.manifest.json"; data = load_json(path); data["candidate_sha256"] = "0" * 64; write_json(path, data)
        self.assert_blocked(mutate, "FINAL-000")

    def test_FINAL_M08_verification_manifest_mutation(self) -> None:
        def mutate(root):
            path = root / "pipeline/manifests/verification.manifest.json"; data = load_json(path); data["status"] = "BLOCKED"; write_json(path, data)
        self.assert_blocked(mutate, "FINAL-000")

    def test_FINAL_M09_candidate_final_byte_mutation(self) -> None:
        self.assertEqual(finalize_candidate(self.root, "FIXTURE")["status"], "FINALIZED")
        path = self.root / "BOOK_FINAL.md"; path.write_bytes(path.read_bytes() + b"tamper")
        self.assertEqual(verify_final(self.root, "FIXTURE")["status"], "INVALID")

    def test_FINAL_M10_final_manifest_mutation(self) -> None:
        self.assertEqual(finalize_candidate(self.root, "FIXTURE")["status"], "FINALIZED")
        path = self.root / "pipeline/manifests/final.manifest.json"; data = load_json(path); data["final_sha256"] = "0" * 64; write_json(path, data)
        self.assertEqual(verify_final(self.root, "FIXTURE")["status"], "INVALID")

    def test_FINAL_M11_final_artifact_deletion(self) -> None:
        self.assertEqual(finalize_candidate(self.root, "FIXTURE")["status"], "FINALIZED")
        (self.root / "BOOK_FINAL.md").unlink()
        self.assertEqual(verify_final(self.root, "FIXTURE")["status"], "BLOCKED")

    def test_FINAL_M12_stale_certificate(self) -> None:
        def mutate(root):
            path = root / "artifacts/verification/RELEASE_CERTIFICATE.json"; data = load_json(path); data["bindings"]["source_manifest_hash"] = "0" * 64; write_json(path, data)
        self.assert_blocked(mutate, "FINAL-000")

    def test_FINAL_M13_preexisting_conflicting_final(self) -> None:
        (self.root / "BOOK_FINAL.md").write_bytes(b"conflicting preexisting final")
        result = finalize_candidate(self.root, "FIXTURE")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual((self.root / "BOOK_FINAL.md").read_bytes(), b"conflicting preexisting final")

    def test_FINAL_M14_final_payload_mutation(self) -> None:
        self.assertEqual(finalize_candidate(self.root, "FIXTURE")["status"], "FINALIZED")
        path = self.root / "BOOK_FINAL.md"; path.write_bytes(path.read_bytes().replace(b"One.", b"Changed.", 1))
        self.assertEqual(verify_final(self.root, "FIXTURE")["status"], "INVALID")

    def test_FINAL_M15_unauthorized_finalization_attempt(self) -> None:
        with self.assertRaises(Exception):
            finalize_certified_candidate(self.root)
        self.assertFalse((self.root / "BOOK_FINAL.md").exists())


if __name__ == "__main__":
    unittest.main()
