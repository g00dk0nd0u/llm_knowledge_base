# Architectural Semantic Model for Drawing Review

Status: **Phase 0 architecture decision / design baseline**  
Tracking: Issue #40  
Production schema/code change: **none in this document**

## 1. Purpose

The project should evolve from a portable evidence/search database into an architectural review system that can preserve and retrieve **building structure, spatial relationships, drawing relationships, project-specific properties, and source evidence** without flattening everything into text chunks.

The target use cases are:

- construction/shop drawing review;
- construction-document / implementation-design markup review;
- cross-drawing consistency checks;
- architectural / structural / MEP coordination;
- finding a small evidence-backed set of locations that deserves human review across a large drawing set.

This is **not** a proposal to reconstruct a full RVT/DWG, simulate a building world, or let an LLM invent missing BIM semantics. The model is a compact, source-driven, BIM-like semantic representation optimized for architectural retrieval and LLM consumption.

## 2. Architecture decision

The system should keep Query Core as the authoritative portable evidence/fact store and add an **Architectural Semantic Projection** above it.

```text
Revit / IFC / PDF / schedules / consultant drawings
                     |
                     v
              Query Core evidence
      text / tables / parameters / geometry
      spaces / occurrences / page / bbox / provenance
                     |
                     v
        Architectural Semantic Projection
       entities + sparse properties + relations
        building topology + drawing topology
                     |
                     v
          Architectural Evidence Retrieval
                     |
          +----------+-----------+
          |                      |
          v                      v
     deterministic           optional Vision
       retrieval             observations/checks
          |                      |
          +----------+-----------+
                     v
                    LLM
                     |
                     v
        grounded review / explanation
```

The semantic projection is not a second source of truth. Every material property or relationship must remain traceable to source evidence or to an explicitly identified deterministic derivation.

## 3. Design rules

### 3.1 Source-driven, not schema-first

Do not define a universal door/equipment/room table with a large fixed set of nullable fields.

A project schedule may contain, for example:

- electric lock;
- fire rating;
- glass thickness;
- threshold / 靴ずり;
- hardware set;
- finish;
- smoke seal;
- security level;
- acoustic performance;
- project-specific notes.

Only properties actually present in the project source should exist as facts. A different project may expose a different set of properties.

### 3.2 Preserve source terminology

Every extracted property must preserve the source field name and source value.

Example:

```text
source_name  = "電気錠"
source_value = "EL560"
```

Optional normalization may add:

```text
canonical_name = "access_control.lock_model"
```

but must never replace the source field/value. If normalization is uncertain, `canonical_name` remains absent.

### 3.3 Sparse properties

Property count is variable per entity. Missing properties are absent, not represented by pre-created empty columns.

### 3.4 Separate source facts from interpretation

A source fact, a deterministic derivation, and a Vision/LLM observation are different evidence classes and must never be silently merged.

### 3.5 Building topology and drawing topology are both first-class

The system must model both:

1. **what the building is** — storeys, spaces, elements, systems, containment, adjacency, connectivity; and
2. **how the building is represented** — plans, sections, elevations, enlarged plans, details, schedules, callouts, occurrences.

### 3.6 3D geometry is optional, topology is not

The semantic projection does not require a complete 3D model. Selected exact geometry may be retained where Query Core already has it or where a review question needs it. The primary LLM-facing structure is hierarchy + topology + properties + evidence.

## 4. What already exists in Query Core v2

The current Query Core already provides much of the required foundation and should be reused rather than duplicated.

Existing capabilities include:

- `documents`, `pdf_pages`;
- PDF text blocks / lines / spans with bbox;
- conservative PDF tables / cells / source-span links;
- `source_models`, `link_instances`;
- `sheets`, `views`, `viewports`;
- `levels`, `spaces`, `element_types`, `elements`;
- sparse `parameters` rows with instance/type scope, source definition metadata, raw and normalized values, units, provenance, confidence and evidence;
- generic `relationships` rows;
- annotations and annotation references;
- many-to-many `entity_appearances` with sheet/view/page/bbox;
- spatial boundaries and boundary-source provenance;
- selected exact geometry and conservative spatial queries;
- source PDF evidence navigation;
- explicit ambiguous / conflict / insufficient-data outcomes.

