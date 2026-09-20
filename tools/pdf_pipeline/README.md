# PDF Pipeline v1

This deterministic pipeline builds a searchable, page-preserving Markdown layer from
embedded PDF text. The generated layer is an index—not authoritative evidence.

## Process every project

From the repository root, with Python 3.12:

```bash
python -m pip install -e '.[test]'
python -m tools.pdf_pipeline process --all
```

The command scans `projects/<project-id>/source/**/*.pdf`. For each PDF it computes a
SHA-256, reads available embedded metadata, and records per-page dimensions, rotation,
media/crop boxes, embedded text primitives, image count, drawing count, extraction
status, and a vision-review hint. It does not infer missing metadata, classify documents,
or add semantic interpretation. It does not call OCR, an LLM, or any external service.

`document_id` is the truncated SHA-256 of the normalized repository-relative source path,
prefixed with `doc-`. It therefore remains stable when content at that path changes.
`source_sha256` separately identifies the current bytes. Generated output is:

```text
projects/<project-id>/knowledge/<source-stem>--<stable-id>/
├── .pdf-pipeline-v1
├── document.json
├── index.md
└── pages/
    ├── p0001.md
    ├── p0001.json
    └── ...
```

Page Markdown contains machine-readable YAML-compatible front matter and source-faithful
embedded text. Each page JSON sidecar preserves ordered text blocks, lines, and spans,
including their text, bounding boxes, and embedded-PDF-text provenance. Span font name,
size, and flags are copied only when PyMuPDF provides them. Dimensions and bounding boxes
use `pdf_points_top_left`: PyMuPDF's unrotated, top-left page coordinates in PDF points;
the page rotation is recorded separately. These coordinates are not Revit, model, or
world coordinates. `no_text` and `minimal_text` are not extraction success. A page is marked
`vision_recommended` when it has under 40 extracted characters, contains a raster image,
or has at least 200 vector drawing objects. This is a processing hint only, not a legal,
technical, or semantic conclusion.

Unchanged PDF bytes and pipeline versions are skipped. Updated bytes regenerate the same
document identity. Deleted source entries remove only directories bearing the pipeline's
management marker. Changed documents are fully staged before replacement; a corrupt or
password-protected PDF fails clearly before existing generated output or the manifest is
changed. Manifests are written through an atomic file replacement.

## Render selected pages

```bash
python -m tools.pdf_pipeline render \
  projects/example/source/test.pdf \
  --pages 1,3-5 --dpi 300 --output artifacts/vision
```

Use `--all` instead of `--pages` only when every page is deliberately required. DPI must
be between 36 and 1200. PNGs are temporary, ignored under the documented artifact paths,
and cannot be written into `projects/*/knowledge/`. Very large sheets can consume
substantial memory at high DPI; v1 intentionally does not implement tiling.

## Validation

```bash
python -m pytest
```

`schema/document.schema.json` validates generated `document.json`,
`schema/pdf_page.schema.json` validates structured page sidecars, and
`schema/manifest.schema.json` validates project manifests.
