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
cut; ordinary generated sections, callouts, and elevations remain future work.

Each marker occurrence gets page-level source evidence on every exported source-sheet
placement. Its PDF bbox and coordinate space are null because no exact Revit-sheet to
PDF-points transform is proven. A resolved target View remains exact even when it is
unplaced; a target sheet is recorded only for one known exported placement, so PDF
navigation can legitimately be unavailable. Target evidence is not duplicated, and
the visible `printed_reference` is intentionally not reconstructed from view or sheet
metadata. Snapshot v1 adds the optional `drawing_references` collection to new exports;
old v1 snapshots without that collection remain valid.

For Revit 2025 safety, Phase B1 deliberately never uses the three-argument linked-view
`FilteredElementCollector`. Linked documents are collected independently and link
appearance visibility is conservative. This avoids reported 2025.x native crashes and
does not change the common snapshot contract.

## Required real-Revit smoke test (not performed by CI)

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
* Exact reference-marker PDF bboxes and ordinary section/callout/elevation topology
  are not available; linked-model drawing topology is also outside Phase 3B2a.
* GitHub CI builds/tests only the Autodesk-independent Core. Autodesk-dependent hosts
  require local compilation against matching installed `RevitAPI.dll` and
  `RevitAPIUI.dll`, and cannot be run in CI without licensed local installations.
* Phase B1 exports source data only; it contains no geometry-derived adjacency,
  near/nearest, distance, Issue #5 corridor/compliance, security, egress,
  change-impact, LLM, OCR, or inference logic.
