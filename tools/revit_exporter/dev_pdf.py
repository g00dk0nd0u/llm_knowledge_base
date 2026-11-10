"""Local PDF diagnostic inspection and Windows orchestration. Standard library only."""
from __future__ import annotations

import csv
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

COMMAND = "Diagnose PDF Export (max 2 sheets)"
REPORT = "pdf_diagnostic_report.json"
EVENTS = "diagnostic_events.jsonl"
TRANSACTION_FIELDS = {
    "Start": ("start_return_status", "status_after_start"),
    "Rollback": ("rollback_attempted", "rollback_return_status", "end_status", "rollback_succeeded"),
    "disposal/cleanup": ("dispose_succeeded", "exception", "cleanup_exceptions"),
}


def sheet_numbers(value: str) -> str:
    numbers = [part.strip() for part in value.split(",")]
    if not 1 <= len(numbers) <= 2 or any(not part or "\x00" in part for part in numbers) or len(set(numbers)) != len(numbers):
        raise ValueError("--sheets requires one or two distinct, nonempty, comma-separated Sheet numbers")
    return ",".join(numbers)


def validate(args) -> None:
    from .cli import select_profile

    select_profile(args.version, args.runtime)
    args.sheets = sheet_numbers(args.sheets)
    if args.mode == "saved" and (not args.setup_name or not args.setup_name.strip()):
        raise ValueError("--mode saved requires --setup-name (exact existing setting name)")
    if args.mode == "default" and args.setup_name is not None:
        raise ValueError("--setup-name is only valid with --mode saved")
    if args.setup_name and "\x00" in args.setup_name:
        raise ValueError("--setup-name must not contain NUL")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("--timeout must be a finite positive number of seconds")


def revit_pids() -> list[int]:
    # Enumerate all rows: a filtered tasklist's no-match message is localized.
    result = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], check=True, capture_output=True,
                            text=True, errors="replace", timeout=30)
    try:
        rows = list(csv.reader(result.stdout.splitlines(), strict=True))
    except csv.Error as error:
        raise ValueError("Invalid tasklist CSV; no Revit was started") from error
    if not rows or any(len(row) != 5 or not row[1].isdigit() for row in rows):
        raise ValueError("Could not safely determine running processes from tasklist; no Revit was started")
    return [int(row[1]) for row in rows if row[0].casefold() == "revit.exe"]


def require_closed() -> None:
    pids = revit_pids()
    if pids:
        raise ValueError(f"Revit.exe is already running (PID: {', '.join(map(str, pids))}). "
                         "Close Revit manually, then rerun dev-pdf. No process was terminated.")


def manifest_plan(root: Path, version: str) -> tuple[Path, ET.Element]:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise ValueError("APPDATA is unavailable; cannot locate the per-user Revit Addins directory")
    directory = Path(appdata) / "Autodesk/Revit/Addins" / version
    target = directory / "LlmKnowledgeBase.Revit.addin"
    template = ET.parse(root / "revit_exporter/manifests" / version / "LlmKnowledgeBase.Revit.addin.template").getroot()
    identities = {((item.findtext("AddInId") or "").upper(), item.findtext("FullClassName")) for item in template.findall("AddIn")}
    directories = [directory]
    if os.environ.get("PROGRAMDATA"):
        directories.append(Path(os.environ["PROGRAMDATA"]) / "Autodesk/Revit/Addins" / version)
    for folder in directories:
        for path in sorted(folder.glob("*.addin")):
            try:
                existing = ET.parse(path).getroot()
            except (ET.ParseError, OSError, ValueError, LookupError) as error:
                if path == target:
                    raise ValueError(f"Cannot validate managed LlmKnowledgeBase manifest {path}: {error}") from error
                print(
                    f"WARNING: Cannot inspect third-party Revit manifest {path}: {error}. "
                    "Its AddInId/FullClassName conflicts remain unverified.",
                    file=sys.stderr,
                )
                continue
            entries = {((item.findtext("AddInId") or "").upper(), item.findtext("FullClassName")) for item in existing.findall("AddIn")}
            if path == target:
                if entries != identities or len(existing.findall("AddIn")) != len(identities):
                    raise ValueError(f"Refusing to replace an unrelated manifest: {path}")
            elif any(a == c or b == d for a, b in identities for c, d in entries):
                raise ValueError(f"Conflicting add-in registration: {path}. Resolve it manually before rerunning.")
    return target, template


