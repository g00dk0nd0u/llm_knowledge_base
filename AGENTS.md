# AGENTS.md

## Purpose
This repository is a private personal knowledge base for LLM-assisted retrieval across architecture, interiors, BIM, building codes, data centers, and project-specific technical documents.

## Core rules
- Preserve original source PDFs unchanged.
- Never overwrite a source PDF with a processed derivative.
- Never invent document metadata, page numbers, drawing numbers, titles, dates, disciplines, or project assignments.
- Every derived Markdown document must identify its source file and relevant source page(s).
- Prefer source-faithful extraction over aggressive summarization.
- Keep project-specific knowledge under `projects/<project-id>/`.
- Put only genuinely reusable, project-independent material under `common/`.
- Treat generated knowledge as an index/retrieval layer, not as authoritative evidence.
- For diagrams, plans, sections, details, tables, or visually meaningful pages, use the source PDF for final visual verification.
- Do not commit bulk rendered page images unless explicitly required.
- Temporary PNG/WebP/crops/tiles belong outside version control by default.
- Pipeline changes must remain reproducible and deterministic where practical.
- PDF pipeline v1 extracts embedded text only; do not add OCR, LLM summaries, inferred
  metadata, or image captions to generated output.
- Treat `.pdf-pipeline-v1` as the ownership marker for generated document directories;
  stale cleanup must never delete an unmarked directory.
- Keep on-demand rendered images in ignored artifact locations, never under `knowledge/`.

## Project structure
Each project should follow:

```text
projects/<project-id>/
├─ source/       # original PDFs
├─ knowledge/    # derived Markdown/indexes
└─ manifest.json # project/document metadata
```

## Retrieval priority
1. Search derived Markdown and metadata.
2. Resolve the source PDF and source page.
3. If the answer depends on layout, geometry, drawing symbols, tables, or image content, render only the required page/region for vision inspection.
4. Cite or report the original PDF/page as the evidence location.

## File naming
- Use stable, filesystem-safe project IDs.
- Keep original PDF filenames unless there is a strong reason to normalize them.
- Generated Markdown filenames should remain traceable to the source document.

## Pipeline checks

- Use Python 3.12 and install test dependencies with `python -m pip install -e '.[test]'`.
- Run `python -m pytest` after pipeline or schema changes.
- Run `python -m tools.pdf_pipeline process --all` from the repository root; a second run
  should produce no tracked diff.
