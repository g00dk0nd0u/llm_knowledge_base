from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
SUPPORTED = {"2025": 8, "2026": 8, "2027": 10}


def default_install(version: str) -> Path:
    return Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Autodesk" / f"Revit {version}"


def sdk_majors() -> set[int]:
    dotnet = shutil.which("dotnet")
    if not dotnet:
        return set()
    result = subprocess.run([dotnet, "--list-sdks"], check=True, text=True, capture_output=True)
    return {int(match.group(1)) for line in result.stdout.splitlines() if (match := re.match(r"(\d+)\.", line))}


def build(version: str, install: Path, configuration: str, manifest: Path | None) -> Path:
    missing = [name for name in ("RevitAPI.dll", "RevitAPIUI.dll") if not (install / name).is_file()]
    if missing:
        raise SystemExit(f"Revit {version} assemblies not found under {install}: {', '.join(missing)}. Use --revit-install-dir.")
    required = SUPPORTED[version]
    if required not in sdk_majors():
        raise SystemExit(f"A .NET {required} SDK is required for Revit {version}; install it and ensure dotnet is on PATH.")
    project = ROOT / "revit_exporter" / "src" / f"LlmKnowledgeBase.Revit{version}" / f"LlmKnowledgeBase.Revit{version}.csproj"
    subprocess.run(["dotnet", "build", str(project), "--configuration", configuration, f"-p:RevitInstallDir={install}"], check=True)
    framework = "net10.0-windows" if version == "2027" else "net8.0-windows"
    assembly = project.parent / "bin" / configuration / framework / f"LlmKnowledgeBase.Revit{version}.dll"
    if not assembly.is_file():
        raise SystemExit(f"Build completed but expected assembly is missing: {assembly}")
    if manifest:
        template = ROOT / "revit_exporter" / "manifests" / version / "LlmKnowledgeBase.Revit.addin.template"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(template.read_text(encoding="utf-8").replace("{{ASSEMBLY_PATH}}", str(assembly.resolve())), encoding="utf-8")
        print(f"Manifest: {manifest}")
    print(f"Assembly: {assembly}")
    return assembly


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m tools.revit_exporter")
    commands = root.add_subparsers(dest="command", required=True)
    command = commands.add_parser("build", help="build one host against a locally installed Revit")
    command.add_argument("--version", choices=SUPPORTED, required=True)
    command.add_argument("--revit-install-dir", type=Path)
    command.add_argument("--configuration", default="Release", choices=("Debug", "Release"))
    command.add_argument("--manifest", type=Path, help="write a machine-local .addin file")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "build":
        build(args.version, args.revit_install_dir or default_install(args.version), args.configuration, args.manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
