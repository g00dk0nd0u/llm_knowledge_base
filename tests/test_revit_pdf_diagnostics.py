"""Host source/registration contracts; these do not execute or compile Autodesk APIs."""

from pathlib import Path
import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from tests.test_revit_exporter_build import CORE, msbuild_with_sdk


ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "revit_exporter/src/Revit.Shared"
DIAGNOSTIC = (SHARED / "DiagnosePdfExportCommand.cs").read_text(encoding="utf-8")
NORMAL = (SHARED / "ExportOfflineKnowledgeCommand.cs").read_text(encoding="utf-8")


@pytest.mark.parametrize("version", ["2025", "2026", "2027"])
def test_manifest_adds_distinct_diagnostic_command_preserving_normal_registration(version):
    template = ROOT / "revit_exporter/manifests" / version / "LlmKnowledgeBase.Revit.addin.template"
    commands = ET.fromstring(template.read_text(encoding="utf-8")).findall("AddIn")
    assert len(commands) == 2
    normal, diagnostic = commands
    assert normal.findtext("Name") == "Export Offline Knowledge Package"
    assert normal.findtext("FullClassName") == "LlmKnowledgeBase.Revit.ExportOfflineKnowledgeCommand"
    assert normal.findtext("AddInId") == f"8D56ED5A-1D6B-4B4E-A0{version[-2:]}-000000000004"
    assert diagnostic.findtext("Name") == "Diagnose PDF Export (max 2 sheets)"
    assert diagnostic.findtext("FullClassName") == "LlmKnowledgeBase.Revit.DiagnosePdfExportCommand"
    assert diagnostic.findtext("AddInId") != normal.findtext("AddInId")
    assert all(command.attrib == {"Type": "Command"} for command in commands)
    assert all(command.findtext("Assembly") == "{{ASSEMBLY_PATH}}" for command in commands)


def test_diagnostic_environment_cannot_redirect_normal_all_sheet_or_unattended_export():
    assert "LLM_KB_PDF_DIAG_" not in NORMAL
    assert "PdfDiagnosticRequest" not in NORMAL
    assert "PdfExportDiagnostics" not in NORMAL
    assert "OfflineExportService.Run(document, new OfflineExportOptions())" in NORMAL
    assert "new RevitSnapshotExporter(document, options ?? new OfflineExportOptions()).Export()" in NORMAL
    assert ".Where(s => !s.IsPlaceholder && s.CanBePrinted)" in NORMAL
    assert ".OrderBy(s => s.SheetNumber, StringComparer.Ordinal)" in NORMAL
    assert "var page = index + 1" in NORMAL
    assert "new PDFExportOptions { Combine = true, FileName = \"drawing\" }" in NORMAL


def test_diagnostic_has_one_real_synchronous_export_after_strict_validation():
    assert DIAGNOSTIC.count("document.Export(") == 1
    parse = DIAGNOSTIC.index("PdfDiagnosticRequest.Parse(")
    selection = DIAGNOSTIC.index("request.SelectSheets(")
    acquisition = DIAGNOSTIC.index("using var options = GetOptions(")
    combine = DIAGNOSTIC.index("options.Combine = true;")
    filename = DIAGNOSTIC.index('options.FileName = "drawing";')
    background = DIAGNOSTIC.index("options.SetExportInBackground(false);")
    call = DIAGNOSTIC.index("document.Export(folder, ids, options)")
    assert parse < selection < acquisition < combine < filename < background < call
    assert 'sheet => !sheet.IsPlaceholder && sheet.CanBePrinted' in DIAGNOSTIC
    assert "var ids = sheets.Select(sheet => sheet.Id).ToList();" in DIAGNOSTIC
    assert "[Transaction(TransactionMode.ReadOnly)]" in DIAGNOSTIC
    assert 'if (options.GetExportInBackground())' in DIAGNOSTIC
    assert 'if (!exported) throw new InvalidOperationException' in DIAGNOSTIC
    assert "File.Exists(expectedPdf)" in DIAGNOSTIC
    assert "new FileInfo(expectedPdf).Length == 0" in DIAGNOSTIC
    assert "return Result.Failed;" in DIAGNOSTIC


