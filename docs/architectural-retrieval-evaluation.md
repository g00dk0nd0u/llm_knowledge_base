# Architectural retrieval evaluation v1

`tools.retrieval_eval` measures the existing Query Core baseline. It does not
change retrieval, select APIs from natural language, generate answers, or infer
architectural facts. Runtime dependencies are Python 3.12 standard library only.
No evaluation tables are added to SQLite; Query Core schema v2 and Revit snapshot
contract v1 remain unchanged.

## Architectural framing

Architectural information is intentionally distributed across source media with
different information characteristics. Revit, IFC, PDFs, schedules,
specifications, details, and consultant documents must not individually be
assumed to contain the complete architectural truth. Source authority means
keeping each claim inspectable in its native source and provenance.

| Source/context example | Information characteristics |
|---|---|
| Revit / IFC | Elements, types, spatial relations, parameters, geometry |
| Sheet / View | Edited publication and communication context |
| Detail / Callout | Localized construction and design information |
| Schedule | Classification, aggregation, codes, performance information |
| Specification | Textual requirements, conditions, exceptions |
| Legend / Keynote | Mapping between graphical notation and meaning |
| Issued PDF | Revision-specific published evidence |
| Consultant documents | Discipline-specific evidence |

These examples describe information roles; they do not define a fixed ontology
or require source-format labels in evaluation cases.

A semantic/architectural entity is primarily a cross-source identity and
comparison anchor, not a Source of Truth. Door D-105 may connect Revit, Plan,
Door Schedule, Detail, and Specification evidence. Those source-native facts and
their provenance remain separately inspectable rather than being collapsed into
one authoritative synthesized record.

Different sources or revisions may legitimately expose conflicting claims.
Cross-format integration must preserve those claims and their evidence so a
human reviewer can inspect the discrepancy. This foundation does not resolve
conflicts or silently replace one source's claim with another's.

The goal is not to reconstruct a complete digital twin, but to reconstruct the
information relationships a human reviewer needs to inspect. Evaluation therefore
measures access to the relevant source facts, relationships, and evidence locations.

```bash
python -S -m tools.retrieval_eval run \
  --database artifacts/project.sqlite \
  --cases artifacts/cases.json \
  --output artifacts/results.json
```

stdout is UTF-8 JSON. `--output` is optional and creates a **new** file; it never
overwrites an existing database, source, case, or result file. Errors produce JSON
on stderr and exit code 2. The evaluator opens an existing validated database
through Query Core's read-only connection. It performs no PDF ingestion or writes
to source files, database records, or source facts.

## Case contract

```json
{
  "format": "architectural-retrieval-cases",
  "version": 1,
  "cases": [{
    "case_id": "door-plan",
    "human_question": "Find D-101 on the correct Plan drawing.",
    "task_category": "drawing-location",
    "baseline": {
      "operation": "semantic_discovery_context",
      "arguments": {"query": "D-101", "entity_class": "Door"}
    },
    "expected": {
      "status": "ok",
      "semantic_entity_ids": ["semantic-door-101"],
      "evidence": [{
        "document_identity": "source/door-schedule.pdf",
        "pdf_page": 1,
        "sheet_number": "A-601",
        "view_id": "view-door-model",
        "source_reference": {
          "kind": "entity_appearance", "id": "appearance-door-model"
        }
      }],
      "checks": [{
        "path": ["context", "properties"],
        "contains": {"source_name": "電気錠", "source_value": "EL160", "scope": "schedule"}
      }]
    }
  }]
}
```

This example uses the safe acceptance fixture identities, not real project data.
`case_id`, `baseline`, and `expected` are required. Human question, category, and
notes are optional descriptive strings. They never influence execution. Every
baseline declares its operation and arguments explicitly. Unknown fields,
unsupported operations/versions, duplicate JSON keys/case IDs, invalid argument types,
nonpositive limits/pages, and malformed evidence/checks fail validation before
execution. Queries retain literal whitespace and existing Query Core semantics.

