# PDF Pipeline v2

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
status, a vision-review hint, and conservative ruled-table structure. It does not infer missing metadata, classify documents,
or add semantic interpretation. It does not call OCR, an LLM, or any external service.

`document_id` is the truncated SHA-256 of the normalized repository-relative source path,
prefixed with `doc-`. It therefore remains stable when content at that path changes.
`source_sha256` separately identifies the current bytes. Generated output is:

```text
projects/<project-id>/knowledge/<source-stem>--<stable-id>/
├── .pdf-pipeline-v2
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
Ruled tables use PyMuPDF 1.28.2 `lines_strict` detection with cached vector drawings.
Only complete, unambiguous grids are retained; cells reference their contributing spans
and reconstruct text from those spans. Tables remain derived evidence and the source PDF
remains authoritative. Borderless, partial-rule, and merged-cell extraction is deferred.
size, and flags are copied only when PyMuPDF provides them. Dimensions and bounding boxes
use `pdf_points_top_left`: PyMuPDF's unrotated, top-left page coordinates in PDF points,
with x increasing right and y increasing down. The raw PDF `/MediaBox` is converted with
PyMuPDF's page transformation matrix, and `crop_box` records the page-local crop extent
used by text bboxes. The page rotation is recorded separately. These coordinates are not
Revit, model, or world coordinates. `no_text` and `minimal_text` are not extraction
success. A page is marked
`vision_recommended` when it has under 40 extracted characters, contains a raster image,
or has at least 200 vector drawing objects. This is a processing hint only, not a legal,
technical, or semantic conclusion.

Unchanged PDF bytes and pipeline versions are skipped. Updated bytes regenerate the same
document identity. Deleted source entries remove only directories bearing the pipeline's
management marker. Changed documents are fully staged before replacement; a corrupt or
password-protected PDF fails clearly before existing generated output or the manifest is
changed. Manifests are written through an atomic file replacement.

## Report existing project knowledge

After processing, generate a deterministic project-level intake and readiness report:

```bash
python -S -m tools.pdf_pipeline report projects/my-project
python -S -m tools.pdf_pipeline report projects/my-project --format json
```

The report reads only the manifest, `document.json` summaries, and structured page
sidecars. It never opens source PDF bytes, so Python 3.12's standard library is enough
and no virtual environment or pip install is required once Pipeline v2 knowledge exists.
Raw ingestion and rendering still require the project dependencies, including PyMuPDF.

For permitted third-party or legacy PDFs: place files in `source/`, process them on a
dependency-enabled ingestion machine, run the report, and inspect `review_pages`. Then
build the project Query Core and query normally. Searching an existing `project.sqlite`
is likewise standard-library-only. Return to the authoritative source PDF whenever
layout or visual evidence is ambiguous. Generated knowledge is only an index;
`vision_recommended` is only a processing hint, and a review entry does not mean OCR or
Vision is required. Do not commit confidential client or consultant PDFs for testing.

## Render selected pages

```bash
python -m tools.pdf_pipeline render \
  projects/example/source/test.pdf \
  --pages 1,3-5 --dpi 300 --output artifacts/vision
