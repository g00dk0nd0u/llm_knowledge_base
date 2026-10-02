# Query Core v2

Query Core is the portable, Revit-independent runtime. Revit participates only at
export time: a future adapter writes `revit_snapshot_v1` JSON, Python validates and
imports it into SQLite v2, and the existing packager embeds `project.sqlite` in the
Enhanced Drawing PDF. Query time requires neither JSON nor Revit/RVT/API assemblies.

## Query existing SQLite — zero-install runtime

Querying an existing `project.sqlite` requires only a repository checkout and a
normal Python 3.12 installation. It uses the Python standard library only: do **not**
create or activate a virtual environment, run `pip`, or install this repository or
any package.

From the repository root on Windows:

```powershell
py -3.12 -S -m tools.query_core search project.sqlite "query"
```

If `python` resolves to Python 3.12, `python -S -m tools.query_core ...` is also
supported. From the repository root on macOS:

```bash
python3 -S -m tools.query_core search project.sqlite "query"
```

This zero-install boundary applies specifically to SQLite query time. Building or
extracting Enhanced PDFs, packaging PDFs, generating PDF fixtures, processing PDF
Pipeline input, and finalizing Revit exports may require the dependencies declared
by the project, including PyMuPDF or jsonschema.

## Identity and parameters

### Optional explicit drawing references

The optional, source-neutral `drawing_references` table stores explicit reference
facts separately from the Revit-oriented `relationships` table and from
`semantic_relationships`, which requires resolved semantic endpoints. Its required
`source_evidence_id` is authoritative for the page/view/sheet/bbox containing the
mark. `relation_type` accepts any non-blank string; its vocabulary is intentionally
extensible. `printed_reference` is nullable and, when present, is retained exactly.

`exact` and `resolved_deterministically` facts require at least one target view,
sheet, or evidence pointer. Multiple pointers must exist, belong to one document,
agree with any sheet/view carried by target evidence, and—where existing placement
data can prove it—the target view must be placed on the target sheet. `ambiguous` and
`unresolved` facts retain their printed reference with every target pointer null;
Query Core never guesses a winner.

`QueryCore.has_drawing_reference_capability()` detects the table without changing
schema version 2 or making it mandatory for legacy databases.
`get_drawing_reference(id)` returns the stored fact, authoritative source evidence
and navigation, plus resolved target records/navigation when available;
`get_drawing_references_for_view(id)` deterministically lists facts whose source
evidence belongs to that view. A PDF/project-only database may use this capability
without a `source_models` host row. This slice performs no automatic Revit/PDF
reference extraction, target matching, semantic mutation, or duplication into
`semantic_relationships`.

### Optional semantic projection

Current v2 builds include optional `semantic_entities`, `semantic_bindings`,
`semantic_properties`, and `semantic_relationships` tables.
They give source-neutral project concepts a stable identity and explicit binding state
(`exact`, `resolved_deterministically`, `ambiguous`, or `unresolved`) without changing
the meaning of existing Query Core records. Bindings can name an `element`,
`element_type`, `space`, `level`, `evidence`, or a future adapter's PDF table/cell/text
record. Resolved bindings to known Query Core source kinds are checked against their
actual source rows; unknown future adapter kinds remain valid and extensible.

`QueryCore.has_semantic_capability()` detects the Phase 1A tables physically and does
not require `semantic_properties`. `QueryCore.has_semantic_property_capability()`
separately detects the Phase 1B sparse-property table, while
`QueryCore.has_semantic_relationship_capability()` detects the independent,
source-neutral relationship table. Neither optional table is required by the base
semantic capability. A pre-semantic v2
database without either table remains valid, returns `False`, and continues to support
all existing queries. `QueryCore.get_semantic_entity(id)` returns a compact read-only
view containing capability status, semantic identity, bindings, projected sparse
properties, existing relationships, and evidence references. For resolved Revit
element/type bindings, properties are read dynamically from the authoritative
`parameters` rows and preserve `definition_name`, source value, scope, provenance, and
evidence. Persisted project/PDF facts are added to the same `properties` list with
`fact_kind=semantic_property`, exact `source_name`/`source_value`, optional numeric and
unit fields, scope, provenance, and binding/evidence references. Project facts and
projected parameters with the same name are both returned; the API performs no winner
selection or name normalization. Existing `relationships` are exposed dynamically with
`fact_kind=query_core_relationship` rather than copied. Explicit project/PDF/source-
backed relationships between semantic entities are stored separately and returned with
`fact_kind=semantic_relationship`. Both shapes preserve stored fields and add a relative
`direction` (`outgoing`, `incoming`, or `self`) plus `evidence_refs`; semantic rows retain
`source_binding_id`. Results are ordered by fact kind and ID. The semantic relationship
table is not a Revit table, does not require a host source model, and does not change or
duplicate authoritative Revit relationship facts. Absent properties are omitted, and
Revit parameters are never copied into `semantic_properties`.

