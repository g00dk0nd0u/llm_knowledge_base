# LLM Knowledge Base

Private monorepo for project-specific and shared technical knowledge optimized for LLM retrieval and later vision-assisted review.

## Design principles

1. **Source of truth = original PDF**  
   Keep the original document unchanged under each project's `source/` directory.

2. **Text-first retrieval**  
   Generate searchable Markdown and metadata under `knowledge/`. Every derived item must retain its source PDF and page mapping.

3. **Vision on demand**  
   Do not permanently commit large page-image sets by default. Render PNG/crops/tiles from the source PDF only when visual inspection is needed.

4. **One private monorepo, project-separated**  
   Project-specific material lives under `projects/<project-id>/`. Reusable material lives under `common/`.

5. **Traceability over convenience**  
   LLM-generated summaries are never treated as the source. Answers should be traceable to the original document and page.

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
├─ schema/
├─ tools/
│  └─ pdf_pipeline/
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

## PDF pipeline v1

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

The pipeline extracts embedded text only. It performs **no OCR, LLM summarization,
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

The intended boundary is: **Revit = future export-time source; Enhanced PDF =
portable handover artifact; SQLite = embedded v2 machine payload; Query Core =
runtime; LLM = optional consumer.** A Revit 2025/2026/2027 Phase B1 offline exporter is available under
`revit_exporter/`; it emits this contract but adds no corridor compliance reasoning
or change-impact analysis.

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
logical source paths remain distinct documents. Revision-history storage and project
bundle packaging are not yet implemented.
