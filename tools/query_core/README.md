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
`get_annotation_references`, and `get_spatial_boundaries`.

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
