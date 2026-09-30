# Revit 2026 adapter mapping (Phase B contract)

Phase B should extract only; it must not duplicate the Python SQLite writer.

| Snapshot concern | Intended Revit 2026 API source |
|---|---|
| Entity/type identity | `Element.UniqueId`, `Element.GetTypeId()`, `FamilySymbol`, `ElementType` family/type data |
| PDF and ordering | `Document.Export(..., PDFExportOptions, ...)`, `PDFExportOptions.Combine`; preserve supplied export order |
| Visible candidates | `FilteredElementCollector(document, viewId)` for host-view candidates; a safe host-view collection identifies candidate `RevitLinkInstance` placements, then linked documents are collected independently. Linked appearance quality is no better than `may_be_visible` without projection proof; the three-argument linked-view collector is deliberately not used. |
| Linked document | `RevitLinkInstance.GetLinkDocument()` resolves the single linked `source_models` identity |
| Link placement | `RevitLinkInstance.UniqueId` maps to `link_instances.source_unique_id`; `RevitLinkInstance.GetTotalTransform()` maps to that instance's basis/origin `transform_to_host` |
| Dimensions | `Dimension.Value`, `Dimension.Segments`, `Dimension.References`, `DimensionSegment.Value`, `DimensionSegment.ValueString` |
| Spots and tags | `SpotDimension`; `IndependentTag.GetTaggedElementIds()` including multiple, linked, and unresolved targets |
| Rooms/spaces | `SpatialElement.GetBoundarySegments()` with finish-face `SpatialElementBoundaryOptions`; `BoundarySegment.ElementId` is the local producer or, for a linked source, its `RevitLinkInstance`; `BoundarySegment.LinkElementId` is resolved in that instance's linked document. Runtime curve type maps to `curve_kind`. Record the selected phase for FromRoom/ToRoom. |
| View placement | `Viewport.GetBoxOutline()`, `Viewport.GetBoxCenter()`, `View.Scale`, `View.CropBox` |
| Schedules | `ScheduleSheetInstance`, represented as a schedule placement rather than a Viewport |
| Explicit drawing references | For exported placed source views only, `View.GetReferenceSections()`, `View.GetReferenceCallouts()`, and `View.GetReferenceElevations()` identify markers; `ReferenceableViewUtils.GetReferencedViewId()` resolves the authoritative target View. Source evidence is page-level with null PDF bbox, `printed_reference` is null, and a target sheet is retained only for exactly one exported placement. |
| Ordinary callout topology | One host-document View pass selects non-template `View.IsCallout` Views, and `View.GetCalloutParentId()` supplies the exact parent. A `callout_to` record is emitted per exported parent occurrence with page-level, null-bbox evidence; the callout View remains exact when unplaced, and its sheet is retained only for exactly one placement. Printed reference is not reconstructed. |

A linked source model may have multiple `RevitLinkInstance` / `link_instances` rows.
They share one linked element identity but produce distinct host-space geometry and
drawing occurrences. Boundary occurrence `link_instance_id` is separate from boundary
source `source_link_instance_id`. For a host Room bounded by a linked Wall,
`LinkElementId` maps to the linked source model and actual Wall UniqueId while
`ElementId` maps only to the source link occurrence. For a linked Room with a local
Wall, that Room's top-level occurrence is retained as the source occurrence too.
Nested-link boundary sources are deliberately unresolved with an explicit warning.
Non-line curves retain their kind but only endpoint approximations. No adjacency,
near/nearest, or distance query is implemented in this phase.

An explicit reference section maps to `section_reference_to`, not
`section_cut_to`; reference callouts and elevations map to `callout_to` and
`elevation_reference_to`. These facts do not infer ordinary generated markers,
geometry, annotation text, or linked-model topology. An exact target View may be
unplaced and therefore have no target sheet or PDF navigation. Snapshot v1 keeps this
collection optional for backward compatibility.

Ordinary callouts coexist with those explicit reference-marker facts and use only
`IsCallout` plus `GetCalloutParentId`; the parent is never inferred or replaced by a
primary/dependent View. Ordinary sections and elevations remain deferred.

The adapters target Revit 2025, 2026, and 2027 while emitting the same versioned,
Revit-independent snapshot. No Autodesk binary, RVT parser, or live-query dependency
belongs in the offline Query Core.
