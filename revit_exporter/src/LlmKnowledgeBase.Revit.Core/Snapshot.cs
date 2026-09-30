using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace LlmKnowledgeBase.Revit.Core;

public sealed record ExportWarning(string Code, string Message, string? SourceId = null);

public sealed record ExportManifest(
    string ExporterVersion,
    string RevitVersion,
    string HostModel,
    DateTimeOffset ExportTimestamp,
    string DrawingPdf,
    string DrawingPdfSha256,
    string SnapshotFile,
    int SheetCount,
    int ElementCount,
    int SpaceCount,
    int AnnotationCount,
    int GeometryCount,
    IReadOnlyList<ExportWarning> Warnings)
{
    public int WarningCount => Warnings.Count;
}

public static class SnapshotContract
{
    public const int Version = 1;
    public const string CoordinateSystem = "host_revit_internal_origin";
    public const string Unit = "mm";
    public static readonly string[] RecordCollections =
    [
        "documents", "source_models", "link_instances", "sheets", "views",
        "viewports", "levels", "spaces", "element_types", "elements", "evidence",
        "parameters", "relationships", "annotations", "annotation_segments",
        "annotation_references", "entity_appearances", "spatial_boundaries",
        "spatial_boundary_segments", "geometries", "drawing_references", "search_content"
    ];

    public static JsonObject Create(string projectId, string createdFrom,
        string sourceIdentity, string drawingSha256)
    {
        if (drawingSha256.Length != 64 || drawingSha256.Any(c => !Uri.IsHexDigit(c)))
            throw new ArgumentException("A hexadecimal SHA-256 is required.", nameof(drawingSha256));
        var records = new JsonObject();
        foreach (var name in RecordCollections) records[name] = new JsonArray();
        return new JsonObject
        {
            ["snapshot_version"] = Version,
            ["coordinate_system"] = CoordinateSystem,
            ["unit"] = Unit,
            ["project"] = new JsonObject
            {
                ["project_id"] = projectId,
                ["created_from"] = createdFrom,
                ["source_document_identity"] = sourceIdentity,
                ["source_document_sha256"] = drawingSha256.ToLowerInvariant()
            },
            ["records"] = records
        };
    }
}

public static class StableIds
{
    public static string For(string kind, string sourceModelId, string uniqueId) =>
        $"{kind}:{sourceModelId}:{uniqueId}";
    public static string Hash(string kind, params string[] components)
    {
        var bytes = SHA256.HashData(Encoding.UTF8.GetBytes(string.Join("\u001f", components)));
        return $"{kind}:{Convert.ToHexString(bytes).ToLowerInvariant()[..24]}";
    }

    public static string Document(string identityKind, string identityValue) =>
        Hash("document", identityKind, identityValue);

    public static string ReferenceEvidence(string sourceModelId, string markerUniqueId,
        string sourceSheetUniqueId) =>
        Hash("evidence", sourceModelId, markerUniqueId, sourceSheetUniqueId);

    public static string DrawingReference(string sourceModelId, string markerUniqueId,
        string sourceSheetUniqueId, string relationType) =>
        Hash("drawing-reference", sourceModelId, markerUniqueId, sourceSheetUniqueId, relationType);
}

public enum DimensionSemantic
{
    Unsupported,
    Linear,
    Angular,
    SpotElevation,
    SpotCoordinate
}

public sealed record DimensionValueSemantics(string? Unit, bool Supported);

public static class DimensionValues
{
    public static DimensionValueSemantics Describe(DimensionSemantic semantic) =>
        semantic switch
        {
            DimensionSemantic.Linear or DimensionSemantic.SpotElevation or DimensionSemantic.SpotCoordinate => new("mm", true),
            DimensionSemantic.Angular => new("degrees", true),
            _ => new(null, false)
        };
}

public static class ExportFiles
{
    public static string Sha256(string path)
    {
        using var stream = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
    }

    public static void WriteJson(string path, object value)
    {
        var options = new JsonSerializerOptions
        {
            PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
            WriteIndented = true
        };
        File.WriteAllText(path, JsonSerializer.Serialize(value, options) + Environment.NewLine,
            new UTF8Encoding(false));
    }
}
