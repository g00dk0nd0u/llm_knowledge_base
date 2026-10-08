# Revit Offline Exporter — Phase B1

This read-only add-in exports an ordinary combined `drawing.pdf`, a schema-compatible
`revit_snapshot.json`, and an `export_manifest.json`. Python Query Core then creates
`project.sqlite` and embeds it in `enhanced.pdf`; Revit is never needed at query time.
No Autodesk binaries are stored here.

## Version matrix and prerequisites

| Profile | Host target | SDK |
|---|---|---|
| 2025 / `net8` | `net8.0-windows` | .NET 8 |
| 2026 / `net8` (pre-.NET 10 line) | `net8.0-windows` | .NET 8 |
| 2026 / `net10` (2026.5+ line) | `net10.0-windows` | .NET 10 |
| 2027 / `net10` | `net10.0-windows` | .NET 10 |

Revit point releases can change CLR runtime. The runtime profile is therefore
explicit; in particular, the build helper never guesses between the two 2026 lines.
The profile table is intentionally extensible, but no unverified 2025/.NET 10 host is
provided.

The CLI passes the selected profile's Core framework as the global MSBuild property
`RevitCoreTargetFramework` (for example, `net8.0`). This limits the referenced Core's
restore and build graph to that framework. The 2025/`net8` and 2026/`net8` profiles
therefore need only the .NET 8 SDK; they do not restore or evaluate `net10.0`.
The 2026/`net10` and 2027/`net10` profiles require the .NET 10 SDK and fail in CLI
preflight if it is absent. Host frameworks, assembly names and Autodesk references
are unchanged.

Core development builds without this property retain the default
`net8.0;net10.0` multi-target graph and the SDK/runtime prerequisites used by CI.
To build only Core's .NET 8 graph on an SDK 8-only machine, use
`dotnet build revit_exporter/src/LlmKnowledgeBase.Revit.Core/LlmKnowledgeBase.Revit.Core.csproj -p:RevitCoreTargetFramework=net8.0`.

Install the matching Revit and SDK on Windows. Hosts reference `RevitAPI.dll` and
`RevitAPIUI.dll` from `RevitInstallDir` (`C:\Program Files\Autodesk\Revit YYYY` by
default), with `Private=false`; a missing assembly is a build error. Shared DTO,
hashing, deterministic IDs, manifest and serialization code has no Autodesk
reference. One shared Revit source implementation is linked into four thin runtime-profile hosts.

Build and optionally create a local manifest (do not commit generated manifests):

```powershell
python -m tools.revit_exporter build --version 2025 --runtime net8 --manifest "$env:APPDATA\Autodesk\Revit\Addins\2025\LlmKnowledgeBase.Revit.addin"
python -m tools.revit_exporter build --version 2026 --runtime net8 --manifest "$env:APPDATA\Autodesk\Revit\Addins\2026\LlmKnowledgeBase.Revit.addin"
python -m tools.revit_exporter build --version 2026 --runtime net10 --manifest "$env:APPDATA\Autodesk\Revit\Addins\2026\LlmKnowledgeBase.Revit.addin"
python -m tools.revit_exporter build --version 2027 --runtime net10 --manifest "$env:APPDATA\Autodesk\Revit\Addins\2027\LlmKnowledgeBase.Revit.addin"
```

The portable templates under `manifests/` contain `{{ASSEMBLY_PATH}}`, never a
machine path. `LLM_KB_EXPORT_ROOT` may set the export root. Otherwise exports go to
`Documents/LlmKnowledgeBaseExports/<project>/<UTC-run-id>/`, never beside the RVT.
A `.tmp` staging directory is promoted only after PDF, snapshot, and manifest are
written. The command never starts a transaction or changes/saves/synchronizes the RVT.

Run **Export Offline Knowledge Package**, then finalize:

```bash
python -m tools.query_core finalize-revit-export /path/to/completed-run
python -m tools.query_core inspect /path/to/completed-run/enhanced.pdf
```

Finalization validates JSON Schema and verifies `drawing.pdf` SHA-256 before using
the existing SQLite builder and PDF packager. Files in the run remain diagnostic;
`enhanced.pdf` is the portable handover artifact.

