# LLM Knowledge Base

**Architectural & Interior Design Knowledge Base for BIM, Revit, drawings, schedules, specifications, PDFs, semantic retrieval, and LLM-assisted review.**

LLM Knowledge Base is a source-driven system for the **integrated understanding of architectural and interior design information**.

Architecture and interior projects rarely live in one place. Design intent is distributed across plans, sections, elevations, details, schedules, specifications, BIM/Revit data, consultant drawings, room information, element parameters, and revision sets. A conventional document search or RAG pipeline can retrieve matching text, but it often loses the relationships that make the information meaningful as architecture.

This project is designed to preserve those relationships.

Instead of flattening a project into isolated text chunks, it builds a portable, evidence-backed, BIM-like knowledge layer that can retain:

- building hierarchy and spatial context;
- rooms/spaces, elements, types, and project-specific properties;
- sheets, views, schedules, and drawing occurrences;
- drawing relationships such as callouts, elevations, and explicit references;
- PDF text, tables, pages, and source locations;
- Revit-derived source facts and provenance;
- explicit ambiguity, conflicts, and missing evidence.

The result is intended to let an LLM, reviewer, or downstream application understand a project as a **connected body of architectural information**, not just a collection of files.

## Why this is useful

A useful architectural answer often requires connecting information that appears in different representations.

For example, a door may need to be understood through:

```text
Door D-105
  -> located in / connects spaces
  -> shown on floor plan
  -> listed in door schedule
  -> referenced by detail or callout
  -> carries Revit instance/type parameters
  -> may have related fire, hardware, security, finish, or glazing information
  -> remains traceable to the original drawing, schedule, model object, or PDF page
```

The same pattern applies to walls, ceilings, finishes, equipment, lighting, furniture, millwork, structure, MEP coordination, room data, and many other architectural/interior objects.

This makes the system useful for tasks such as:

- architectural and interior design information retrieval;
- construction drawing and shop drawing review;
- design-development and construction-document review;
- plan / schedule / detail cross-checking;
- architectural / structural / MEP coordination;
- specification and technical-information lookup;
- BIM / Revit knowledge retrieval;
- tracing a design answer back to its source evidence;
- reducing hundreds or thousands of pages to a smaller evidence-backed review set;
- future change-impact and revision-review workflows.

The goal is **not autonomous design approval**. The goal is to make the relevant project context easier to retrieve, connect, inspect, and verify so that a human reviewer can make better decisions faster.

## What is different from conventional RAG

Conventional document RAG usually follows a pattern like:

```text
document -> text chunks -> embeddings/search -> LLM
```

That works well for prose, but architectural information depends heavily on relationships between objects, spaces, drawings, schedules, and visual evidence.

This project therefore aims for:

```text
PDF / Revit / BIM / schedules / consultant drawings
                    |
                    v
             Query Core evidence
     text / tables / parameters / geometry
     spaces / occurrences / page / provenance
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

The semantic layer is **not a second source of truth**. It is a structured projection over traceable source facts and documented deterministic derivations.

See [`docs/architectural-semantic-model.md`](docs/architectural-semantic-model.md) for the architecture and evidence model.

## Core design ideas

### 1. Integrated architectural understanding

The primary goal is not merely document search. It is to connect **building information** and **drawing information** so they can be retrieved together.

The model treats both as first-class structures:

```text
Building world
Project -> Site -> Building -> Storey -> Space -> Element / System / Finish / Equipment