def register_manifest(target: Path, template: ET.Element, assembly: Path) -> None:
    if not assembly.is_file():
        raise ValueError(f"Built DLL does not exist: {assembly}")
    for item in template.findall("AddIn/Assembly"):
        item.text = str(assembly.resolve())
    target.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement of this exporter's registration; XML escapes machine-local paths.
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        ET.ElementTree(template).write(stream, encoding="utf-8", xml_declaration=True)
    try:
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    if not target.is_file() or any(item.text != str(assembly.resolve()) for item in ET.parse(target).findall("AddIn/Assembly")):
        raise ValueError(f"Manifest verification failed: {target}")


def child_environment(args, run_root: Path) -> dict[str, str]:
    # Avoid leaking unrelated unattended/export configuration into this diagnostic session.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("LLM_KB_")}
    env.update(LLM_KB_EXPORT_ROOT=str(run_root), LLM_KB_PDF_DIAG_SHEET_NUMBERS=args.sheets,
               LLM_KB_PDF_DIAG_MODE=args.mode)
    if args.mode == "saved":
        env["LLM_KB_PDF_DIAG_SETUP_NAME"] = args.setup_name
    return env


@dataclass
class Logs:
    folder: Path | None
    report: dict | None = None
    events: list | None = None
    complete: bool = False
    problem: str = "diagnostic not started"
    terminal: bool = False