Legacy v2 databases without semantic tables and Phase 1A/1B/1C databases without
`semantic_relationships` remain readable. The first has no semantic API capability;
the others retain their existing projections and report the relationship capability as
unavailable. Unknown non-empty relationship types remain valid for future adapters.

`QueryCore.get_semantic_spatial_context(id)` is a read-only building-topology
projection for resolved (`exact` or `resolved_deterministically`) `space`, `element`,
and `level` bindings. Each binding remains a separate, deterministically ordered
context. Space and element contexts reuse `get_spatial_context()` and therefore the
existing containment, connection, level, type-membership, and adjacency APIs rather
than implementing topology again. Stored relationships and context are labeled
`evidence_class=source_fact`; adjacency and type-sibling observations are labeled
`evidence_class=deterministic_derived` and are never inserted into
`semantic_relationships`. A referenced source entity includes all existing resolved
semantic references, or an empty semantic-reference list when no binding exists. The
API does not create entities, fuzzy-match records, or convert a Query Core level into a
semantic Storey.

`QueryCore.get_semantic_drawing_context(id)` similarly projects only existing source
locations from resolved bindings. Query Core `element`, `element_type`, `space`, and
`level` bindings reuse occurrence evidence and navigation; `pdf_table_cell`,
`pdf_text_span`, and `evidence` bindings retain their persisted document, one-based
page, bbox, coordinate space, provenance, and available source traceability. Every
appearance row and binding stays independently identifiable and is labeled
`evidence_class=source_fact`. Results are ordered by document identity, page,
sheet/view, bbox, occurrence ID, and binding ID. The API returns `insufficient_data`
rather than resolving names or inferring drawing-topology relationships.

This read path uses only `sqlite3` and the Python standard library. It therefore keeps
the same Python 3.12 `-S` boundary as all other existing-SQLite and Project Query Bundle
queries; semantic creation remains a build-time concern.

### Semantic Entity Discovery — Phase 4B

Semantic discovery is the deterministic entry point when the internal entity ID is not
known. Search stored entity IDs, numbers, labels, and optional sparse property values,
then explicitly pass the selected ID to architectural context retrieval:

```text
semantic-find → semantic entity ID → architectural-context
```

```bash
python -S -m tools.query_core semantic-find project.sqlite "D-101" --entity-class Door
python -S -m tools.query_core architectural-context project.sqlite semantic-door-101
```

`semantic-find` returns every matching candidate up to `--limit` in deterministic
precedence order and explains each exact or substring match. It never resolves an
ambiguity or recursively retrieves candidate context. Matching is SQLite `NOCASE`
(ASCII case-insensitive), not fuzzy or language-aware. If `semantic_properties` is
absent, identity lookup remains available and property matching is reported unavailable.

### Architectural Evidence Retrieval — Phase 4A

`QueryCore.get_architectural_evidence_context(semantic_entity_id)` composes the existing
semantic entity, spatial context, and drawing context projections into one read-only,
LLM-independent envelope:

```text
semantic entity
  ├─ properties
  ├─ relationships
  ├─ spatial context
  ├─ drawing occurrences
  ├─ evidence/navigation
  └─ source documents
```

The envelope is one-hop only: it includes the requested identity, direct bindings and
facts, existing deterministic spatial contexts and direct occurrences, plus evidence
referenced by direct facts. Relationship endpoints are references and are never
recursively expanded. Source facts remain authoritative; ambiguous and unresolved
bindings remain explicit and do not contribute projected source facts. No LLM or Vision
dependency is used, and no drawing reference is automatically associated merely because
it shares a view. Schedule appearances remain distinct occurrences, including
`appearance_kind=schedule` and `bbox_quality=page_only`.

The query remains standard-library-only and read-only. Example:

```bash
python -S -m tools.query_core architectural-context project.sqlite semantic-door-101
```

