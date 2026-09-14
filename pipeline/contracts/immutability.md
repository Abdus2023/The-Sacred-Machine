# Immutability contract

`Source.md` is authoritative and is never edited by the pipeline. The source payload is the concatenation of the `original_text` fields in ascending `source.sequence` order. Structural headings, IDs, markers, manifests, and reports are envelope data and are not source payload.

The decomposer chooses contiguous spans only. It does not summarize, translate, correct, merge, delete, or interpret source text. Every source byte belongs to one and only one canonical block span.

A missing outline, mapping, or verification result is a blocking input failure. The pipeline does not infer an outline or create editorial prose to fill a gap.
