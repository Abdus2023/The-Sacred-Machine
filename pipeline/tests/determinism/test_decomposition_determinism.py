from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pipeline.core import blocks_jsonl_hash, decompose


class DecompositionDeterminismTests(unittest.TestCase):
    def test_same_source_and_contract_produce_same_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "Source.md"
            source.write_text("# T\n\n## [1] USER\n\nPayload\n", encoding="utf-8")
            first_manifest, first_blocks = decompose(source)
            second_manifest, second_blocks = decompose(source)
            self.assertEqual(first_manifest, second_manifest)
            self.assertEqual(blocks_jsonl_hash(first_blocks), blocks_jsonl_hash(second_blocks))
            self.assertEqual(first_blocks, second_blocks)


if __name__ == "__main__":
    unittest.main()
