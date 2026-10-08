"""Host source/registration contracts; these do not execute or compile Autodesk APIs."""

from pathlib import Path
import re
import xml.etree.ElementTree as ET

import pytest


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
    assert 'ExportFiles.WriteJson(reportPath, report);' in DIAGNOSTIC.split("finally")[1]
    for forbidden in ("OfflineExportService", "RevitSnapshotExporter", "SnapshotContract", "export_manifest",
                      "revit_snapshot", "enhanced.pdf", "sqlite", "Directory.Move", "File.Move"):
        assert forbidden not in DIAGNOSTIC