def read_logs(run_root: Path) -> Logs:
    folders = sorted(path for path in (run_root / "pdf-diagnostics").glob("*") if path.is_dir())
    if not folders:
        return Logs(None)
    if len(folders) != 1:
        return Logs(None, problem=f"ambiguous: {len(folders)} diagnostic attempts in this session; inspect {run_root}")
    folder = folders[0]
    report = None
    events = []
    problems = []
    terminal = False
    try:
        report = json.loads((folder / REPORT).read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("report must be an object")
    except (OSError, ValueError, RecursionError) as error:
        problems.append(f"report incomplete/invalid: {error}")
        report = None
    try:
        content = (folder / EVENTS).read_text(encoding="utf-8")
        for line in content.splitlines():
            event = json.loads(line)
            if (not isinstance(event, dict) or not isinstance(event.get("event"), str)
                    or (event.get("data") is not None and not isinstance(event["data"], dict))):
                raise ValueError("invalid JSONL event record")
            events.append(event)
        if not content.endswith("\n"):
            raise ValueError("JSONL final line is not yet flushed")
        if not events or events[0]["event"] != "diagnostic_started" or events[-1]["event"] != "diagnostic_finished":
            raise ValueError("diagnostic_finished not received")
        if not isinstance(events[-1].get("data"), dict) or type(events[-1]["data"].get("successful")) is not bool:
            raise ValueError("final event has no boolean outcome")
        terminal = True
    except (OSError, ValueError, RecursionError) as error:
        problems.append(f"events incomplete/invalid: {error}")
    if report is not None:
        try:
            validate_report(report)
        except ValueError as error:
            problems.append(str(error))
    return Logs(folder, report, events, not problems, "; ".join(problems), terminal)


def validate_report(report: dict) -> None:
    if report.get("diagnostic_only") is not True or type(report.get("successful")) is not bool:
        raise ValueError("invalid diagnostic report outcome")
    for key in ("started_at", "finished_at", "revit_version", "revit_version_build", "mode", "output_folder"):
        if not isinstance(report.get(key), str) or not report[key]:
            raise ValueError(f"missing/invalid report field: {key}")
    if not isinstance(report.get("transaction"), dict) or not isinstance(report.get("timings_ms"), dict):
        raise ValueError("missing/invalid transaction or timings_ms")
    missing = [field for fields in TRANSACTION_FIELDS.values() for field in fields if field not in report["transaction"]]
    if missing:
        raise ValueError(f"missing transaction evidence; cannot verify: {', '.join(missing)}")
    for key in ("sheets", "pdf_files"):
        if not isinstance(report.get(key), list) or any(not isinstance(item, dict) for item in report[key]):
            raise ValueError(f"missing/invalid {key}")
    if type(report.get("sheet_count")) is not int or report["sheet_count"] != len(report["sheets"]):
        raise ValueError("invalid sheet_count")
    if type(report.get("export_called")) is not bool or (report.get("export_return_value") is not None and type(report["export_return_value"]) is not bool):
        raise ValueError("invalid export result")
    if report.get("exception") is not None and not isinstance(report["exception"], dict):
        raise ValueError("invalid exception")


def success_problems(logs: Logs) -> list[str]:
    """Require independent report, event, transaction and actual PDF evidence."""
    report, events = logs.report, logs.events
    if not logs.complete or report is None or not events or logs.folder is None:
        return [logs.problem]
    problems = []
    if report["successful"] is not True or events[-1]["data"]["successful"] is not True or events[-1]["data"].get("exception_type"):
        problems.append("diagnostic reported failure")
    tx = report["transaction"]
    if tx.get("start_return_status") != "Started" or tx.get("status_after_start") != "Started":
        problems.append("Transaction Start not confirmed")
    if (tx.get("rollback_attempted") is not True or tx.get("rollback_succeeded") is not True
            or tx.get("rollback_return_status") != "RolledBack" or tx.get("end_status") != "RolledBack"):
        problems.append("Transaction Rollback not confirmed")
    if tx.get("dispose_succeeded") is not True or tx.get("cleanup_exceptions") != [] or tx.get("exception") or report.get("exception") or report.get("logging_error"):
        problems.append("exception, cleanup or logging failure")
    if report.get("export_called") is not True or report.get("export_return_value") is not True:
        problems.append("Document.Export did not return true")
    names = [item["event"] for item in events]
    required = ["diagnostic_started", "transaction_started", "pdf_export_started", "pdf_export_completed", "transaction_rollback_started", "transaction_finished", "diagnostic_finished"]
    if any(names.count(name) != 1 for name in required) or [name for name in names if name in required] != required:
        problems.append("required completion events missing or out of order")
    started = next((item.get("data") for item in events if item["event"] == "transaction_started"), None) or {}
    exported = next((item.get("data") for item in events if item["event"] == "pdf_export_completed"), None) or {}
    if started.get("start_return_status") != "Started" or started.get("status") != "Started":
        problems.append("transaction_started does not confirm Start")
    if exported.get("export_return_value") is not True or "pdf_export_failed" in names:
        problems.append("export events do not confirm successful Document.Export")
    finished = next((item["data"] for item in events if item["event"] == "transaction_finished"), None) or {}
    if (finished.get("rollback_attempted") is not True or finished.get("rollback_succeeded") is not True or finished.get("end_status") != "RolledBack"
            or finished.get("rollback_return_status") != "RolledBack" or finished.get("dispose_succeeded") is not True
            or finished.get("exception_type") or finished.get("cleanup_exception_type")):
        problems.append("transaction_finished does not confirm clean rollback")
    pdf = logs.folder / "drawing.pdf"
    try:
        size = pdf.stat().st_size if pdf.is_file() else 0
    except OSError:
        size = 0
    recorded = next((item.get("size_bytes") for item in report["pdf_files"] if item.get("file_name") == "drawing.pdf"), None)
    if report.get("expected_pdf_exists") is not True or size <= 0 or type(recorded) is not int or recorded != size:
        problems.append("drawing.pdf missing/empty or size differs from report")
    try:
        requested = sheet_numbers(report.get("requested_sheet_numbers", "")).split(",")
    except (ValueError, AttributeError):
        requested = []
    if not requested or requested != [item.get("sheet_number") for item in report["sheets"]]:
        problems.append("selected Sheets do not match the bounded request")
    if report["mode"] not in ("default", "saved") or (report["mode"] == "saved" and
            (not report.get("setup_name") or report.get("setup_name") != report.get("requested_setup_name"))):
        problems.append("PDF mode/setup does not match the request")
    try:
        # Compare recorded metadata without resolving or opening a log-supplied path.
        same_folder = os.path.normcase(os.path.abspath(report["output_folder"])) == os.path.normcase(str(logs.folder.resolve()))
    except (OSError, ValueError):
        same_folder = False
    if not same_folder:
        problems.append("report output_folder does not match this diagnostic folder")
    return problems


def result_status(logs: Logs) -> str:
    if not logs.complete:
        return "incomplete"
    return "failed" if success_problems(logs) else "success"


def inspect(session: Path) -> int:
    if not session.is_dir():
        raise ValueError(f"Session directory does not exist or is not a directory: {session}")
    session = session.resolve()
    logs = read_logs(session)
    status = result_status(logs)
    display(status, logs, session)
    return {"success": 0, "failed": 1, "incomplete": 5}[status]


def monitor(run_root: Path, process, timeout: float) -> tuple[str, Logs]:
    deadline = time.monotonic() + timeout
    while True:
        logs = read_logs(run_root)
        if logs.complete:
            return result_status(logs), logs
        stopped = "incomplete" if logs.terminal else None
        if stopped is None:
            if process.poll() is not None:
                stopped = "revit_exited"
            elif time.monotonic() >= deadline:
                stopped = "timeout"
        if stopped is not None:
            # The writer may finish between the first read and poll/deadline checks.
            logs = read_logs(run_root)
            if logs.complete or logs.terminal:
                return result_status(logs), logs
            return stopped, logs
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))


