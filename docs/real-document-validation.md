# Representative real-document validation

This is the next step after Phase 3B characterization.

The goal is not to improve extraction recall by default. The goal is to measure whether the current production P0 baseline is already sufficient for practical technical-PDF retrieval.

The original PDF remains authoritative.

## Safety boundary

Use only PDFs that are permitted for local evaluation.

`projects/_local_validation/` is intentionally ignored by Git and must remain local-only. Do not commit client, employer, consultant, project, or other confidential PDFs merely to create a test corpus.

Synthetic fixtures should be added to the repository only when they reproduce a specific recurring real-document failure that materially affects retrieval.

## Create the local validation project

Copy the tracked template locally:

```bash
cp -R projects/_template projects/_local_validation
```

On PowerShell:

```powershell
Copy-Item projects/_template projects/_local_validation -Recurse
```

Edit `projects/_local_validation/manifest.json` so the initial empty manifest is:

```json
{
  "project_id": "_local_validation",
  "project_name": "Representative real-document validation",
  "description": "Local-only validation corpus; never commit source PDFs.",
  "pipeline_version": "2",
  "documents": []
}
```

Place permitted representative PDFs under:

```text
projects/_local_validation/source/
```

Start with a small set, typically 5–8 PDFs. Prefer diversity over volume:

- technical specification or report;
- door, equipment, room, or finish schedule;
- code / compliance table;
- A3 technical schedule;
- large-format architectural / structural / MEP / consultant drawing;
- mixed page containing drawing + notes + table.

## Run the existing production baseline

Do not enable characterization profiles or widened `join_tolerance`.

```bash
python -m pip install -e '.[test]'
python -m tools.pdf_pipeline process --all
```

Prepare manual review with the metadata-only intake report:

```bash
python -S -m tools.pdf_pipeline report projects/_local_validation
python -S -m tools.pdf_pipeline report projects/_local_validation --format json
```

The report uses already-generated knowledge only and needs neither PyMuPDF nor installed
site packages. Its `review_pages` identifies factual no/minimal-text, raster-containing,
vector-heavy, rejected-table-candidate, and table-extraction-error pages. It is a triage
aid, not a replacement for the manual validation below: `vision_recommended` is only a
hint, and report inclusion does not assert that OCR or Vision is required.

Build a project Query Core outside tracked project content:

```bash
python -m tools.query_core build-pdf-project --repo-root . \
  projects/_local_validation artifacts/real-validation.sqlite
```

Use the existing Query Core search / table APIs for representative queries. Do not add a new evaluator merely to automate this step unless manual review demonstrates a recurring need.

The complete legacy-PDF workflow is: place permitted PDFs in the project `source/`, run
the dependency-enabled Pipeline v2 ingestion, review the intake report, build the project
Query Core, query it normally, and return to the authoritative source PDF for visual
ambiguity. Raw ingestion requires PyMuPDF/project dependencies; reporting generated
knowledge and querying an existing SQLite Query Core are Python 3.12 standard-library-only.

## What to evaluate

For each representative PDF, record only practical retrieval outcomes:

1. Is embedded text searchable where it exists?
2. Does the result return the correct source document and one-based source page?
3. Is the bbox useful for navigation where available?
4. Is deterministic ruled-table structure recovered when the visible table is clearly ruled?
5. When table structure is uncertain, does the system fall back safely to source-faithful text instead of inventing cells?
6. Is opening the authoritative source PDF sufficient to resolve the remaining visual ambiguity?
7. Does any recurring failure materially block a real query?

Avoid turning this into a generic PDF reconstruction benchmark.

## Minimal observation record

A simple local note is sufficient. For each document / query record:

```text
Document:
Page:
Query:
Text retrieval: pass / partial / fail
Page navigation: pass / partial / fail
BBox navigation: pass / partial / n/a / fail
Table structure: pass / partial / safely rejected / n/a / fail
Source PDF resolves ambiguity: yes / no
Practical impact:
Notes:
```

The observation file itself may contain sensitive project information, so keep it under an ignored local path such as `artifacts/` unless it has been deliberately sanitized.

## Decision rule

After the first representative sample:

- If P0 is sufficient for normal work, keep production unchanged.
- If one recurring real-world pattern materially blocks retrieval, isolate that pattern and create the smallest deterministic regression / fallback justified by it.
- If failures are rare or visually ambiguous, keep source-PDF verification rather than increasing reconstruction complexity.

Do not reopen Phase 3B algorithm work based only on synthetic recall improvements.

## Cleanup / verification

Before any future commit or PR, verify that local PDFs and local generated content are not tracked:

```bash
git status --short
```

`projects/_local_validation/` and `artifacts/` should not appear as tracked changes.
