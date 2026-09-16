# Revit Offline Exporter — Phase B1

This read-only add-in exports an ordinary combined `drawing.pdf`, a schema-compatible
`revit_snapshot.json`, and an `export_manifest.json`. Python Query Core then creates
`project.sqlite` and embeds it in `enhanced.pdf`; Revit is never needed at query time.
No Autodesk binaries are stored here.

## Version matrix and prerequisites

| Revit | Host target | SDK |
|---|---|---|
| 2025 | `net8.0-windows` | .NET 8 |
| 2026 | `net8.0-windows` | .NET 8 |
| 2027 | `net10.0-windows` | .NET 10 |

Install the matching Revit and SDK on Windows. Hosts reference `RevitAPI.dll` and
`RevitAPIUI.dll` from `RevitInstallDir` (`C:\Program Files\Autodesk\Revit YYYY` by
default), with `Private=false`; a missing assembly is a build error. Shared DTO,
hashing, deterministic IDs, manifest and serialization code has no Autodesk
reference. One shared Revit source implementation is linked into three thin hosts.

Build and optionally create a local manifest (do not commit generated manifests):

```powershell
python -m tools.revit_exporter build --version 2025 --manifest "$env:APPDATA\Autodesk\Revit\Addins\2025\LlmKnowledgeBase.Revit.addin"
python -m tools.revit_exporter build --version 2026 --manifest "$env:APPDATA\Autodesk\Revit\Addins\2026\LlmKnowledgeBase.Revit.addin"
python -m tools.revit_exporter build --version 2027 --manifest "$env:APPDATA\Autodesk\Revit\Addins\2027\LlmKnowledgeBase.Revit.addin"
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

## Extraction policy

Sheets are all non-placeholder printable sheets ordered by sheet number and UniqueId.
That same list drives synchronous native PDF export; its positions become the
one-based `export_order` and `pdf_page`. Model identity priority is cloud model path,
workshared central path, normalized saved path, then `CreationGUID`; document version
GUID/save count is recorded separately. Loaded linked documents are deduplicated as
source models while every link instance retains its own UniqueId and total transform.
Linked occurrence geometry is transformed to host coordinates before centralized
Revit-unit conversion to millimetres.

The exporter collects elements in placed host views plus a bounded model-context
category set, types, useful instance/type parameters, levels, rooms/spaces, finish-face
spatial boundaries, compact bbox/point/line geometry, schedules, dimensions and
segments/references, spots, text notes, tags, and grids. Failures scoped to individual
records become manifest warnings; PDF, identity, and package failures fail the run.

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
* Curved spatial boundaries retain endpoints and emit an approximation warning.
* Linked tag/reference resolution is conservative when the API cannot safely resolve it.
* Autodesk-dependent hosts cannot be compiled or run in CI without licensed local installations.
* Phase B1 exports source data only; it contains no Issue #5 corridor/compliance,
  security, egress, change-impact, LLM, OCR, or inference logic.
