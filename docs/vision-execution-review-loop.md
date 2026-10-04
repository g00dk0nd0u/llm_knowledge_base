# Phase 5 completion and Phase 6A synthetic review loop

## Internal subphases and architecture

1. **Execution foundation:** `tools.vision.runner.run_vision` revalidates the closed
   request, content ID, PNG SHA, dimensions, chunks, compressed pixel stream and
   scanline filters immediately before sending immutable bytes. No path reopening
   occurs in the provider. Input limits are 64 MiB, 64 million pixels and 256 MiB
   decoded pixels. Old V1/V3 builders, schemas and IDs are unchanged.
2. **Optional OpenAI transport:** `tools.vision.providers.openai.OpenAIProvider`
   uses Responses image inputs and strict JSON schema output. `httpx` is isolated
   in the `vision-openai` extra and imported only during execution. Model is a
   required caller argument. Only `OPENAI_API_KEY` supplies authentication.
3. **Additive V0/V2 contracts:** stage envelopes and schemas are separate from
   Phase 5C–5F. V0 carries one whole-page exact-evidence input. V2 carries two
   distinct exact-evidence inputs and a resolved relation with source trace.
   Nested V3 contracts describe image surfaces; only the outer V0/V2 stage is
   executed. V1 fixed-margin geometry is never relabeled as V3 exact geometry.
4. **Review orchestration:** `plan_review` starts with selected semantic identity
   and deterministic architectural context, follows explicit one-hop drawing
   references, and collects target evidence or recorded sheet-page navigation.
   Shared semantic bindings permit deterministic cooccurrence comparisons;
   they never establish inferred drawing-reference facts. Explicit pairs are
   prioritized/deduplicated. The default focused policy suppresses generic
   cooccurrences when a resolved reference exists; the exhaustive opt-in retains
   the old bounded schedule. Focused V3 unions exact cells in a recorded table row
   while preserving every original member and reference endpoint. `execute_review`
   regenerates the plan, verifies all source assignments, renders into ignored
   artifacts, constructs requests, optionally runs a provider and returns a
   transient source-traced review packet.
5. **Operating safety and proof:** CLI plan/execute, fake-provider E2E and OpenAI
   mock-transport tests cover every stage and failure paths. No database writes,
   autonomous approval, OCR, embeddings or source-fact generation are introduced.

```text
selected semantic entity
  -> architectural context + explicit one-hop drawing references
  -> deterministic plan (stages, surfaces, source refs, planned sends)
  -> source-SHA/geometry-verified render -> request + PNG revalidation
  -> optional provider -> closed provider-neutral observation
  -> review packet (facts, ambiguity, requests, observations, failures, trace)
  -> human review of original sources
```

## Stage coverage and planning policy

| Stage | Scheduled evidence | Meaning |
| --- | --- | --- |
| V0 | Distinct whole source pages, deduplicated with combined source refs | Derived page/sheet triage only |
| V1 | Region candidates with the existing fixed 144 pt margin | Nearby context inspection; page-only candidates are explicitly skipped |
| V2 | Resolved explicit pairs; bounded cooccurrences only as fallback or exhaustive opt-in | Comparison; unresolved/ambiguous or missing distinct targets are skipped and preserved |
| V3 | Exact reference endpoints, original candidates and eligible exact table-row unions | Final inspection with complete member trace |

`policy="focused"` is the API/CLI default. Any recorded `exact` or
`resolved_deterministically` drawing reference disables generic V2 cooccurrences,
even when its surfaces cannot form a distinct pair. Each valid explicit unordered
pair is scheduled once. Ambiguous/unresolved references never produce explicit
pairs. With no resolved reference, the previous bounded cooccurrence fallback
remains. `policy="exhaustive"` / `--policy exhaustive` retains the previous job
order, inputs and job IDs, including the fixed 32-job generic bound and all V3
candidates. Policy participates in `plan_id` and execution regenerates that policy;
old saved plans need replanning. Existing function arguments remain compatible.

`v2_cooccurrence` exposes algebraic eligible, scheduled, suppressed and omitted
counts. Eligible = scheduled + suppressed + omitted. Suppression uses the named
`explicit_relation_first` reason and never iterates the suppressed pairs. The
fallback/exhaustive limit uses `deterministic_cooccurrence_limit`; iteration stays
lazy and does not materialize omitted pairs. Every job has a `selection_reason`.
Provider/network budgets count actual scheduled jobs, including aggregated V3 jobs.

