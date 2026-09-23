# PDF ingestion roadmap

This document is the canonical implementation roadmap for generic / legacy technical PDF ingestion into Query Core.

## Design intent

The original PDF is always the source of truth. The repository should prefer deterministic, source-faithful extraction over aggressive reconstruction.

The operating order is:

1. preserve source identity, page, bbox, and provenance;
2. make embedded text searchable;
3. add deterministic structure only when it is directly supported by the PDF;
4. fall back to the source PDF for visual verification when structure is uncertain;
5. add OCR / Vision only as selective fallback, never as silent authority.

The repository is a knowledge and retrieval system, not a general-purpose PDF reconstruction engine.

## Current production baseline

Production remains PDF Pipeline v2 with conservative `lines_strict` ruled-table extraction.

Current production guarantees:

- original PDFs unchanged;
- embedded text blocks / lines / spans with PDF-space bboxes;
- deterministic single-document and project Query Core v2 ingestion;
- incremental project updates;
- Project Query Bundle v1;
- Enhanced PDF for single-document payloads;
- optional ruled-table persistence and deterministic table/cell query APIs;
- table cell text linked back to authoritative embedded source spans;
- no OCR, Vision, LLM interpretation, semantic headers, borderless-table inference, or merged-cell inference.

## Completed implementation

### Phase 0 — generic Query Core contract

Complete.

- PDF-native ingestion does not fabricate Revit host/source-model semantics.
- Query Core schema remains v2.
- single-document and project binding modes are explicit.

### Phase 1 — born-digital PDF evidence

Complete.

- page metadata;
- text blocks / lines / spans;
- deterministic PDF coordinates;
- source SHA and stable document identity;
- Japanese text search;
- exact source page / bbox navigation.

### Phase 2 — scalable project ingestion

Complete.

- project full rebuild;
- incremental update;
- added / changed / removed / unchanged reporting;
- duplicate-byte reporting without semantic deduplication;
- Project Query Bundle v1;
- deterministic source mapping.

### Phase 3A — conservative ruled tables

Complete.

- Pipeline v2 ruled-table sidecars;
- Query Core v2 table persistence;
- `pdf_tables`, `pdf_table_cells`, and `pdf_table_cell_spans` optional capability;
- public table read/query APIs;
- table-specific substring search without table FTS;
- source-span traceability;
- Enhanced PDF and Project Query Bundle round-trip coverage;
- legacy v2 tableless payload compatibility.

## Phase 3B — partially ruled tables

### 3B-0 characterization

Complete.

Synthetic born-digital fixtures showed that simply increasing PyMuPDF `join_tolerance` is unsafe:

- a wider tolerance can repair fragmented separators;
- the same tolerance can also merge independent adjacent tables;
- a completely absent separator cannot be recovered from geometry alone;
- one-axis text/line hybrid profiles were not conservative enough;
- merged-looking geometry remains ambiguous.

See `docs/phase-3b-partially-ruled-characterization.md`.

### 3B-1 monotonic repair guard characterization

Characterization is complete in PR #34 pending merge.

The strengthened guard requires one P0 anchor, stable outer bbox and dimensions, unchanged geometry for every pre-existing cell, unchanged authoritative source-span assignments for those cells, and repair only of previously missing cell slots.

This stronger definition reveals an important result: many simple one-gap repairs reshape other observable cells internally and therefore cannot be treated as source-faithful monotonic repair.

Therefore **do not production-enable widened `join_tolerance` or guarded fallback yet**.

## Next step — representative real-document validation

Stop expanding the synthetic corpus unless a specific real-document failure requires a targeted regression.

The next work should measure the current production baseline against representative real technical PDFs, for example:

- technical specifications / reports;
- door schedules;
- equipment schedules;
- room / finish schedules;
- code tables;
- A3 technical schedules;
- large-format architectural / MEP / consultant drawings;
- mixed pages containing drawing + notes + tables.

For each sample, record only practical retrieval outcomes:

- embedded text searchable?;
- correct document/page/bbox returned?;
- useful table structure recovered?;
- table structure rejected conservatively when uncertain?;
- source PDF sufficient for visual verification?;
- any recurring failure that materially blocks real queries?

Decision rule:

- if P0 is sufficient for normal work, keep it;
- if a recurring real-world partially ruled pattern materially blocks retrieval, add the smallest deterministic fallback justified by those examples;
- if failures are rare or ambiguous, keep source-PDF visual verification instead of adding reconstruction complexity.

## Deferred work

### Phase 3C — borderless / text-derived tables

Deferred until real-document evidence demonstrates a material need. No fully text-based table detector should be enabled by default.

### Phase 4 — selective OCR / image fallback

Deferred. OCR must be page/region selective, preserve separate provenance/confidence, and never overwrite embedded source text.

### Phase 5 — conservative domain structure

Deferred. Potential derived targets include title-block fields, revision/date, room labels, grid labels, callouts, specification headings, and table headers. Every derived result must retain page, bbox, method, provenance, and ambiguity state.

## Production boundary

Do not add complexity merely to maximize extraction coverage.

When deterministic structure cannot be proven, the correct behavior is to preserve searchable source evidence and return to the authoritative PDF for visual verification.
