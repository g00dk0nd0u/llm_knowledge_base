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

Spatial boundaries are ordered outer/inner finish-face loops of directed segments.
`spatial_boundaries.link_instance_id` (and the segment's matching
`link_instance_id`) identifies the occurrence of the Room/Space itself.
`spatial_boundary_segments.source_link_instance_id` independently identifies the
top-level link occurrence containing the boundary-producing element. Thus a host
Room bounded by a linked Wall has a null boundary `link_instance_id`, but the Wall's
linked `source_model_id`, actual `source_unique_id`, and non-null
`source_link_instance_id`. A linked Room bounded locally uses its own top-level link
occurrence for both occurrence columns. Unresolved and unsupported nested-link
sources remain null rather than becoming dangling identities.

`curve_kind` distinguishes `line`, `arc`, `ellipse`, `spline`, and `other`. Stored
endpoints are exact segment geometry only for `line`; nonlinear endpoints are an
explicit approximation and the exporter emits a warning.

## Phase 4A relationships and Phase 4B1 geometry queries

Phase 4A APIs report explicit stored relationships. They do not infer relationships
from geometry. Phase 4B1 adds `get_entity_geometries`, `get_location_distance`,
`find_nearby_by_location`, `get_nearest_by_location`, and `get_vertical_relation`.
Location distance is the deterministic 3-D distance between one stored Revit
`LocationPoint` or finite straight `LocationCurve(Line)` primitive per occurrence.
It is **not** physical-solid, finish-face, code-compliance, or clear distance.
Unsupported, malformed, conflicting, or bbox-only location geometry returns
`insufficient_data` (or `ambiguous_geometry` for conflicting primitives); coordinate
systems and units are never converted at query time.

The `geometry_rtree` is only a broad-phase candidate index. Nearby and bounded-nearest
queries expand the subject primitive bounds, retrieve indexed candidates, then use
the exact stored point/line primitives for filtering and ranking. R-Tree bounds are
never reported as exact element distance. Nearest search is deliberately bounded by
the required `max_radius_mm` argument.

Vertical relation uses only strict separation of stored `bbox3d` Z ranges. It is a
conservative bounding-volume guarantee: separated ranges are definitely above or
below, while touching or overlapping ranges are `indeterminate`. It is not a semantic
floor relation, physical surface clearance, or center-point relation.

Phase 4B2 Room/Space adjacency is a conservative, positive deterministic proof.
`get_adjacent_spaces` requires straight outer boundary segments with the exact same
boundary source occurrence (including its top-level link instance) **and** positive
finite longitudinal overlap. Shared Wall/source identity alone is insufficient.
Because Revit Finish boundaries can be offset across a Wall thickness, matching lines
need not be collinear; their directions must be parallel or anti-parallel, and their
projected finite intervals must overlap beyond a tiny floating-point epsilon. No
perpendicular-distance or other arbitrary “near enough” tolerance is used. Curved and
inner-loop segments are not positive evidence.

Spatial occurrences retain `link_instance_id`, so repeated placements are never
merged. Omitting it when a space has multiple boundary occurrences returns
`ambiguous_occurrence` and the available occurrence IDs. Coverage reports skipped
nonlinear and unresolved-source segments; incomplete coverage never proves
non-adjacency. Pre-PR18 v2 databases remain readable, but without both additive
boundary provenance columns this API safely returns `insufficient_data` rather than
mutating or rejecting the database. Corridor clear-width remains deferred.

Both new boundary-segment columns are additive v2 capabilities. Databases created before
their introduction remain valid and read-only; callers that need them must detect the
physical columns and report reduced capability rather than rejecting or rewriting an
older payload. Newly built databases include an optional boundary-source occurrence
index for candidate lookup; validation does not require that index from existing v2
databases.

## Evidence, search, and packaging

PDF evidence remains one-based and uses inclusive ordered `pdf_points_top_left`
bounds. FTS5 uses `unicode61`, with deterministic Unicode substring fallback for
Japanese. Enhanced PDFs retain `/AFRelationship /Data`, SQLite MIME metadata,
drawing/payload SHA binding, validated read-only cache, and SQLite integrity checks.

New v2 databases also expose the additive `pdf_pages` capability. Each row identifies
a one-based page in `documents` and preserves its finite positive width/height in PDF
points, rotation, and provenance. Page sizes may vary within a document; these values
describe PDF evidence space, not Revit/model geometry. Existing v2 databases without
this table remain valid and readable.

Payload metadata distinguishes `single_document` from `project` binding. Missing
binding metadata in an older v2 payload means `single_document`, which continues to
require and verify the top-level source identity and SHA-256. A `project` payload may
contain multiple documents and instead treats each `documents.identity` and lowercase
`documents.source_sha256` as authoritative. `package_pdf()` remains exclusively for
single-document Enhanced PDFs and rejects project-bound payloads; project bundle
generation is not part of this contract.

Public APIs include `search_text`, `find_entities`, `get_entity`,
`get_numeric_facts`, `get_dimensions`, `get_spot_elevations`,
`get_spatial_candidates`, `get_related_entities`, `get_pdf_evidence`, plus v2
`get_source_model`, `get_appearances`, `get_annotation_segments`,
`get_annotation_references`, `get_occurrence_evidence`, and
`get_spatial_boundaries`, and `get_adjacent_spaces`. Navigation APIs include `get_navigation_targets` and
`get_evidence_navigation`. Change-impact APIs include `get_type_instances`,
`get_related_spaces`, and `get_change_impact`.

