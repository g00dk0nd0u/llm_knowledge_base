"""Thin pyRevit/Clarity entry point; Revit extraction remains in the .NET exporter."""

import json
import os
import subprocess

import clr
from System import AppDomain
from System.Reflection import Assembly

from assembly_selection import SERVICE_TYPE, configured_fallback, find_loaded_exporter


def load_exporter_assembly(config):
    assembly = find_loaded_exporter(AppDomain.CurrentDomain.GetAssemblies())
    if assembly is None:
        path = configured_fallback(config)
        if not os.path.isfile(path):
            raise RuntimeError("Configured exporter_assembly does not exist: " + path)
        try:
            assembly = Assembly.LoadFrom(path)
        except Exception as error:
            raise RuntimeError("Failed to load configured exporter_assembly: " + path) from error
    service_type = assembly.GetType(SERVICE_TYPE, False)
    if service_type is None:
        raise RuntimeError(
            "Exporter assembly does not expose LlmKnowledgeBase.Revit.OfflineExportService")
    clr.AddReference(assembly.GetName().Name)
    return assembly


def main():
    config_path = os.environ.get("LLM_KB_UNATTENDED_CONFIG")
    if not config_path:
        raise RuntimeError("LLM_KB_UNATTENDED_CONFIG is not set")
    with open(config_path, "r") as stream:
        config = json.load(stream)
    required = ("project_key", "export_root", "publish_root", "archive_timezone",
                "python_executable", "repository_root")
    missing = [name for name in required if not config.get(name)]
    if missing:
        raise RuntimeError("Invalid unattended config; missing: " + ", ".join(missing))
    for name in ("export_root", "publish_root", "python_executable", "repository_root"):
        if not os.path.isabs(config[name]):
            raise RuntimeError("Invalid unattended config; {0} must be absolute".format(name))

    load_exporter_assembly(config)
    from LlmKnowledgeBase.Revit import OfflineExportOptions, OfflineExportService

    ui_document = getattr(__revit__, "ActiveUIDocument", None)  # noqa: F821 - supplied by pyRevit
    document = getattr(ui_document, "Document", None)
    if document is None:
        raise RuntimeError("No active Revit document is available")
    options = OfflineExportOptions()
    options.ExportRoot = config["export_root"]
    options.IncludeLinks = config.get("include_links", True)
    completed_run = str(OfflineExportService.Run(document, options))
    command = [config["python_executable"], "-m", "tools.unattended_export",
               "finalize-publish", "--config", config_path, "--run", completed_run]
    result = subprocess.call(command, cwd=config["repository_root"])
    if result:
        raise RuntimeError("Finalize/publish failed with exit code {0}".format(result))
    return completed_run


main()