Important consequence: **Phase 1 must not create another parameter system merely because the new semantic model needs variable properties. Query Core already uses row-oriented variable parameters for Revit. The new design should generalize this idea to project/PDF-derived semantic entities and preserve the existing Revit path.**

## 5. Reference models — borrow concepts, not runtimes

### 5.1 Revit

Use as a reference for practical BIM data density and authoring semantics.

Borrow:

- stable source identity;
- instance vs type;
- Family/Type/Category-like classification where available;
- arbitrary parameter collections;
- Level / Room / Space;
- host relationships;
- FromRoom / ToRoom connector semantics;
- MEP system relationships;
- View / Sheet / Viewport / Schedule placement;
- occurrence identity for linked models.

Do not copy:

- Revit runtime/API dependency into query-time;
- authoring-only object complexity;
- assumptions that every semantic entity originates in Revit;
- Revit categories as the universal architectural ontology.

### 5.2 IFC

Use as the main Open BIM semantic reference.

Borrow:

- Project/Site/Building/Storey/Space hierarchy;
- distinction between spatial decomposition and element containment;
- object type vs occurrence;
- variable property sets and occurrence/type property assignment;
- the idea that elements may be referenced by multiple spatial structures while retaining one primary containment relationship;
- open classification rather than authoring-application identity.

Do not copy:

- the full EXPRESS entity hierarchy;
- complete IFC exchange serialization into Query Core;
- mandatory mapping of every project fact to an IFC class/property before it can be stored;
- IFC complexity not needed for review retrieval.

### 5.3 BOT — Building Topology Ontology

Use as the strongest reference for a **small topology vocabulary**.

Borrow:

- Site / Building / Storey / Space as nested zones;
- containment;
- adjacency;
- intersection;
- Zone-to-Element relationships;
- the useful idea of an `Interface` between zones/elements for relationships that need their own evidence or properties.

Do not copy:

- RDF/OWL as a mandatory storage/runtime format;
- a topology-only scope as the complete project model;
- the requirement that all geometry be represented as linked 3D-model resources.

### 5.4 Brick

Use primarily for building-system relationship semantics.

Borrow where explicit source evidence exists:

- `hasPart` / `part_of`;
- `hasLocation` / `located_in`;
- `feeds` / `isFedBy`;
- equipment/system relationships;
- separation of physical location from composition.

Do not copy:

- telemetry/controls ontology as the core architectural model;
- the full Brick class taxonomy;
- RDF as a runtime requirement.

### 5.5 Project Haystack

Use as a reference for an **LLM-friendly sparse representation**, not as the project ontology.

Borrow:

- entity as a sparse dictionary of facts;
- explicit typed values and units;
- opaque IDs / references between entities;
- structural typing / additive tags;
- absence rather than large matrices of empty fields.

Do not copy:

- Haystack tag naming restrictions as the source data model;
- IoT/HVAC-centric vocabulary as the architectural master vocabulary;
- loss of original project field names through forced tag normalization.

### 5.6 LangChain / LlamaIndex

Reference designs only. They are not connection targets and are not planned runtime dependencies. Only general ideas such as separation of retrieval from answer synthesis are relevant.

## 6. Logical semantic model

Phase 0 defines logical objects, not final SQL names.

### 6.1 Semantic Entity

A semantic entity is a project concept that the review system can reason about.

Examples:

- Project, Site, Building, Storey, Space;
- Door, Wall, Floor, Ceiling, Column, Beam, Window;
- equipment, lighting, furniture, millwork;
- systems;
- finish types;
- drawing views/details/schedules where they need graph identity.

Minimal logical fields:

```text
entity_id
entity_class                 # broad class, extensible
label / number               # only when present
instance_or_type             # instance | type | unknown
source_binding(s)            # links to Query Core records/evidence
status                       # resolved | unresolved | ambiguous where needed
```