Drawing world
Document -> Sheet -> View / Schedule -> Occurrence -> Detail / Callout / Reference
```

These worlds are connected through source-backed relationships and occurrences.

### 2. Source-driven, not schema-first

Architectural projects contain highly variable information. A door schedule may contain electric lock, fire rating, glass thickness, threshold, hardware set, finish, smoke seal, security level, acoustic performance, or project-specific fields.

The database therefore does not require a universal fixed property matrix with hundreds of empty columns. It stores the facts that actually exist in the project.

### 3. Preserve source terminology

Source names and values are retained exactly where possible.

For example:

```text
source_name  = "電気錠"
source_value = "EL560"
```

Optional semantic normalization may be added, but it must never silently replace the original field/value.

### 4. Building topology and drawing topology are separate

The system distinguishes:

- what the building is — spaces, elements, systems, containment, adjacency, connectivity;
- how the building is represented — plans, sections, elevations, details, schedules, callouts, and occurrences.

This distinction is important for architectural reasoning and drawing review.

### 5. Evidence before inference

Source facts, deterministic derivations, Vision observations, and LLM hypotheses have different authority.

Recommended evidence classes include:

- `source_fact`;
- `deterministic_derived`;
- `vision_observation`;
- `llm_hypothesis`.

If sources conflict, the system should retain the conflict and its evidence rather than silently selecting a winner.

### 6. Vision is optional

Vision can inspect drawings, diagrams, symbols, linework, dimensions, room boundaries, tags, and other visual context when useful, but it does not silently overwrite deterministic source facts.

### 7. Portable and framework-independent

Query Core remains a Python-standard-library-compatible SQLite query layer. LangChain, LlamaIndex, vector databases, OCR, cloud LLMs, or Vision providers are not mandatory runtime dependencies.

## Current capabilities

The repository currently includes:

- a source-faithful PDF Pipeline with embedded-text extraction and conservative ruled-table extraction;
- page/source metadata and deterministic PDF evidence mapping;
- portable Query Core v2 SQLite payloads;
- full-text search and structured retrieval;
- documents, sheets, views, viewports, levels, spaces, elements, element types, parameters, annotations, relationships, geometry, and source evidence;
- semantic entities, source bindings, sparse semantic properties, and source-neutral semantic relationships;
- deterministic PDF-table-to-semantic mapping through explicit configuration;
- entity occurrences and drawing/PDF navigation;
- a Revit 2025/2026/2027 offline exporter architecture;
- Revit source-model and link-instance identity;
- spatial boundaries and selected geometry;
- explicit Revit reference section/callout/elevation topology;
- deterministic ordinary Revit callout topology;
- deterministic ordinary Revit elevation-marker topology;
- optional Vision-oriented page/crop rendering without permanently storing large image sets.

Some relationships intentionally remain unresolved rather than guessed. For example, ordinary Revit section-cut topology is currently deferred because the supported API does not yet provide the same reliable source-marker -> target-view relationship available for ordinary callouts and elevations.

## Typical usage

### PDF-only project

```text
Original project PDFs
        |
        v
PDF Pipeline
        |
        +-> source-faithful Markdown / text / tables / page metadata
        |
        v
Query Core SQLite
        |
        +-> search / structured retrieval / evidence navigation
        |
        v
Architectural semantic retrieval / optional LLM or Vision consumer
```

1. Put unchanged original PDFs under `projects/<project-id>/source/`.
2. Run the PDF Pipeline.
3. Build or update the project Query Core database.
4. Query the structured evidence directly or consume it from an LLM application.

### Revit-backed project

```text
Revit model
   |
   +-> native PDF export
   +-> structured Revit snapshot
               |
               v
           Query Core
               |
               +-> spaces / elements / parameters
               +-> sheets / views / occurrences
               +-> drawing topology
               +-> source evidence
               |
               v
       portable architectural knowledge layer