Explicit spatial APIs include `get_contained_elements`,
`get_containing_spaces`, `get_space_connections`, `get_same_type_elements`,
`get_same_space_elements`, `get_level_difference`, and `get_spatial_context`.
Containment and same-space membership use only `elements.space_id` and
`contained_in`; From/To room relationships are connector semantics, not containment.
Connections group a connector's `from_space` and `to_space` rows by
`phase_source_unique_id`, retain one-sided groups as `partial`, and expose the
connector's existing navigation targets. Relationships without an explicit phase
remain separate `partial` results with `phase_context_missing`; a null phase never
proves that opposite sides share a phase. Level difference is stored elevation A minus
stored elevation B and succeeds only when both levels have the same explicit
`source_model_id` and unit. Phase 4A does not compare levels across models; elevation
comparison using link-instance transforms is deferred. No API in this group derives
relationships or elevations from geometry.

`get_change_impact` accepts an element or element-type ID. An element first resolves
its stored `type_id`; the result then deterministically aggregates every instance of
that type, explicit `elements.space_id` and
`from_space`/`to_space`/`contained_in` relationships, and drawing occurrences.
Top-level related spaces are unique by space ID and carry ordered `contexts` containing
the instance, relation semantic, relationship ID, phase, provenance, confidence, and
evidence ID. Coverage counts distinguish all instances from those with stored spatial
context or drawing occurrences. Missing context remains explicit in warnings and is
never inferred from geometry. Occurrences preserve page-only (`bbox=null`), link
instance, provenance, and bbox-quality data.

Related-schedule queries are deferred until a reliable structured schedule export
contract exists; Query Core does not infer schedule membership from drawing content.

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

For an element, qualifying instance parameters take precedence over parameters on its
`element_type`; a valid instance value therefore overrides a different type value
without creating a conflict. Parameter results expose `parameter_scope` (`instance`
or `type`) and `fact_entity` so callers can audit which element or type supplied the
answer.

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

Query Core returns viewer-neutral navigation descriptors; it does not open a PDF
viewer or execute operating-system commands. Each descriptor contains its source
identity, document, sheet/view, one-based PDF page, and stored bbox metadata. A
stored bbox sets `can_zoom=true`; page-only evidence has `bbox=null` and
`can_zoom=false`. UI clients are responsible for opening the page and, when
available, zooming to or highlighting the bbox. Query Core never generates a bbox.
The practical query and change-impact results expose the same descriptors in their
`navigation` field. Schedule navigation and geometry-derived regions are out of
scope.

Ordinary domain outcomes use `ok`, `not_found`, `insufficient_data`, `ambiguous`, or `conflict` status.
Multiple plausible related openings are `ambiguous`; conflicting equally preferred
facts are `conflict`. Neither operation guesses a target or value. Invalid inputs or
unsupported/malformed databases still raise errors.

These APIs do not perform corridor clear-width, obstruction, or other geometry
analysis. Such analysis and real-Revit smoke testing are intentionally deferred.

See [`REVIT_2026_MAPPING.md`](REVIT_2026_MAPPING.md) for the Phase B extraction map.

## Build one PDF Pipeline document

`build-pdf` consumes one pipeline-owned knowledge directory (`document.json`, its
declared `pages/pNNNN.json` sidecars, and `.pdf-pipeline-v1`) and writes one
single-document Query Core database. It does not scan sibling documents or extract
the PDF again. The adapter verifies the logical document/page identities, page
metadata, text hierarchy, and the current source PDF byte hash before building.

```bash
python -m tools.query_core build-pdf --repo-root . \
  projects/example/knowledge/example--0123456789ab artifacts/example.sqlite
```

The additive `pdf_text_blocks`, `pdf_text_lines`, and `pdf_text_spans` tables retain
pipeline order, text, bboxes, provenance, and `pdf_points_top_left` coordinates.
Block text is indexed as `pdf_text_block`; `QueryCore.search_pdf_text()` returns the
source document identity and SHA, one-based PDF page, bbox, and the standard
viewer-neutral evidence navigation descriptor. Databases created before these
optional PDF-native tables remain readable.

## Build or incrementally update a PDF project

`build-pdf-project` consumes the current project manifest and strictly reparses every
owned PDF Pipeline v1 document into a fresh project-bound Query Core. It is the
correctness-reference full rebuild.

```bash
python -m tools.query_core build-pdf-project --repo-root . \
  projects/example artifacts/example-project.sqlite
```

After PDF Pipeline has been rerun, `update-pdf-project` may reuse a compatible existing
project database. It compares logical source identities and SHA-256 revisions, validates
unchanged authoritative sources plus their document metadata, and intentionally skips
unchanged page-sidecar parsing. Added and changed documents still go through the strict
single-document parser.

```bash
python -m tools.query_core update-pdf-project --repo-root . \
  projects/example artifacts/example-project.sqlite
```

The updater never mutates the live database in place. It snapshots the validated base
through SQLite Backup API, applies added/changed/removed deltas only to the temporary
database, validates cached PDF search/evidence mappings, checks FTS5 external-content
integrity, checks foreign keys and Query Core metadata, then atomically replaces the
output. A failed update leaves the previous database unchanged. A logical path rename is
an explicit remove plus add; equal byte hashes at different logical paths remain distinct
documents. Incremental update does not persist revision history, deduplicate equal PDFs,
or generate a project bundle.

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