`entity_class` must not be a closed universal enum in Phase 1. A small core may be recognized, but source-specific classes must remain representable.

### 6.2 Source Binding / Mention

Do not assume every PDF occurrence that says `D-105` is automatically the same canonical Door.

A source binding records that a semantic entity is represented by, derived from, or corresponds to a specific Query Core record or PDF region.

Possible bindings:

- Revit element / element type;
- space / level;
- PDF table row/cell set;
- PDF text block/span;
- entity appearance;
- annotation/reference;
- page/bbox region.

Resolution state must be explicit:

```text
exact
resolved_deterministically
ambiguous
unresolved
```

### 6.3 Sparse Property Fact

Logical shape:

```text
property_id
entity_id
source_name
source_value
value_type
unit
scope                         # instance | type | sheet | schedule | unknown
canonical_name                # optional
canonical_value               # optional; never replaces source_value
source_binding / evidence_id
provenance
status
```

A property is a fact row. Entity classes do not predeclare every possible property.

For a Revit source, existing `parameters` should be exposed through this semantic contract rather than copied where possible.

For a PDF schedule, the schedule header/cell structure supplies `source_name` and the row/cell supplies `source_value`. If row semantics cannot establish instance vs type, `scope=unknown` rather than guessing.

### 6.4 Semantic Relationship Fact

Logical shape:

```text
relationship_id
subject_ref
source_relation_name          # when a relation is expressed in source material
canonical_relation            # optional controlled core
object_ref
source_binding / evidence_id
provenance
status
```

The relationship vocabulary is extensible. Unknown source relations do not need to be discarded merely because they are not yet normalized.

## 7. Core building topology vocabulary

Use a small controlled core only where semantics are well defined and evidence exists.

### Hierarchy / composition

- `contains`
- `located_in`
- `part_of`
- `hosted_by`
- `instance_of`

### Spatial / interface

- `adjacent_to`
- `bounds`
- `opens_to`
- `connects_to`
- `intersects`
- `above`
- `below`
- `near`

### Systems

- `serves`
- `feeds`
- `connected_to`

Existing Query Core deterministic operations remain authoritative for relations they already prove. The semantic layer must not replace exact stored adjacency/distance logic with LLM inference.

A relation may be emitted only when one of these is true:

1. explicitly present in a structured source;
2. produced by an existing deterministic Query Core rule;
3. produced by a future deterministic algorithm with documented coverage and limitations.

Vision/LLM observations do not become authoritative topology automatically.

## 8. Drawing topology

Drawing structure is separate from building topology but connected to it.

### Existing reusable records

- document;
- sheet;
- view;
- viewport/schedule placement;
- entity appearance;
- page/bbox evidence.

### Target relations

- `shown_on`
- `scheduled_on`
- `detailed_on`
- `section_cut_to`
- `elevation_reference_to`
- `callout_to`
- `enlarged_view_of`
- `detail_of`
- `references`

A drawing reference needs, where available:

```text
source drawing/view/page
source region/bbox
reference label as printed
resolved target drawing/view/page
resolved target region/bbox if known
resolution status
provenance
```

If a section marker reads a target number but the target sheet cannot be resolved safely, retain the printed reference and `unresolved` status rather than inventing a link.

This model supports architectural traversal such as:

```text
Door D-105
  -> shown_on Floor Plan A-101
  -> scheduled_on Door Schedule A-601
  -> detailed_on Door Detail A-801

Floor Plan A-101
  -> section_cut_to Section A-301 / 2
  -> enlarged_view_of / callout_to A-121
```

## 9. Evidence and authority model

Do not use a single confidence float as the truth model.

Every semantic fact should have an evidence class and a resolution state.

### Evidence class

Recommended core:

- `source_fact` — explicitly stored in Revit/IFC/PDF text/table/source metadata;
- `deterministic_derived` — produced from source facts by a deterministic documented operation;
- `vision_observation` — image-model observation tied to an exact source page/crop;
- `llm_hypothesis` — optional transient proposal, never authoritative by itself.

### Resolution state

