# Phase 5E — provider-neutral V1 context inspection request v1

`tools.vision` adds a separate V1 request contract for successful Phase 5D
`render_vision_context_candidate(...)` results. Phase 5C's V3 builder, schema,
output and `vision-request-` IDs retain their existing meaning. V1 does not
introduce observations, execute Vision or modify source facts. Query Core
`SCHEMA_VERSION=2` and `SnapshotContract.Version=1` remain unchanged.

```python
from tools.vision import (
    build_vision_context_inspection_request,
    validate_vision_context_inspection_request,
)

request = build_vision_context_inspection_request(render_result, image_path, instruction)
validated = validate_vision_context_inspection_request(request)
```

The builder requires `status="ok"`, `stage="V1"`, region scope,
`pdf_points_top_left`, `policy="fixed_margin_v1"` and `margin_pt=144.0`.
It preserves the supplied instruction verbatim, including whitespace and language,
and rejects empty/whitespace-only text. No values are repaired, inferred or rounded.

The closed request contains `contract_version=1`,
`kind="vision_context_inspection_request"`, stage, request ID, instruction,
semantic entity/candidate IDs, canonical document ID/identity/source SHA, PDF
page, scope, separate `evidence_bbox` and `context_bbox`, coordinate space,
policy/margin/clamped edges, DPI, PNG dimensions/SHA and renderer metadata.
It has no standalone `bbox`, local path, image bytes or base64 field. Optional
document navigation/title metadata is excluded; document identity is preserved
from the canonical renderer metadata, not derived from `image_path`.

Both rectangles must be finite, ordered and nonnegative. Context must contain
all evidence and add area. Clamped edges must be a unique subsequence of
`left, top, right, bottom` in that order. Unclamped coordinates must equal the
144pt expansion; clamped coordinates must reduce it while retaining evidence.
Left/top clamps use the page-local origin, zero. The contract has no PDF page
extent and cannot independently attest right/bottom page boundaries: source
SHA verification, page bounds and rendering remain Phase 5D's responsibility.
Renderer results are trusted provenance, not signed attestations.

The builder reads the explicitly supplied PNG once. The unchanged Phase 5C
stdlib helper checks PNG signature, chunk framing/CRC, first/unique IHDR and
its parameters, IDAT presence and final IEND. SHA-256 of those bytes must equal
`output_sha256`; IHDR width/height must match the result. This does not decode
pixel data. `output_path` is never a fallback and no file is written.

`request_id` is `vision-context-request-<sha256>` over all request content except
`request_id`: UTF-8 JSON, sorted keys, compact separators, `ensure_ascii=False`,
`allow_nan=False`. Paths, timestamps, UUIDs and hostnames are absent. Identical
PNG bytes copied to another path produce identical content/ID. Instruction,
image SHA, candidate, geometry and clamp metadata participate in the hash.
Numeric JSON representation is preserved, including `144` versus `144.0`.
Different policy names or margin values are rejected, not assigned valid IDs.

`schema/vision_context_inspection_request_v1.schema.json` defines the closed
envelope and ordered clamp lists. Runtime additionally validates finite ordered
geometry, containment, margin/clamp consistency and recomputed IDs. The validate
API returns a deep copy without file I/O; schema/validate calls cannot verify a
PNG. V1 requests/results remain rejected by the V3 builder/validator/observation
contract. There are no DB writes, tables, migrations, caches or persistence.

Before actual V1 Vision execution, a separate provider adapter must map this
request and an explicitly verified local image into the provider API, define
authentication/execution policy and failure handling, and test provider behavior.
A V1 observation contract and any persistence/authority rules require separate
work. V0/V2, paired images, OCR, LLMs, embeddings, automatic candidate selection
and Issue #47 remain pending.