### Vision Evidence Routing Contract — Phase 5A

```text
semantic-find → explicit entity selection → architectural-context → vision-candidates → future renderer/provider
```

```bash
python -S -m tools.query_core vision-candidates project.sqlite semantic-door-101
```

`QueryCore.get_vision_evidence_candidates(semantic_entity_id)` routes only existing
direct architectural-context occurrences and evidence. It accepts a selected ID,
not a free-text query; CLI sources are existing SQLite databases or Project Query
Bundles. Each candidate contains a stable surface-based `candidate_id`,
`input_scope`, canonical `document` (including authoritative `source_sha256`),
`pdf_page`, unchanged `bbox`, stored `bbox_quality` (null when absent),
`coordinate_space`, `navigation`, and sorted `source_refs` / `routing_reasons`.
Usable boxes route as `region`; no box with a valid document/page routes as `page`,
including schedule `bbox_quality=page_only`. Invalid boxes are skipped, never padded
or replaced with a crop. Identical document/page/scope/box targets are deduplicated;
distinct source locations remain separate.
Every contributing occurrence/evidence navigation and quality survives in `source_refs`;
the lexically first surface supplies primary navigation and quality. Direct fact IDs
and reasons are aggregated through existing evidence and binding references.

Results have `status=ok`, `semantic_entity_id`, `candidates`, and
`coverage.candidate_count`, plus the existing semantic capability descriptor. An
existing entity without routable surfaces succeeds with an empty candidate list;
a missing entity returns null. No new capability, schema, table, or cache is required.
`vision-candidates` does not call Vision, does not render images, and does not change
source facts. These are routing targets, not observations or review findings. Phase 5
is not complete; rendering and provider execution remain future work. Issue #47
real-document validation remains pending until a permitted real PDF environment exists.

### Explicit PDF table semantic adapter

`SemanticTableMapping` and `apply_semantic_table_mapping(records, mapping)` provide a
build-time-only, opt-in adapter for one explicitly selected `pdf_tables.id`. The generic
mapping names the header row(s), entity class, exact key header(s), instance/type value,
property scope, optional label/number headers, and either exact property headers or
`all_non_key`. `canonical_names` is an optional exact header-to-name dictionary. Header
matching is exact: a missing requested header, a duplicate requested header, or a
multi-valued configured header column is an error. No schedule discovery, fuzzy matching,
domain synonym, OCR, or semantic guessing occurs, and normal PDF build/update commands do
not invoke this adapter.

An entity ID is the SHA-256 encoding of the JSON tuple `(mapping_id, source_namespace,
entity_class, exact_key_values)`, prefixed by `semantic-pdf-`. Key text is neither trimmed
nor normalized for identity (whitespace is examined only to decide whether a key is
blank), so table row reordering does not affect identity. Blank keys are reported as
errors and every occurrence of a duplicated key is reported as ambiguous; none of those
rows is materialized.

Each non-empty selected data cell creates one sparse `semantic_properties` row. Its
`source_name` is the exact matched header, its `source_value` is the exact cell text, and
`canonical_name` remains null unless the mapping explicitly supplies it. The property
references a dedicated exact `semantic_bindings` row whose `source_kind` is
`pdf_table_cell` and whose `source_id` is the actual cell ID. Entity key cells receive the
same kind of exact binding. The adapter emits no `relationships`, performs no PDF/Revit
entity fusion, and leaves source PDFs and PDF Pipeline output untouched.

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

In occurrence projections, `appearance_sheet_id` and `appearance_view_id` preserve
the raw direct appearance references. The projected `sheet_id` and `view_id` are
navigation context resolved within one document; direct references take precedence
over viewport fallbacks, and references from another document are not projected.

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
declared `pages/pNNNN.json` sidecars, and the matching `.pdf-pipeline-v1` or
`.pdf-pipeline-v2` marker) and writes one
single-document Query Core database. It does not scan sibling documents or extract
the PDF again. The adapter verifies the logical document/page identities, page
metadata, text hierarchy, and the current source PDF byte hash before building.

```bash
python -m tools.query_core build-pdf --repo-root . \
  projects/example/knowledge/example--0123456789ab artifacts/example.sqlite
```

