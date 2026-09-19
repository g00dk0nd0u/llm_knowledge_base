using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Architecture;
using Autodesk.Revit.DB.Mechanical;
using Autodesk.Revit.UI;
using LlmKnowledgeBase.Revit.Core;
using System.Text.Json.Nodes;

namespace LlmKnowledgeBase.Revit;

[Transaction(TransactionMode.ReadOnly)]
public sealed class ExportOfflineKnowledgeCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
    {
        try
        {
            var document = data.Application.ActiveUIDocument?.Document
                ?? throw new InvalidOperationException("Open a project before exporting.");
            var completed = OfflineExportService.Run(document, new OfflineExportOptions());
            TaskDialog.Show("Offline Knowledge Export", $"Export completed:\n{completed}");
            return Result.Succeeded;
        }
        catch (Exception error)
        {
            message = error.Message;
            TaskDialog.Show("Offline Knowledge Export failed", error.ToString());
            return Result.Failed;
        }
    }
}

public sealed class OfflineExportOptions
{
    public string? ExportRoot { get; set; }
    public bool IncludeLinks { get; set; } = true;
}

/// <summary>UI-free, read-only entry point shared by the manual command and automation hosts.</summary>
public static class OfflineExportService
{
    public static string Run(Document document, OfflineExportOptions? options = null)
    {
        ArgumentNullException.ThrowIfNull(document);
        return new RevitSnapshotExporter(document, options ?? new OfflineExportOptions()).Export();
    }
}

internal sealed class RevitSnapshotExporter
{
    private const string Provenance = "revit_api";
    private readonly Document _document;
    private readonly OfflineExportOptions _options;
    private readonly List<ExportWarning> _warnings = [];
    private readonly Dictionary<ElementId, List<(ViewSheet Sheet, View View, Viewport Port, int Page)>> _placements = [];
    private readonly Dictionary<string, JsonObject> _elements = [];
    private readonly Dictionary<string, JsonObject> _types = [];
    private readonly Dictionary<string, HashSet<string>> _recordIds = [];
    private readonly HashSet<string> _searchIds = [];
    private JsonObject _snapshot = null!;
    private JsonObject Records => _snapshot["records"]!.AsObject();
    private string _hostModelId = "";
    private string _documentId = "";

    public RevitSnapshotExporter(Document document, OfflineExportOptions options)
    {
        _document = document;
        _options = options;
    }