Focused V3 compaction applies only when a resolved reference exists. Candidates
with only stored `pdf_table_cell` occurrences can join when their exact document,
page, table ID and row index match and each cell spans one row. The planner verifies
stored cell geometry/document/page against the routed surface. Non-cell/mixed
occurrences, inconsistent cells, singleton groups and all resolved reference
endpoints keep their original surfaces. Exact duplicate bboxes were already deduped
by routing; page equality, proximity and bbox containment alone never select or
discard evidence. Headers and source text-block evidence stay separate even if
visually overlapping.

A union has the min/max enclosing bbox of its members. It is explicitly
`deterministic_derived` with `exact_table_row_union_v1` provenance and unknown bbox
quality. Its canonical ID hashes the aggregation key and sorted complete member
records, including geometry/navigation/source refs; no source ID or value is
rewritten. Each aggregate stores `members`, combined `source_refs` and routing
reasons. Its primary navigation is null because navigation belongs to its exact
members. Original context/candidates remain intact. `skipped` maps every replaced
member ID to its aggregate ID, and `v3_selection` reports eligible/scheduled/member
counts and the named reason. Resolved endpoints lead the focused V3 sequence.

These are transient review surfaces rendered through the existing internal
regenerated-candidate path. Phase 5 public render entry points, request/observation
schemas and golden IDs remain unchanged; contracts carry the aggregate's ordinary
region identity while full member trace stays in the local plan/packet. Query Core
schema/version and Revit SnapshotContract are unchanged.

Zero candidates or all selected stages skipped produce a plan and packet with
`insufficient_evidence`. Execution starts no renderer/provider and the CLI exits 1.
The plan command still displays this state without network or writes.

Default stages are V1/V2/V3. V0 is explicitly selectable. Planning uses only
available deterministic evidence; observations never select, rewrite or approve
source facts. A reference with recorded target sheet/page but no target bbox
gets a clearly marked `deterministic_derived` page surface. A view-only reference
without a known page is retained and skipped, never guessed. Traversal is one-hop,
not recursive. Plans expose every job, evidence surface, skip reason, provider
call count and live network call count. Fake execution makes zero network calls.

Plans, jobs, added stage contracts and packets have version 1 content hashes.
Execution regenerates and compares the complete plan before any provider call;
stale source/context changes require replanning. PNG reads are bounded and their
same verified bytes go to the transport. The runner gives providers an isolated
minimized provider input. Canonical requests and full source trace remain local.
The provider sees only stage, exact caller instruction, closed minimal metadata and
verified PNG bytes. OpenAI sends trusted stage/data guidance as a developer message,
with instruction/metadata/images in the user message. V0 triages a page; V1 receives
only a normalized focus box (corrected for displayed image rotation); V2 compares
image A/B with only `relation_type`; V3 inspects exact evidence. All stages explicitly
treat image text as data, never instructions. No document identity/filename/SHA,
semantic/candidate/request IDs, navigation, source refs or local/DB paths are sent.
Caller instruction is preserved verbatim; callers should include only information
intended for external processing in that instruction. Rendering reuses the existing
Phase 5B/5D internals; their public
entry points still regenerate their own Query Core candidates.

## API and CLI

Query Core and `tools.vision` imports remain Python 3.12 stdlib-only, including
under `python -S`. Rendering requires the normal PDF dependencies; live OpenAI
execution additionally requires `pip install -e '.[vision-openai]'`.

```python
from tools.query_core.query import QueryCore
from tools.vision import plan_review, execute_review
from tools.vision.providers.fake import FakeProvider

with QueryCore(database) as core:
    plan = plan_review(core, entity_id, model="explicit-test-model", provider="fake",
                       stages=["V0", "V1", "V2", "V3"], dpi=72)
    packet = execute_review(core, plan, sources={document_id: source_pdf},
                            root=repo_root, database=database, provider=FakeProvider())
```

Network-free preview (no PNG writes, no source-file reads, no provider imports):

```bash
python -S -m tools.vision plan --database artifacts/project.sqlite \
  --entity SELECTED_ENTITY_ID --provider openai --model EXPLICIT_MODEL \
  --stages V0 V1 V2 V3 --policy focused
```

Fake proof execution / render-only execution:

```bash
python -m tools.vision execute --database artifacts/project.sqlite \
  --entity SELECTED_ENTITY_ID --provider fake --model explicit-test-model \
  --source DOCUMENT_ID=projects/example/source/drawings.pdf \
  --stages V0 V1 V2 V3 --dpi 72
# Use --provider none to render/build requests without invoking any provider.
```

Live execution needs **both** `--provider openai` and `--live`, an explicit model,
an explicit paid-call budget through `--max-live-calls N`,
explicit `--source DOCUMENT_ID=PDF` mappings for collected documents and a configured
`OPENAI_API_KEY`. Never run a paid call merely to validate code. With explicit
live opt-in but no key, the CLI reports `SKIPPED / auth_missing`. Timeout is
configurable through `--timeout` (0 < seconds <= 300); transport has no automatic
retries or redirects. `execute_review(..., max_live_calls=N)` performs the same
budget gate before source reads/render/network; direct `run_vision` network execution
requires a budget covering its single call. Planned calls above budget or a missing
budget fail; `--live` alone cannot initiate paid calls. Budgets count calls, not tokens
or currency. Fake/none need no paid budget. CLI exports JSON atomically under the
render artifact
policy and prints only packet identity/status/output path/failure count.

## Packet and authority

The packet keeps the selected entity, sparse source property names/values,
bindings, deterministic relationships/spatial context, drawing occurrences,
source documents, explicit references and ambiguous/unresolved records. Original
retrieval counts remain in `retrieval_coverage`; augmented counts describe the
collected context. The plan also retains candidate source refs/navigation.

Each observation contains its complete validated request, provider/model/run ID,
text and one canonical status. Executions map jobs to request IDs, image paths and
source refs; source assignments retain original PDF path, document ID and SHA.
`human_review_required` is always true. Packet `ok` means execution succeeded,
not approval or evidence agreement. Fake results are explicitly synthetic and
unresolved. A provider-neutral `conflict` never changes source property values.

Provider response/usage/confidence is discarded. Structured output accepts only
`observation` and `status`; run identity comes from the transport response.
Optional provider `created_at`
Unix seconds are converted deterministically to strict timezone-aware UTC ISO,
using decimal arithmetic and half-even rounding to microseconds. Missing timestamps
become `None`; no current timestamp is generated. Bool, malformed, nonfinite,
negative or calendar-out-of-range timestamps fail. V1/V3 use their unchanged builders
and keep identical output/IDs when the timestamp is missing. V0/V2 have nullable
`created_at` in both runtime and schema. V2 relations are a closed
`{relation_type, source_refs}` shape; refs contain only `{kind, id}` pointers to
local `drawing_reference` / `semantic_entity` records. Full provenance stays in
the local plan context/candidate trace.
Timeout, auth, HTTP, transport, refusal, malformed/duplicate/nonfinite JSON and
partial output fail explicitly. Failures carry safe categories, never raw response
or exception text, key or base64. Individual job failures make the whole packet
`failed` while retaining any completed observations. Input/plan/source mismatches
fail before provider execution. No failure is converted to `supported`.

## Official API references checked

On 2026-10-03 UTC, implementation checked the official OpenAI SDK's generated API
specification types:

- [Responses creation](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_create_params.py)
- [Image input (URL or base64 data URL)](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_input_image_param.py)
- [Strict structured text output](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_format_text_json_schema_config_param.py)

The adapter sends `POST https://api.openai.com/v1/responses`, explicit `model`,
`store: false`, `input_text`, `input_image` with high detail and
`text.format.type: json_schema`. It does not use Assistants or provider-specific
fields in canonical observations. Model support for image input/structured output
must be confirmed by the operator for the chosen model; unsupported combinations
return a failure.

## Proof, verification and remaining real-document work

`tests/test_vision_review_loop.py` extends the existing synthetic Door fixture
with plan/schedule/detail/section pages and explicit references. Fake-provider
E2E verifies all four stages, conflicting EL160/EL560 source values, source
names, relation trace, ambiguity, bbox/page/source SHA/output SHA, JSON export and
unchanged source/database bytes. OpenAI mock transport runs every stage and
checks image count, minimized structured request shape, response normalization and
failures. `tests/test_vision_hardening.py` checks 120 direct candidates with bounded
lazy iteration, pair deduplication, early budget rejection, zero-job failure for
all provider modes, forbidden payload metadata, all stage guidance, V1 focus rotation,
closed runtime/schema relations and provider timestamp provenance.
`tests/test_vision_runner_openai.py` additionally checks image replacement,
invalid compressed pixels, live opt-in, missing auth, timeout, refusal, HTTP,
partial/malformed output and rejected provider confidence.