def test_saved_options_are_copied_without_creation_mutation_or_fallback():
    method = DIAGNOSTIC.split("private static PDFExportOptions GetOptions(")[1].split(
        "private static JsonObject DescribeException(",
    )[0]
    assert 'if (request.Mode == "default")' in method
    assert method.count("return new PDFExportOptions();") == 1
    assert "ExportPDFSettings.ListNames(document)" in method
    assert "names.Contains(request.SetupName, StringComparer.Ordinal)" in method
    assert 'report["available_setup_names"]' in method
    assert "ExportPDFSettings.FindByName(document, request.SetupName!)" in method
    assert "return settings.GetOptions();" in method
    assert 'throw new InvalidOperationException' in method
    assert "catch" not in method
    assert "ExportPDFSettings.Create(" not in DIAGNOSTIC
    assert not re.search(r"\b(?:SetOptions|Save|SynchronizeWithCentral|Commit|Start)\s*\(", DIAGNOSTIC)
    assert "new Transaction" not in DIAGNOSTIC


def test_reports_diagnostic_source_settings_export_outcome_exception_and_files_only():
    assert 'Path.Combine(baseDirectory, "pdf-diagnostics"' in DIAGNOSTIC
    assert '"pdf_diagnostic_report.json"' in DIAGNOSTIC
    for field in (
        "diagnostic_only", "revit_version", "revit_version_build", "requested_sheet_numbers",
        "sheet_number", "element_id", "order", "sheet_count", "options_source", "setup_name",
        "options_before_overrides", "options", "paper_format", "orientation", "zoom_type",
        "zoom_percentage", "always_use_raster", "background", "output_folder", "export_called",
        "export_return_value", "successful", "type", "message", "stack_trace", "details",
        "expected_pdf_exists", "pdf_files", "size_bytes",
    ):
        assert f'["{field}"]' in DIAGNOSTIC
    assert 'report["exception"] = DescribeException(error);' in DIAGNOSTIC
    assert 'report["successful"] = false;' in DIAGNOSTIC
    assert 'ExportFiles.WriteJson(reportPath, report);' in DIAGNOSTIC.rsplit("finally", 1)[1]
    for forbidden in ("OfflineExportService", "RevitSnapshotExporter", "SnapshotContract", "export_manifest",
                      "revit_snapshot", "enhanced.pdf", "sqlite", "Directory.Move", "File.Move"):
        assert forbidden not in DIAGNOSTIC


def test_diagnostic_events_are_bounded_and_flushed_before_the_real_export():
    log_source = (ROOT / "revit_exporter/src/LlmKnowledgeBase.Revit.Core/LocalExportLog.cs").read_text()
    assert '"diagnostic_events.jsonl"' in DIAGNOSTIC
    assert "stream.Write(bytes);" in log_source
    assert "stream.Flush(flushToDisk: true);" in log_source
    assert "using var stream = new FileStream" in log_source
    assert "FileMode.Append" in log_source
    for event in (
        "diagnostic_started", "sheets_selected", "pdf_options_ready", "pdf_export_started",
        "pdf_export_completed", "pdf_export_failed", "diagnostic_finished",
    ):
        assert DIAGNOSTIC.count(f'"{event}"') == 1
    started = DIAGNOSTIC.index('LocalExportLog.AppendEvent(eventsPath, "pdf_export_started"')
    assert started < DIAGNOSTIC.index('report["export_called"] = true;') < DIAGNOSTIC.index("document.Export(")
    # Normal successful exports get no event-file writes or diagnostic env dependencies.
    assert "AppendEvent" not in NORMAL
    assert "diagnostic_events" not in NORMAL