Optional expected lists are `semantic_entity_ids`, `document_identities`,
`sheet_ids`, `sheet_numbers`, `view_ids`, `pdf_pages`, `evidence_ids`, and typed
`source_references` (`kind`, `id`). These independent field checks measure presence
of any expected value. They do not establish that values belong to one drawing.
Use **conjunctive evidence selectors** for that distinction.

Each `expected.evidence` selector specifies an evidence ID, typed source reference,
or document identity + one-based page. Optional fields include document ID, Sheet
ID/number, View ID, bbox, and coordinate space. All supplied fields must match the
same returned navigation record. Exact bbox expectations require document
identity, page, coordinate space, and four ordered finite coordinates. Comparison
is numerical coordinate equality; there is no tolerance or IoU threshold.
Page-only selectors remain page-only. `bbox: null` can explicitly check that a
known page-only occurrence has no region; it does not enable bbox scoring.

`checks` address the native returned payload with a JSON path (object keys and
nonnegative array indices). `equals` compares the complete addressed value;
`contains` requires a list member matching all supplied object fields, recursively.
Unspecified object fields are ignored; nested lists/scalars compare exactly.
Missing paths fail even when the expected value is null. Checks can distinguish
source property/value/scope/binding, relationship type/endpoints, spatial Level,
appearance kind, and nested resolution state without defining a fixed Door schema.

## Explicit baseline operations

| Operation | Arguments / behavior |
|---|---|
| `search_semantic_entities` | `query`, optional `entity_class`, `limit`; unchanged exact/contains precedence, candidates, and status |
| `semantic_discovery_context` | Same discovery arguments; calls `get_architectural_evidence_context` **only** after `exact_unique`, selecting its first exact candidate |
| `get_architectural_evidence_context` | Explicit `semantic_entity_id`; native structured envelope |
| `get_semantic_spatial_context` | Explicit `entity_id`; native spatial/relationship projection |
| `get_drawing_reference` | Explicit `reference_id`; native source/target evidence and resolution state |
| `search_text` | `query`, optional `limit`; native ordered search records, without navigation expansion |
| `search_pdf_text` | `query`, optional `limit`; native ordered PDF blocks and stored navigation |

An `exact_unique` result may include additional contains candidates. Conversely,
`multiple_exact` remains ambiguous even with `limit: 1`. Contains-only, no-match,
and capability-unavailable results never trigger context retrieval. Unique
discovery identifies an entity; its stored ambiguous/unresolved bindings remain
unchanged. There is no automatic graph expansion or ambiguity resolution.

## Result and metric contracts

Output uses `format: architectural-retrieval-results`, `version: 1`, per-case
`case`, `retrieval`, `metrics`, and an aggregate MRR. Retrieval retains the entire
native `payload`, status, original source facts/provenance/navigation, and ordered
candidate positions. Composed payloads contain both `discovery` and `context`.
No confidence, vector score, similarity score, or ranking is fabricated. Existing
source confidence fields, if present, survive inside the native payload.

`query_core_status` preserves the native status (drawing references use their
native `resolution_state`). APIs returning None have adapter status `not_found`;
text lists have adapter status `returned` or `no_match`, with native status null.
Nested states remain visible. No mismatched properties are automatically relabeled
as `conflict`: test their source facts explicitly. State scoring accepts exact
expected strings, including a future adapter's conflict/insufficient-evidence
states, without forcing them onto Query Core's vocabulary.

The `observed` index contains returned semantic identities, document identities,
source references, evidence IDs, and navigation records. It neither retrieves
missing navigation nor invents regions. Navigation can be nested inside spatial
contexts or PDF table descriptors. Identical navigation records, including their
typed source IDs and location fields, count once. Different source IDs/locations
remain separate evidence units; this counting convention must be shared by any
future adapter used for comparison.
Explicit returned block/span/cell/appearance/property/parameter ID columns also
provide typed source references; raw facts and navigation remain in the payload.

