# Query Core v2

Query Core is the portable, Revit-independent runtime. Revit participates only at
export time: a future adapter writes `revit_snapshot_v1` JSON, Python validates and
imports it into SQLite v2, and the existing packager embeds `project.sqlite` in the
Enhanced Drawing PDF. Query time requires neither JSON nor Revit/RVT/API assemblies.

## Identity and parameters

`source_models` separates stable model identity (`model_identity_kind` plus
`model_identity`) from changing `snapshot_version_guid`/`snapshot_save_number`.
`DocumentVersion.VersionGUID` must never be used as permanent model identity.
Revit entities use `(source_model_id, source_unique_id)` uniqueness, so equal
`Element.UniqueId` values in unrelated host/link documents are valid. Element types
retain family, type, and category separately.

`link_instances` represents placement separately: one linked `source_models` row may
be referenced by many host link instances, each with its own Revit instance UniqueId
and transform. Linked geometry, appearances, and annotation references carry the
applicable link instance ID without duplicating the linked element identity.

Parameters retain instance/type scope, definition key and name, storage type,
data/spec/parameter/unit identifiers, shared GUID, raw text/internal numeric value,
and normalized `numeric_value + unit`. A Double alone never implies a physical unit.

## Geometry and drawing occurrences

Every R-Tree bound is in `host_revit_internal_origin`, `mm`, on host Revit XYZ axes.
Linked geometry must be transformed before insertion. Exact analytic JSON—not the
float R-Tree—is authoritative for measurement. A link transform has exactly
`basis_x`, `basis_y`, `basis_z`, `origin` (three numbers each), and
`source_unit: revit_internal`.

`entity_appearances` provides independent, many-to-many occurrences with one-based
PDF page, `pdf_points_top_left` bbox, appearance kind, and explicit quality:
`page_only`, `may_be_visible`, `view_bbox`, `projected_bbox`, or `exact`. Collector
visibility must not be called exact. `viewports` separately records Revit sheet bbox
and unit, PDF bbox, affine mapping, and mapping quality. `placement_kind=schedule`
supports ScheduleSheetInstance without pretending it is a Viewport.

Printable sheets/views carry explicit `export_order`; combined PDF page numbers
follow that order and are never reconstructed by sorting sheet numbers.

Dimensions keep ordered segments and source-model-aware references. The same
reference structure supports linked/multiple/unresolved tag targets. Spot elevation
and coordinate annotations retain numeric and display values. Relationships preserve
`contained_in`, `from_space`, `to_space`, `hosted_by`, and `belongs_to_level`, with
phase identity where applicable.

Spatial boundaries are ordered outer/inner finish-face loops of directed segments,
optionally identifying their boundary element. Compact obstruction footprints and
bounds belong in `geometries`; full meshes are intentionally out of scope.

## Evidence, search, and packaging

PDF evidence remains one-based and uses inclusive ordered `pdf_points_top_left`
bounds. FTS5 uses `unicode61`, with deterministic Unicode substring fallback for
Japanese. Enhanced PDFs retain `/AFRelationship /Data`, SQLite MIME metadata,
drawing/payload SHA binding, validated read-only cache, and SQLite integrity checks.

Public APIs include `search_text`, `find_entities`, `get_entity`,
`get_numeric_facts`, `get_dimensions`, `get_spot_elevations`,
`get_spatial_candidates`, `get_related_entities`, `get_pdf_evidence`, plus v2
`get_source_model`, `get_appearances`, `get_annotation_segments`,
`get_annotation_references`, `get_occurrence_evidence`, and
`get_spatial_boundaries`.

## Practical structured queries

Two intent-specific operations compose entity, relationship, structured fact, and
PDF-evidence lookup. They use only stored data; they require no LLM, Revit runtime,
RVT file, OCR, or external API.

```python
from tools.query_core import QueryCore

with QueryCore("artifacts/query-demo/project.sqlite") as query:
    opening = query.get_opening_width("element-dl03")
    # status=ok, 4500 mm, Shutter SD-03, A-312, PDF page 1, evidence bbox

    roof = query.get_relative_elevation("element-roof")
    # status=ok, RFL + 1250 mm, A-421, PDF page 2, evidence bbox
```

`get_opening_width` first checks the stable Revit semantic parameter key
`builtin:DOOR_WIDTH`, then an explicit normalized opening-width name, and finally an
explicit opening-width dimension. A generic parameter named `Width` is not sufficient
without the semantic key. The query follows only one bounded relationship step and
deduplicates parallel relationships to the same target.

Annotations may be associated either by canonical `related_entity_id` or by resolved
`annotation_references`. Revit reference matching always uses the
`source_model_id + source_unique_id` identity pair; a UniqueId alone is not globally
unique. `get_relative_elevation` uses `kind=spot_elevation` as its primary
classification, but returns a datum such as RFL only when stored display or segment
text proves it. A requested but unproven datum returns `insufficient_data`; different
explicit datums are not collapsed merely because their numbers match.

Practical evidence can come from explicit `evidence` records or joined Revit
`entity_appearances` for the subject, target, and selected annotation. Appearance IDs
remain distinct from evidence IDs. Page-only occurrences retain their sheet, page,
and view while returning `bbox=null`; Query Core never fabricates PDF coordinates.
Results preserve provenance and confidence and order deduplicated evidence by
document, PDF page, sheet, view, and stable ID.

Ordinary domain outcomes use `ok`, `not_found`, `insufficient_data`, `ambiguous`, or `conflict` status.
Multiple plausible related openings are `ambiguous`; conflicting equally preferred
facts are `conflict`. Neither operation guesses a target or value. Invalid inputs or
unsupported/malformed databases still raise errors.

These APIs do not perform corridor clear-width, obstruction, or other geometry
analysis. Such analysis and real-Revit smoke testing are intentionally deferred.

See [`REVIT_2026_MAPPING.md`](REVIT_2026_MAPPING.md) for the Phase B extraction map.

```bash
python -m tools.query_core build-fixture artifacts/query-demo
python -m tools.query_core package artifacts/query-demo/drawing.pdf artifacts/query-demo/project.sqlite artifacts/query-demo/enhanced.pdf
python -m tools.query_core inspect artifacts/query-demo/enhanced.pdf
```

## Finalize a Revit offline export

After the Revit Phase B1 command has completed its staged export, validate its snapshot,
verify the drawing SHA, build the existing v2 payload, and package the portable PDF:

```bash
python -m tools.query_core finalize-revit-export /path/to/export-run
```

This writes `project.sqlite` and `enhanced.pdf` alongside the diagnostic export files.
See [`../../revit_exporter/README.md`](../../revit_exporter/README.md) for host builds,
deployment, smoke testing, and known limitations.