```

The Revit exporter is intentionally offline and source-oriented. It extracts explicit Revit facts rather than embedding review conclusions into the exporter.

See [`revit_exporter/README.md`](revit_exporter/README.md) for deployment, extraction policy, drawing-reference behavior, and required real-Revit smoke tests.

## Design principles

1. **Original sources remain authoritative**  
   Keep original project PDFs unchanged under each project's `source/` directory. For PDF-native facts, the original PDF/page remains the final visual authority. Revit-derived facts retain explicit Revit source identity and provenance.

2. **Text-first, structure-preserving retrieval**  
   Generate searchable Markdown and metadata, but preserve page/source mappings, tables, entities, spatial context, drawing context, and provenance instead of reducing everything to text chunks.

3. **Vision on demand**  
   Do not permanently commit large page-image sets by default. Render PNG/crops/tiles from the source PDF only when visual inspection is needed.

4. **One private monorepo, project-separated**  
   Project-specific material lives under `projects/<project-id>/`. Reusable material lives under `common/`.

5. **Traceability over convenience**  
   LLM-generated summaries are never treated as the source. Material answers should remain traceable to the original document, page, model object, or deterministic source relationship.

6. **Do not invent missing architectural semantics**  
   Ambiguous, conflicting, unsupported, and unresolved relationships remain explicit rather than being filled by fuzzy inference.

## Repository layout

```text
llm_knowledge_base/
├─ projects/
│  └─ _template/
│     ├─ source/
│     ├─ knowledge/
│     └─ manifest.json
├─ common/
│  ├─ codes/
│  ├─ revit/
│  └─ data-center/
├─ docs/
│  └─ architectural-semantic-model.md
├─ revit_exporter/
├─ schema/
├─ tests/
├─ tools/
│  ├─ pdf_pipeline/
│  └─ query_core/
├─ .github/
│  └─ workflows/
├─ AGENTS.md
├─ .gitattributes
└─ .gitignore
```

## Intended ingestion flow

```text
PDF
  ↓
projects/<project-id>/source/
  ↓
PDF processing pipeline
  ├─ text / structure extraction
  ├─ Markdown generation
  ├─ page/source metadata
  └─ project index update
  ↓
projects/<project-id>/knowledge/

When visual confirmation is needed:
source PDF → selected page → high-resolution PNG/crop/tile → Vision LLM
```

## PDF Pipeline v2

Use Python 3.12 and install the local package:

```bash
python -m pip install -e '.[test]'
```

1. Create `projects/<project-id>/source/` and place unchanged original PDFs there.
2. Run `python -m tools.pdf_pipeline process --all` from the repository root.
3. Review `projects/<project-id>/manifest.json` and the generated directory under
   `projects/<project-id>/knowledge/`.

Each document directory contains `document.json`, `index.md`, and one Markdown file per
source page under `pages/`. These files record the repository-relative source path,
source SHA-256, stable path-derived document ID, and original one-based PDF page number.
Reruns skip unchanged sources; updates retain the document ID; removed PDFs have their
pipeline-managed knowledge removed.

The pipeline extracts embedded text first and adds only conservative, fully ruled table
structure derived from vector lines. Cell text is rebuilt from, and linked to, the
source-faithful spans. Borderless and merged-cell tables are deferred. It performs **no OCR, LLM summarization,
metadata inference, image captioning, or architectural interpretation**. A textless or
low-text page is explicitly marked and recommended for visual review. This recommendation
is only a processing hint. The original PDF and cited page remain the authority, especially
for layouts, drawings, diagrams, symbols, and tables.

Render only required pages for temporary vision inspection (the page selector or `--all`
is mandatory):

```bash
python -m tools.pdf_pipeline render \
  projects/example/source/test.pdf --pages 1,3-5 --dpi 300 \
  --output artifacts/vision
```

`artifacts/` is ignored by Git. The renderer refuses to write into a project's
`knowledge/` directory and never modifies the source PDF. See
[`tools/pdf_pipeline/README.md`](tools/pdf_pipeline/README.md) for details.

## Portable Query Core v2

The [Query Core](tools/query_core/README.md) is a separate, Revit-independent layer.
It builds a versioned SQLite payload containing architectural entities, numeric
annotations, relationships, exact compact geometry, provenance, and one-based PDF
evidence. The payload can be embedded in an ordinary drawing PDF, extracted to an
ignored content-addressed cache, and queried read-only through a structured Python
API. The included fixture data is entirely synthetic.

Query Core returns deterministic, viewer-neutral navigation descriptors rather than
opening a PDF viewer or executing `open_sheet`, zoom, highlight, or OS commands.
The UI opens the stored PDF page and may zoom to/highlight a bbox only when the
descriptor reports `can_zoom=true`; Query Core never invents a missing bbox.

The intended boundary is: **Revit = export-time structured source; Enhanced PDF / project bundle =
portable handover artifact; SQLite = embedded machine payload; Query Core = deterministic runtime;
Architectural Semantic Projection = structured interpretation layer; LLM / Vision = optional consumers.**

A Revit 2025/2026/2027 offline exporter is available under `revit_exporter/`; it emits
this contract but does not embed corridor compliance reasoning, autonomous drawing
approval, or LLM inference into the source export.

For one already-processed PDF, `python -m tools.query_core build-pdf` creates a
`binding_mode=single_document` database. For a fresh project snapshot, use:

```bash
python -m tools.query_core build-pdf-project --repo-root . \
  projects/example artifacts/example-project.sqlite
