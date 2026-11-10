"""Stdlib orchestration tests; no Autodesk API execution or real Revit launch."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from tools.revit_exporter import cli, dev_pdf as dev

ROOT = Path(__file__).resolve().parents[1]


def arguments(tmp_path, *extra):
    return cli.parser().parse_args([
        "dev-pdf", "--version", "2025", "--runtime", "net8", "--sheets", " Admin CD.31 ",
        "--mode", "default", "--output-root", str(tmp_path / "output"), *extra,
    ])


def completed(run_root, *, successful=True):
    folder = run_root / "pdf-diagnostics" / "unique-attempt"
    folder.mkdir(parents=True)
    (folder / "drawing.pdf").write_bytes(b"%PDF-diagnostic-test")
    tx = dict(start_return_status="Started", status_after_start="Started", rollback_attempted=True,
              rollback_return_status="RolledBack", end_status="RolledBack", rollback_succeeded=True,
              dispose_succeeded=True, exception=None, cleanup_exceptions=[])
    report = dict(diagnostic_only=True, successful=successful, started_at="2026-11-05T00:00:00Z",
                  finished_at="2026-11-05T00:00:01Z", revit_version="2025", revit_version_build="2025.4",
                  output_folder=str(folder), mode="default", requested_sheet_numbers="Admin CD.31",
                  setup_name=None, sheets=[dict(sheet_number="Admin CD.31", order=1)], sheet_count=1,
                  transaction=tx, timings_ms=dict(pdf_export_ms=100, diagnostic_total_ms=200),
                  export_called=True, export_return_value=True, exception=None, logging_error=None,
                  expected_pdf_exists=True, pdf_files=[dict(file_name="drawing.pdf", size_bytes=(folder / "drawing.pdf").stat().st_size)])
    names = ["diagnostic_started", "sheets_selected", "pdf_options_ready", "transaction_starting",
             "transaction_started", "pdf_export_started", "pdf_export_completed",
             "transaction_rollback_started", "transaction_finished", "diagnostic_finished"]
    events = [dict(utc_timestamp="2026-11-05T00:00:00Z", event=name, stage="test", data={}) for name in names]
    for event in events:
        if event["event"] in ("transaction_starting", "transaction_rollback_started"):
            event["data"] = None  # LocalExportLog.AppendEvent without a data argument
    events[4]["data"] = dict(start_return_status="Started", status="Started")
    events[6]["data"] = dict(export_return_value=True, elapsed_ms=100)
    events[-2]["data"] = dict(tx, exception_type=None, cleanup_exception_type=None)
    events[-1]["data"] = dict(successful=successful, elapsed_ms=201, exception_type=None)
    write_logs(folder, report, events)
    return folder, report, events


def write_logs(folder, report, events):
    (folder / dev.REPORT).write_text(json.dumps(report), encoding="utf-8")
    (folder / dev.EVENTS).write_text("".join(json.dumps(item) + "\n" for item in events), encoding="utf-8")


@pytest.mark.parametrize("sheets", ["", " ", ",A", "A,", "A,A", " A , A ", "A,B,C", "A\0"])
def test_invalid_sheets(tmp_path, sheets):
    with pytest.raises(ValueError, match="--sheets"):
        dev.validate(arguments(tmp_path, "--sheets", sheets))


@pytest.mark.parametrize("extra,message", [
    (["--version", "2027", "--runtime", "net8"], "unsupported"),
    (["--mode", "saved"], "setup-name"),
    (["--mode", "saved", "--setup-name", " "], "setup-name"),
    (["--setup-name", "Unexpected"], "saved"),
    (["--timeout", "0"], "timeout"), (["--timeout", "-1"], "timeout"),
    (["--timeout", "nan"], "timeout"), (["--timeout", "inf"], "timeout"),
])
def test_invalid_options(tmp_path, extra, message):
    with pytest.raises(ValueError, match=message):
        dev.validate(arguments(tmp_path, *extra))


def test_explicit_bounded_selection_and_saved_setup(tmp_path):
    args = arguments(tmp_path, "--sheets", " Admin CD.31 , G6.001 ", "--mode", "saved", "--setup-name", " Exact setting ")
    dev.validate(args)
    assert args.sheets == "Admin CD.31,G6.001"
    assert args.setup_name == " Exact setting "


@pytest.mark.parametrize("argv", [[], ["dev-pdf"], ["dev-pdf", "--all"]])
def test_cli_required_options(argv):
    with pytest.raises(SystemExit):
        cli.parser().parse_args(argv)


def test_tasklist_localized_columns_and_all_revit_versions(monkeypatch):
    calls = []
    def tasklist(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout='"System","4","Services","0","100 K"\n"Revit.exe","101","Console","1","1,000 K"\n"REVIT.EXE","202","Console","1","2.000 K"\n')
    monkeypatch.setattr(dev.subprocess, "run", tasklist)
    assert dev.revit_pids() == [101, 202]
    with pytest.raises(ValueError, match="Close Revit manually"):
        dev.require_closed()
    assert calls[0] == (["tasklist", "/FO", "CSV", "/NH"], dict(check=True, capture_output=True, text=True, errors="replace", timeout=30))


@pytest.mark.parametrize("output", ["", "INFO: no tasks", '"Revit.exe","???","x","y","z"', '"Revit.exe",'])
def test_process_detection_fails_closed(monkeypatch, output):
    monkeypatch.setattr(dev.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=output))
    with pytest.raises(ValueError):
        dev.require_closed()


def test_tasklist_failure_propagates(monkeypatch):
    def fail(*a, **k):
        raise subprocess.CalledProcessError(1, "tasklist")
    monkeypatch.setattr(dev.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        dev.require_closed()


def test_no_revit_process(monkeypatch):
    monkeypatch.setattr(dev.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout='"System","4","Services","0","100 K"\n'))
    dev.require_closed()


def test_child_environment_is_private_and_removes_stale_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_KB_UNATTENDED_CONFIG", "must-not-inherit")
    monkeypatch.setenv("LLM_KB_PDF_DIAG_SETUP_NAME", "stale")
    monkeypatch.setenv("llm_kb_export_root", "case-insensitive-stale")
    before = dict(os.environ)
    args = arguments(tmp_path)
    dev.validate(args)
    env = dev.child_environment(args, tmp_path)
    assert dict(os.environ) == before
    assert "LLM_KB_UNATTENDED_CONFIG" not in env
    assert "LLM_KB_PDF_DIAG_SETUP_NAME" not in env
    assert "llm_kb_export_root" not in env
    assert env["LLM_KB_EXPORT_ROOT"] == str(tmp_path)
    assert env["LLM_KB_PDF_DIAG_SHEET_NUMBERS"] == "Admin CD.31"
    args.mode, args.setup_name = "saved", "PDF Export Setup Settings 1"
    assert dev.child_environment(args, tmp_path)["LLM_KB_PDF_DIAG_SETUP_NAME"] == args.setup_name


def test_complete_success_and_console_summary(tmp_path, capsys):
    completed(tmp_path)
    logs = dev.read_logs(tmp_path)
    assert logs.complete
    assert dev.success_problems(logs) == []
    dev.display("success", logs, tmp_path)
    output = capsys.readouterr().out
    for expected in ("Revit: 2025 / build 2025.4", "Admin CD.31", "count=1", "PDF mode: default", "return=Started", "return=True", "size_bytes", "confirmed=True", "pdf_export_ms", "Exception: null", str(tmp_path.resolve())):
        assert expected in output


@pytest.mark.parametrize("field,value,message", [
    ("rollback_succeeded", False, "Rollback not confirmed"),
    ("rollback_return_status", "Pending", "Rollback not confirmed"),
    ("end_status", None, "Rollback not confirmed"),
    ("rollback_attempted", False, "Rollback not confirmed"),
    ("start_return_status", "Uninitialized", "Start not confirmed"),
    ("status_after_start", "Pending", "Start not confirmed"),
    ("dispose_succeeded", False, "cleanup"),
    ("cleanup_exceptions", [{"operation": "dispose", "exception": {"type": "IOException", "message": "failed"}}], "cleanup"),
])
def test_pdf_success_requires_clean_confirmed_transaction(tmp_path, field, value, message):
    folder, report, events = completed(tmp_path)
    report["transaction"][field] = value
    write_logs(folder, report, events)
    assert any(message in item for item in dev.success_problems(dev.read_logs(tmp_path)))


@pytest.mark.parametrize("mutation", ["export_false", "export_null", "exception", "final_failed", "event_rollback_failed", "missing_event", "unordered", "duplicate", "logging_error", "missing_pdf", "empty_pdf", "size_mismatch"])
def test_never_accept_false_success(tmp_path, mutation):
    folder, report, events = completed(tmp_path)
    if mutation == "export_false":
        report["export_return_value"] = False
    elif mutation == "export_null":
        report["export_return_value"] = None
    elif mutation == "exception":
        report["exception"] = dict(type="Autodesk.Revit.Exceptions.InternalException", message="Export failed")
    elif mutation == "final_failed":
        events[-1]["data"]["successful"] = False
    elif mutation == "event_rollback_failed":
        events[-2]["data"]["rollback_succeeded"] = False
    elif mutation == "missing_event":
        del events[4]
    elif mutation == "unordered":
        events[4], events[5] = events[5], events[4]
    elif mutation == "duplicate":
        events.insert(4, events[4])
    elif mutation == "logging_error":
        report["logging_error"] = "Could not append event"
    elif mutation == "missing_pdf":
        (folder / "drawing.pdf").unlink()
    elif mutation == "empty_pdf":
        (folder / "drawing.pdf").write_bytes(b"")
    else:
        report["pdf_files"][0]["size_bytes"] = 1
    write_logs(folder, report, events)
    assert dev.read_logs(tmp_path).complete
    assert dev.monitor(tmp_path, SimpleNamespace(poll=lambda: None), 1)[0] == "failed"


@pytest.mark.parametrize("target,content", [
    (dev.REPORT, "{"), (dev.REPORT, "[]"), (dev.REPORT, "null"),
    (dev.REPORT, '{"successful": true}'), (dev.REPORT, '{"transaction": []}'),
    (dev.EVENTS, '{"event":'), (dev.EVENTS, "[]\n"), (dev.EVENTS, "\n"),
    (dev.EVENTS, '{"event":"diagnostic_finished", "data": {"successful":true}}\n'),
])
def test_malformed_or_partial_logs_not_complete(tmp_path, target, content, capsys):
    folder, _, _ = completed(tmp_path)
    (folder / target).write_text(content, encoding="utf-8")
    logs = dev.read_logs(tmp_path)
    assert not logs.complete
    assert dev.success_problems(logs)
    dev.display("timeout", logs, tmp_path)  # malformed input must also render safely
    assert "Logs: incomplete" in capsys.readouterr().out


def test_partial_line_retried_until_complete(tmp_path, monkeypatch):
    folder, report, events = completed(tmp_path)
    (folder / dev.EVENTS).write_text(json.dumps(events[0]) + "\n" + '{"event":', encoding="utf-8")
    monkeypatch.setattr(dev.time, "sleep", lambda _: write_logs(folder, report, events))
    assert dev.monitor(tmp_path, SimpleNamespace(poll=lambda: None), 5)[0] == "success"


def test_missing_report_despite_finished_event_is_incomplete(tmp_path):
    folder, _, _ = completed(tmp_path)
    (folder / dev.REPORT).unlink()
    assert not dev.read_logs(tmp_path).complete
    assert dev.monitor(tmp_path, SimpleNamespace(poll=lambda: None), 10)[0] == "incomplete"


def test_no_flushed_newline_is_incomplete(tmp_path):
    folder, _, _ = completed(tmp_path)
    path = folder / dev.EVENTS
    path.write_text(path.read_text().rstrip("\n"), encoding="utf-8")
    assert not dev.read_logs(tmp_path).complete


def test_old_logs_and_multiple_attempts_are_not_misattributed(tmp_path):
    completed(tmp_path / "old-session")
    run_root = tmp_path / "new-session"
    run_root.mkdir()
    assert dev.read_logs(run_root).folder is None
    completed(run_root)
    assert dev.read_logs(run_root).complete
    (run_root / "pdf-diagnostics" / "second-attempt").mkdir()
    logs = dev.read_logs(run_root)
    assert not logs.complete and "ambiguous" in logs.problem


@pytest.mark.parametrize("has_logs", [True, False])
def test_timeout_and_exit_are_distinct(tmp_path, monkeypatch, has_logs):
    if has_logs:
        folder, _, events = completed(tmp_path)
        (folder / dev.EVENTS).write_text(json.dumps(events[0]) + "\n", encoding="utf-8")
    ticks = iter([0, 2])
    monkeypatch.setattr(dev.time, "monotonic", lambda: next(ticks))
    status, logs = dev.monitor(tmp_path, SimpleNamespace(poll=lambda: None), 1)
    assert status == "timeout" and not logs.complete
    assert (logs.folder is not None) == has_logs
    monkeypatch.setattr(dev.time, "monotonic", lambda: 0)
    assert dev.monitor(tmp_path, SimpleNamespace(poll=lambda: 0), 1)[0] == "revit_exited"


def test_complete_logs_win_over_process_exit(tmp_path):
    completed(tmp_path)
    assert dev.monitor(tmp_path, SimpleNamespace(poll=lambda: 0), 1)[0] == "success"


@pytest.fixture
def windows_run(tmp_path, monkeypatch):
    args = arguments(tmp_path)
    install = tmp_path / "Revit installation"
    install.mkdir()
    (install / "Revit.exe").touch()
    args.revit_install_dir = install
    monkeypatch.setattr(dev, "os", SimpleNamespace(name="nt", environ=os.environ, path=os.path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "ProgramData"))
    monkeypatch.setattr(dev, "revit_pids", lambda: [])
    assembly = tmp_path / "bin & output" / "host.dll"
    assembly.parent.mkdir()
    assembly.touch()
    builds, launches = [], []
    def build(*values):
        builds.append(values)
        return assembly
    def launch(command, **kwargs):
        launches.append((command, kwargs))
        completed(Path(kwargs["env"]["LLM_KB_EXPORT_ROOT"]))
        return SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(cli, "build", build)
    monkeypatch.setattr(dev.subprocess, "Popen", launch)
    monkeypatch.setattr(dev.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    return args, assembly, builds, launches


def test_build_register_launch_monitor_without_real_revit(windows_run):
    args, assembly, builds, launches = windows_run
    env_before = dict(os.environ)
    assert dev.run(args) == 0
    assert builds == [("2025", "net8", args.revit_install_dir.resolve(), "Release", None)]
    manifest = Path(os.environ["APPDATA"]) / "Autodesk/Revit/Addins/2025/LlmKnowledgeBase.Revit.addin"
    assert [item.text for item in ET.parse(manifest).findall("AddIn/Assembly")] == [str(assembly)] * 2
    assert dict(os.environ) == env_before
    command, options = launches[0]
    assert command == [str(args.revit_install_dir / "Revit.exe")]
    assert options["creationflags"] == 512 and options["cwd"] == args.revit_install_dir
    assert options["env"]["LLM_KB_PDF_DIAG_SHEET_NUMBERS"] == "Admin CD.31"
    first_root = options["env"]["LLM_KB_EXPORT_ROOT"]
    assert dev.run(args) == 0
    assert launches[-1][1]["env"]["LLM_KB_EXPORT_ROOT"] != first_root


def test_build_failure_prevents_manifest_and_launch(windows_run, monkeypatch):
    args, _, _, launches = windows_run
    def failed(*args):
        raise subprocess.CalledProcessError(1, "dotnet")
    monkeypatch.setattr(cli, "build", failed)
    with pytest.raises(subprocess.CalledProcessError):
        dev.run(args)
    assert launches == []
    assert not list(Path(os.environ["APPDATA"]).rglob("*.addin"))


@pytest.mark.parametrize("pids", [[100], []])
def test_running_revit_stops_before_build_or_after_build(windows_run, monkeypatch, pids):
    args, _, builds, launches = windows_run
    values = iter([pids, [200]])
    monkeypatch.setattr(dev, "revit_pids", lambda: next(values))
    with pytest.raises(ValueError, match="Close Revit manually"):
        dev.run(args)
    assert len(builds) == (0 if pids else 1)
    assert launches == []


@pytest.mark.parametrize("status,code", [("timeout", 3), ("revit_exited", 4), ("failed", 1), ("incomplete", 5), ("interrupt", 130)])
def test_stop_never_terminates_revit(windows_run, monkeypatch, status, code):
    args, _, _, launches = windows_run
    def monitor(*unused):
        if status == "interrupt":
            raise KeyboardInterrupt
        return status, dev.read_logs(Path(launches[0][1]["env"]["LLM_KB_EXPORT_ROOT"]))
    monkeypatch.setattr(dev, "monitor", monitor)
    # Fake process exposes only poll; terminate/kill/wait/context-manager use would fail.
    assert dev.run(args) == code
    assert len(launches) == 1


def test_non_windows_rejected_before_build(tmp_path, monkeypatch):
    monkeypatch.setattr(dev, "os", SimpleNamespace(name="posix", environ=os.environ))
    monkeypatch.setattr(cli, "build", lambda *a: pytest.fail("must not build"))
    with pytest.raises(ValueError, match="requires Windows"):
        dev.run(arguments(tmp_path))
    assert not (tmp_path / "output").exists()


def test_missing_revit_executable_stops(windows_run):
    args, _, builds, launches = windows_run
    (args.revit_install_dir / "Revit.exe").unlink()
    with pytest.raises(ValueError, match="Revit.exe not found"):
        dev.run(args)
    assert builds == launches == []


def test_missing_dll_stops_before_registration(windows_run):
    args, assembly, _, launches = windows_run
    assembly.unlink()
    with pytest.raises(ValueError, match="DLL does not exist"):
        dev.run(args)
    assert launches == []


@pytest.mark.parametrize("scope", ["roaming", "machine", "foreign_target", "invalid_xml"])
def test_conflicting_manifest_never_overwritten(windows_run, scope):
    args, _, builds, launches = windows_run
    base = Path(os.environ["PROGRAMDATA" if scope == "machine" else "APPDATA"])
    path = base / "Autodesk/Revit/Addins/2025" / ("LlmKnowledgeBase.Revit.addin" if scope in ("foreign_target", "invalid_xml") else "other.addin")
    path.parent.mkdir(parents=True)
    content = "<" if scope == "invalid_xml" else ("<RevitAddIns><AddIn><FullClassName>Other.Command</FullClassName></AddIn></RevitAddIns>" if scope == "foreign_target" else (ROOT / "revit_exporter/manifests/2025/LlmKnowledgeBase.Revit.addin.template").read_text())
    path.write_text(content, encoding="utf-8")
    with pytest.raises((ValueError, ET.ParseError)):
        dev.run(args)
    assert path.read_text() == content
    assert builds == launches == []



@pytest.mark.parametrize("failure", ["empty", "permission", "encoding"])
def test_unreadable_third_party_manifest_warns_without_modifying_it(windows_run, monkeypatch, capsys, failure):
    args, _, _, launches = windows_run
    folder = Path(os.environ["APPDATA"]) / "Autodesk/Revit/Addins/2025"
    folder.mkdir(parents=True)
    foreign = folder / "external-vendor.addin"
    if failure == "empty":
        foreign.write_bytes(b"")
    elif failure == "encoding":
        foreign.write_bytes(b'<?xml version="1.0" encoding="UTF-16"?><RevitAddIns/>')
    else:
        foreign.write_bytes(b"<RevitAddIns/>")
        original_parse = ET.parse

        def fail_for_foreign(source, *a, **kw):
            if Path(source) == foreign:
                raise PermissionError(13, "Permission denied", str(foreign))
            return original_parse(source, *a, **kw)

        monkeypatch.setattr(dev.ET, "parse", fail_for_foreign)

    original = foreign.read_bytes()
    assert dev.run(args) == 0
    captured = capsys.readouterr()
    assert "WARNING:" in captured.err
    assert str(foreign) in captured.err
    assert "conflicts remain unverified" in captured.err
    assert foreign.read_bytes() == original
    assert len(launches) == 1


def test_unreadable_foreign_does_not_bypass_readable_conflict(windows_run, capsys):
    args, _, builds, launches = windows_run
    folder = Path(os.environ["PROGRAMDATA"]) / "Autodesk/Revit/Addins/2025"
    folder.mkdir(parents=True)
    (folder / "a-empty.addin").write_bytes(b"")
    other = folder / "z-conflict.addin"
    other.write_text(
        (ROOT / "revit_exporter/manifests/2025/LlmKnowledgeBase.Revit.addin.template").read_text(),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Conflicting add-in registration"):
        dev.run(args)
    assert builds == [] and launches == []
    assert "a-empty.addin" in capsys.readouterr().err


def test_invalid_managed_manifest_fails_with_its_path(windows_run):
    args, _, builds, launches = windows_run
    target = Path(os.environ["APPDATA"]) / "Autodesk/Revit/Addins/2025/LlmKnowledgeBase.Revit.addin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"")
    with pytest.raises(ValueError, match="Cannot validate managed LlmKnowledgeBase manifest") as error:
        dev.run(args)
    assert str(target) in str(error.value)
    assert builds == [] and launches == []
    assert target.read_bytes() == b""

def test_journal_collection_only_new_files_and_no_overwrite(tmp_path, capsys):
    journals = tmp_path / "Journals"
    journals.mkdir()
    old = journals / "journal.0001.txt"
    old.write_text("old")
    before = dev.journal_files(journals)
    old.write_text("old modified")
    new = journals / "journal.0002.txt"
    new.write_text("new session")
    root = tmp_path / "session"
    root.mkdir()
    dev.collect_journals(journals, before, root)
    assert not (root / "journals" / old.name).exists()
    assert (root / "journals" / new.name).read_text() == "new session"
    new.write_text("new session more")
    dev.collect_journals(journals, before, root)
    assert (root / "journals" / new.name).read_text() == "new session"
    assert "Journal candidate snapshot" in capsys.readouterr().out


def test_cli_expected_error_has_no_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(dev, "run", lambda args: (_ for _ in ()).throw(ValueError("expected failure")))
    assert cli.main(["dev-pdf", "--version", "2025", "--runtime", "net8", "--sheets", "A", "--mode", "default", "--output-root", str(tmp_path)]) == 2
    assert "dev-pdf stopped: expected failure" in capsys.readouterr().err


@pytest.mark.parametrize("mutation", ["null_transaction_event", "wrong_folder", "no_sheets", "wrong_sheets", "bad_mode", "unconfirmed_setup", "final_exception"])
def test_inconsistent_success_evidence_is_rejected(tmp_path, mutation):
    folder, report, events = completed(tmp_path)
    if mutation == "null_transaction_event":
        events[-2]["data"] = None
    elif mutation == "wrong_folder":
        report["output_folder"] = str(tmp_path / "past-run")
    elif mutation == "no_sheets":
        report["sheets"], report["sheet_count"] = [], 0
    elif mutation == "wrong_sheets":
        report["sheets"][0]["sheet_number"] = "Other Sheet"
    elif mutation == "bad_mode":
        report["mode"] = "all"
    elif mutation == "unconfirmed_setup":
        report["mode"], report["setup_name"], report["requested_setup_name"] = "saved", "Other", "Expected"
    else:
        events[-1]["data"]["exception_type"] = "IOException"
    write_logs(folder, report, events)
    assert dev.success_problems(dev.read_logs(tmp_path))


def test_early_validation_failure_is_completed_failure(tmp_path, capsys):
    folder, report, events = completed(tmp_path, successful=False)
    report.update(sheets=[], sheet_count=0, export_called=False, export_return_value=None,
                  exception=dict(type="InvalidOperationException", message="Sheet not found"),
                  expected_pdf_exists=False, pdf_files=[])
    report["transaction"] = dict(start_return_status=None, status_after_start=None,
                                rollback_attempted=False, rollback_return_status=None,
                                end_status=None, rollback_succeeded=False, dispose_succeeded=False,
                                exception=None, cleanup_exceptions=[])
    events = [events[0], events[-1]]
    write_logs(folder, report, events)
    status, logs = dev.monitor(tmp_path, SimpleNamespace(poll=lambda: None), 10)
    assert status == "failed" and logs.complete
    dev.display(status, logs, tmp_path)
    output = capsys.readouterr().out
    assert "InvalidOperationException" in output and "Sheet not found" in output
    assert "called=False, return=None" in output


def test_stdlib_cli_import_and_validation_subprocess(tmp_path):
    result = subprocess.run([
        __import__("sys").executable, "-S", "-m", "tools.revit_exporter", "dev-pdf",
        "--version", "2025", "--runtime", "net8", "--sheets", "A,B,C", "--mode", "default",
        "--output-root", str(tmp_path),
    ], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 2
    assert "--sheets requires" in result.stderr
    assert "Traceback" not in result.stderr


def test_build_preflight_exit_is_reported_as_preflight_error(windows_run, monkeypatch):
    args, _, _, launches = windows_run
    def failed(*unused):
        raise SystemExit("A .NET 8 SDK is required")
    monkeypatch.setattr(cli, "build", failed)
    with pytest.raises(ValueError, match="Release build preflight failed: A .NET 8 SDK"):
        dev.run(args)
    assert launches == []


@pytest.mark.parametrize("event_name,data", [
    ("transaction_started", {"start_return_status": "Pending", "status": "Pending"}),
    ("pdf_export_completed", {"export_return_value": False}),
    ("transaction_finished", {"rollback_attempted": False}),
])
def test_contradictory_event_evidence_cannot_succeed(tmp_path, event_name, data):
    folder, report, events = completed(tmp_path)
    next(event for event in events if event["event"] == event_name)["data"].update(data)
    write_logs(folder, report, events)
    assert dev.success_problems(dev.read_logs(tmp_path))


@pytest.mark.parametrize("boundary", ["exit", "timeout"])
def test_logs_completed_during_stop_check_are_reloaded(tmp_path, monkeypatch, boundary):
    folder, report, events = completed(tmp_path)
    (folder / dev.REPORT).unlink()
    (folder / dev.EVENTS).write_text(json.dumps(events[0]) + "\n", encoding="utf-8")
    ticks = iter([0, 2])
    monkeypatch.setattr(dev.time, "monotonic", lambda: next(ticks))
    def poll():
        write_logs(folder, report, events)
        return 0 if boundary == "exit" else None
    assert dev.monitor(tmp_path, SimpleNamespace(poll=poll), 1)[0] == "success"


@pytest.mark.parametrize("encoding", ["unknown-vendor-encoding", "UTF-32"])
def test_unknown_encoding_third_party_manifest_warns(windows_run, capsys, encoding):
    args, _, _, launches = windows_run
    foreign = Path(os.environ["APPDATA"]) / "Autodesk/Revit/Addins/2025/vendor.addin"
    foreign.parent.mkdir(parents=True)
    content = f'<?xml version="1.0" encoding="{encoding}"?><RevitAddIns/>'.encode("ascii")
    foreign.write_bytes(content)
    assert dev.run(args) == 0
    assert len(launches) == 1
    assert foreign.read_bytes() == content
    assert "vendor.addin" in capsys.readouterr().err


def inspect_cli(session):
    return cli.main(["inspect-pdf", "--session", str(session)])


def test_offline_inspection_requires_no_host_and_does_not_change_files(tmp_path, monkeypatch, capsys):
    completed(tmp_path)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in tmp_path.rglob("*") if path.is_file()}
    env_before = dict(os.environ)
    def forbidden(*args, **kwargs):
        pytest.fail("offline inspection must not build, launch or collect journals")
    monkeypatch.setattr(cli, "build", forbidden)
    monkeypatch.setattr(dev, "revit_pids", forbidden)
    monkeypatch.setattr(dev, "child_environment", forbidden)
    monkeypatch.setattr(dev, "collect_journals", forbidden)
    monkeypatch.setattr(dev.subprocess, "Popen", forbidden)
    monkeypatch.setattr(dev.subprocess, "run", forbidden)
    assert inspect_cli(tmp_path) == 0
    output = capsys.readouterr().out
    for text in ("Result: success", "Revit: 2025", "Admin CD.31", "Document.Export:", "Transaction Start:", "Transaction Rollback:", "Timings (ms):", "Exception:", str(tmp_path.resolve())):
        assert text in output
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in tmp_path.rglob("*") if path.is_file()} == before
    assert dict(os.environ) == env_before


@pytest.mark.parametrize("failure", ["api_exception", "rollback_pending", "rollback_unconfirmed", "missing_pdf", "event_failed", "failed_event_present"])
def test_offline_failures_cannot_be_pdf_only_success(tmp_path, capsys, failure):
    folder, report, events = completed(tmp_path)
    if failure == "api_exception":
        report.update(successful=False, export_return_value=None,
                      exception={"type": "Autodesk.Revit.Exceptions.InternalException", "message": "API export failed"})
        events[-1]["data"]["successful"] = False
    elif failure == "rollback_pending":
        report["transaction"].update(rollback_succeeded=False, rollback_return_status="Pending", end_status="Pending")
    elif failure == "rollback_unconfirmed":
        report["transaction"]["rollback_succeeded"] = False
    elif failure == "missing_pdf":
        (folder / "drawing.pdf").unlink()
    elif failure == "event_failed":
        events[-1]["data"]["successful"] = False
    else:
        events.insert(6, dict(event="pdf_export_failed", data={"export_return_value": False}))
    write_logs(folder, report, events)
    assert inspect_cli(tmp_path) == 1
    output = capsys.readouterr().out
    assert "Result: failed" in output
    if failure == "api_exception":
        assert "InternalException" in output and "API export failed" in output
    elif failure.startswith("rollback"):
        assert "Transaction Rollback not confirmed" in output


def test_inspection_after_timeout_sees_later_completed_logs(tmp_path, monkeypatch, capsys):
    folder, report, events = completed(tmp_path)
    (folder / dev.REPORT).unlink()
    (folder / dev.EVENTS).write_text(json.dumps(events[0]) + "\n", encoding="utf-8")
    ticks = iter([0, 2])
    monkeypatch.setattr(dev.time, "monotonic", lambda: next(ticks))
    assert dev.monitor(tmp_path, SimpleNamespace(poll=lambda: None), 1)[0] == "timeout"
    assert inspect_cli(tmp_path) == 5
    write_logs(folder, report, events)
    assert inspect_cli(tmp_path) == 0
    assert "Result: success" in capsys.readouterr().out


@pytest.mark.parametrize("name,content", [
    (dev.REPORT, b"{"), (dev.REPORT, b"[]"), (dev.REPORT, b"null"),
    (dev.REPORT, b"\xff"), (dev.REPORT, b"[" * 2000),
    (dev.EVENTS, b'{"event":'), (dev.EVENTS, b"\xff"),
    (dev.EVENTS, b"[]\n"), (dev.EVENTS, b"[" * 2000),
])
def test_offline_corrupt_logs_are_incomplete(tmp_path, capsys, name, content):
    folder, _, _ = completed(tmp_path)
    (folder / name).write_bytes(content)
    assert inspect_cli(tmp_path) == 5
    output = capsys.readouterr().out
    assert "Result: incomplete" in output
    assert "incomplete/invalid" in output


@pytest.mark.parametrize("missing", [dev.REPORT, dev.EVENTS, "both"])
def test_offline_missing_logs_are_incomplete(tmp_path, capsys, missing):
    folder, _, _ = completed(tmp_path)
    for name in (dev.REPORT, dev.EVENTS) if missing == "both" else (missing,):
        (folder / name).unlink()
    assert inspect_cli(tmp_path) == 5
    assert "Logs: incomplete" in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_offline_nonexistent_or_non_directory_session(tmp_path, capsys, kind):
    session = tmp_path / "no-session"
    if kind == "file":
        session.touch()
    assert inspect_cli(session) == 2
    assert "Session directory does not exist or is not a directory" in capsys.readouterr().err
    assert not (session / "pdf-diagnostics").exists()


@pytest.mark.parametrize("legacy", ["no_transaction", "empty_transaction", "no_timing", "old_event_outcome", "no_events", "transaction_string"])
def test_old_log_formats_show_available_values_and_unverified_evidence(tmp_path, capsys, legacy):
    folder, report, events = completed(tmp_path)
    if legacy == "no_transaction":
        del report["transaction"]
    elif legacy == "empty_transaction":
        report["transaction"] = {}
    elif legacy == "no_timing":
        del report["timings_ms"]
    elif legacy == "old_event_outcome":
        events[-1]["data"] = {"succeeded": True}
    elif legacy == "transaction_string":
        report["transaction"] = "ReadOnly"
    write_logs(folder, report, events)
    if legacy == "no_events":
        (folder / dev.EVENTS).unlink()
    assert inspect_cli(tmp_path) == 5
    output = capsys.readouterr().out
    assert "Result: incomplete" in output and "Revit: 2025" in output
    if "transaction" in legacy:
        assert "Unverified: Transaction Start" in output
        assert "Unverified: Transaction Rollback" in output
    elif legacy == "no_timing":
        assert "Unverified: processing time" in output
    else:
        assert "events incomplete/invalid" in output


def test_offline_empty_and_ambiguous_sessions(tmp_path, capsys):
    assert inspect_cli(tmp_path) == 5
    assert "diagnostic not started" in capsys.readouterr().out
    completed(tmp_path)
    (tmp_path / "pdf-diagnostics" / "another-attempt").mkdir()
    assert inspect_cli(tmp_path) == 5
    assert "ambiguous: 2" in capsys.readouterr().out


def test_final_event_report_read_race_is_retried_at_monitor_boundary(tmp_path, monkeypatch):
    folder, _, _ = completed(tmp_path)
    original = Path.read_text
    reads = []
    def read(path, *args, **kwargs):
        if path == folder / dev.REPORT:
            reads.append(path)
            if len(reads) == 1:
                return "{"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    assert dev.monitor(tmp_path, SimpleNamespace(poll=lambda: None), 1)[0] == "success"
    assert len(reads) == 2


def test_recorded_output_path_is_never_resolved_or_opened(tmp_path, monkeypatch):
    folder, report, events = completed(tmp_path)
    recorded = tmp_path / "outside-session"
    report["output_folder"] = str(recorded)
    write_logs(folder, report, events)
    original = Path.resolve
    def resolve(path, *args, **kwargs):
        assert path != recorded, "log metadata must not trigger filesystem access"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", resolve)
    assert dev.inspect(tmp_path) == 1


@pytest.mark.parametrize("case,code", [("success", 0), ("incomplete", 5), ("missing", 2)])
def test_offline_cli_runs_with_stdlib_only(tmp_path, case, code):
    import sys
    if case == "success":
        completed(tmp_path)
    session = tmp_path / "missing" if case == "missing" else tmp_path
    result = subprocess.run([
        sys.executable, "-S", "-m", "tools.revit_exporter", "inspect-pdf", "--session", str(session),
    ], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == code
    assert "Traceback" not in result.stderr
