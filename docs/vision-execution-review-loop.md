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
   they never establish inferred drawing-reference facts. `execute_review`
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
| V2 | Resolved explicit drawing-reference pairs and selected-entity cooccurrences | Comparison; unresolved/ambiguous or missing distinct targets are skipped and preserved |
| V3 | Exact candidate bbox or recorded page | Final evidence inspection |

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
request copy. Rendering reuses the existing Phase 5B/5D internals; their public
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
  --stages V0 V1 V2 V3
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
explicit `--source DOCUMENT_ID=PDF` mappings for collected documents and a configured
`OPENAI_API_KEY`. Never run a paid call merely to validate code. With explicit
live opt-in but no key, the CLI reports `SKIPPED / auth_missing`. Timeout is
configurable through `--timeout` (0 < seconds <= 300); transport has no automatic
retries or redirects. CLI exports JSON atomically under the render artifact
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
checks image count, structured request shape, response normalization and failures.
`tests/test_vision_runner_openai.py` additionally checks image replacement,
invalid compressed pixels, live opt-in, missing auth, timeout, refusal, HTTP,
partial/malformed output and rejected provider confidence.

Run focused/full pytest, the three existing `python -S` smokes, a `tools.vision`
stdlib import smoke, compileall, diff check and the PDF pipeline twice. CI includes
the full tests and triggers on `tools/vision/**` changes. The checkout currently
contains no manifest-managed source PDFs; both repository pipeline runs therefore
report `processed=0 unchanged=0 removed=0`. Synthetic tests perform actual PDF
rendering; zero-file pipeline runs do not establish real-document validation.

Live smoke is **SKIPPED** (no configured API key; no paid request made).
`SCHEMA_VERSION=2`, `SnapshotContract.Version=1`, prior Phase 5A–5F schemas and
public golden outputs remain unchanged. Issue #47 real-document validation stays
pending: actual drawing conventions, source coverage, reviewer usefulness,
provider interpretation quality and real BIM/PDF evidence alignment require real
source documents and human review. The synthetic executable flow is complete;
it does not claim those real-document findings.
