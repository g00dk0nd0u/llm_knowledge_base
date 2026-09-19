"""Revit-independent selection rules for the thin Clarity bridge."""

import os

SERVICE_TYPE = "LlmKnowledgeBase.Revit.OfflineExportService"


def find_loaded_exporter(assemblies):
    return next((assembly for assembly in assemblies
                 if assembly.GetName().Name.startswith("LlmKnowledgeBase.Revit20")
                 and assembly.GetType(SERVICE_TYPE, False) is not None), None)


def configured_fallback(config):
    path = config.get("exporter_assembly")
    if not path:
        raise RuntimeError(
            "Revit exporter assembly is not loaded and exporter_assembly is not configured")
    if not os.path.isabs(path):
        raise RuntimeError("Invalid unattended config; exporter_assembly must be absolute")
    return path
