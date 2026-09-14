# Verbatim contract

For every payload placement, the verifier compares bytes, not meaning:

`book_payload == UTF-8(canonical original_text)`

and compares the resulting SHA-256 to the block's `normalized_sha256`. There is no fuzzy matching or whitespace forgiveness beyond the canonicalization contract. Any mismatch blocks release and produces a failure report; the evidence is not edited to make the check pass.