Pipeline v2 is the current producer; Pipeline v1 remains supported for legacy inputs.
Its conservatively accepted ruled tables are persisted as an optional Query Core v2
capability. Source PDF spans remain authoritative evidence: every non-empty derived
cell traces back to ordered `pdf_text_spans`. Ordinary FTS remains source-text-only;
table text is not duplicated into `search_content` or `search_fts`. Marker, declared
version, and `created_from` generation must agree.

The additive `pdf_text_blocks`, `pdf_text_lines`, and `pdf_text_spans` tables retain
pipeline order, text, bboxes, provenance, and `pdf_points_top_left` coordinates.
Block text is indexed as `pdf_text_block`; `QueryCore.search_pdf_text()` returns the
source document identity and SHA, one-based PDF page, bbox, and the standard
viewer-neutral evidence navigation descriptor. Databases created before these
optional PDF-native tables remain readable.

`QueryCore.has_pdf_table_capability()` distinguishes those legacy tableless v2
databases from current databases containing zero accepted tables. The read-only APIs
`list_pdf_tables(document_identity=None, pdf_page=None)`, `get_pdf_table(table_id)`,
`get_pdf_table_cell(cell_id)`, and `search_pdf_table_cells(query, limit=20)` expose
deterministically ordered tables, cells, authoritative span links, and viewer-neutral
navigation. Cell search is a direct Unicode substring search, not FTS, and rejects a
non-positive or non-integer `limit` with `QueryCoreError`. This capability makes no
header or other semantic inference and does not yet support borderless, partially
ruled, or merged-cell tables.

## Build or incrementally update a PDF project

`build-pdf-project` consumes the current project manifest and strictly reparses every
owned PDF Pipeline v1 or v2 document into a fresh project-bound Query Core. A project
must use one generation consistently, and incremental updates require the database
`created_from` generation to match. It is the
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

## Package a portable PDF project

There are three distinct dependency boundaries:

1. **Existing SQLite query runtime:** an existing `project.sqlite` can be searched
   from a repository checkout with Python 3.12, `-S`, and only the standard library.
2. **Existing Project Query Bundle runtime:** an already-created bundle can likewise
   be inspected and searched with Python 3.12, `-S`, and only the standard library.
3. **Creation and ingestion:** PDF Pipeline processing, intake, Query Core building,
   and bundle packaging run on the ingestion machine and may require the project's
   installed dependencies. Raw PDF ingestion is not a zero-install operation.

The third-party PDF handoff is deliberately split as follows:

```text
Ingestion machine:
source PDFs -> PDF Pipeline -> intake report -> project Query Core -> Project Query Bundle

Receiving/query machine:
Project Query Bundle -> inspect -> search
```

The receiving/query machine needs only Python 3.12's standard library and a repository
checkout. For example:

```bash
python3 -S -m tools.query_core inspect-pdf-project-bundle path/to/bundle
python3 -S -m tools.query_core search path/to/bundle "fire resistance"
```

Single-document packaging is unchanged: an original `source.pdf` and a
`binding_mode=single_document` Query Core become one Enhanced PDF. It never accepts a
project-bound payload.

For a current project database, `package-pdf-project` creates Project Query Bundle v1:

```bash
python -m tools.query_core package-pdf-project --repo-root . \
  projects/example artifacts/example-project.sqlite artifacts/example-query-bundle
python -m tools.query_core inspect-pdf-project-bundle \
  artifacts/example-query-bundle
python -m tools.query_core search \
  artifacts/example-query-bundle "fire resistance"
```

Its canonical, portable directory layout is:

```text
example-query-bundle/
├─ bundle.json
├─ project.sqlite
└─ sources/
   └─ <original relative path below projects/example/source/>
```

`bundle.json` maps every canonical logical identity to its bundled path, exact source
SHA-256, and a one-to-one source/Query Core page map. Source PDFs are copied
byte-for-byte and remain authoritative, with original one-based page numbering. There
is no binder PDF, duplicate per-PDF database, or runtime dependency on `knowledge/`
sidecars.

Packaging rejects a stale database and writes into a sibling temporary directory before
strict verification and atomic rename. Normal bundle-directory search validates the
manifest essentials, database SHA, Query Core schema, and project binding, but does not
scan or hash all source PDFs. `inspect-pdf-project-bundle` exhaustively verifies every
source path, SHA, mapping, symlink boundary, and case-insensitive collision. The public
`project_bundle_source()` resolver verifies only the selected PDF before navigation.
ZIP, tar, binder, and other archive/container forms are not the Phase 2C1 contract.

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