```

Use `--all` instead of `--pages` only when every page is deliberately required. DPI must
be between 36 and 1200. PNGs are temporary, ignored under the documented artifact paths,
and cannot be written into `projects/*/knowledge/`. Very large sheets can consume
substantial memory at high DPI; v2 intentionally does not implement tiling.

## Render an explicitly selected evidence candidate — Phase 5B

Discover and explicitly select a semantic entity and Phase 5A `candidate_id` using
Query Core, then render it on a dependency-enabled machine:

```bash
python -m tools.pdf_pipeline render-candidate \
  artifacts/project.sqlite <semantic-entity-id> <candidate-id> \
  --pdf projects/example/source/source.pdf --dpi 300 --output artifacts/vision
```

The Python API is
`tools.pdf_pipeline.vision_render.render_vision_candidate(repo_root, database,
semantic_entity_id, candidate_id, source_pdf, *, dpi=300, output=...)`.
Relative paths resolve against `repo_root`; `output` is a directory. This command
regenerates candidates with `QueryCore.get_vision_evidence_candidates()` and
requires exactly one matching ID. It does not accept a bbox or search query.

The explicitly supplied PDF must match the candidate document's canonical,
64-character lowercase hexadecimal `source_sha256`. Missing/malformed hashes,
mismatches, absent candidates/entities, invalid pages, unreadable/corrupt PDFs,
and password-required PDFs fail explicitly. Copies or renamed files with identical
bytes are valid; their names never replace stored document metadata. The PDF is
opened from the same byte buffer used for hashing, avoiding a source replacement
race. This retains the complete PDF bytes in memory during rendering.

A page candidate requires `bbox=null` and renders the full displayed source page.
A region candidate requires a finite, positive-area bbox and exactly
`coordinate_space=pdf_points_top_left`. Bounds are checked against the actual
unrotated page-local crop extent used by Pipeline v2 extraction, rather than the
raw MediaBox, offset CropBox, or rotated page dimensions. Partially outside boxes
fail; there is no padding, clamping, page fallback, scaling policy, or tiling.
PyMuPDF [`Page.rotation_matrix`](https://pymupdf.readthedocs.io/en/latest/page.html#Page.rotation_matrix)
maps that stored rectangle into displayed page space for
[`Page.get_pixmap`](https://pymupdf.readthedocs.io/en/latest/page.html#Page.get_pixmap)
clipping. The PNG retains source display rotation; 90°/270° swap region dimensions.
The original bbox remains unchanged in metadata. Synthetic tests prove dimensions
and colored marker locations for 0°, 90°, 180°, and 270°, including offset crops.
Fractional clip boundaries use PyMuPDF's normal enclosing integer pixel grid.

Rendering shares `render_pages()`'s `fitz.Matrix(dpi / 72, dpi / 72)`, opaque PNG,
artifact-directory safety, and atomic replacement helpers. DPI defaults to 300
and must be an integer from 36 through 1200. The existing `render` CLI keeps its
page selection, filenames, full-page behavior, and output policy. Absolute
external output directories remain supported; repository-local output must use
`artifacts/`, `vision/`, `renders/`, `.tmp/`, `.cache/`, or `tiles/`. Failed saves or
replacements preserve an existing PNG and clean staging files.

The stable filename is
`<safe-document-stem>-<full-candidate-id>-p0001-300dpi.png`; the stem is sanitized
and bounded, and the complete candidate identity avoids truncated-ID collisions.
JSON stdout/API results contain `status`, `candidate_id`, `semantic_entity_id`,
`input_scope`, canonical `document`, one-based `pdf_page`, unchanged `bbox` (null
for pages), stored `coordinate_space`, `dpi`, `pixel_width`, `pixel_height`,
`renderer` (name, PyMuPDF library/version, page rotation, alpha policy),
`output_path`, and the SHA-256 of the atomically published PNG (`output_sha256`).
Paths are repository-relative when possible, otherwise absolute. Identical renders
produce identical PNG bytes in the pinned PyMuPDF 1.28.2 test environment;
cross-version byte identity is not promised, so library version is recorded.

Inputs, candidate metadata, source PDFs, and the SQLite database remain read-only.
There is no sidecar, database migration, table, image cache, or observation write.
Query Core remains stdlib-only with schema version 2 and SnapshotContract version 1.
This is a rendering boundary only: Vision/provider execution and Issue #47
permitted real-document validation remain future work.

## Render deterministic candidate context — Phase 5D / V1

For V1 candidate-region inspection (architectural semantic model §10), explicitly
select a Phase 5A **region** candidate and supply the authoritative source PDF:

```bash
python -m tools.pdf_pipeline render-context-candidate \
  artifacts/project.sqlite <semantic-entity-id> <candidate-id> \
  --pdf projects/example/source/source.pdf --dpi 300 --output artifacts/vision-context
```

The Python API is
`tools.pdf_pipeline.vision_render.render_vision_context_candidate(repo_root,
database, semantic_entity_id, candidate_id, source_pdf, *, dpi=300, output=...)`.
`output` is a directory. The API and CLI offer no margin argument, bbox override,
automatic candidate selection, or source filesystem scan.

The fixed `fixed_margin_v1` policy expands the unchanged `evidence_bbox` by
**144.0 PDF points** on all four sides in unrotated `pdf_points_top_left` space.
Only this expanded rectangle is clamped to the actual page-local crop extent;
an evidence bbox outside that extent fails rather than being adjusted. The
resulting `context_bbox` always contains the evidence. Bboxes are never rounded,
snapped, or guessed. `clamped_edges` lists exactly the edges changed by clamping,
in `left`, `top`, `right`, `bottom` order. An expanded edge that exactly meets the
extent is not marked clamped. Page candidates and crops with no additional
context fail explicitly. Context that reaches the entire extent remains a
region crop with both bboxes; there is no region-to-page fallback. A different
margin requires a future policy version.

V1 shares Phase 5B's exact candidate regeneration/ID selection, canonical document
SHA validation, same-buffer PDF opening, DPI bounds, rotation mapping, ignored
artifact output restrictions, and atomic opaque PNG publication. Offset CropBox
coordinates use the same unrotated page-local extent as extraction. The PNG
retains the displayed rotation, including 90°/270° dimension swaps; fractional
boundaries use PyMuPDF's enclosing pixel grid without modifying either bbox.

JSON/API results include `status=ok`, `stage=V1`, `semantic_entity_id`,
`candidate_id`, canonical `document` (including identity and `source_sha256`),
one-based `pdf_page`, `input_scope=region`, `evidence_bbox`, `context_bbox`,
`coordinate_space=pdf_points_top_left`, `policy=fixed_margin_v1`, `margin_pt=144.0`,
`clamped_edges`, `dpi`, pixel dimensions, renderer library/version/rotation/alpha,
`output_path`, and `output_sha256`. No ambiguous `bbox` field is emitted, so the
existing Phase 5C V3 request builder rejects a V1 result. Phase 5B results and
behavior are unchanged. The filename includes the full candidate ID and policy:
`<safe-document-stem>-<candidate-id>-fixed_margin_v1-p0001-300dpi.png`.
Changing the output directory changes only `output_path`, never identity or
image bytes in the pinned renderer environment.

Source PDFs, the SQLite database, and candidate metadata remain read-only;
generated PNGs stay outside version control. Query Core SCHEMA_VERSION stays 2
and SnapshotContract.Version stays 1. This phase adds no V1 request schema,
provider execution, OCR, observations, persistence, V0/V2, paired images, LLM,
or embeddings. Before actual V1 Vision execution, a separate V1 request contract
must bind the evidence/context geometry and policy to the image, followed by
provider integration and optional derived-observation handling. Issue #47
permitted real-document validation remains pending.

## Validation

```bash
python -m pytest
```

`schema/document.schema.json` validates generated `document.json`,
`schema/pdf_page.schema.json` validates structured page sidecars, and
`schema/manifest.schema.json` validates project manifests.