Run focused/full pytest, the three existing `python -S` smokes, a `tools.vision`
stdlib import smoke, compileall, diff check and the PDF pipeline twice. CI includes
the full tests and triggers on `tools/vision/**` changes. The original synthetic checkout
contained no manifest-managed source PDFs; its two repository pipeline runs
reported `processed=0 unchanged=0 removed=0`. Synthetic tests perform actual PDF
rendering; zero-file pipeline runs do not establish real-document validation.

Live smoke is **SKIPPED** (no configured API key; no paid request made).
`SCHEMA_VERSION=2`, `SnapshotContract.Version=1`, prior Phase 5A–5F schemas and
public golden outputs remain unchanged. At that synthetic milestone, Issue #47
real-document validation was pending: actual drawing conventions, source coverage, reviewer usefulness,
provider interpretation quality and real BIM/PDF evidence alignment require real
source documents and human review. The synthetic executable flow is complete;
it does not claim those real-document findings.

## Phase 6 review selectivity proof

Authorized local PDF proof reconstructed two distinct schedule entities sharing
one number, with explicitly recorded schedule-to-elevation relations. The mapping
and relation assignments were caller supplied from embedded source evidence; no
new relation discovery or source text repair was added. Input PDF, mappings,
extracted records, candidate/source identity inventory, before/after plans, PNGs
and fake packets stay ignored/local-only.

Each entity had 14 direct bindings/occurrences (seven data cells and seven header
spans), five sparse source properties, and nine collected evidence records. Four
header evidence bboxes exactly duplicate their span occurrence, leaving 19 routed
regions: seven cells, seven header spans and five additional evidence surfaces.
Eighteen are on the schedule page and one is the explicit elevation target.
The row source block and header blocks overlap smaller surfaces but have distinct
source identities/geometry and are preserved. Fourteen occurrence candidates
create 91 eligible generic pairs; the explicit pair uses separate source/target
evidence surfaces. The old planner sent one explicit pair plus 32 generic pairs,
omitting 59 by its bound.

| Per entity, all four stages at 72 DPI | Base / exhaustive | Focused |
| --- | ---: | ---: |
| Original routed regions | 19 | 19 |
| V0 | 2 | 2 |
| V1 | 19 | 19 |
| V2 explicit | 1 | 1 |
| V2 generic | 32 | 0 |
| V3 | 19 | 13 |
| Total scheduled jobs | 73 | 35 |

Focused V2 suppresses all 91 eligible generic pairs. V3 replaces seven same-row
cells with one union and keeps the other 12 surfaces, including both relation
endpoints. Expanding V3 members gives exactly the original candidate set. Base and
exhaustive job IDs/inputs/order were compared directly and matched. Source context,
properties, original bboxes/navigation, PDF/DB hashes and source assignments were
unchanged. Before/after fake execution completed with `status=ok`, complete
request/observation counts and `human_review_required=true`. OpenAI live calls: 0.

The generic combinatorial planning blocker is removed for explicit-relation
reviews. V1 still inspects all regions, and headers/overlapping text-block V3
surfaces remain conservative. No human usefulness assessment, live model quality
or real BIM-to-PDF alignment is established by fake execution. These remain
real-review validation work; Issue #40 is not updated or closed by this change.

Selectivity regression validation: focused pytest 770 passed; full pytest 1,310
passed; all four Python 3.12 `-S` smokes, real SQLite `-S` CLI plans for both
policies, compileall and diff check passed. Tests include suppression without pair
iteration, absent/ambiguous/unresolved fallback, multiple/deduplicated references,
resolved nondistinct surfaces, stable policy IDs, exhaustive execution, exact row
geometry and member/source-ref/navigation preservation, row/page isolation,
protected endpoints and exact mock live budget accounting.

Two repository Pipeline v2 runs also passed with the ignored real input present:
first `processed=1 unchanged=0 removed=0`, then
`processed=0 unchanged=1 removed=0`. The second run preserved all local
source/knowledge/manifest bytes and produced no additional tracked changes.
