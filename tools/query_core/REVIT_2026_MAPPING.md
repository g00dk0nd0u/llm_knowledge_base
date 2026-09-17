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
| Rooms/spaces | `SpatialElement.GetBoundarySegments()` with finish-face `SpatialElementBoundaryOptions`; record the selected phase for FromRoom/ToRoom |
| View placement | `Viewport.GetBoxOutline()`, `Viewport.GetBoxCenter()`, `View.Scale`, `View.CropBox` |
| Schedules | `ScheduleSheetInstance`, represented as a schedule placement rather than a Viewport |

A linked source model may have multiple `RevitLinkInstance` / `link_instances` rows.
They share one linked element identity but produce distinct host-space geometry and
drawing occurrences. Future `LinkElementId` extraction maps, where available, to the
target source model, linked element UniqueId, and target link instance together.

The adapters target Revit 2025, 2026, and 2027 while emitting the same versioned,
Revit-independent snapshot. No Autodesk binary, RVT parser, or live-query dependency
belongs in the offline Query Core.
