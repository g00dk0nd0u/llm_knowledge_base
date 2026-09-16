# Query Core v1

Query Core is the portable, Revit-independent runtime layer. Its handover model is:

```text
future Revit Exporter (export time only)
  -> SQLite v1 machine payload
  -> Enhanced PDF portable handover artifact
  -> local read-only cache
  -> Query Core runtime
  -> structured facts, exact geometry, and PDF evidence
  -> optional LLM consumer
```

The existing PDF Pipeline remains the text/Markdown retrieval fallback and is not
replaced by this layer. No Revit, LLM, OCR, vision, or RVT dependency exists here.

## Schema and evidence

`schema.sql` defines schema version **1**. Both `metadata.schema_version` and
`PRAGMA user_version` carry that version. Canonical input owns stable string IDs;
the builder sorts records and atomically replaces its output, providing semantic
determinism without promising identical SQLite bytes across SQLite versions.

Public PDF page numbers are one-based. An evidence bbox is
`x_min, y_min, x_max, y_max` in PDF points in `pdf_points_top_left`: origin at the
top-left of the unrotated page, x rightward and y downward. Bounds are inclusive
and ordered. A null bbox means page-level evidence; partial or inverted bboxes are
invalid.

Geometry uses explicit JSON analytic primitives plus normalized 3D extents. The
v1 fixture demonstrates points within footprints and line axes. Coordinates are
in the named coordinate system and unit. `geometry_rtree` performs only candidate
filtering; callers receive the exact JSON geometry and must perform any later
deterministic measurement themselves. It does not assert architectural relations.

FTS5 uses the portable built-in `unicode61` tokenizer. This provides normal token
search and some Japanese behavior, but unspaced Japanese segmentation varies.
`search_text()` therefore falls back to deterministic Unicode substring matching
when FTS returns no result. No external tokenizer or search service is required.

## Enhanced PDF and validation

PyMuPDF copies the existing PDF object/page structure and adds `project.sqlite`
through the standard PDF EmbeddedFiles mechanism; pages are neither rasterized nor
reconstructed. The file specification is linked from the catalog `/AF` array with
`/AFRelationship /Data`, and its embedded stream declares MIME
`application/vnd.sqlite3`. The attachment descriptor carries schema/project/source
identity, generator version, and payload SHA-256.

Validation is deliberately non-circular: SQLite metadata stores the original
drawing SHA-256, while the PDF attachment descriptor stores the SQLite payload
SHA-256. The enhanced PDF's own hash is only used as a cache key and is not stored
inside itself.

`.cache/query-core/<enhanced-pdf-sha256>/<payload-sha256>/project.sqlite` is the
default ignored cache. Both hashes prevent stale reuse. Existing cache content is
revalidated for byte hash, SQLite integrity, and supported schema. Query connections
use SQLite URI `mode=ro` plus `PRAGMA query_only=ON`.

## Developer harness

```bash
python -m tools.query_core build-fixture artifacts/query-demo
python -m tools.query_core package artifacts/query-demo/drawing.pdf \
  artifacts/query-demo/project.sqlite artifacts/query-demo/enhanced.pdf
python -m tools.query_core inspect artifacts/query-demo/enhanced.pdf
python -m tools.query_core extract artifacts/query-demo/enhanced.pdf
python -m tools.query_core search artifacts/query-demo/enhanced.pdf SD-03
```

The programmatic API includes `search_text`, `find_entities`, `get_entity`,
`get_numeric_facts`, `get_dimensions`, `get_spot_elevations`,
`get_spatial_candidates`, `get_related_entities`, and `get_pdf_evidence`. Results
are dictionaries, never prose.