## Clarity unattended daily export

The manual path remains **Revit command → completed run → Query Core finalize**. Both
that command and unattended automation call the UI-free, read-only API
`OfflineExportService.Run(Document, OfflineExportOptions)`, which returns the completed
run directory. The service does not display dialogs, select files, start transactions,
save, or synchronize the model; only the manual command supplies its surrounding
`TaskDialog`.

The unattended path is **Clarity pyRevit script → shared exporter → Query Core
finalize/publish → latest + archive**. Configure the Clarity task to run
`clarity/export_unattended.py`, set `LLM_KB_UNATTENDED_CONFIG` to an absolute JSON
configuration path, and start with [`clarity/config.example.json`](clarity/config.example.json).
The wrapper contains no extraction implementation: it gets the active document, calls
the shared .NET service, and launches:

```bash
python -m tools.unattended_export finalize-publish \
  --config D:/LlmKnowledgeBase/config.json --run D:/LlmKnowledgeBase/exports/model/run-id
```

Configuration requires `project_key`, `export_root`, `publish_root`, an IANA
`archive_timezone`, `python_executable`, and `repository_root`. Optional settings are
`archive_retention_days` (use `null` to disable pruning) and `include_links`.
Unattended runs always finalize through Query Core. Invalid configuration fails without publication.
The first-party `tzdata` dependency supplies IANA timezone data on Windows machines
that do not provide it through the operating system.

`exporter_assembly` is an optional loading fallback. If the matching exporter host
assembly is already present in Revit's `AppDomain`, the wrapper reuses it. Otherwise,
this field must be an absolute path to the deployed host DLL for that worker's exact
Revit/runtime profile (for example `LlmKnowledgeBase.Revit2026.dll`). The wrapper
rejects a DLL that does not expose `LlmKnowledgeBase.Revit.OfflineExportService`.

Publication validates the manifest hashes and names, SQLite database, enhanced PDF,
and its embedded SQLite payload. It converts the manifest export timestamp—not the
later publication time—to the configured timezone and copies a complete archive to
`<publish_root>/<project_key>/archive/YYYY-MM-DD/<run-id>/`, and promotes a fully
staged directory to `latest/`. A per-project filesystem lock serializes publishers.
Promotion renames the prior `latest` to a backup and restores it if promotion fails;
files are never replaced individually.

### Required Clarity/Revit smoke test (not performed here)

1. Build and deploy the correct Revit host assembly and pyRevit script on the Clarity worker.
2. Create a machine-local config and set `LLM_KB_UNATTENDED_CONFIG` for the task.
3. Open a disposable test RVT through Clarity and run the script once.
4. Confirm the completed run, dated archive, and complete `latest/` each contain all five files.
5. Query/inspect `enhanced.pdf`, compare the exported PDF pages visually, and review manifest warnings.
6. Force a finalization failure and confirm the previously valid `latest/` remains byte-for-byte intact.

Real Clarity/Revit execution requires licensed installed hosts and remains pending; it
is not validated by repository CI.

## Extraction policy

Sheets are all non-placeholder printable sheets ordered by sheet number and UniqueId.
That same list drives synchronous native PDF export; its positions become the
one-based `export_order` and `pdf_page`. Model identity priority is cloud model path,
workshared central path, normalized saved path, then `CreationGUID`; document version
GUID/save count is recorded separately. Loaded linked documents are deduplicated as
source models while every link instance retains its own UniqueId and total transform.
Linked occurrence geometry is transformed to host coordinates before centralized
Revit-unit conversion to millimetres. Linked rooms/spaces likewise retain one shared
source identity regardless of how many times their model is placed.