    public string Export()
    {
        var project = SafeName(string.IsNullOrWhiteSpace(_document.Title) ? "untitled" : _document.Title);
        var root = _options.ExportRoot ?? Environment.GetEnvironmentVariable("LLM_KB_EXPORT_ROOT");
        var baseDirectory = string.IsNullOrWhiteSpace(root)
            ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "LlmKnowledgeBaseExports")
            : Path.GetFullPath(root);
        var run = DateTimeOffset.UtcNow.ToString("yyyyMMddTHHmmssfffZ");
        var finalDirectory = Path.Combine(baseDirectory, project, run);
        var staging = finalDirectory + ".tmp";
        Directory.CreateDirectory(staging);
        try
        {
            var sheets = CollectSheets();
            if (sheets.Count == 0) throw new InvalidOperationException("No non-placeholder printable sheets were found.");
            var pdf = Path.Combine(staging, "drawing.pdf");
            ExportPdf(staging, pdf, sheets);
            var sha = ExportFiles.Sha256(pdf);
            var identity = DocumentIdentityResolver.Resolve(_document);
            _hostModelId = StableIds.Hash("model", identity.Kind, identity.Value);
            _documentId = StableIds.Document(identity.Kind, identity.Value);
            _snapshot = SnapshotContract.Create(project, $"Revit {_document.Application.VersionNumber}", identity.Value, sha);
            AddDocumentAndModels(identity, sha);
            AddSheetsViewsAndPlacements(sheets);
            AddLevels(_document, _hostModelId);
            if (_options.IncludeLinks) AddLinks();
            CollectHostElements();
            AddSpatialElements(_document, _hostModelId);
            AddSpatialBoundaries(_document, _hostModelId, null, Transform.Identity);
            AddAnnotations();
            FlushElements();
            SortRecords();
            var snapshotFile = Path.Combine(staging, "revit_snapshot.json");
            ExportFiles.WriteJson(snapshotFile, _snapshot);
            var manifest = new ExportManifest("revit-exporter/1.0", _document.Application.VersionNumber,
                identity.Value, DateTimeOffset.UtcNow, "drawing.pdf", sha, "revit_snapshot.json",
                sheets.Count, Count("elements"), Count("spaces"), Count("annotations"), Count("geometries"), _warnings);
            ExportFiles.WriteJson(Path.Combine(staging, "export_manifest.json"), manifest);
            Directory.Move(staging, finalDirectory);
            return finalDirectory;
        }
        catch
        {
            if (Directory.Exists(staging))
                ExportFiles.WriteJson(Path.Combine(staging, "export_failure.json"), new { successful = false, warnings = _warnings });
            throw;
        }
    }

    private List<ViewSheet> CollectSheets() => new FilteredElementCollector(_document)
        .OfClass(typeof(ViewSheet)).Cast<ViewSheet>()
        .Where(s => !s.IsPlaceholder && s.CanBePrinted)
        .OrderBy(s => s.SheetNumber, StringComparer.Ordinal).ThenBy(s => s.UniqueId, StringComparer.Ordinal).ToList();

    private void ExportPdf(string folder, string expected, IReadOnlyList<ViewSheet> sheets)
    {
        var options = new PDFExportOptions { Combine = true, FileName = "drawing" };
        var ids = sheets.Select(s => s.Id).ToList();
        if (!_document.Export(folder, ids, options)) throw new InvalidOperationException("Revit PDF export returned false.");
        var emitted = Directory.EnumerateFiles(folder, "*.pdf").SingleOrDefault();
        if (emitted is null) throw new InvalidOperationException("Revit reported success but no PDF was produced.");
        if (!Path.GetFullPath(emitted).Equals(Path.GetFullPath(expected), StringComparison.OrdinalIgnoreCase)) File.Move(emitted, expected);
        if (!File.Exists(expected) || new FileInfo(expected).Length == 0) throw new InvalidOperationException("drawing.pdf is missing or empty.");
    }

    private void AddDocumentAndModels(DocumentIdentity identity, string sha)
    {
        Add("documents", Obj(("id", _documentId), ("identity", identity.Value), ("title", _document.Title),
            ("source_filename", "drawing.pdf"), ("source_sha256", sha)));
        Add("source_models", SourceModel(_document, _hostModelId, "host", identity));
    }

    private void AddSheetsViewsAndPlacements(IReadOnlyList<ViewSheet> sheets)
    {
        var views = new HashSet<ElementId>();
        for (var index = 0; index < sheets.Count; index++)
        {
            var sheet = sheets[index]; var page = index + 1; var sheetId = Id("sheet", _hostModelId, sheet.UniqueId);
            Add("sheets", Obj(("id", sheetId), ("document_id", _documentId), ("number", sheet.SheetNumber), ("name", sheet.Name),
                ("pdf_page", page), ("export_order", page), ("source_model_id", _hostModelId), ("source_unique_id", sheet.UniqueId)));
            AddSearch("sheet", sheetId, $"{sheet.SheetNumber} {sheet.Name}");
            foreach (var viewportId in sheet.GetAllViewports())
            {
                if (_document.GetElement(viewportId) is not Viewport port || _document.GetElement(port.ViewId) is not View view) continue;
                var viewId = Id("view", _hostModelId, view.UniqueId); var portId = Id("viewport", _hostModelId, port.UniqueId);
                if (views.Add(view.Id)) Add("views", Obj(("id", viewId), ("document_id", _documentId), ("name", view.Name),
                    ("view_type", view.ViewType.ToString()), ("export_order", null), ("source_model_id", _hostModelId), ("source_unique_id", view.UniqueId)));
                AddSearch("view", viewId, $"{view.Name} {view.ViewType}");
                var outline = port.GetBoxOutline();
                Add("viewports", Obj(("id", portId), ("sheet_id", sheetId), ("view_id", viewId), ("placement_kind", "viewport"),
                    ("sheet_x_min", outline.MinimumPoint.X), ("sheet_y_min", outline.MinimumPoint.Y), ("sheet_x_max", outline.MaximumPoint.X),
                    ("sheet_y_max", outline.MaximumPoint.Y), ("sheet_coordinate_unit", "revit_sheet_feet"), ("pdf_x_min", null),
                    ("pdf_y_min", null), ("pdf_x_max", null), ("pdf_y_max", null), ("pdf_coordinate_space", null),
                    ("sheet_to_pdf_transform", null), ("mapping_quality", "unknown")));
                if (!_placements.TryGetValue(view.Id, out var list)) _placements[view.Id] = list = [];
                list.Add((sheet, view, port, page));
            }
            foreach (var scheduleId in new FilteredElementCollector(_document, sheet.Id).OfClass(typeof(ScheduleSheetInstance)).ToElementIds())
            {
                if (_document.GetElement(scheduleId) is not ScheduleSheetInstance schedule || schedule.IsTitleblockRevisionSchedule) continue;
                var view = _document.GetElement(schedule.ScheduleId) as ViewSchedule; if (view is null) continue;
                var viewId = Id("view", _hostModelId, view.UniqueId);
                if (views.Add(view.Id)) Add("views", Obj(("id", viewId), ("document_id", _documentId), ("name", view.Name), ("view_type", "Schedule"),
                    ("export_order", null), ("source_model_id", _hostModelId), ("source_unique_id", view.UniqueId)));
                AddSearch("view", viewId, $"{view.Name} Schedule");
                Add("viewports", Obj(("id", Id("schedule", _hostModelId, schedule.UniqueId)), ("sheet_id", sheetId), ("view_id", viewId),
                    ("placement_kind", "schedule"), ("sheet_x_min", null), ("sheet_y_min", null), ("sheet_x_max", null), ("sheet_y_max", null),
                    ("sheet_coordinate_unit", "revit_sheet_feet"), ("pdf_x_min", null), ("pdf_y_min", null), ("pdf_x_max", null), ("pdf_y_max", null),
                    ("pdf_coordinate_space", null), ("sheet_to_pdf_transform", null), ("mapping_quality", "unknown")));
            }
        }
    }

    private void AddLinks()
    {
        var models = new Dictionary<Document, string>();
        var linkPlacements = BuildLinkPlacementMap();
        foreach (var link in new FilteredElementCollector(_document).OfClass(typeof(RevitLinkInstance)).Cast<RevitLinkInstance>().OrderBy(x => x.UniqueId))
        {
            try
            {
                var linked = link.GetLinkDocument();
                if (linked is null) { _warnings.Add(new("unloaded_revit_link", $"Link '{link.Name}' is unloaded.", link.UniqueId)); continue; }
                var transform = link.GetTotalTransform();
                if (!models.TryGetValue(linked, out var modelId))
                {
                    var identity = DocumentIdentityResolver.Resolve(linked);
                    modelId = StableIds.Hash("model", identity.Kind, identity.Value); models[linked] = modelId;
                    Add("source_models", SourceModel(linked, modelId, "link", identity));
                    AddLevels(linked, modelId);
                    AddSpatialElements(linked, modelId);
                }
                var linkId = Id("link", _hostModelId, link.UniqueId);
                Add("link_instances", Obj(("id", linkId), ("host_source_model_id", _hostModelId), ("linked_source_model_id", modelId),
                    ("source_unique_id", link.UniqueId), ("name", link.Name), ("transform_to_host", TransformJson(transform)), ("provenance", Provenance)));
                AddSpatialBoundaries(linked, modelId, linkId, transform);
                // Deliberately avoid the Revit 2025-unsafe three-argument linked-view collector.
                var placements = linkPlacements.TryGetValue(link.Id, out var candidates) ? candidates : [];
                foreach (var element in CollectContextElements(linked))
                {
                    if (placements.Count == 0)
                    {
                        AddElement(element, modelId, transform, linkId, null);
                        continue;
                    }
                    foreach (var placement in placements) AddElement(element, modelId, transform, linkId, placement);
                }
            }
            catch (Exception error) { _warnings.Add(new("link_extraction_failed", error.Message, link.UniqueId)); }
        }
    }

    private Dictionary<ElementId, List<(ViewSheet Sheet, View View, Viewport Port, int Page)>> BuildLinkPlacementMap()
    {
        var result = new Dictionary<ElementId, List<(ViewSheet Sheet, View View, Viewport Port, int Page)>>();
        foreach (var pair in _placements)
        {
            try
            {
                var linkIds = new FilteredElementCollector(_document, pair.Key)
                    .OfClass(typeof(RevitLinkInstance)).ToElementIds();
                foreach (var linkId in linkIds)
                {
                    if (!result.TryGetValue(linkId, out var list)) result[linkId] = list = [];
                    list.AddRange(pair.Value);
                }
            }
            catch (Exception error)
            {
                _warnings.Add(new("linked_view_candidate_collection_failed", error.Message));
            }
        }
        return result;
    }

    private void CollectHostElements()
    {
        foreach (var placement in _placements.SelectMany(x => x.Value.Select(p => (x.Key, p))))
        {
            foreach (var element in new FilteredElementCollector(_document, placement.Key).WhereElementIsNotElementType())
            {
                if (element is View || element is Viewport || element is AnnotationSymbol) continue;
                AddElement(element, _hostModelId, Transform.Identity, null, placement.p);
            }
        }
        foreach (var element in CollectContextElements(_document)) AddElement(element, _hostModelId, Transform.Identity, null, null);
    }

    private static IEnumerable<Element> CollectContextElements(Document document)
    {
        var categories = new[] { BuiltInCategory.OST_Walls, BuiltInCategory.OST_Doors, BuiltInCategory.OST_Windows,
            BuiltInCategory.OST_StructuralColumns, BuiltInCategory.OST_Columns, BuiltInCategory.OST_Floors,
            BuiltInCategory.OST_Roofs, BuiltInCategory.OST_Casework, BuiltInCategory.OST_GenericModel,
            BuiltInCategory.OST_SpecialityEquipment, BuiltInCategory.OST_ShaftOpening, BuiltInCategory.OST_Rooms,
            BuiltInCategory.OST_MEPSpaces };
        return new FilteredElementCollector(document).WhereElementIsNotElementType()
            .WherePasses(new ElementMulticategoryFilter(categories)).ToElements();
    }

    private void AddElement(Element element, string modelId, Transform transform, string? linkId,
        (ViewSheet Sheet, View View, Viewport Port, int Page)? placement)
    {
        try
        {
            if (element is Room or Space) return;
            var id = Id("element", modelId, element.UniqueId);
            if (!_elements.ContainsKey(id))
            {
                string? typeId = null;
                if (element.GetTypeId() != ElementId.InvalidElementId && element.Document.GetElement(element.GetTypeId()) is ElementType type)
                {
                    typeId = Id("type", modelId, type.UniqueId);
                    _types.TryAdd(typeId, Obj(("id", typeId), ("family_name", type.FamilyName), ("type_name", type.Name),
                        ("category", CategoryName(type)), ("source_model_id", modelId), ("source_unique_id", type.UniqueId),
                        ("provenance", Provenance), ("confidence", null)));
                    AddParameters(type, "element_type", typeId, "type");
                    AddSearch("element_type", typeId, $"{type.FamilyName} {type.Name} {CategoryName(type)}");
                }
                _elements[id] = Obj(("id", id), ("name", string.IsNullOrWhiteSpace(element.Name) ? CategoryName(element) : element.Name),
                    ("category", CategoryName(element)), ("type_id", typeId), ("space_id", null), ("level_id", ResolveLevel(element, modelId)),
                    ("source_model_id", modelId), ("source_unique_id", element.UniqueId), ("provenance", Provenance), ("confidence", null));
                AddParameters(element, "element", id, "instance");
                AddSearch("element", id, $"{element.Name} {CategoryName(element)}");
            }
            AddGeometry(element, id, transform, linkId);
            if (placement is { } p)
            {
                if (linkId is null && element is FamilyInstance family) AddRoomRelations(family, id, p.View);
                var appearanceId = StableIds.Hash("appearance", id, linkId ?? "host", p.Sheet.UniqueId, p.View.UniqueId, p.Port.UniqueId);
                Add("entity_appearances", Obj(("id", appearanceId), ("entity_kind", "element"), ("entity_id", id),
                    ("sheet_id", Id("sheet", _hostModelId, p.Sheet.UniqueId)), ("view_id", Id("view", _hostModelId, p.View.UniqueId)),
                    ("viewport_id", Id("viewport", _hostModelId, p.Port.UniqueId)), ("link_instance_id", linkId), ("pdf_page", p.Page),
                    ("x_min", null), ("y_min", null), ("x_max", null), ("y_max", null), ("coordinate_space", "pdf_points_top_left"),
                    ("appearance_kind", "model"), ("bbox_quality", "may_be_visible"), ("provenance", Provenance)));
            }
        }
        catch (Exception error) { _warnings.Add(new("element_extraction_failed", error.Message, element.UniqueId)); }
    }

    private void AddRoomRelations(FamilyInstance family, string elementId, View view)
    {
        try
        {
            var phaseId = view.get_Parameter(BuiltInParameter.VIEW_PHASE)?.AsElementId();
            if (phaseId is null || phaseId == ElementId.InvalidElementId || _document.GetElement(phaseId) is not Phase phase)
            {
                _warnings.Add(new("phase_context_unavailable", "FromRoom/ToRoom requires the exported view phase.", family.UniqueId));
                return;
            }
            foreach (var (relation, room) in new[] { ("from_space", family.FromRoom[phase]), ("to_space", family.ToRoom[phase]) })
            {
                if (room is null) continue;
                Add("relationships", Obj(("id", StableIds.Hash("relationship", elementId, relation, room.UniqueId, phase.UniqueId)),
                    ("source_kind", "element"), ("source_id", elementId), ("relation_type", relation), ("target_kind", "space"),
                    ("target_id", Id("space", _hostModelId, room.UniqueId)), ("phase_source_unique_id", phase.UniqueId),
                    ("provenance", Provenance), ("confidence", null), ("evidence_id", null)));
            }
        }
        catch (Exception error) { _warnings.Add(new("room_relation_extraction_failed", error.Message, family.UniqueId)); }
    }

    private void AddParameters(Element element, string kind, string entityId, string scope)
    {
        foreach (Parameter parameter in element.Parameters)
        {
            try
            {
                if (!parameter.HasValue || parameter.Definition is null) continue;
                var dataType = parameter.Definition.GetDataType(); var dataTypeId = dataType.TypeId;
                var rawNumeric = parameter.StorageType == StorageType.Double ? parameter.AsDouble() : (double?)null;
                var normalized = ParameterNormalizer.Normalize(parameter, dataType);
                Add("parameters", Obj(("id", StableIds.Hash("parameter", entityId, scope, parameter.Id.Value.ToString())),
                    ("entity_kind", kind), ("entity_id", entityId), ("scope", scope), ("definition_name", parameter.Definition.Name),
                    ("definition_key", ParameterDefinitionKey(parameter)), ("storage_type", parameter.StorageType.ToString()), ("data_type_id", dataTypeId),
                    ("parameter_type_id", dataTypeId), ("shared_parameter_guid", parameter.IsShared ? parameter.GUID.ToString("D") : null),
                    ("unit_type_id", parameter.GetUnitTypeId()?.TypeId), ("raw_value_text", SafeValueString(parameter)),
                    ("raw_numeric_value", rawNumeric), ("numeric_value", normalized.Value), ("unit", normalized.Unit),
                    ("value_text", normalized.Text), ("provenance", Provenance), ("confidence", null), ("evidence_id", null)));
                if (rawNumeric is not null && normalized.Value is null)
                    _warnings.Add(new("unsupported_parameter_data_type", $"Unsupported numeric spec {dataTypeId}", element.UniqueId));
            }
            catch (Exception error) { _warnings.Add(new("parameter_extraction_failed", error.Message, element.UniqueId)); }
        }
    }

    private void AddSpatialElements(Document document, string modelId)
    {
        foreach (var spatial in new FilteredElementCollector(document).WhereElementIsNotElementType()
                     .Where(e => e is Room or Space).Cast<SpatialElement>().OrderBy(e => e.UniqueId))
        {
            try
            {
                var id = Id("space", modelId, spatial.UniqueId);
                var levelId = spatial.LevelId == ElementId.InvalidElementId ? null : ResolveLevel(spatial, modelId);
                var number = spatial.get_Parameter(BuiltInParameter.ROOM_NUMBER)?.AsString();
                Add("spaces", Obj(("id", id), ("kind", spatial is Room ? "Room" : "Space"), ("name", spatial.Name), ("number", number),
                    ("level_id", levelId), ("phase_source_unique_id", PhaseUniqueId(spatial)), ("source_model_id", modelId),
                    ("source_unique_id", spatial.UniqueId), ("provenance", Provenance), ("confidence", null)));
                AddSearch("space", id, $"{number} {spatial.Name}");
            }
            catch (Exception error) { _warnings.Add(new("space_extraction_failed", error.Message, spatial.UniqueId)); }
        }
    }

    private void AddSpatialBoundaries(Document document, string modelId, string? linkId, Transform transform)
    {
        var options = new SpatialElementBoundaryOptions { SpatialElementBoundaryLocation = SpatialElementBoundaryLocation.Finish };
        foreach (var spatial in new FilteredElementCollector(document).WhereElementIsNotElementType()
                     .Where(e => e is Room or Space).Cast<SpatialElement>().OrderBy(e => e.UniqueId))
        {
            try
            {
                var id = Id("space", modelId, spatial.UniqueId);
                if (!_recordIds.TryGetValue("spaces", out var spaceIds) || !spaceIds.Contains(id)) continue;
                var loops = spatial.GetBoundarySegments(options); if (loops is null) continue;
                for (var loopIndex = 0; loopIndex < loops.Count; loopIndex++)
                {
                    var boundaryId = linkId is null
                        ? StableIds.Hash("boundary", id, loopIndex.ToString())
                        : StableIds.Hash("boundary", id, linkId, loopIndex.ToString());
                    Add("spatial_boundaries", Obj(("id", boundaryId), ("space_id", id), ("link_instance_id", linkId), ("loop_index", loopIndex),
                        ("loop_kind", loopIndex == 0 ? "outer" : "inner"), ("coordinate_system", SnapshotContract.CoordinateSystem),
                        ("unit", "mm"), ("provenance", Provenance)));
                    for (var segmentIndex = 0; segmentIndex < loops[loopIndex].Count; segmentIndex++)
                    {
                        var segment = loops[loopIndex][segmentIndex]; var curve = segment.GetCurve();
                        var a = Units.Point(curve.GetEndPoint(0), transform); var b = Units.Point(curve.GetEndPoint(1), transform);
                        var source = document.GetElement(segment.ElementId);
                        Add("spatial_boundary_segments", Obj(("id", StableIds.Hash("boundary-segment", boundaryId, segmentIndex.ToString())),
                            ("boundary_id", boundaryId), ("link_instance_id", linkId), ("segment_index", segmentIndex),
                            ("start_x", a.X), ("start_y", a.Y), ("start_z", a.Z),
                            ("end_x", b.X), ("end_y", b.Y), ("end_z", b.Z), ("source_model_id", source is null ? null : modelId),
                            ("source_unique_id", source?.UniqueId)));
                        if (curve is not Line) _warnings.Add(new("non_linear_boundary_approximated",
                            "Curved finish boundary is represented by endpoints only.", spatial.UniqueId));
                    }
                }
            }
            catch (Exception error) { _warnings.Add(new("space_extraction_failed", error.Message, spatial.UniqueId)); }
        }
    }

    private void AddAnnotations()
    {
        foreach (var pair in _placements)
        foreach (var element in new FilteredElementCollector(_document, pair.Key).WhereElementIsNotElementType())
        {
            if (element is not (Dimension or SpotDimension or TextNote or IndependentTag or Grid)) continue;
            try
            {
                var view = _document.GetElement(pair.Key) as View; if (view is null) continue;
                var semantic = element is Dimension dimension ? DimensionSemanticFor(dimension) : DimensionSemantic.Unsupported;
                var kind = element switch
                {
                    SpotDimension when semantic == DimensionSemantic.SpotElevation => "spot_elevation",
                    SpotDimension when semantic == DimensionSemantic.SpotCoordinate => "spot_coordinate",
                    SpotDimension => "dimension",
                    TextNote => "text_annotation", IndependentTag => "tag", Grid => "grid_reference", _ => "dimension"
                };
                var text = element switch { TextNote note => note.Text, IndependentTag tag => tag.TagText, Grid grid => grid.Name,
                    Dimension dimension => dimension.ValueString ?? dimension.Name, _ => element.Name };
                var annotationId = Id("annotation", _hostModelId, element.UniqueId);
                var normalized = element is Dimension d ? NormalizeDimension(d.Value, semantic, element.UniqueId) : new NormalizedDimension(null, null);
                Add("annotations", Obj(("id", annotationId), ("kind", kind), ("semantic_type", semantic.ToString().ToLowerInvariant()),
                    ("display_text", text ?? ""), ("numeric_value", normalized.Value), ("unit", normalized.Unit),
                    ("related_entity_kind", null), ("related_entity_id", null), ("source_model_id", _hostModelId),
                    ("source_unique_id", element.UniqueId), ("view_id", Id("view", _hostModelId, view.UniqueId)),
                    ("provenance", Provenance), ("confidence", null), ("evidence_id", null)));
                AddSearch("annotation", annotationId, text ?? "");
                if (element is Dimension dimension) AddDimensionDetails(dimension, annotationId, semantic);
                if (element is IndependentTag tag) AddTagReferences(tag, annotationId);
                foreach (var p in pair.Value)
                    Add("entity_appearances", Obj(("id", StableIds.Hash("appearance", annotationId, p.Sheet.UniqueId, p.View.UniqueId)),
                        ("entity_kind", "annotation"), ("entity_id", annotationId), ("sheet_id", Id("sheet", _hostModelId, p.Sheet.UniqueId)),
                        ("view_id", Id("view", _hostModelId, p.View.UniqueId)), ("viewport_id", Id("viewport", _hostModelId, p.Port.UniqueId)),
                        ("link_instance_id", null), ("pdf_page", p.Page), ("x_min", null), ("y_min", null), ("x_max", null), ("y_max", null),
                        ("coordinate_space", "pdf_points_top_left"), ("appearance_kind", "annotation"), ("bbox_quality", "page_only"), ("provenance", Provenance)));
            }
            catch (Exception error) { _warnings.Add(new("annotation_extraction_failed", error.Message, element.UniqueId)); }
        }
        _warnings.Add(new("pdf_mapping_page_level", "Phase B1 annotation and element PDF evidence is page-level."));
    }

    private void AddDimensionDetails(Dimension dimension, string annotationId, DimensionSemantic semantic)
    {
        var segments = dimension.NumberOfSegments > 0 ? dimension.Segments.Cast<DimensionSegment>().ToList() : [];
        if (segments.Count == 0)
            Add("annotation_segments", Segment(annotationId, 0, dimension.Value, dimension.ValueString,
                Safe(() => dimension.ValueOverride), dimension.Prefix, dimension.Suffix, dimension.Above, dimension.Below,
                SafePoint(() => dimension.Origin), SafePoint(() => dimension.TextPosition), semantic, dimension.UniqueId));
        else for (var i = 0; i < segments.Count; i++)
        {
            var segment = segments[i];
            Add("annotation_segments", Segment(annotationId, i, segment.Value, segment.ValueString,
                Safe(() => segment.ValueOverride), segment.Prefix, segment.Suffix, segment.Above, segment.Below,
                SafePoint(() => segment.Origin), SafePoint(() => segment.TextPosition), semantic, dimension.UniqueId));
        }
        var references = dimension.References;
        for (var i = 0; i < references.Size; i++) AddReference(annotationId, i, references.get_Item(i));
    }

    private JsonObject Segment(string annotationId, int index, double? value, string? text, string? valueOverride,
        string? prefix, string? suffix, string? above, string? below, JsonArray? origin, JsonArray? textPosition,
        DimensionSemantic semantic, string sourceId)
    {
        var normalized = NormalizeDimension(value, semantic, sourceId, false);
        return Obj(("id", StableIds.Hash("annotation-segment", annotationId, index.ToString())), ("annotation_id", annotationId),
            ("segment_index", index), ("numeric_value", normalized.Value), ("unit", normalized.Unit), ("display_text", text),
            ("value_override", valueOverride), ("prefix", prefix), ("suffix", suffix), ("above", above), ("below", below),
            ("origin", origin), ("text_position", textPosition));
    }

    private DimensionSemantic DimensionSemanticFor(Dimension dimension)
    {
        if (dimension is SpotDimension)
        {
            if (dimension.Category?.Id.Value == (long)BuiltInCategory.OST_SpotElevations) return DimensionSemantic.SpotElevation;
            if (dimension.Category?.Id.Value == (long)BuiltInCategory.OST_SpotCoordinates) return DimensionSemantic.SpotCoordinate;
            return DimensionSemantic.Unsupported;
        }
        if (_document.GetElement(dimension.GetTypeId()) is not DimensionType type) return DimensionSemantic.Unsupported;
        return type.StyleType switch
        {
            DimensionStyleType.Linear or DimensionStyleType.LinearFixed or DimensionStyleType.Radial or DimensionStyleType.ArcLength
                => DimensionSemantic.Linear,
            DimensionStyleType.Angular => DimensionSemantic.Angular,
            _ => DimensionSemantic.Unsupported
        };
    }

    private NormalizedDimension NormalizeDimension(double? value, DimensionSemantic semantic, string sourceId, bool warn = true)
    {
        if (value is null) return new(null, null);
        if (semantic is DimensionSemantic.Linear or DimensionSemantic.SpotElevation or DimensionSemantic.SpotCoordinate)
            return new(UnitUtils.ConvertFromInternalUnits(value.Value, UnitTypeId.Millimeters), "mm");
        if (semantic == DimensionSemantic.Angular)
            return new(UnitUtils.ConvertFromInternalUnits(value.Value, UnitTypeId.Degrees), "degrees");
        if (warn) _warnings.Add(new("unsupported_dimension_numeric_semantics",
            "Dimension numeric value was omitted because its semantics are unsupported or uncertain.", sourceId));
        return new(null, null);
    }

    private static T? Safe<T>(Func<T> getter) where T : class { try { return getter(); } catch { return null; } }
    private static JsonArray? SafePoint(Func<XYZ> getter)
    {
        try { return Vec(Units.Point(getter(), Transform.Identity)); } catch { return null; }
    }

    private void AddTagReferences(IndependentTag tag, string annotationId)
    {
        var index = 0;
        foreach (var target in tag.GetTaggedElementIds())
        {
            if (target.LinkedElementId != ElementId.InvalidElementId && _document.GetElement(target.HostElementId) is RevitLinkInstance link)
            {
                if (!_options.IncludeLinks)
                {
                    Add("annotation_references", ReferenceObject(annotationId, index++, null, null, null,
                        true, "unresolved", null));
                    _warnings.Add(new("linked_annotation_reference_omitted",
                        "Linked tag target was omitted because link export is disabled.", tag.UniqueId));
                    continue;
                }
                var linkedDocument = link.GetLinkDocument(); var linkedElement = linkedDocument?.GetElement(target.LinkedElementId);
                if (linkedDocument is not null && linkedElement is not null)
                {
                    var identity = DocumentIdentityResolver.Resolve(linkedDocument);
                    var modelId = StableIds.Hash("model", identity.Kind, identity.Value);
                    Add("annotation_references", ReferenceObject(annotationId, index++, modelId, linkedElement.UniqueId,
                        Id("link", _hostModelId, link.UniqueId), true, "resolved", null));
                    continue;
                }
            }
            if (target.HostElementId != ElementId.InvalidElementId)
            {
                var host = _document.GetElement(target.HostElementId);
                Add("annotation_references", ReferenceObject(annotationId, index++, host is null ? null : _hostModelId,
                    host?.UniqueId, null, false, host is null ? "unresolved" : "resolved", null));
            }
            else
            {
                Add("annotation_references", ReferenceObject(annotationId, index++, null, null, null, true, "unresolved", null));
                _warnings.Add(new("annotation_reference_unresolved", "Tag target could not be safely resolved.", tag.UniqueId));
            }
        }
    }

    private void AddReference(string annotationId, int index, Reference reference)
    {
        string? stable = null;
        try { stable = reference.ConvertToStableRepresentation(_document); }
        catch (Exception error) { _warnings.Add(new("stable_reference_unavailable", error.Message)); }
        try
        {
            if (reference.LinkedElementId != ElementId.InvalidElementId && _document.GetElement(reference.ElementId) is RevitLinkInstance link)
            {
                if (!_options.IncludeLinks)
                {
                    Add("annotation_references", ReferenceObject(annotationId, index, null, null, null,
                        true, "unresolved", stable));
                    _warnings.Add(new("linked_annotation_reference_omitted",
                        "Linked dimension target was omitted because link export is disabled."));
                    return;
                }
                var linkedDocument = link.GetLinkDocument(); var linkedElement = linkedDocument?.GetElement(reference.LinkedElementId);
                if (linkedDocument is not null && linkedElement is not null)
                {
                    var identity = DocumentIdentityResolver.Resolve(linkedDocument);
                    Add("annotation_references", ReferenceObject(annotationId, index,
                        StableIds.Hash("model", identity.Kind, identity.Value), linkedElement.UniqueId,
                        Id("link", _hostModelId, link.UniqueId), true, "resolved", stable));
                    return;
                }
                Add("annotation_references", ReferenceObject(annotationId, index, null, null,
                    Id("link", _hostModelId, link.UniqueId), true, "unresolved", stable));
                _warnings.Add(new("annotation_reference_unresolved", "Linked dimension target is unavailable."));
                return;
            }
            var target = _document.GetElement(reference.ElementId);
            Add("annotation_references", ReferenceObject(annotationId, index, target is null ? null : _hostModelId,
                target?.UniqueId, null, false, target is null ? "unresolved" : "resolved", stable));
            if (target is null) _warnings.Add(new("annotation_reference_unresolved", "Host dimension target is unavailable."));
        }
        catch (Exception error)
        {
            Add("annotation_references", ReferenceObject(annotationId, index, null, null, null,
                reference.LinkedElementId != ElementId.InvalidElementId, "unresolved", stable));
            _warnings.Add(new("annotation_reference_unresolved", error.Message));
        }
    }

    private void AddGeometry(Element element, string entityId, Transform transform, string? linkId)
    {
        try
        {
            var box = element.get_BoundingBox(null);
            if (box is not null)
            {
                var corners = new[]
                {
                    new XYZ(box.Min.X, box.Min.Y, box.Min.Z), new XYZ(box.Min.X, box.Min.Y, box.Max.Z),
                    new XYZ(box.Min.X, box.Max.Y, box.Min.Z), new XYZ(box.Min.X, box.Max.Y, box.Max.Z),
                    new XYZ(box.Max.X, box.Min.Y, box.Min.Z), new XYZ(box.Max.X, box.Min.Y, box.Max.Z),
                    new XYZ(box.Max.X, box.Max.Y, box.Min.Z), new XYZ(box.Max.X, box.Max.Y, box.Max.Z)
                }.Select(p => Units.Point(p, transform)).ToArray();
                var minimum = new XYZ(corners.Min(p => p.X), corners.Min(p => p.Y), corners.Min(p => p.Z));
                var maximum = new XYZ(corners.Max(p => p.X), corners.Max(p => p.Y), corners.Max(p => p.Z));
                AddGeometryRow(entityId, linkId, "bbox3d", new JsonObject { ["min"] = Vec(minimum), ["max"] = Vec(maximum) }, corners);
            }
            if (element.Location is LocationPoint point)
            {
                var p = Units.Point(point.Point, transform); AddGeometryRow(entityId, linkId, "point", new JsonObject { ["point"] = Vec(p) }, [p]);
            }
            else if (element.Location is LocationCurve location && location.Curve is Line line)
            {
                var a = Units.Point(line.GetEndPoint(0), transform); var b = Units.Point(line.GetEndPoint(1), transform);
                AddGeometryRow(entityId, linkId, "line", new JsonObject { ["start"] = Vec(a), ["end"] = Vec(b) }, [a, b]);
            }
        }
        catch (Exception error) { _warnings.Add(new("element_geometry_unavailable", error.Message, element.UniqueId)); }
    }

    private void AddGeometryRow(string entityId, string? linkId, string type, JsonObject geometry, IReadOnlyList<XYZ> points) =>
        Add("geometries", Obj(("id", StableIds.Hash("geometry", entityId, linkId ?? "host", type)), ("entity_kind", "element"),
            ("entity_id", entityId), ("link_instance_id", linkId), ("geometry_type", type), ("geometry", geometry),
            ("coordinate_system", SnapshotContract.CoordinateSystem), ("unit", "mm"), ("min_x", points.Min(p => p.X)),
            ("max_x", points.Max(p => p.X)), ("min_y", points.Min(p => p.Y)), ("max_y", points.Max(p => p.Y)),
            ("min_z", points.Min(p => p.Z)), ("max_z", points.Max(p => p.Z)), ("provenance", Provenance),
            ("confidence", null), ("evidence_id", null)));

    private void AddLevels(Document document, string modelId)
    {
        foreach (var level in new FilteredElementCollector(document).OfClass(typeof(Level)).Cast<Level>().OrderBy(x => x.UniqueId))
        {
            Add("levels", Obj(("id", Id("level", modelId, level.UniqueId)), ("name", level.Name), ("elevation", Units.Length(level.Elevation)),
                ("unit", "mm"), ("source_model_id", modelId), ("source_unique_id", level.UniqueId), ("provenance", Provenance), ("confidence", null)));
        }
    }

    private JsonObject SourceModel(Document document, string id, string role, DocumentIdentity identity)
    {
        string? guid = null; int? save = null;
        try
        {
            using var version = Document.GetDocumentVersion(document);
            guid = version.VersionGUID.ToString("D"); save = version.NumberOfSaves;
        }
        catch (Exception error) { _warnings.Add(new("document_version_unavailable", error.Message)); }
        return Obj(("id", id), ("role", role), ("title", document.Title), ("revit_version", document.Application.VersionNumber),
            ("model_identity_kind", identity.Kind), ("model_identity", identity.Value), ("snapshot_version_guid", guid), ("snapshot_save_number", save));
    }

    private static JsonObject TransformJson(Transform t) => new()
    {
        ["basis_x"] = Vec(t.BasisX), ["basis_y"] = Vec(t.BasisY), ["basis_z"] = Vec(t.BasisZ),
        ["origin"] = Vec(t.Origin), ["source_unit"] = "revit_internal"
    };
    private static JsonArray Vec(XYZ p) => new(p.X, p.Y, p.Z);
    private string? ResolveLevel(Element e, string modelId) => e.LevelId == ElementId.InvalidElementId || e.Document.GetElement(e.LevelId) is not Level l ? null : Id("level", modelId, l.UniqueId);
    private static string? PhaseUniqueId(Element e) => e.CreatedPhaseId == ElementId.InvalidElementId ? null : e.Document.GetElement(e.CreatedPhaseId)?.UniqueId;
    private static string CategoryName(Element e) => e.Category?.Name ?? "Uncategorized";
    private static string SafeValueString(Parameter p) { try { return p.AsValueString() ?? p.AsString() ?? p.AsElementId()?.Value.ToString() ?? ""; } catch { return ""; } }
    private static string ParameterDefinitionKey(Parameter parameter)
    {
        if (parameter.IsShared) return $"shared:{parameter.GUID:D}";
        var value = parameter.Id.Value;
        var name = value < 0 ? Enum.GetName(typeof(BuiltInParameter), (BuiltInParameter)value) : null;
        return name is null ? $"id:{value}" : $"builtin:{name}";
    }
    private static string Id(string kind, string model, string unique) => StableIds.For(kind, model, unique);
    private void Add(string collection, JsonObject value)
    {
        if (value["id"] is JsonNode id)
        {
            if (!_recordIds.TryGetValue(collection, out var ids)) _recordIds[collection] = ids = [];
            if (!ids.Add(id.ToString())) return;
        }
        Records[collection]!.AsArray().Add(value);
    }
    private void AddSearch(string kind, string id, string text)
    {
        if (!string.IsNullOrWhiteSpace(text) && _searchIds.Add($"{kind}\u001f{id}"))
            Add("search_content", Obj(("record_kind", kind), ("record_id", id), ("content", text)));
    }
    private int Count(string collection) => Records[collection]!.AsArray().Count;
    private void FlushElements() { foreach (var x in _types.OrderBy(x => x.Key)) Add("element_types", x.Value); foreach (var x in _elements.OrderBy(x => x.Key)) Add("elements", x.Value); }
    private void SortRecords() { foreach (var name in SnapshotContract.RecordCollections) { var a = Records[name]!.AsArray(); var sorted = a.OrderBy(x => x?["export_order"]?.GetValue<int>() ?? int.MaxValue).ThenBy(x => x?["id"]?.ToString() ?? x?["record_id"]?.ToString(), StringComparer.Ordinal).ToList(); a.Clear(); foreach (var x in sorted) a.Add(x); } }
    private static JsonObject Obj(params (string Key, object? Value)[] values) { var o = new JsonObject(); foreach (var (key, value) in values) o[key] = value is JsonNode node ? node : JsonValue.Create(value); return o; }
    private JsonObject ReferenceObject(string annotationId, int index, string? model, string? unique, string? link, bool linked, string state, string? stable) =>
        Obj(("id", StableIds.Hash("annotation-reference", annotationId, index.ToString())), ("annotation_id", annotationId), ("reference_index", index),
            ("target_source_model_id", model), ("target_source_unique_id", unique), ("target_link_instance_id", link), ("stable_reference", stable),
            ("reference_type", null), ("is_linked", linked ? 1 : 0), ("resolution_state", state));
    private static string SafeName(string value) => string.Concat(value.Select(c => Path.GetInvalidFileNameChars().Contains(c) ? '_' : c));
}

