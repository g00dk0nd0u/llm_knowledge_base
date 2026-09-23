# Phase 3B-0: partially ruled table characterization

## Scope and method

This report records measured PyMuPDF 1.28.2 observations from generated born-digital
PDFs. Fixture names encode synthetic ground truth; the extractor sees only observable
PDF geometry and text. Temporary fixtures are not source evidence. Coordinates are
unrotated `pdf_points_top_left`.

P0 is the unchanged production call (`lines_strict`, `use_layout=False`, cached
`paths`). P1 uses explicit `join_tolerance` values 1, 2, 3, 4, 6, 9, and 12 pt. P2 uses
strict vertical rules plus text-derived horizontal structure; P3 is the inverse. There
is no both-text profile. A fixture is opened once and its drawings and production-order
text hierarchy (`get_text("dict", sort=True)`) are reused by every profile.

Characterization and production share the same Phase 3A candidate evaluator: reading
order, dimension and shape checks, contained valid cells, `None` rejection, accepted
candidate overlap rejection, and block/line/span ordered unique mapping. P0 parity tests
compare the real production extraction path for fully ruled, 4 pt broken, merged,
Japanese/multiline/empty, rotated, negative drawing, and two-table fixtures. Candidate
counts, accepted counts, rejection reasons, bboxes, rows, and columns agree.

## Measured facts

### Gap and profile matrix

| Input | P0 observation | P1 observation | P2 / P3 observation |
|---|---|---|---|
| fully ruled 3x3 | one accepted 3x3 | unchanged through 12 | P2 none; P3 rejected 1x3 |
| H/V gaps 1–3 pt | accepted 3x3 | accepted | not useful |
| H/V gaps 4/6 pt | candidate has one `None`, rejected | recovered at 4/6 respectively | not useful |
| H/V gaps 9/12 pt | candidate has one `None`, rejected | recovered at 9/12 respectively | not useful |
| three 2 pt fragments | accepted 3x3 | accepted | not useful |
| merged-looking H/V | 3x3 with one `None`, rejected | unchanged | malformed or absent |
| Japanese empty/multiline | accepted 3x3; empty cell retains geometry | unchanged | P3 rejected 1x3 |
| rotated 4 pt gap | one `None`, rejected | recovered at 4 | absent |

The current PyMuPDF default behaves like a 3 pt joining boundary in this corpus. An
explicit tolerance of 4 is the first setting that recovers a 4 pt broken separator.

### Targeted adjacency controls

Each independent table is 2x2. “Merged” means PyMuPDF returned one accepted candidate
with altered topology, not merely an expanded decoration bbox.

| Separation | Side-by-side | Vertically stacked |
|---:|---|---|
| 1–3 pt | already merged by P0 into 2x4 / 4x2 | already merged by P0 into 2x4 / 4x2 |
| 4 pt | two 2x2 through tolerance 3; **2x5 at tolerance 4** | two 2x2 through tolerance 3; **5x2 at tolerance 4** |
| 6 pt | first merge at tolerance 6 | first merge at tolerance 6 |
| 9 pt | first merge at tolerance 9 | first merge at tolerance 9 |
| 12 pt | first merge at tolerance 12 | first merge at tolerance 12 |

Thus the first topology-change boundary beyond current/default behavior is **4 pt** for
both orientations. This directly contradicts treating 6 pt as safe solely because broad
negative controls remain at zero.

For a valid 3x3 table plus collinear line segments outside its right border, candidate
count, `[50,50,320,158]` bbox, and 3x3 topology remain unchanged for every combination
of 1/2/3/4/6/9/12 pt separation and P1 tolerance. The adjacent-linework boundary was
**not observed in this corpus**. The five broad controls (aligned prose, background
rectangles, unrelated rectangles, floor-plan linework, sparse connected boxes) also
remain at zero candidates through 12 pt.

### Missing geometry: observation versus fixture intent

The fully missing internal-rule fixture has known synthetic intent of 3x3. Observable
geometry contains only a 2x3 or 3x2 partition, and P0 accepts that reduced topology. It
is **not a successful rejection**. Geometry-only extraction cannot know that an absent
separator was intended and must not invent it; correcting this under-segmentation needs
a separately justified signal/profile later.

Likewise, missing top or left outer borders are accepted as truncated 2x3 or 3x2 visible
topology. Phase 3B-1 has no deterministic basis to reconstruct the absent outer extent.

### Merged-looking and resource observations

Merged-looking fixtures produce a 3x3 candidate with one `None`. Broken-rule versus
intentional span is not unambiguous, so `row_span` / `column_span` must not be inferred.

The single-shape A3-like schedule has 1 drawing path, 1 candidate, 1 accepted 20x12
table, 240 cells, and 240 linked source spans. The same schedule with each of 34 grid
lines committed separately has 34 drawing paths and the identical 1 candidate, accepted
20x12 topology, 240 cells, and 240 linked spans. Neither P0 case shows candidate
explosion; no timing assertion is used.

## Interpretation and Phase 3B-1 recommendation

Do **not** widen production join tolerance in Phase 3B-1 on this evidence. Although 4
and 6 pt recover matching broken separators, 4 pt is also the first explicit tolerance
that merges independent tables separated by 4 pt and changes topology. Retain current
/default production behavior while collecting representative real-document evidence or
developing a deterministic guard that distinguishes fragmentation from adjacency.
Do not enable hybrids, absent-rule inference, outer-border inference, or merged cells.

P2 recovered none of the focused small corpus and creates a malformed 39x10 candidate
on the large schedule. P3 frequently creates one-row or dimension-altered candidates.
Neither hybrid is sufficiently conservative for rollout.

## Future contract and Query Core compatibility

If a later geometry-only profile is validated globally, keeping
`pymupdf_lines_strict` and bumping algorithm version is the minimal option. A combined
ruled identifier alone is not auditable when multiple profiles run on one page. Before
hybrid fallback, canonical per-table detection-profile metadata (method plus explicit
axis strategies and tolerances) is required.

Existing Pipeline v2 sidecars remain readable and are not regenerated by this work.
Query Core v2 can continue storing the effective method in
`pdf_tables.detection_method`; any richer future profile requires deterministic
projection or an explicit later contract migration. Incremental updates, Enhanced PDF
`/1`, Project Query Bundle `/1`, and table query APIs are unchanged. Source spans remain
authoritative; table structure remains derived evidence.
