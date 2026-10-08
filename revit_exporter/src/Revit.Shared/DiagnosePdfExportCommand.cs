using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using LlmKnowledgeBase.Revit.Core;
using System;
using System.IO;
using System.Linq;
using System.Text.Json.Nodes;

namespace LlmKnowledgeBase.Revit;

[Transaction(TransactionMode.ReadOnly)]
public sealed class DiagnosePdfExportCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
    {
        try
        {
            var document = data.Application.ActiveUIDocument?.Document
                ?? throw new InvalidOperationException("Open a project before diagnosing PDF export.");
            var report = PdfExportDiagnostics.Run(document);
            TaskDialog.Show("PDF Export Diagnostics", $"Diagnostic export completed (not a knowledge package).\nReport: {report}");
            return Result.Succeeded;
        }
        catch (Exception error)
        {
            var report = error.Data["pdf_diagnostic_report"];
            message = $"{error.Message}\nDiagnostic report: {report ?? "not written"}";
            TaskDialog.Show("PDF Export Diagnostics failed", $"{error}\n\nDiagnostic report: {report ?? "not written"}");
            return Result.Failed;
        }
    }
}

/// <summary>One real, synchronous Document.Export call; no package creation or document writes.</summary>
internal static class PdfExportDiagnostics
{
    public static string Run(Document document)
    {
        var root = Environment.GetEnvironmentVariable("LLM_KB_EXPORT_ROOT");
        var baseDirectory = string.IsNullOrWhiteSpace(root)
            ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "LlmKnowledgeBaseExports")
            : Path.GetFullPath(root);
        var folder = Path.Combine(baseDirectory, "pdf-diagnostics",
            $"{DateTimeOffset.UtcNow:yyyyMMddTHHmmssfffZ}-{Guid.NewGuid():N}");
        Directory.CreateDirectory(folder);
        var reportPath = Path.Combine(folder, "pdf_diagnostic_report.json");
        var expectedPdf = Path.Combine(folder, "drawing.pdf");
        var sheetInput = Environment.GetEnvironmentVariable("LLM_KB_PDF_DIAG_SHEET_NUMBERS");
        var modeInput = Environment.GetEnvironmentVariable("LLM_KB_PDF_DIAG_MODE");
        var setupInput = Environment.GetEnvironmentVariable("LLM_KB_PDF_DIAG_SETUP_NAME");
        var report = new JsonObject
        {
            ["diagnostic_only"] = true,
            ["successful"] = false,
            ["started_at"] = DateTimeOffset.UtcNow.ToString("O"),
            ["revit_version"] = document.Application.VersionNumber,
            ["revit_version_build"] = document.Application.VersionBuild,
            ["requested_sheet_numbers"] = sheetInput,
            ["mode"] = modeInput,
            ["requested_setup_name"] = setupInput,
            ["output_folder"] = folder,
            ["sheets"] = new JsonArray(),
            ["sheet_count"] = 0,
            ["options_source"] = null,
            ["setup_name"] = null,
            ["options_before_overrides"] = null,
            ["options"] = null,
            ["export_called"] = false,
            ["export_return_value"] = null,
            ["exception"] = null
        };
        try
        {
            var request = PdfDiagnosticRequest.Parse(sheetInput, modeInput, setupInput);
            using var collector = new FilteredElementCollector(document);
            var sheets = request.SelectSheets(collector.OfClass(typeof(ViewSheet)).Cast<ViewSheet>(),
                sheet => sheet.SheetNumber, sheet => !sheet.IsPlaceholder && sheet.CanBePrinted);
            var sheetRecords = report["sheets"]!.AsArray();
            for (var index = 0; index < sheets.Count; index++)
                sheetRecords.Add(new JsonObject
                {
                    ["order"] = index + 1,
                    ["sheet_number"] = sheets[index].SheetNumber,
                    ["element_id"] = sheets[index].Id.Value
                });
            report["sheet_count"] = sheets.Count;

            using var options = GetOptions(document, request, report);
            report["options_before_overrides"] = DescribeOptions(options);
            // GetOptions returns a copy and does not serialize FileName (Revit 2025 API).
            options.Combine = true;
            options.FileName = "drawing";
            options.SetExportInBackground(false);
            report["options"] = DescribeOptions(options);
            if (options.GetExportInBackground())
                throw new InvalidOperationException("PDF diagnostics require synchronous export.");
            var ids = sheets.Select(sheet => sheet.Id).ToList();
            report["export_called"] = true;
            var exported = document.Export(folder, ids, options);
            report["export_return_value"] = exported;
            if (!exported) throw new InvalidOperationException("Revit PDF export returned false.");
            if (!File.Exists(expectedPdf) || new FileInfo(expectedPdf).Length == 0)
                throw new InvalidOperationException("Revit reported success but drawing.pdf is missing or empty.");
            report["successful"] = true;
        }
        catch (Exception error)
        {
            report["successful"] = false;
            report["exception"] = DescribeException(error);
            error.Data["pdf_diagnostic_report"] = reportPath;
            throw;
        }
        finally
        {
            report["finished_at"] = DateTimeOffset.UtcNow.ToString("O");
            report["expected_pdf_exists"] = File.Exists(expectedPdf);
            report["pdf_files"] = new JsonArray();
            // A reporting failure must also fail the command, retaining the export exception.
            try
            {
                foreach (var file in Directory.EnumerateFiles(folder, "*.pdf").OrderBy(path => path, StringComparer.Ordinal))
                    report["pdf_files"]!.AsArray().Add(new JsonObject
                    {
                        ["file_name"] = Path.GetFileName(file),
                        ["size_bytes"] = new FileInfo(file).Length
                    });
                ExportFiles.WriteJson(reportPath, report);
            }
            catch (Exception reportError)
            {
                throw new IOException($"Could not complete PDF diagnostic report at {reportPath}. "
                    + $"Original export exception: {report["exception"]}", reportError);
            }
        }
        return reportPath;
    }

    private static PDFExportOptions GetOptions(Document document, PdfDiagnosticRequest request, JsonObject report)
    {
        report["setup_name"] = request.SetupName;
        if (request.Mode == "default")
        {
            report["options_source"] = "new PDFExportOptions()";
            return new PDFExportOptions();
        }
        report["options_source"] = "ExportPDFSettings.GetOptions()";
        var names = ExportPDFSettings.ListNames(document).OrderBy(name => name, StringComparer.Ordinal).ToArray();
        report["available_setup_names"] = new JsonArray(names.Select(name => (JsonNode?)JsonValue.Create(name)).ToArray());
        if (!names.Contains(request.SetupName, StringComparer.Ordinal))
            throw new InvalidOperationException($"PDF setup '{request.SetupName}' was not found. Available settings: {string.Join(", ", names)}");
        var settings = ExportPDFSettings.FindByName(document, request.SetupName!)
            ?? throw new InvalidOperationException($"PDF setup '{request.SetupName}' could not be retrieved.");
        return settings.GetOptions();
    }

    private static JsonObject DescribeException(Exception error) => new()
    {
        ["type"] = error.GetType().FullName,
        ["message"] = error.Message,
        ["stack_trace"] = error.StackTrace,
        ["details"] = error.ToString()
    };

    private static JsonObject DescribeOptions(PDFExportOptions options) => new()
    {
        ["combine"] = options.Combine,
        ["file_name"] = options.FileName,
        ["paper_format"] = options.PaperFormat.ToString(),
        ["orientation"] = options.PaperOrientation.ToString(),
        ["paper_placement"] = options.PaperPlacement.ToString(),
        ["origin_offset_x"] = options.OriginOffsetX,
        ["origin_offset_y"] = options.OriginOffsetY,
        ["zoom_type"] = options.ZoomType.ToString(),
        ["zoom_percentage"] = options.ZoomPercentage,
        ["always_use_raster"] = options.AlwaysUseRaster,
        ["processing"] = options.AlwaysUseRaster ? "raster" : "vector_when_possible",
        ["raster_quality"] = options.RasterQuality.ToString(),
        ["export_quality"] = options.ExportQuality.ToString(),
        ["color_depth"] = options.ColorDepth.ToString(),
        ["background"] = options.GetExportInBackground(),
        ["stop_on_error"] = options.StopOnError,
        ["hide_crop_boundaries"] = options.HideCropBoundaries,
        ["hide_reference_plane"] = options.HideReferencePlane,
        ["hide_scope_boxes"] = options.HideScopeBoxes,
        ["hide_unreferenced_view_tags"] = options.HideUnreferencedViewTags,
        ["mask_coincident_lines"] = options.MaskCoincidentLines,
        ["replace_halftone_with_thin_lines"] = options.ReplaceHalftoneWithThinLines,
        ["view_links_in_blue"] = options.ViewLinksInBlue
    };
}