internal sealed record NormalizedDimension(double? Value, string? Unit);

internal sealed record DocumentIdentity(string Kind, string Value);
internal static class DocumentIdentityResolver
{
    public static DocumentIdentity Resolve(Document document)
    {
        if (document.IsModelInCloud)
        {
            var cloud = document.GetCloudModelPath();
            return new("cloud_model_guid", $"{cloud.GetProjectGUID():D}/{cloud.GetModelGUID():D}");
        }
        if (document.IsWorkshared)
        {
            var central = document.GetWorksharingCentralModelPath();
            if (central is not null) return new("central_model_path", Normalize(ModelPathUtils.ConvertModelPathToUserVisiblePath(central)));
        }
        if (!string.IsNullOrWhiteSpace(document.PathName)) return new("saved_path", Normalize(document.PathName));
        var guid = document.CreationGUID;
        if (guid != Guid.Empty) return new("creation_guid", guid.ToString("D"));
        throw new InvalidOperationException("A stable source document identity could not be determined. Save the model and retry.");
    }
    private static string Normalize(string path) => Path.GetFullPath(path).Replace('\\', '/').TrimEnd('/').ToLowerInvariant();
}

internal static class Units
{
    public static double Length(double feet) => UnitUtils.ConvertFromInternalUnits(feet, UnitTypeId.Millimeters);
    public static XYZ Point(XYZ p, Transform transform) { var x = transform.OfPoint(p); return new XYZ(Length(x.X), Length(x.Y), Length(x.Z)); }
}