Door/window FromRoom and ToRoom relationships use the exported View's `VIEW_PHASE`,
via `get_FromRoom(phase)` and `get_ToRoom(phase)` in C#. The Revit 2025 API reference
defines these as named Phase-indexed properties:
[FromRoom(Phase)](https://github.com/ADN-DevTech/revit-api-chms/blob/main/html/2025/html/c4a37990-0603-50e0-ca97-1cd5449940dd.htm)
and [ToRoom(Phase)](https://github.com/ADN-DevTech/revit-api-chms/blob/main/html/2025/html/94e34f74-b6d1-2e4b-df44-b93aac5543c6.htm).
The parameterless properties refer to the last project phase and are not used as a
fallback. Missing exported-view phase context remains a warning with no relationship.

The exporter collects elements in placed host views plus a bounded model-context
category set, types, useful instance/type parameters, levels, rooms/spaces, finish-face
spatial boundaries (including distinct Room/Space and boundary-source link
occurrences), compact bbox/point/line geometry, schedules, dimensions and
segments/references, spots, text notes, tags, and grids. Failures scoped to individual
records become manifest warnings; PDF, identity, and package failures fail the run.

Phase 3B2a also treats `View.GetReferenceSections()`,
`View.GetReferenceCallouts()`, and `View.GetReferenceElevations()` as deterministic
Revit source facts. Only views in the exported `_placements` map are inspected, and
`ReferenceableViewUtils.GetReferencedViewId()` supplies the target. A reference
section is an explicit reference to an existing view, **not** an ordinary section
cut. Ordinary callouts are discovered separately in one host-document View pass:
`View.IsCallout` plus `View.GetCalloutParentId()` emits deterministic parent-to-child
`callout_to` topology only when the exact parent has an exported placement. Ordinary
sections remain deferred. Ordinary elevation topology is sourced only from exported
views in `_placements`: a view-scoped `FilteredElementCollector(Document, sourceView.Id)`
selects non-reference `ElevationMarker` occurrences, and each occupied slot from
`MaximumViewCount` / `GetViewId(index)` resolves its hosted `ViewSection`.
`OwnerViewId` is not used to establish the source relationship. Reference markers
remain exclusively on the Phase 3B2a path.

Each marker occurrence gets page-level source evidence on every exported source-sheet
placement. Its PDF bbox and coordinate space are null because no exact Revit-sheet to
PDF-points transform is proven. A resolved target View remains exact even when it is
unplaced; a target sheet is recorded only for one known exported placement, so PDF
navigation can legitimately be unavailable. Target evidence is not duplicated, and
the visible `printed_reference` is intentionally not reconstructed from view or sheet
metadata. Snapshot v1 adds the optional `drawing_references` collection to new exports;
old v1 snapshots without that collection remain valid.

Ordinary callouts follow the same evidence and target rules: source evidence is
page-level for every exported exact-parent occurrence, with no marker bbox or
reconstructed printed reference. The callout View is an exact target even when it is
unplaced; its target sheet (and thus PDF navigation) is included only when exactly one
exported placement is known.

Ordinary elevations follow those rules per source View, marker, slot, and source-sheet
occurrence. View-scoped collection is Revit's deterministic View association but only
means marker graphics may be visible; it does not prove printed-pixel visibility.
Consequently source evidence has a null PDF bbox, target evidence is null, and printed
marker text is not reconstructed. A hosted target View remains exact without PDF
navigation; its target sheet is populated only for exactly one exported placement.

Phase 3C1 keeps `ScheduleSheetInstance` placements in a dedicated
`_schedulePlacements` map, separate from graphical `_placements`. For each unique
eligible ordinary host `ViewSchedule`, one view-scoped
`FilteredElementCollector(Document, viewSchedule.Id)` pass with
`WhereElementIsNotElementType()` and one with `WhereElementIsElementType()` provide
the only membership facts. Supported members map to the existing `element`,
`element_type`, `space` (Room/Space), or `level` identities; a schedule-only model
instance receives the same source-faithful element/type record as a graphical member.
Each safe placed occurrence emits `entity_appearances` with
`appearance_kind=schedule`, `bbox_quality=page_only`, null bbox coordinates, and the
existing schedule-placement viewport ID. This asserts page membership only—not a
row, cell, or printed position—and never parses PDF or schedule text or duplicates
the fact as a semantic relationship.

Membership projection is deliberately skipped with one warning per schedule View
for Filter by Sheet, split schedules, key schedules, material takeoffs, embedded
schedules, and schedules including linked files. These modes cannot yet guarantee
truthful host entity-to-placement page membership without row/segment or linked-model
semantics. Unsupported collected objects are aggregated by runtime type and category
rather than forced into fake elements. Ordinary section `section_cut_to` inference
remains deferred.

For Revit 2025 safety, Phase B1 deliberately never uses the three-argument linked-view
`FilteredElementCollector`. Linked documents are collected independently and link
appearance visibility is conservative. This avoids reported 2025.x native crashes and
does not change the common snapshot contract.

## Required real-Revit smoke test (not performed by CI)

### Separate, bounded PDF diagnostics

**Diagnose PDF Export (max 2 sheets)** is a separate External Tools command. It
does not call `OfflineExportService.Run` or create a knowledge package. The normal
**Export Offline Knowledge Package** command and Clarity's unattended entry point
ignore all `LLM_KB_PDF_DIAG_*` variables and retain their full printable-Sheet export
and existing Sheet-order/PDF-page mapping.

Configure the diagnostic command with process environment variables:

| Variable | Contract |
| --- | --- |
| `LLM_KB_PDF_DIAG_SHEET_NUMBERS` | Required: one or two comma-separated, distinct Sheet numbers. Delimiter whitespace is trimmed; lookup is otherwise exact and case-sensitive, in the specified order. Missing, ambiguous, placeholder, or unprintable Sheets fail before export; no replacement is selected. |
| `LLM_KB_PDF_DIAG_MODE` | Required: exactly `default` or `saved`. Missing or invalid modes fail. |
| `LLM_KB_PDF_DIAG_SETUP_NAME` | Required for `saved`: the exact, case-sensitive name of an existing `ExportPDFSettings`. A missing setting records available names and fails without fallback. Ignored by `default`. |

`default` constructs `new PDFExportOptions()`. `saved` reads a copy using
`ExportPDFSettings.GetOptions()`, without modifying or creating a settings element.
Both then set only `Combine=true`, `FileName="drawing"`, and
`SetExportInBackground(false)` before one real `Document.Export` call. The Revit
2025 API confirms [GetOptions returns a copy and requires FileName to be reset](https://github.com/ADN-DevTech/revit-api-chms/blob/main/html/2025/html/f5d51aa8-71ae-0526-1668-e0d97eb8315e.htm)
and provides [SetExportInBackground(bool)](https://github.com/ADN-DevTech/revit-api-chms/blob/main/html/2025/html/c130e24c-eca7-3211-2905-2ee1769dde6f.htm).
[Document.Export accepts an ordered list of Sheet/View IDs](https://github.com/ADN-DevTech/revit-api-chms/blob/main/html/2025/html/93d66d57-c20e-a103-39a1-77bc2ea05183.htm).
Vector processing is reported as `vector_when_possible` when `AlwaysUseRaster`
is false; this does not assert that every object in the output is vector data.

Each attempt writes to a unique local
`<LLM_KB_EXPORT_ROOT>/pdf-diagnostics/<timestamp>-<guid>/` directory; the default
root is `Documents/LlmKnowledgeBaseExports`. This directory is separate from
completed `<project>/<run>` packages and is never promoted into one. It contains
`pdf_diagnostic_report.json` and, if emitted, the diagnostic PDF. No Snapshot,
SQLite, Enhanced PDF, or publication manifest is generated. Keep these files local;
if placing them inside this checkout, use ignored `revit_exporter/local/`.

The report records Revit version/build, requested and selected Sheet numbers,
ElementIds, count/order, setup lookup and available names, options before and after
the three overrides, output folder, whether export was called, its nullable return
value, exception type/message/stack trace/details, PDF existence and sizes. A thrown
export has `export_return_value=null`; a returned `false` stays false. False return,
missing/empty `drawing.pdf`, or an exception fails the command even if a partial PDF
exists. Reporting I/O failures also fail and identify the original export exception.
There are no retries, per-Sheet fallback, or document modification/save/synchronize
operations. Validation failures are reported where the output folder can be created;
if the folder itself is inaccessible, the command displays that I/O failure.

#### Revit 2025.4 comparison on the real machine

The user has verified the existing PR's host build (zero warnings/errors), add-in
loading, and command launch. Normal API PDF export throws `InternalException` even
under `C:\Temp\LlmKnowledgeBaseExports`; the native UI successfully exports
`Admin CD.31` and `G6.001` with `PDF Export Setup Settings 1` (ISO A3, Landscape,
Fit to Page, Vector, Background OFF). The root cause is still unknown. This
diagnostic command is not a permanent PDF-export fix. Its host build and API execution
must be checked against the installed Autodesk DLLs; CI does not perform either.

Close Revit before rebuilding/updating its manifest. In PowerShell, from the checkout:

```powershell
git fetch origin
git switch fix/revit-net8-core-framework-selection
git pull --ff-only origin fix/revit-net8-core-framework-selection
python -m tools.revit_exporter build --version 2025 --runtime net8 --manifest "$env:APPDATA\Autodesk\Revit\Addins\2025\LlmKnowledgeBase.Revit.addin"

$env:LLM_KB_EXPORT_ROOT = "C:\Temp\LlmKnowledgeBaseExports"
$env:LLM_KB_PDF_DIAG_SHEET_NUMBERS = "Admin CD.31,G6.001"
$env:LLM_KB_PDF_DIAG_MODE = "default"
Start-Process "C:\Program Files\Autodesk\Revit 2025\Revit.exe"
```

Open the same project and invoke **Diagnose PDF Export (max 2 sheets)** once. Retain
the report even if the command fails. Close Revit without saving or synchronizing
changes for this diagnostic, then use the same PowerShell session:

```powershell
$env:LLM_KB_PDF_DIAG_MODE = "saved"
$env:LLM_KB_PDF_DIAG_SETUP_NAME = "PDF Export Setup Settings 1"
Start-Process "C:\Program Files\Autodesk\Revit 2025\Revit.exe"
```

Open the same project and run the diagnostic once again. Revit must be launched from
this shell after setting the variables: changing another shell's environment does
not change an already-running Revit process. Compare the two JSON reports, including
the actual saved options rather than assuming they match the UI. Do not test all
Sheets or adopt an inferred permanent fix before reviewing these two results.

### Complete package smoke test

For **each** installed version (2025, 2026, 2027):

1. Build its host against installed Autodesk DLLs.
2. Deploy its generated `.addin` manifest.
3. Launch Revit and open a test RVT.
4. Run **Export Offline Knowledge Package**.
5. Confirm `drawing.pdf` and `revit_snapshot.json`.
6. Validate/finalize with `finalize-revit-export`.
7. Inspect/query `enhanced.pdf` on a machine/process without Revit.
8. Review warnings and visually verify source sheets/pages.

## Known limitations

* No exact linked-element per-view visibility; linked collection is conservative.
* PDF evidence is generally page-level, not an exact element bbox.
* Curved spatial boundaries retain `curve_kind` plus endpoints and emit an
  approximation warning; only `curve_kind=line` makes those endpoints an exact
  straight segment.
* Nested linked boundary-source provenance is not fabricated; it remains unresolved
  and emits `nested_link_boundary_source_unsupported`.
* Linked tag/reference resolution is conservative when the API cannot safely resolve it.
* Exact reference-marker PDF bboxes and ordinary section topology are not available;
  linked-model drawing topology is also outside Phase 3B2c.
* GitHub CI builds/tests the Autodesk-independent Core, checks host restore graphs,
  and compiles a standard-library import probe from the shared source with implicit
  usings disabled. Plain C# scope probes also check identifiers taken from the boundary
  and annotation source. These probes do not compile Autodesk-dependent host source. Hosts
  require local compilation against matching installed `RevitAPI.dll` and
  `RevitAPIUI.dll`, and cannot be run in CI without licensed local installations.
* Phase B1 exports source data only; it contains no geometry-derived adjacency,
  near/nearest, distance, Issue #5 corridor/compliance, security, egress,
  change-impact, LLM, OCR, or inference logic.
