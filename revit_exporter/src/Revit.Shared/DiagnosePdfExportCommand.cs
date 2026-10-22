using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using LlmKnowledgeBase.Revit.Core;
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.ExceptionServices;
using System.Text.Json.Nodes;

namespace LlmKnowledgeBase.Revit;

[Transaction(TransactionMode.Manual)]
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
            var logError = error.Data["pdf_diagnostic_log_error"];
            var transactionError = error.Data["pdf_diagnostic_transaction_error"];
            message = $"{error.Message}\nDiagnostic report: {report ?? "not written"}";
            TaskDialog.Show("PDF Export Diagnostics failed", $"{error}\n\nDiagnostic report: {report ?? "not written"}"
                + (logError is null ? "" : $"\n\nDiagnostic log could not be written:\n{logError}")
                + (transactionError is null ? "" : $"\n\nTransaction cleanup failed:\n{transactionError}"));
            return Result.Failed;
        }
    }
}

/// <summary>One real, synchronous Document.Export call; no package creation or document writes.</summary>
internal static class PdfExportDiagnostics
{
    public static string Run(Document document)
    {
        var elapsed = Stopwatch.StartNew();
        var phaseElapsed = new Stopwatch();
        var stage = "input_validation";
        Exception? failure = null;
        var root = Environment.GetEnvironmentVariable("LLM_KB_EXPORT_ROOT");
        var baseDirectory = string.IsNullOrWhiteSpace(root)
            ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "LlmKnowledgeBaseExports")
            : Path.GetFullPath(root);
        var folder = Path.Combine(baseDirectory, "pdf-diagnostics",
            $"{DateTimeOffset.UtcNow:yyyyMMddTHHmmssfffZ}-{Guid.NewGuid():N}");
        Directory.CreateDirectory(folder);
        var reportPath = Path.Combine(folder, "pdf_diagnostic_report.json");
        var eventsPath = Path.Combine(folder, "diagnostic_events.jsonl");
        var expectedPdf = Path.Combine(folder, "drawing.pdf");
        var sheetInput = Environment.GetEnvironmentVariable("LLM_KB_PDF_DIAG_SHEET_NUMBERS");
        var modeInput = Environment.GetEnvironmentVariable("LLM_KB_PDF_DIAG_MODE");
        var setupInput = Environment.GetEnvironmentVariable("LLM_KB_PDF_DIAG_SETUP_NAME");
        var timings = new JsonObject
        {
            ["sheet_acquisition_ms"] = null,
            ["pdf_options_ms"] = null,
            ["pdf_export_ms"] = null,
            ["diagnostic_total_ms"] = null
        };
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
        var transactionReport = new JsonObject
        {
            ["mode"] = "Manual",
            ["initial_status"] = null,
            ["start_return_status"] = null,
            ["status_after_start"] = null,
            ["status_before_rollback"] = null,
            ["rollback_attempted"] = false,
            ["rollback_return_status"] = null,
            ["end_status"] = null,
            ["rollback_succeeded"] = false,
            ["dispose_succeeded"] = false,
            ["exception"] = null,
            ["cleanup_exceptions"] = new JsonArray()
        };
        report["transaction"] = transactionReport;
        report["timings_ms"] = timings;
        try
        {
            LocalExportLog.AppendEvent(eventsPath, "diagnostic_started", stage,
                new { revit_version = document.Application.VersionNumber });
            var request = PdfDiagnosticRequest.Parse(sheetInput, modeInput, setupInput);
            stage = "sheet_acquisition";
            phaseElapsed.Restart();
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
            phaseElapsed.Stop();
            timings["sheet_acquisition_ms"] = phaseElapsed.Elapsed.TotalMilliseconds;
            LocalExportLog.AppendEvent(eventsPath, "sheets_selected", stage,
                new { sheet_count = sheets.Count, elapsed_ms = phaseElapsed.Elapsed.TotalMilliseconds });

            stage = "pdf_options";
            phaseElapsed.Restart();
            using var options = GetOptions(document, request, report);
            report["options_before_overrides"] = DescribeOptions(options);
            // GetOptions returns a copy and does not serialize FileName (Revit 2025 API).
            options.Combine = true;
            options.FileName = "drawing";
            options.SetExportInBackground(false);
            report["options"] = DescribeOptions(options);
            if (options.GetExportInBackground())
                throw new InvalidOperationException("PDF diagnostics require synchronous export.");
            phaseElapsed.Stop();
            timings["pdf_options_ms"] = phaseElapsed.Elapsed.TotalMilliseconds;
            LocalExportLog.AppendEvent(eventsPath, "pdf_options_ready", stage, new
            {
                mode = request.Mode,
                options.Combine,
                options.FileName,
                paper_format = options.PaperFormat.ToString(),
                orientation = options.PaperOrientation.ToString(),
                zoom_type = options.ZoomType.ToString(),
                options.AlwaysUseRaster,
                background = options.GetExportInBackground(),
                elapsed_ms = phaseElapsed.Elapsed.TotalMilliseconds
            });
            var ids = sheets.Select(sheet => sheet.Id).ToList();
            Transaction? transaction = null;
            Exception? transactionFailure = null;
            var transactionStarted = false;
            try
            {
                stage = "transaction_start";
                LocalExportLog.AppendEvent(eventsPath, "transaction_starting", stage);
                transaction = new Transaction(document, "Diagnose PDF Export (rollback only)");
                transactionReport["initial_status"] = transaction.GetStatus().ToString();
                var startStatus = transaction.Start();
                transactionStarted = startStatus == TransactionStatus.Started;
                transactionReport["start_return_status"] = startStatus.ToString();
                var statusAfterStart = transaction.GetStatus();
                transactionReport["status_after_start"] = statusAfterStart.ToString();
                LocalExportLog.AppendEvent(eventsPath, "transaction_started", stage,
                    new { start_return_status = startStatus.ToString(), status = statusAfterStart.ToString() });
                if (!transactionStarted || statusAfterStart != TransactionStatus.Started)
                    throw new InvalidOperationException("PDF diagnostic transaction did not start.");

                stage = "pdf_export";
                // AppendEvent writes, Flush(true)s and closes before the one API call.
                LocalExportLog.AppendEvent(eventsPath, "pdf_export_started", stage,
                    new { sheet_count = sheets.Count });
                report["export_called"] = true;
                phaseElapsed.Restart();
                bool exported;
                try
                {
                    exported = document.Export(folder, ids, options);
                }
                finally
                {
                    phaseElapsed.Stop();
                    timings["pdf_export_ms"] = phaseElapsed.Elapsed.TotalMilliseconds;
                }
                report["export_return_value"] = exported;
                if (!exported) throw new InvalidOperationException("Revit PDF export returned false.");
                stage = "pdf_export_result_logging";
                LocalExportLog.AppendEvent(eventsPath, "pdf_export_completed", "pdf_export",
                    new { export_return_value = exported, elapsed_ms = phaseElapsed.Elapsed.TotalMilliseconds });
            }
            catch (Exception error)
            {
                transactionFailure = error;
                transactionReport["exception"] = DescribeException(error);
                // Preserve the failed-call event before cleanup enters Revit again.
                if (stage == "pdf_export" && report["export_called"]!.GetValue<bool>())
                    LocalExportLog.WriteBestEffort(error, "pdf_diagnostic_log_error", () =>
                        LocalExportLog.AppendEvent(eventsPath, "pdf_export_failed", stage, new
                        {
                            exception_type = error.GetType().FullName,
                            export_return_value = report["export_return_value"]?.GetValue<bool>(),
                            elapsed_ms = phaseElapsed.Elapsed.TotalMilliseconds
                        }));
                throw;
            }
            finally
            {
                // Cleanup and its logging are independent; neither may mask the original exception.
                var cleanupError = RollBackAndDispose(transaction, transactionStarted,
                    transactionReport, eventsPath, transactionFailure);
                if (transactionFailure is null && cleanupError is not null)
                {
                    stage = "transaction_rollback";
                    ExceptionDispatchInfo.Capture(cleanupError).Throw();
                }
            }
            stage = "pdf_validation";
            if (!File.Exists(expectedPdf) || new FileInfo(expectedPdf).Length == 0)
                throw new InvalidOperationException("Revit reported success but drawing.pdf is missing or empty.");
            report["successful"] = true;
        }
        catch (Exception error)
        {
            failure = error;
            phaseElapsed.Stop();
            if (stage is "sheet_acquisition" or "pdf_options")
                timings[stage + "_ms"] = phaseElapsed.Elapsed.TotalMilliseconds;
            report["successful"] = false;
            report["exception"] = DescribeException(error);
            error.Data["pdf_diagnostic_report"] = reportPath;
            throw;
        }
        finally
        {
            report["finished_at"] = DateTimeOffset.UtcNow.ToString("O");
            report["stage"] = stage;
            report["expected_pdf_exists"] = File.Exists(expectedPdf);
            report["pdf_files"] = new JsonArray();
            // Finish both logs independently; a logging failure must not replace an export failure.
            var reportError = LocalExportLog.WriteBestEffort(failure, "pdf_diagnostic_log_error", () =>
            {
                foreach (var file in Directory.EnumerateFiles(folder, "*.pdf").OrderBy(path => path, StringComparer.Ordinal))
                    report["pdf_files"]!.AsArray().Add(new JsonObject
                    {
                        ["file_name"] = Path.GetFileName(file),
                        ["size_bytes"] = new FileInfo(file).Length
                    });
                timings["diagnostic_total_ms"] = elapsed.Elapsed.TotalMilliseconds;
                report["logging_error"] = failure?.Data["pdf_diagnostic_log_error"] as string;
                ExportFiles.WriteJson(reportPath, report);
            });
            elapsed.Stop();
            var finishError = LocalExportLog.WriteBestEffort(failure ?? reportError, "pdf_diagnostic_log_error", () =>
                LocalExportLog.AppendEvent(eventsPath, "diagnostic_finished", stage, new
                {
                    successful = report["successful"]!.GetValue<bool>() && reportError is null,
                    exception_type = (failure ?? reportError)?.GetType().FullName,
                    elapsed_ms = elapsed.Elapsed.TotalMilliseconds
                }));
            if (failure is null && (reportError ?? finishError) is Exception loggingError)
            {
                var error = new IOException($"Could not complete PDF diagnostic logs in {folder}.", loggingError);
                error.Data["pdf_diagnostic_report"] = reportPath;
                throw error;
            }
        }
        return reportPath;
    }

    private static Exception? RollBackAndDispose(Transaction? transaction, bool started,
        JsonObject report, string eventsPath, Exception? originalFailure)
    {
        if (transaction is null) return null;
        Exception? cleanupFailure = null;
        // Each step is attempted even when an earlier status read or log write failed.
        void Attempt(string operation, Action action)
        {
            var error = LocalExportLog.WriteBestEffort(originalFailure ?? cleanupFailure,
                "pdf_diagnostic_transaction_error", action);
            if (error is null) return;
            cleanupFailure ??= error;
            report["cleanup_exceptions"]!.AsArray().Add(new JsonObject
            {
                ["operation"] = operation,
                ["exception"] = DescribeException(error)
            });
        }

        TransactionStatus? beforeRollback = null;
        Attempt("status_before_rollback", () =>
        {
            beforeRollback = transaction.GetStatus();
            report["status_before_rollback"] = beforeRollback.ToString();
        });
        // RollBack is legal only for Started. If a status read fails after Start
        // succeeded, still attempt RollBack rather than letting logging skip cleanup.
        if (beforeRollback == TransactionStatus.Started || (started && beforeRollback is null))
        {
            Attempt("rollback_started_event", () =>
                LocalExportLog.AppendEvent(eventsPath, "transaction_rollback_started", "transaction_rollback"));
            report["rollback_attempted"] = true;
            Attempt("rollback", () =>
            {
                var result = transaction.RollBack();
                report["rollback_return_status"] = result.ToString();
                if (result != TransactionStatus.RolledBack)
                    throw new InvalidOperationException($"PDF diagnostic RollBack returned {result}.");
            });
        }
        else
        {
            report["rollback_skipped_reason"] = beforeRollback?.ToString() ?? "status_unavailable";
        }
        Attempt("end_status", () =>
        {
            var endStatus = transaction.GetStatus();
            report["end_status"] = endStatus.ToString();
            report["rollback_succeeded"] = endStatus == TransactionStatus.RolledBack
                && report["rollback_return_status"]?.GetValue<string>() == "RolledBack";
            if (started && !report["rollback_succeeded"]!.GetValue<bool>())
                throw new InvalidOperationException($"PDF diagnostic rollback was not confirmed; status is {endStatus}.");
        });
        Attempt("dispose", () =>
        {
            transaction.Dispose();
            report["dispose_succeeded"] = true;
        });
        Attempt("transaction_finished_event", () =>
            LocalExportLog.AppendEvent(eventsPath, "transaction_finished", "transaction_rollback", new
            {
                status_before_rollback = report["status_before_rollback"]?.GetValue<string>(),
                rollback_attempted = report["rollback_attempted"]!.GetValue<bool>(),
                rollback_return_status = report["rollback_return_status"]?.GetValue<string>(),
                end_status = report["end_status"]?.GetValue<string>(),
                rollback_succeeded = report["rollback_succeeded"]!.GetValue<bool>(),
                dispose_succeeded = report["dispose_succeeded"]!.GetValue<bool>(),
                exception_type = (originalFailure ?? cleanupFailure)?.GetType().FullName,
                cleanup_exception_type = cleanupFailure?.GetType().FullName
            }));
        return cleanupFailure;
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