internal sealed record NormalizedParameter(double? Value, string? Unit, string? Text);
internal static class ParameterNormalizer
{
    public static NormalizedParameter Normalize(Parameter parameter, ForgeTypeId spec)
    {
        if (parameter.StorageType == StorageType.String) return new(null, null, parameter.AsString());
        if (parameter.StorageType == StorageType.Integer)
        {
            var integer = parameter.AsInteger();
            if (spec == SpecTypeId.Boolean.YesNo) return new(null, null, integer == 0 ? "false" : "true");
            return new(integer, "number", integer.ToString(System.Globalization.CultureInfo.InvariantCulture));
        }
        if (parameter.StorageType == StorageType.ElementId) return new(null, null, parameter.AsElementId().Value.ToString());
        if (parameter.StorageType != StorageType.Double) return new(null, null, Safe(parameter));
        var value = parameter.AsDouble();
        if (spec == SpecTypeId.Length) return new(UnitUtils.ConvertFromInternalUnits(value, UnitTypeId.Millimeters), "mm", Safe(parameter));
        if (spec == SpecTypeId.Area) return new(UnitUtils.ConvertFromInternalUnits(value, UnitTypeId.SquareMeters), "m2", Safe(parameter));
        if (spec == SpecTypeId.Volume) return new(UnitUtils.ConvertFromInternalUnits(value, UnitTypeId.CubicMeters), "m3", Safe(parameter));
        if (spec == SpecTypeId.Angle) return new(UnitUtils.ConvertFromInternalUnits(value, UnitTypeId.Degrees), "degrees", Safe(parameter));
        return new(null, null, Safe(parameter));
    }
    private static string Safe(Parameter p) { try { return p.AsValueString() ?? ""; } catch { return ""; } }
}
