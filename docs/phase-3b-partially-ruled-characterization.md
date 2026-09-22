# Phase 3B-0: partially ruled table characterization

This report records observations produced by `tools.pdf_pipeline.characterization` with
PyMuPDF 1.28.2. Fixtures are generated born-digital PDFs; they are not evidence sources
and are created in temporary test directories. Coordinates are unrotated
`pdf_points_top_left`. Production `_extract_tables()` is not called or changed by this
harness.

## Method

P0 is `lines_strict`, `use_layout=False`, cached `paths`; P1 adds explicit
`join_tolerance` values 1, 2, 3, 4, 6, 9, and 12 pt. P2 uses strict vertical rules plus
text horizontal structure (`min_words_horizontal=2`); P3 is the inverse
(`min_words_vertical=2`). Both-text detection is deliberately absent. Every observation
contains fixture/profile/parameters, supplied drawing-path count, candidate count, bbox,
rows, columns, cell slots, `None` count and bboxes, extracted text, Phase 3A acceptance
and rejection reason, and unique source-span mapping status. Canonical JSON comparison
proves repeatability without committing generated PDFs or bulky output.

## Observed matrix

| Case | P0 | P1 result | P2 | P3 |
|---|---|---|---|---|
| fully ruled 3x3 | 1 accepted 3x3 | unchanged at 1–12 | 0 | 1 candidate, rejected 1x3 |
| horizontal/vertical gaps 1–3 pt | accepted 3x3 | accepted | 0 | H: rejected 1x3; V: rejected 1x3 |
| gaps 4/6 pt | 1 candidate with one `None`, rejected | recovered at tolerance 4/6 respectively | 0 | not useful |
| gaps 9/12 pt | one `None`, rejected | recovered only at 9/12 respectively | 0 | not useful |
| three 2 pt gaps | accepted 3x3 | accepted | 0 | rejected 1x3 |
| fully missing internal H/V | accepted as 2x3 / 3x2 (lost logical boundary) | no recovery through 12 | 0 | H: 0; V: rejected 1x3 |
| missing top/left border | accepted as truncated 2x3 / 3x2 | unchanged | 0 | rejected 1x3 |
| vertical-only / horizontal-only | 0 / 0 | 0 / 0 | 0 / 0 | 0 / rejected 1x3 |
| merged-looking H/V | 3x3 with one `None`, rejected | unchanged | 0 | rejected malformed candidates |
| empty + multiline Japanese | accepted 3x3; empty bbox remains real | unchanged | 0 | rejected 1x3 |
| rotated 4 pt partial | one `None`, rejected | recovered at tolerance 4; bbox `[50,50,320,158]` | 0 | 0 |
| A3-like 20x12 | 1 path, 1 candidate, 1 accepted, 240 cells/spans | unchanged | 1 malformed 39x10 | 1 18x12 candidate |
| five negative controls | 0 candidates | 0 through tolerance 12 | 0 | 0 |

The controls are aligned prose, text on background rectangles, unrelated rectangles,
floor-plan-like linework, and a sparse connected-box diagram. No P1 false positive was
observed through 12 pt. Thus this corpus establishes **no observed false-positive
boundary**, rather than claiming that 12 pt is safe generally.

## Decisions and Phase 3B-1 recommendation

* **Short gaps:** P0 already joins through 3 pt. An explicit 6 pt tolerance adds recovery
  for 4–6 pt gaps and all negative controls remain negative. Use **6 pt as the sole
  Phase 3B-1 candidate**, gated by real-document validation; do not adopt 9 or 12 pt
  merely because these synthetic controls pass. The first false-positive boundary was
  not observed, so this is deliberately conservative.
* **Fully missing separators:** geometry joining cannot recreate absent geometry. The
  detector silently collapses 3x3 intent to 2x3/3x2, which current topology checks cannot
  recognize. Keep these out of the first rollout and do not infer the missing rule.
* **Hybrid profiles:** P2 recovered none of this corpus and produced a malformed 39x10
  large-schedule candidate; P3 frequently returned one-row or dimension-altered
  candidates. Neither is conservative enough for rollout. Study each separately with a
  broader corpus rather than combining it with gap joining.
* **Merged cells:** PyMuPDF reports a 3x3 grid with one `None`; surrounding geometry does
  not uniquely distinguish an intentional span from a broken separator. Continue
  rejection. `row_span`/`column_span` cannot be populated without guessing.
* **Resources:** the A3-like schedule is one drawing path, one P0 candidate, one accepted
  20x12 table, 240 cells, and 240 uniquely mapped non-empty source spans. Profiles reuse
  the single cached drawings list per invocation; no timing threshold is used and no
  candidate explosion appeared under P0.

## Future contract and Query Core compatibility

Option 1 (keep `pymupdf_lines_strict`, bump algorithm version) is minimally sufficient
only while every table uses the same geometry-only profile. Option 2 (a combined ruled
identifier) describes a family but cannot audit which method produced an individual
table. If multiple profiles can run on one page, **Option 3 is required**: retain a
page-level algorithm/version and add canonical per-table detection-profile metadata
(method plus explicit tolerances/axis strategies). Phase 3B-1 should initially use
Option 1 with an algorithm-version bump when the production change is made; migrate to
Option 3 before any hybrid fallback. Do not use a combined identifier as a substitute
for profile provenance.

Existing Pipeline v2 sidecars remain readable under the current schema and should not
be regenerated by this characterization task. A future algorithm-version change should
make incremental processing regenerate owned derived sidecars while leaving source PDFs
untouched. Query Core v2 can continue storing the effective method in
`pdf_tables.detection_method` and algorithm version using its existing validation; a
future per-table profile must be deterministically projected there or require an
explicit later contract migration. Incremental updates, Enhanced PDF `/1`, Project Query
Bundle `/1`, and existing table APIs need no change for geometry-only Phase 3B-1.
Source spans remain authoritative and table structure remains derived evidence.
