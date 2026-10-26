using System;
using System.IO;
using System.Text.Json;
using LlmKnowledgeBase.Revit.Core;
using Xunit;

public sealed class LocalExportLogTests : IDisposable
{
    private readonly string _folder = Path.Combine(Path.GetTempPath(), "export-log-tests-" + Guid.NewGuid());

    public LocalExportLogTests() => Directory.CreateDirectory(_folder);
    public void Dispose() => Directory.Delete(_folder, recursive: true);

    [Fact]
    public void AppendedEventsAreImmediatelyReadableOrderedUtf8JsonLines()
    {
        var path = Path.Combine(_folder, "diagnostic_events.jsonl");
        LocalExportLog.AppendEvent(path, "diagnostic_started", "input_validation");
        Assert.Single(File.ReadAllLines(path));
        LocalExportLog.AppendEvent(path, "sheets_selected", "sheet_acquisition",
            new { sheet_count = 2, elapsed_ms = 1.25 });
        var lines = File.ReadAllLines(path);
        Assert.Equal(2, lines.Length);
        Assert.NotEqual((byte)0xEF, File.ReadAllBytes(path)[0]);
        using var first = JsonDocument.Parse(lines[0]);
        using var second = JsonDocument.Parse(lines[1]);
        Assert.Equal("diagnostic_started", first.RootElement.GetProperty("event").GetString());
        Assert.Equal("sheet_acquisition", second.RootElement.GetProperty("stage").GetString());
        Assert.Equal(2, second.RootElement.GetProperty("data").GetProperty("sheet_count").GetInt32());
        Assert.Equal(1.25, second.RootElement.GetProperty("data").GetProperty("elapsed_ms").GetDouble());
        Assert.Equal(TimeSpan.Zero, DateTimeOffset.Parse(
            first.RootElement.GetProperty("utc_timestamp").GetString()!).Offset);
    }

    [Fact]
    public void EmbeddedLineBreaksCannotCreateAdditionalJsonLines()
    {
        var path = Path.Combine(_folder, "events.jsonl");
        LocalExportLog.AppendEvent(path, "event", "stage", new { value = "日本語\nsecond line" });
        var line = Assert.Single(File.ReadAllLines(path));
        using var record = JsonDocument.Parse(line);
        Assert.Equal("日本語\nsecond line", record.RootElement.GetProperty("data").GetProperty("value").GetString());
    }

    [Fact]
    public void FailureReportRetainsExistingWarningsAndOperationalFailureFields()
    {
        var path = Path.Combine(_folder, "export_failure.json");
        var error = CaptureFailure();
        Assert.True(LocalExportLog.TryWriteFailure(path, "pdf_export", error, 12.5, "2025",
            new[] { new ExportWarning("test_warning", "test warning") }));
        using var report = JsonDocument.Parse(File.ReadAllText(path));
        var root = report.RootElement;
        Assert.False(root.GetProperty("successful").GetBoolean());
        Assert.Single(root.GetProperty("warnings").EnumerateArray());
        Assert.Equal("pdf_export", root.GetProperty("stage").GetString());
        Assert.Equal(typeof(InvalidOperationException).FullName, root.GetProperty("exception_type").GetString());
        Assert.Equal("original failure", root.GetProperty("exception_message").GetString());
        Assert.Equal(error.StackTrace, root.GetProperty("stack_trace").GetString());
        Assert.Equal(12.5, root.GetProperty("elapsed_ms").GetDouble());
        Assert.Equal("2025", root.GetProperty("revit_version").GetString());
    }

    [Fact]
    public void FileWriteFailureCannotReplaceTheOriginalExceptionOrItsStack()
    {
        var original = CaptureFailure();
        var originalStack = original.StackTrace;
        var path = Path.Combine(_folder, "missing-directory", "export_failure.json");
        Assert.False(LocalExportLog.TryWriteFailure(path, "write_snapshot", original, 0, "2025",
            Array.Empty<ExportWarning>()));
        Assert.Equal(originalStack, original.StackTrace);
        Assert.Contains(nameof(DirectoryNotFoundException), original.Data["export_failure_log_error"]!.ToString());
        Assert.Equal("original failure", original.Message);
    }

    [Fact]
    public void MultipleLoggingFailuresRemainSecondaryAndInspectable()
    {
        var original = CaptureFailure();
        var first = new IOException("first logging failure");
        var second = new IOException("second logging failure");
        Assert.Same(first, LocalExportLog.WriteBestEffort(original, "logging_error", () => throw first));
        Assert.Same(second, LocalExportLog.WriteBestEffort(original, "logging_error", () => throw second));
        var secondary = original.Data["logging_error"]!.ToString()!;
        Assert.Contains(first.Message, secondary);
        Assert.Contains(second.Message, secondary);
        Assert.Equal("original failure", original.Message);
    }

    [Fact]
    public void LoggingFailureWithoutAnOriginalFailureIsReturnedToTheCaller()
    {
        var failure = new IOException("logging failure");
        Assert.Same(failure, LocalExportLog.WriteBestEffort(null, "logging_error", () => throw failure));
        Assert.Null(LocalExportLog.WriteBestEffort(null, "logging_error", () => { }));
    }

    private static Exception CaptureFailure()
    {
        try { throw new InvalidOperationException("original failure"); }
        catch (Exception error) { return error; }
    }
}