- **Hit@1/5/10 and reciprocal rank:** only native ordered candidates with specified
  relevant targets. Semantic searches use expected semantic IDs; text searches use
  typed source references. Composed cases rank the discovery stage only. Direct
  context/reference/spatial envelopes have no rank. A cutoff above the requested
  retrieval limit is not applicable. Empty rankings with relevant targets score
  zero; missing/empty relevance expectations are not applicable.
- **MRR:** mean reciprocal rank across applicable ranked cases only. Reported
  `ranked_case_count` states the denominator; no applicable cases produces null.
- **Field hits:** independent entity, document, Sheet, View, page, evidence/source
  reference checks. Unspecified/empty relevance lists are `not_applicable`.
  Bbox hit requires an exact bbox selector matching the same source location.
- **Evidence set:** when `expected.evidence` is supplied, report expected selectors
  retrieved/missing and unexpected returned navigation units. Precision is the
  fraction of returned units matching any selector. Recall/coverage is the
  fraction of expected selectors matched. Zero denominators yield null, not a
  fabricated perfect score. An explicit empty set treats all returned evidence
  as unexpected. Omitting the set makes this metric not applicable.
- **State/check correctness:** exact pass/fail, independently of entity/rank hits.
  An entity hit cannot compensate for lost ambiguity or a wrong drawing.

Page-only ground truth cannot measure localization within that page. Selector
overlap is allowed; recall counts declared expectations, not a one-to-one matching
assignment. There is no overall approval score or nDCG/graded relevance in v1.
Latency is not recorded; canonical JSON has no timestamps, elapsed time, absolute
database path, or timing-dependent IDs.

## Cross-format evidence coverage

The existing v1 `expected.evidence` list can express multiple expected locations
and source references for the same architectural question. A future permitted
real case may require Revit/source-fact evidence alongside a Plan occurrence,
Schedule evidence, Detail evidence, and Specification evidence. Each expected
unit retains its own source identity and any genuinely known location.

For returned navigation-backed evidence units, existing precision, recall, and
coverage measure which expected units were retrieved, which are missing, and
which unexpected units were returned. Model-only source facts without navigation
can be checked through `expected.source_references` and native-payload `checks`;
they must not be assigned invented PDF locations to enter the evidence-set metric.

No new `cross_format_coverage` metric or source-format schema is needed in v1.
Explicit source-class grouping should be considered only if later real datasets
demonstrate that the existing expected-evidence contract is insufficient.

## Proof fixtures and later real cases

`tests/fixtures/retrieval_eval/` contains 14 cases over the existing semantic,
architectural acceptance, PDF-table, and drawing-reference fixture builders.
They cover exact Door discovery, sparse source properties, Door → Room, Room →
Level, model Plan/schedule occurrences, exact and ambiguous references, ambiguous
discovery, contains-only candidates, insufficient/no-match states, and PDF-native
semantic/table/text retrieval without a Revit host. Plan/schedule-only cases
deliberately measure the extra evidence returned by the existing broad context.

**Synthetic fixture results prove evaluator mechanics only. They do not prove one
retrieval architecture is superior in architectural practice.**

Follow [the local real-document validation workflow](real-document-validation.md).
Keep permitted confidential PDFs and generated project material under ignored
`projects/_local_validation/`, and SQLite/case/result files under ignored
`artifacts/`. Manually verify expected identities, pages, exact source regions and
facts against the authoritative sources. Do not derive ground truth from retrieval
output, invent bbox coordinates, or commit proprietary cases/benchmark numbers.
Derived knowledge is an index; original source evidence remains authoritative.

A later optional retriever can provide the same `retrieval` shape (`payload`,
`status`, `observed`, genuine `ranking`/`ranking_limit`) to `score_result(case,
retrieval)` and reuse the expected-evidence contract. It must preserve source
identity/units and report applicable ranking honestly. Such adapters, embeddings,
multimodal retrieval, hybrid search, reranking, graph-guided retrieval, OCR/Vision,
LLM routing/judging, and answer-generation evaluation are deferred experiments.
