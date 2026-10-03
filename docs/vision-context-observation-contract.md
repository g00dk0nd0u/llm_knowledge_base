# Phase 5F — provider-neutral V1 context observation contract v1

`tools.vision` adds a transient V1 observation envelope around a complete,
validated Phase 5E context request. It normalizes already-supplied provider text
without running a provider, interpreting that text or persisting anything.
Phase 5C's V3 APIs, schemas, outputs and IDs remain unchanged. Query Core stays
stdlib-only with `SCHEMA_VERSION=2`; `SnapshotContract.Version=1` is unchanged.

```python
from tools.vision import (
    build_vision_context_observation,
    validate_vision_context_observation,
)

observation = build_vision_context_observation(
    request, provider="provider identifier", model="model identifier",
    observation="Provider observation text", status="unresolved",
    provider_run_id="provider execution identifier",
)
validated = validate_vision_context_observation(observation)
```

Both APIs call `validate_vision_context_inspection_request()`. Its deep copy is
the only evidence identity: source document identity/SHA, page, evidence/context
bboxes, policy/clamps, render parameters, PNG SHA and request ID all remain in
the nested request. No separate evidence override arguments or duplicate
top-level evidence fields are accepted. Caller mutations cannot change an
already-built observation, and validators reject subsequent nested tampering.
Returned dictionaries are ordinary mutable JSON objects; editing them requires
revalidation and a new matching content ID.

The closed envelope contains `contract_version=1`,
`kind="vision_context_observation"`, `observation_id`,
`evidence_class="vision_observation"`, the complete `request`, `provider`,
`model`, `observation`, `status`, `provider_run_id` and `created_at`. Provider,
model and observation must be nonempty strings and are preserved verbatim,
including language and surrounding whitespace. Status is exactly one of
`supported`, `conflict`, `ambiguous`, `insufficient_evidence`, `unresolved`.
The text is not promoted into source facts, a pass/fail decision or an approval.

A nonempty `provider_run_id` or valid `created_at` is required; both keys always
exist, with null for an omitted value. Both may be provided. Any supplied invalid
value is rejected even when the other is valid. Legacy `run_id`, provider-specific
response objects, confidence/probability, approval flags and all unknown fields
are rejected. There is no repair, translation, fallback or implicit current time.

Timestamps use the unchanged V3 expression and calendar validation:
`YYYY-MM-DDTHH:MM:SS`, optional decimal fraction, and explicit `Z` or
`+/-HH:MM`. Gregorian leap years, year 0001–9999 and timezone/hour/minute bounds
match V3. Naive timestamps, compact offsets, offset seconds, alternate separators,
impossible dates, leap seconds and trailing newlines fail. Timestamp text and
fractional precision are retained without timezone conversion. The schema uses
the exact V3 timestamp pattern, so calendar rejection works without a format
checker; runtime also uses `datetime.fromisoformat` as V3 does.

The ID is `vision-context-observation-<sha256>` over the entire envelope except
`observation_id`: UTF-8 JSON with sorted keys, compact separators,
`ensure_ascii=False` and `allow_nan=False`. The validated nested request and its
request ID are included, as are provider/model/status/text/run/time. Identical
content yields the same ID; timestamps and run IDs are only explicitly supplied
values. No path, hostname or generated UUID is added.

`schema/vision_context_observation_v1.schema.json` embeds the exact closed Phase
5E request schema to remain self-contained without external reference resolution.
It also closes the observation envelope and enforces run/time requirements.
Runtime additionally validates nested geometry and recomputes both IDs; schema
validation cannot attest those IDs or actual PNG bytes. Neither observation API
opens the DB, PDF or PNG. Image verification remains with the Phase 5E builder.
V1 APIs reject V3 requests/envelopes; the V3 observation builder rejects V1 requests.

There are no DB tables, migrations, writes, caches or observation persistence.
Before actual V1 Vision execution, a separate provider adapter must map the
request and verified image to an API, define authentication/execution policy and
failure handling, and test provider behavior. Any persistence and authority rules
require separate work. V0/V2, paired images, OCR, LLMs, embeddings, automatic
candidate selection and Issue #47 remain pending.