Recommended core:

- `supported`
- `conflict`
- `ambiguous`
- `insufficient_evidence`
- `unresolved`

A `vision_observation` may support or challenge a source/derived candidate, but cannot silently modify a `source_fact`.

### Conflict handling

If two sources disagree, retain both facts with evidence and expose a conflict set.

Example:

```text
Door D-105
  Door Schedule: electric lock = EL560
  Security Drawing: electric lock = EL160
  state = conflict
```

The LLM/reviewer receives both source locations. The database does not select a winner without an explicit deterministic rule or human decision.

## 10. Vision authority boundary and touchpoints

Vision is optional and may be invoked at more than one stage.

### V0 — page/sheet triage

Use for coarse visual classification or candidate selection, for example plan / section / elevation / detail / schedule / mixed sheet.

Output is derived metadata only.

### V1 — candidate-region inspection

After text/table/entity retrieval yields a bbox, inspect a wider crop around it to understand nearby:

- linework;
- room boundaries;
- door swings;
- dimensions;
- tags;
- grids;
- callouts;
- symbols.

### V2 — drawing-relation verification

Use selected paired regions to inspect relations such as:

- plan <-> section;
- plan <-> enlarged plan;
- plan/section <-> detail;
- plan <-> schedule.

### V3 — final evidence inspection

After retrieval has reduced the candidate set, inspect the original page/high-resolution crop immediately before a grounded answer or review finding.

### Persistence rule

A persisted Vision observation must record at minimum:

```text
source document SHA
page
crop bbox / rendering parameters
model/provider identifier
observation
created_at or run identity if persisted
status
```

Vision output belongs in an optional derived layer/cache, not in the deterministic source-fact tables. Querying an existing Query Core must remain possible without any Vision provider.

## 11. Architectural Evidence Retrieval contract

The LLM should not receive arbitrary database dumps or be expected to invent SQL.

Retrieval should produce a bounded, structured evidence graph.

Conceptual result:

```json
{
  "focus": [
    {
      "id": "door:D-105",
      "class": "door",
      "label": "D-105",
      "properties": [
        {
          "source_name": "電気錠",
          "source_value": "EL560",
          "canonical_name": "access_control.lock_model",
          "scope": "instance",
          "evidence_refs": ["ev:door-schedule:d105:lock"]
        },
        {
          "source_name": "ガラス",
          "source_value": "t=8.0",
          "evidence_refs": ["ev:door-schedule:d105:glass"]
        }
      ]
    }
  ],
  "relationships": [
    {
      "subject": "door:D-105",
      "relation": "opens_to",
      "object": "space:UPS-01",
      "evidence_class": "deterministic_derived"
    }
  ],
  "drawings": [
    {
      "relation": "scheduled_on",
      "document": "A-set.pdf",
      "page": 61,
      "bbox": [100.0, 220.0, 780.0, 510.0]
    }
  ],
  "conflicts": [],
  "coverage": {
    "unresolved_references": 0
  }
}
```

Absent properties are omitted. Do not emit a universal list containing nulls for every possible architectural property.

## 12. Persistence strategy for Phase 1

Phase 0 does **not** commit the SQL schema. The preferred implementation direction is nevertheless clear:

1. Keep all current Query Core v2 tables and meanings unchanged.
2. Prefer an **additive optional capability** over a breaking rewrite.
3. Reuse existing `parameters`, `relationships`, `evidence`, `entity_appearances`, `sheets`, `views`, and PDF-native tables whenever they already represent the fact correctly.
4. Add only the minimum generic semantic tables needed for concepts that cannot safely fit the Revit-oriented `elements/spaces/element_types` tables.
5. Older Query Core v2 payloads must remain readable.
6. The standard-library-only query runtime must remain intact.

A likely minimal additive design to prototype in Phase 1 is:

```text
semantic_entities
semantic_bindings
semantic_properties        # only where not already represented by parameters
semantic_relationships     # only where not already represented by relationships
```

However, Phase 1 must first test whether `semantic_properties` and `semantic_relationships` can instead be projections over existing tables plus generic entity/binding records, avoiding duplicated facts.

