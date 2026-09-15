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

The PDF processing workflow is intentionally not implemented in the initial repository skeleton.
