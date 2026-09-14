# Assembly Contract — Revision 1.7

Assembly is a deterministic structural operation, not an editorial or semantic operation. It may move immutable `original_text` slices from verified decomposition into `BOOK_FINAL_CANDIDATE.md`; it must not rewrite, summarize, paraphrase, correct, deduplicate, or interpret source content.

Assembly is admitted only when `MAPPING_STATUS = AUTHORIZED` and ASM-000 passes. The pipeline consumes the existing authorized canonical mapping and never creates, repairs, infers, or silently adopts mapping input. The mapping input, review record, source manifest, blocks manifest, canonical mapping, mapping validation, pipeline manifest, this contract, and assembly schema are dependency-bound artifacts.

A candidate uses normalized-source semantics explicitly. Every payload is bound to its canonical `block_id`, role, target, placement, source spans, raw hash, normalized hash, and payload hash. `PRIMARY` contributes payload. `REFERENCE` and `EXPLICITLY_UNMAPPED` dispositions remain auditable in mapping and assembly evidence but do not contribute candidate payload unless an explicit contract says otherwise. Equal-content blocks retain distinct identities.

The structural envelope may contain only deterministic headings, outline markers, gap markers, and block boundary markers derived from the reviewed outline and authorized mapping. It must not contain substantive generated prose. Ordering is derived only from reviewed outline order, mapping placement, verified source sequence, and block ID tie-breakers.

ASM-VERBATIM, ASM-PROVENANCE, ASM-COMPLETE, and ASM-ORDER are mandatory verification gates. Any dependency, manifest, candidate, payload, identity, completeness, or order mutation blocks verification; automatic repair is prohibited. Revision 1.7 terminates at a verified candidate, assembly manifest, dependency record, and validation evidence. It never creates `BOOK_FINAL.md`, a certificate, or release artifacts.
