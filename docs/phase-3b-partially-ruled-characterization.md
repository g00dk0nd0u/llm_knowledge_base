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

## Phase 3B-1 Guard Characterization

### Prototype contract and matching

The test-only `evaluate_guarded_repair` runs P0 and one explicit P1 tolerance against
the same page, cached drawings, and production-order text hierarchy. It does not feed
the production extractor. Matching uses geometric intersection, never persistence IDs
or list position: a widened bbox must intersect exactly one P0 candidate. Zero anchors
is `no_baseline_anchor`; more than one is `multiple_baseline_anchors`. With one anchor,
the bboxes must agree within the production `TABLE_EPSILON` (0.25 pt), dimensions must
agree, P0 must have failed specifically with `merged_or_missing_cell`, P0 must contain
one or more `None` cells, and P1 must pass the complete Phase 3A evaluator with zero
`None` cells. Every pre-existing cell must also retain its bbox within the same epsilon;
only a formerly `None` coordinate may gain geometry. An already accepted P0 candidate
is never replaced.

Equal outer bboxes alone are not treated as evidence preservation. The evaluator
explicitly constructs ordered `(block_index, line_index, span_index)` identities for
non-empty spans intersecting each table, compares the table-level source-ref lists, and
then compares refs geometrically contained in every preserved cell. A repaired cell may
receive only refs that were not assigned to an existing P0 cell. The record separately
reports table-level span-set stability, existing-cell geometry stability,
preserved-cell assignment stability, repaired coordinates, preserved refs, and newly
assigned refs. Full Phase 3A acceptance still requires unique widened-cell mapping.
Repeated serialized results are byte-identical.

### Focused matrix

`3x3/1N` means a 3x3 candidate containing one `None` cell; `2x2 + 2x2` means two
independent candidates. All rows use the matching tolerance shown.

| Fixture | Tolerance | P0 topology | naive P1 topology | anchors | same bbox? | same dimensions? | guard | reason |
|---|---:|---|---|---:|:---:|:---:|:---:|---|
| horizontal gap 4 | 4 | 3x3/1N rejected | 3x3 accepted | 1 | yes | yes | reject | `existing_cell_geometry_changed` |
| vertical gap 4 | 4 | 3x3/1N rejected | 3x3 accepted | 1 | yes | yes | reject | `existing_cell_geometry_changed` |
| horizontal / vertical gap 6 | 6 | 3x3/1N rejected | 3x3 accepted | 1 | yes | yes | reject | `existing_cell_geometry_changed` |
| horizontal / vertical gap 9 | 9 | 3x3/1N rejected | 3x3 accepted | 1 | yes | yes | reject | `existing_cell_geometry_changed` |
| horizontal / vertical gap 12 | 12 | 3x3/1N rejected | 3x3 accepted | 1 | yes | yes | reject | `existing_cell_geometry_changed` |
| side-by-side 4 / 6 / 9 / 12 | matching | 2x2 + 2x2 accepted | one 2x5 accepted | 2 | n/a | no | reject | `multiple_baseline_anchors` |
| stacked 4 / 6 / 9 / 12 | matching | 2x2 + 2x2 accepted | one 5x2 accepted | 2 | n/a | no | reject | `multiple_baseline_anchors` |
| broken beside intact | 4 | rejected 3x3/1N + accepted 3x3 | two 3x3 accepted | 1 each | yes | yes | reject both replacements | broken is `existing_cell_geometry_changed`; intact is `baseline_already_accepted` |
| two tables plus broken rule, aligned at 4 pt | 4 | rejected 3x3/1N + accepted 3x3 | one 3x7 candidate | 2 | n/a | no | reject | `multiple_baseline_anchors` |
| two 4 pt gaps on one separator | 4 | 3x3/2N rejected | 3x3 accepted | 1 | yes | yes | reject | `existing_cell_geometry_changed` |
| 4 pt gaps on two separators | 4 | 3x3/4N rejected | 3x3 accepted | 1 | yes | yes | accept | `eligible_monotonic_completion` |
| nested/overlapping rectangles | 4 / 6 / 9 / 12 | 3x3 accepted | unchanged 3x3 | 1 | yes | yes | reject replacement | `baseline_already_accepted` |
| close double side borders | 4 / 6 / 9 / 12 | 3x3 accepted | unchanged 3x3 | 1 | yes | yes | reject replacement | `baseline_already_accepted` |
| mixed page broken table | 4 | 3x3/1N rejected | 3x3 accepted | 1 | yes | yes | reject | `existing_cell_geometry_changed` |
| mixed page intact table | 4 | 3x3 accepted | unchanged 3x3 | 1 | yes | yes | reject replacement | `baseline_already_accepted` |
| merged-looking H / V | 4 / 6 / 9 / 12 | 3x3/1N rejected | still 3x3/1N rejected | 1 | yes | yes | reject | `widened_not_accepted_merged_or_missing_cell` |
| fully missing internal / top / left | 12 | reduced visible topology accepted | unchanged | 1 | yes | yes | reject replacement | `baseline_already_accepted` |

The broken-beside-intact case also remains separated through tolerance 12. The mixed
page additionally contains prose and unrelated orthogonal geometry; decisions remain
candidate-local. The close parallel borders are not treated as evidence of a second
table, avoiding an unsupported double-line heuristic. Japanese/multiline/empty,
rotated, large 20x12, fragmented-large 20x12, and broad negative controls remain
deterministic.

### Conclusions and recommendation

1. The strengthened guard rejects both horizontal and vertical 4 pt fragmented
   separators, and the corresponding 6, 9, and 12 pt cases: PyMuPDF's widened result
   reshapes at least one previously observable cell despite retaining the same outer
   bbox and dimensions.
2. It rejects every matching 4/6/9/12 pt independent side-by-side and stacked merge
   because the widened candidate has two P0 anchors.
3. It keeps the broken-beside-intact decisions candidate-local, but rejects the broken
   candidate for internal reshaping. With aligned tables close enough to merge, it
   rejects the combined candidate for multiple anchors.
4. The two-separator fragmented pattern is a genuine monotonic completion and remains
   eligible. The two-gap single-separator pattern reshapes existing cells and is rejected.
5. It does not convert the two merged-looking controls into span semantics: widened
   candidates still fail Phase 3A. This is observed behavior, not proof that all
   intentional merged-cell drawings are distinguishable from broken separators.
6. Tested nested, surrounding, adjacent, and mixed-page orthogonal linework is not
   absorbed. Double borders remain intact and are not heuristically rejected.
7. Fully absent separators are not inferred. Their reduced visible topology is already
   accepted by P0, so the guard correctly does nothing.
8. Independent tables only 1–3 pt apart remain a **pre-existing P0 adjacency
   limitation**: P0 already merges them, leaving no two-anchor baseline for this guard.
   The prototype prevents new widening-induced merges; it does not repair that baseline
   false positive.

The hardened predicates expose a material false-negative tradeoff: all simple one-gap
recovery controls alter other cell geometry internally and are therefore unsafe under
the required monotonic definition. The focused evidence is not sufficient to
production-enable recovery. Real technical-document sampling, more boundary/overlap
perturbations, and an explicit policy for merged-cell ambiguity are needed.
Recommendation remains **further characterization, not production Phase 3B-2
enablement**. Production remains unchanged at P0/default; there is no persisted profile,
algorithm/version, schema, package, or API change.