def display(status: str, logs: Logs, run_root: Path) -> None:
    print(f"Result: {status}")
    print(f"Session folder: {run_root.resolve()}")
    print(f"Diagnostic folder: {logs.folder.resolve() if logs.folder else 'not identified'}")
    print(f"Logs: {'complete' if logs.complete else 'incomplete'}{': ' + logs.problem if logs.problem else ''}")
    print(f"Last event: {logs.events[-1]['event'] if logs.events else 'not available'}; "
          f"stage: {logs.events[-1].get('stage') if logs.events else 'not available'}")
    report = logs.report
    if report is None:
        return
    print(f"Revit: {report.get('revit_version')} / build {report.get('revit_version_build')}")
    sheets = report.get("sheets")
    print(f"Sheets: {', '.join(str(item.get('sheet_number')) for item in sheets) if isinstance(sheets, list) and all(isinstance(item, dict) for item in sheets) else 'unknown'} (count={report.get('sheet_count')}); requested={report.get('requested_sheet_numbers')}")
    print(f"PDF mode: {report.get('mode')}; setup: {report.get('setup_name') or report.get('requested_setup_name')}")
    tx = report.get("transaction")
    tx = tx if isinstance(tx, dict) else {}
    for name, fields in TRANSACTION_FIELDS.items():
        missing = [field for field in fields if field not in tx]
        if missing:
            print(f"Unverified: Transaction {name}; missing evidence: {', '.join(missing)}")
    if not isinstance(report.get("timings_ms"), dict):
        print("Unverified: processing time; missing/invalid timings_ms")
    print(f"Transaction Start: return={tx.get('start_return_status')}, status={tx.get('status_after_start')}")
    print(f"Document.Export: called={report.get('export_called')}, return={report.get('export_return_value')}")
    print(f"PDF: expected_exists={report.get('expected_pdf_exists')}; files={json.dumps(report.get('pdf_files'), ensure_ascii=False)}")
    print(f"Transaction Rollback: attempted={tx.get('rollback_attempted')}, return={tx.get('rollback_return_status')}, end={tx.get('end_status')}, confirmed={tx.get('rollback_succeeded')}; disposed={tx.get('dispose_succeeded')}")
    print(f"Timings (ms): {json.dumps(report.get('timings_ms'), ensure_ascii=False)}")
    print(f"Exception: {json.dumps(report.get('exception'), ensure_ascii=False)}")
    print(f"Transaction exception: {json.dumps(tx.get('exception'), ensure_ascii=False)}; cleanup: {json.dumps(tx.get('cleanup_exceptions'), ensure_ascii=False)}")
    print(f"Logging error: {report.get('logging_error')}")
    if logs.complete:
        for problem in success_problems(logs):
            print(f"Failure: {problem}")