**Default decision: do not duplicate a Revit parameter or existing Query Core relationship merely to make the semantic layer look uniform. Build a uniform read contract first; materialize only what cannot otherwise be represented.**

## 13. Compatibility requirements

### Query Core v2

- no existing table meaning changes;
- no required migration of old databases merely to search them;
- new capabilities detectable at runtime;
- missing semantic capability produces reduced capability, not corruption/errors.

### Zero-install query runtime

Reading an already-built semantic-capable SQLite/Project Query Bundle must continue to work with Python 3.12 standard library only.

Optional dependencies are allowed only at build/enrichment time, for example:

- PDF extraction;
- future deterministic drawing-reference extraction;
- optional Vision processing.

### Revit independence

Revit remains an export-time source adapter. Query/review must not require Revit or RVT access.

## 14. Source construction pipeline

The semantic model should be built from actual project sources progressively.

```text
A. inventory documents / sheets / pages / views
B. ingest exact text / table / Revit facts
C. create source mentions / bindings
D. resolve entities only where safely supported
E. attach sparse properties
F. attach deterministic building/drawing relations
G. expose unresolved / conflicting references
H. optionally run Vision on selected candidates
```

This prevents the system from trying to create a complete empty BIM database before it has project evidence.

## 15. Review proof cases

Phase 1-6 should be judged against real workflows, not ontology completeness.

Priority proof cases:

1. **Door** — plan vs door schedule vs detail, including electric lock, fire/smoke requirements, glass, hardware, threshold and project-specific fields.
2. **Room / finish** — room/finish schedule vs plan vs reflected ceiling plan.
3. **Plan / section** — verify vertical relationships and section references.
4. **Architecture / structure** — opening, column, beam, slab and level coordination.
5. **Architecture / MEP** — ceiling, equipment, access and spatial coordination.
6. **Revision review** — previous markup vs revised drawing at the same entity/region.
7. **Shop drawing** — submitted construction detail vs issued design-intent evidence.

Success is measured primarily by:

- reduction in drawing-search effort;
- ability to gather the correct related evidence set;
- useful identification of conflicts/uncertainty;
- reliable navigation back to source page/bbox;
- preservation of reviewer authority.

## 16. Phase 0 conclusions

1. **Revit remains the best practical reference for dense project object/parameter semantics, but must not define the universal database.**
2. **IFC provides the strongest reference for open building hierarchy, containment, type/occurrence and variable property concepts.**
3. **BOT provides the clearest lightweight reference for building topology and interfaces.**
4. **Brick contributes useful system/location/composition relations.**
5. **Haystack contributes a useful sparse, reference-based, LLM-digestible representation style.**
6. The project should implement its own compact semantic projection, because none of the reference models alone covers architectural drawing topology + source PDF evidence + bbox + review workflow.
7. The semantic model must grow from actual sheets/schedules/BIM facts rather than from predefined empty property matrices.
8. Vision should be a selective evidence inspector at V0-V3, never the unquestioned source of truth.
9. The immediate next implementation step is a **small Phase 1 prototype of generic entity/binding projection and sparse PDF-derived properties**, not a broad ontology migration or LLM integration.

## 17. References reviewed for this decision

- Autodesk Revit 2026 API — `FamilyInstance`, room/space, type/symbol, parameters, host and FromRoom/ToRoom semantics: https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/
- buildingSMART IFC 4.3 — Spatial Structure / Spatial Containment: https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/concepts/Object_Connectivity/Spatial_Structure/content.html
- buildingSMART IFC 4.3 — `IfcPropertySet` and object/type property assignment: https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcPropertySet.htm
- Building Topology Ontology (BOT): https://w3c-lbd-cg.github.io/bot/
- Brick relationships: https://docs.brickschema.org/brick/relationships.html
- Project Haystack introduction / data model: https://www.project-haystack.org/doc/docHaystack/Intro
- Project Haystack relationships: https://www.project-haystack.org/doc/docHaystack/Relationships