```

The project manifest determines current membership, while every source PDF and all
PDF Pipeline sidecars are independently validated. This command remains the
correctness-reference full rebuild: it creates a fresh atomic database and inserts
one document at a time for bounded memory use.

To inspect the current project against an existing compatible project database without
modifying either side, use:

```bash
python -m tools.query_core compare-pdf-project --repo-root . \
  projects/example artifacts/example-project.sqlite
```

The comparison reports deterministic added, changed, removed, and unchanged logical
documents. Changed documents include the exact previous and current SHA-256 revisions.
It also reports groups of current logical source paths that contain identical PDF bytes;
those paths remain distinct documents and are never deduplicated. A logical path rename
is reported as an explicit removal plus addition, even when the bytes are identical.
The comparison stores no revision history and does not mutate the database, manifest,
source PDFs, or generated knowledge.

After rerunning PDF Pipeline, an existing compatible project database can instead be
updated explicitly:

```bash
python -m tools.query_core update-pdf-project --repo-root . \
  projects/example artifacts/example-project.sqlite
```

Incremental update never mutates the live database in place. It validates the existing
project payload, classifies logical document identities as added/changed/removed/
unchanged, rechecks authoritative source SHA-256 and document metadata for unchanged
sources, and rejects noncanonical cached PDF search/evidence rows. It then snapshots
the old SQLite through the SQLite backup API, applies only document deltas to the
temporary database, checks foreign keys plus the FTS5 external-content index against
`search_content`, validates the resulting Query Core snapshot, and atomically replaces
the output. Unchanged page sidecars are intentionally not reparsed; the previous
validated Query Core is the cached canonical representation for an unchanged source
SHA.

None of the project commands runs PDF Pipeline automatically. Equal PDF bytes at different
logical source paths remain distinct documents. Revision-history storage is not implemented.

### Portable PDF deliverables

A **single-document Enhanced PDF** remains `source.pdf` plus a single-document Query
Core embedded into one output PDF. Project-bound databases remain deliberately rejected
by that packaging command.

A **Project Query Bundle** instead combines many authoritative source PDFs with one
current project-bound Query Core in a portable directory:

```text
example-query-bundle/
├─ bundle.json
├─ project.sqlite
└─ sources/
   └─ <original path below projects/example/source/>
```

```bash
python -m tools.query_core package-pdf-project --repo-root . \
  projects/example artifacts/example-project.sqlite artifacts/example-query-bundle
python -m tools.query_core inspect-pdf-project-bundle \
  artifacts/example-query-bundle
python -m tools.query_core search \
  artifacts/example-query-bundle "fire resistance"
```

The PDFs are byte-for-byte copies and retain their original one-based page numbering;
no binder PDF or knowledge sidecars are included. Ordinary search verifies bundle and
database metadata plus the database SHA without hashing every PDF. Strict inspection
hashes every source, while navigation resolves and hashes only the selected source.
The canonical Phase 2C1 format is this directory, not a ZIP or other archive.

## Related concepts and search terms

This project overlaps with topics often described as:

- architectural knowledge base;
- interior design knowledge base;
- BIM knowledge retrieval;
- Revit knowledge base;
- architectural RAG / BIM RAG;
- architectural semantic search;
- construction drawing review;
- shop drawing review;
- drawing coordination;
- architectural document intelligence;
- multimodal architecture AI;
- building information retrieval;
- drawing topology;
- semantic BIM data;
- evidence-backed LLM retrieval.

The implementation deliberately stays source-driven and framework-independent rather than adopting any one RAG, knowledge-graph, BIM, or LLM framework wholesale.