def journal_files(folder: Path) -> set[Path]:
    return set(folder.glob("journal*.txt"))


def collect_journals(folder: Path, before: set[Path], run_root: Path) -> None:
    # Revit owns the source files. Copy only new files, never alter/truncate them.
    # No journal-to-PID contract exists; explicitly label these as candidates.
    candidates = sorted(journal_files(folder) - before)
    if not candidates:
        print(f"Journal: no new journal*.txt found under {folder}")
    for path in candidates:
        try:
            destination = run_root / "journals" / path.name
            destination.parent.mkdir(exist_ok=True)
            with path.open("rb") as source, destination.open("xb") as target:
                shutil.copyfileobj(source, target)
            print(f"Journal candidate snapshot: {destination} (source: {path}; may still be writing)")
        except OSError as error:
            print(f"Journal snapshot unavailable: {path}: {error}")


def run(args) -> int:
    from . import cli

    validate(args)
    if os.name != "nt":
        raise ValueError("dev-pdf requires Windows with a locally installed Revit; no build or launch performed")
    install = (args.revit_install_dir or cli.default_install(args.version)).resolve()
    executable = install / "Revit.exe"
    if not executable.is_file():
        raise ValueError(f"Revit.exe not found: {executable}. Use --revit-install-dir.")
    require_closed()
    manifest, template = manifest_plan(cli.ROOT, args.version)
    # A fresh private root is the run identifier; the unchanged C# command creates its own child.
    args.output_root.mkdir(parents=True, exist_ok=True)
    run_root = Path(tempfile.mkdtemp(prefix="dev-pdf-", dir=args.output_root.resolve()))
    print(f"Session folder: {run_root}", flush=True)
    try:
        assembly = cli.build(args.version, args.runtime, install, "Release", None).resolve()
    except SystemExit as error:
        raise ValueError(f"Release build preflight failed: {error}") from error
    require_closed()  # Recheck after the build, before changing registration or launching.
    register_manifest(manifest, template, assembly)
    print(f"Assembly: {assembly}\nManifest: {manifest.resolve()}")
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise ValueError("LOCALAPPDATA is unavailable; cannot locate Revit journals")
    journal_dir = Path(local) / "Autodesk/Revit" / f"Autodesk Revit {args.version}" / "Journals"
    before = journal_files(journal_dir)
    env = child_environment(args, run_root)
    print(f"Starting: {executable}\nOpen the model, then click Add-Ins > External Tools > {COMMAND}.\n"
          f"Waiting up to {args.timeout:g} seconds. Ctrl+C stops monitoring and leaves Revit running.", flush=True)
    # A separate process group prevents console Ctrl+C being forwarded to Revit.
    process = subprocess.Popen([str(executable)], cwd=install, env=env,
                               creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    try:
        status, logs = monitor(run_root, process, args.timeout)
    except KeyboardInterrupt:
        status, logs = "interrupted", read_logs(run_root)
    display(status, logs, run_root)
    collect_journals(journal_dir, before, run_root)
    print("Revit was not terminated. No model save or synchronization was requested.")
    return {"success": 0, "failed": 1, "timeout": 3, "revit_exited": 4, "incomplete": 5, "interrupted": 130}[status]
