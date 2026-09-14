# Canonicalization contract

The contract is frozen before hashing:

- encoding: UTF-8, strict decoding;
- Unicode normalization: NFC;
- line endings: CRLF and CR become LF;
- BOM policy: a leading UTF-8 BOM is stripped for the canonical representation only;
- trailing whitespace: preserved;
- blank lines: preserved;
- final newline: preserved.

`raw_sha256` is SHA-256 over the exact source bytes. `normalized_sha256` is SHA-256 over the canonical UTF-8 bytes. These hashes are intentionally distinct. No trimming, space collapsing, punctuation normalization, quotation normalization, or blank-line removal is performed.
