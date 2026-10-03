# Phase 5C — provider-neutral Vision inspection contract v1

`tools.vision` is Python 3.12 standard-library-only. It performs no provider calls,
HTTP, OCR, persistence, candidate selection, or source-fact updates. Only V3 final
evidence inspection is supported. Query Core SCHEMA_VERSION remains 2 and
SnapshotContract.Version remains 1.

```python
from tools.vision import build_vision_inspection_request, build_vision_observation

request = build_vision_inspection_request(render_result, image_path, instruction)
observation = build_vision_observation(
    request, provider="provider identifier", model="model identifier",
    observation="Provider observation text", status="supported", run_id="run identifier",
)
```

The request consumes a successful Phase 5B render result. It preserves semantic
entity/candidate identity, document ID/identity/source SHA, exact page/region,
coordinate space, instruction text, DPI, dimensions, renderer parameters and PNG
SHA. Optional document navigation/title metadata and local paths are excluded.
The image must be supplied explicitly; output_path is never used as a fallback.
There are no image bytes or base64 fields. Page bbox is null; region bbox is a
finite, ordered, nonnegative rectangle in `pdf_points_top_left` coordinates.
Only the renderer verifies the rectangle against the source PDF's page extent.

PNG validation reads one byte buffer and checks signature, chunk framing/CRC,
first IHDR and valid parameters, IDAT presence, final IEND, SHA and dimensions.
It does not decode pixels or claim to reverify source PDF bytes: that is Phase 5B's
responsibility. The renderer result is trusted provenance, not a signed attestation.

IDs hash UTF-8 JSON with sorted keys, compact separators, ensure_ascii=False and
allow_nan=False. The request content excludes request_id and all local paths;
its ID is `vision-request-<sha256>`. The observation excludes observation_id;
its ID is `vision-observation-<sha256>`. Numeric JSON representation is preserved
(no rounding, inferred coordinates, or canonicalization of 1 versus 1.0).

Observation evidence_class is always `vision_observation`. Its nested request is
a deep copy of a validated request, including the verified request ID. Provider
output is accepted only as nonempty observation text, provider/model identifiers,
and one of supported/conflict/ambiguous/insufficient_evidence/unresolved. There
is no provider-supplied evidence identity argument. A nonempty run_id or an
ISO timestamp with a timezone is required; supplied invalid values are rejected
even when the other identity is valid. No implicit current timestamp is added.

The two schemas in `schema/` describe closed JSON envelopes. Runtime validation
also checks bbox ordering, finite numbers and recomputed request ID, which JSON
Schema cannot express. Schema validation does not verify image bytes or IDs.

Before actual Vision execution, a separate provider adapter must explicitly map
this request and the local verified image into a provider API, validate its output
and preserve this authority boundary. Authentication, execution policy, failures,
provider integration tests and any optional observation persistence are deferred.
V0/V1/V2, context padding, paired-image comparison, confidence scoring and Issue
#47 remain pending.
