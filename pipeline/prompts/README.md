# Semantic planning inputs

No LLM prompt is executed by the mechanical pipeline. Classification, duplicate detection, mapping, and gap proposals are planning inputs only and require frozen, reviewable artifacts before they can enter a manifest. The absence of `BOOK_OUTLINE.md` is therefore a blocking input condition, not permission to invent an outline.