def test_stopwatch_times_api_call_separately_from_event_file_io_and_records_failed_calls():
    assert "using System.Diagnostics;" in DIAGNOSTIC
    assert "Stopwatch.StartNew()" in DIAGNOSTIC
    for field in ("sheet_acquisition_ms", "pdf_options_ms", "pdf_export_ms", "diagnostic_total_ms"):
        assert f'["{field}"]' in DIAGNOSTIC
    export = DIAGNOSTIC.split('report["export_called"] = true;')[1].split('stage = "pdf_validation";')[0]
    start, call, stop = map(export.index, (
        "phaseElapsed.Restart();", "document.Export(folder, ids, options)", "phaseElapsed.Stop();",
    ))
    assert start < call < stop
    assert "AppendEvent" not in export[start:stop]
    assert 'timings["pdf_export_ms"] = phaseElapsed.Elapsed.TotalMilliseconds;' in export.split("finally")[1]


def test_normal_failure_stage_timing_and_logging_preserve_original_exception():
    export = NORMAL.split("public string Export()")[1].split("private List<ViewSheet> CollectSheets()")[0]
    assert "Stopwatch.StartNew()" in export
    assert 'stage = "pdf_export";' in export
    assert 'stage = "write_snapshot";' in export
    assert 'stage = "write_manifest";' in export
    assert 'stage = "publish_run";' in export
    failure = export.split("catch (Exception error)")[1]
    assert "elapsed.Stop();" in failure
    assert "LocalExportLog.TryWriteFailure(" in failure
    assert "stage, error, elapsed.Elapsed.TotalMilliseconds, revitVersion, _warnings" in failure
    assert "throw;" in failure
    assert "throw new" not in failure
    assert "ExportFiles.WriteJson" not in failure
    diagnostic_finish = DIAGNOSTIC.rsplit("finally", 1)[1].split("return reportPath;")[0]
    assert "LocalExportLog.WriteBestEffort(failure," in diagnostic_finish
    assert "if (failure is null &&" in diagnostic_finish


def test_flushed_events_survive_abrupt_termination_of_a_real_dotnet_process(tmp_path):
    """Test the actual Core logger, not a mocked Revit export or an Autodesk host."""
    run = msbuild_with_sdk(tmp_path, 8)
    project = tmp_path / "LogCrashProbe.csproj"
    project.write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
        '<TargetFramework>net8.0</TargetFramework><OutputType>Exe</OutputType>'
        '</PropertyGroup><ItemGroup><ProjectReference Include="'
        + str(CORE) + '" /></ItemGroup></Project>', encoding="utf-8",
    )
    (tmp_path / "Program.cs").write_text('''
using System;
using System.Threading;
using LlmKnowledgeBase.Revit.Core;

var events = new[] { "diagnostic_started", "sheets_selected", "pdf_options_ready", "pdf_export_started" };
foreach (var name in events) LocalExportLog.AppendEvent(args[0], name, "logger_probe");
Console.WriteLine("flushed");
Console.Out.Flush();
Thread.Sleep(Timeout.Infinite);
''', encoding="utf-8")
    build = run(project, "-restore", "-t:Build", "-p:RevitCoreTargetFramework=net8.0")
    assert build.returncode == 0, build.stdout + build.stderr
    path = tmp_path / "diagnostic_events.jsonl"
    process = subprocess.Popen(
        [shutil.which("dotnet"), str(tmp_path / "bin/Debug/net8.0/LogCrashProbe.dll"), str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert process.stdout.readline().strip() == "flushed"
        before = path.read_bytes()
        assert len(before.splitlines()) == 4
        process.kill()  # No normal exit/finally; no OS/storage failure is simulated.
        process.wait(timeout=10)
        assert process.returncode != 0
        assert path.read_bytes() == before
        records = [json.loads(line) for line in before.decode("utf-8").splitlines()]
        assert [record["event"] for record in records] == [
            "diagnostic_started", "sheets_selected", "pdf_options_ready", "pdf_export_started",
        ]
        assert all(record["stage"] == "logger_probe" for record in records)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()
