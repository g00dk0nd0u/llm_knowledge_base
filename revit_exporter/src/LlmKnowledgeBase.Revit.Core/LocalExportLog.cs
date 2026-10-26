using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.Json;

namespace LlmKnowledgeBase.Revit.Core;

/// <summary>Small synchronous local logs; no Autodesk, network, or background worker dependencies.</summary>
public static class LocalExportLog
{
    private static readonly JsonSerializerOptions EventOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower
    };

    public static void AppendEvent(string path, string eventName, string stage, object? data = null)
    {
        var line = JsonSerializer.Serialize(new
        {
            utc_timestamp = DateTimeOffset.UtcNow.ToString("O"),
            @event = eventName,
            stage,
            data
        }, EventOptions) + "\n";
        var bytes = new UTF8Encoding(false).GetBytes(line);
        using var stream = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.Read);
        stream.Write(bytes);
        // Complete this write before returning to the caller, especially before Document.Export.
        // This improves survival of process termination, not a guarantee against OS/storage failure.
        stream.Flush(flushToDisk: true);
    }

    /// <summary>Logging failures must not replace a failure already being propagated.</summary>
    public static Exception? WriteBestEffort(Exception? originalError, string failureKey, Action write)
    {
        try
        {
            write();
            return null;
        }
        catch (Exception loggingError)
        {
            try
            {
                if (originalError is not null)
                {
                    var previous = originalError.Data[failureKey] as string;
                    originalError.Data[failureKey] = (previous is null ? "" : previous + "\n") + loggingError;
                }
            }
            catch (Exception)
            {
                // Even diagnostic metadata attachment must not hide the original exception.
            }
            return loggingError;
        }
    }

    public static bool TryWriteFailure(string path, string stage, Exception error,
        double elapsedMs, string? revitVersion, IReadOnlyList<ExportWarning> warnings) =>
        WriteBestEffort(error, "export_failure_log_error", () => ExportFiles.WriteJson(path, new
        {
            successful = false,
            warnings,
            stage,
            exception_type = error.GetType().FullName,
            exception_message = error.Message,
            stack_trace = error.StackTrace,
            elapsed_ms = elapsedMs,
            revit_version = revitVersion
        })) is null;
}
