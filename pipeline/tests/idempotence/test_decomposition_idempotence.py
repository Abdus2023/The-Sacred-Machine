from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pipeline.core import decompose, reconstruct


class DecompositionIdempotenceTests(unittest.TestCase):
    def test_repeat_decomposition_does_not_create_new_ids_or_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "Source.md"
            source.write_text("## [1] USER\n\nPayload\n", encoding="utf-8")
            _, first = decompose(source)
            _, second = decompose(source)
            self.assertEqual([item["block_id"] for item in first], [item["block_id"] for item in second])
            self.assertEqual(reconstruct(first), reconstruct(second))
            self.assertEqual(reconstruct(first), source.read_bytes())


if __name__ == "__main__":
    unittest.main()
