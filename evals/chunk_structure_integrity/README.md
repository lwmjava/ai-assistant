# structure-integrity-v0.1

Eight AI-authored synthetic **Smoke / Adversarial** cases cover fenced and
unfenced box diagrams, code indentation, table rows, identical repeated code,
unclosed fences, CRLF/tilde fences, and an oversized atomic code line. These are
not human-approved Gold and do not measure semantic retrieval quality.

Run with the project's `ai-assistant` interpreter:

```powershell
python -m pytest --noconftest tests/test_chunk_structure_evaluation.py -q
python -m evals.chunk_structure_integrity.run --baseline-ref HEAD --output evals/chunk_structure_integrity/baseline.json
python -m evals.chunk_structure_integrity.run --output evals/chunk_structure_integrity/candidate.json
python -m evals.chunk_structure_integrity.run --offline-hard-limit 100 --output evals/chunk_structure_integrity/candidate_test_limit.json
```

`--noconftest` keeps the standalone evaluator tests from initializing the shared
test database. The runner performs no DB writes or network requests. Semantic
strategy execution uses local `MockEmbeddingProvider(dim=8)` solely to exercise
the split path. Its result is not a production embedding or retrieval metric.

The baseline runner reads chunking and its ingestion delegate from the named Git
commit into a temporary directory. The report records the resolved commit and
dataset SHA256; candidate reports explicitly identify the working tree. Run the
baseline at the intended pre-change revision rather than a later HEAD.

Metrics validate each source slice against actual emitted text before counting
coverage. Offsets are half-open Python character ranges, not bytes or model
tokens. Coverage is the union of faithful original ranges, including whitespace;
missing characters in legacy strategies can therefore include trimmed whitespace
and content whose original range cannot be matched exactly. Legacy ranges are
explicitly inferred, not treated as persisted provenance. The ordering count is
the number of successive original-span starts moving backwards; normal forward
overlap does not count as disorder. An annotated protected unit is broken when
no single faithful source span contains it. Table rows are the annotated atomic
units, allowing safe row grouping with derived repeated headers.

Parent and child layers are reported separately to avoid a complete parent
masking broken children. Repeated headers are derived context and contribute no
original-source coverage. Length reports retain every emitted length, min, max,
and median for independent recalculation. Oversized originals remain part of
coverage, while their count is reported separately; coverage never asserts that
these blocks were vectorized.

The `100` limit run uses `len(text)` as a synthetic exact character-count oracle,
explicitly unrelated to real Embedding token limits. It tests oversized behavior
without certifying a model input policy. No-policy runs do not certify production
limits either. The dataset and reports do not use or modify frozen rag-v0.1 or
holdout records.
