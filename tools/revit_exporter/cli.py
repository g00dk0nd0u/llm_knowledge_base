from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
PROFILES = {
    ("2025", "net8"): ("LlmKnowledgeBase.Revit2025", "net8.0-windows", 8),
    ("2026", "net8"): ("LlmKnowledgeBase.Revit2026", "net8.0-windows", 8),
    ("2026", "net10"): ("LlmKnowledgeBase.Revit2026Net10", "net10.0-windows", 10),
    ("2027", "net10"): ("LlmKnowledgeBase.Revit2027", "net10.0-windows", 10),
}


def select_profile(version: str, runtime: str) -> tuple[str, str, int]:
    try:
        return PROFILES[(version, runtime)]
    except KeyError as error:
        valid = ", ".join(f"{item[0]}/{item[1]}" for item in PROFILES)
        raise ValueError(f"unsupported Revit/runtime profile {version}/{runtime}; valid profiles: {valid}") from error


def default_install(version: str) -> Path:
    return Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Autodesk" / f"Revit {version}"


def sdk_majors() -> set[int]:
    dotnet = shutil.which("dotnet")
    if not dotnet:
        return set()
    result = subprocess.run([dotnet, "--list-sdks"], check=True, text=True, capture_output=True)
    return {int(match.group(1)) for line in result.stdout.splitlines() if (match := re.match(r"(\d+)\.", line))}


def build(version: str, runtime: str, install: Path, configuration: str, manifest: Path | None) -> Path:
    project_name, framework, required = select_profile(version, runtime)
    missing = [name for name in ("RevitAPI.dll", "RevitAPIUI.dll") if not (install / name).is_file()]
    if missing:
        raise SystemExit(f"Revit {version} assemblies not found under {install}: {', '.join(missing)}. Use --revit-install-dir.")
    if required not in sdk_majors():
        raise SystemExit(f"A .NET {required} SDK is required for Revit {version}; install it and ensure dotnet is on PATH.")
    project = ROOT / "revit_exporter" / "src" / project_name / f"{project_name}.csproj"
    subprocess.run(
        ["dotnet", "build", str(project), "--configuration", configuration,
         f"-p:RevitInstallDir={install}",
         f"-p:RevitCoreTargetFramework={framework.removesuffix('-windows')}"],
        check=True,
    )
    assembly = project.parent / "bin" / configuration / framework / f"{project_name}.dll"
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
    command.add_argument("--version", choices=("2025", "2026", "2027"), required=True)
    command.add_argument("--runtime", choices=("net8", "net10"), required=True)
    command.add_argument("--revit-install-dir", type=Path)
    command.add_argument("--configuration", default="Release", choices=("Debug", "Release"))
    command.add_argument("--manifest", type=Path, help="write a machine-local .addin file")
    command = commands.add_parser("dev-pdf", help="build, register, launch and monitor bounded PDF diagnostics on Windows")
    command.add_argument("--version", choices=("2025", "2026", "2027"), required=True)
    command.add_argument("--runtime", choices=("net8", "net10"), required=True)
    command.add_argument("--revit-install-dir", type=Path)
    command.add_argument("--sheets", required=True, help="one or two exact, comma-separated Sheet numbers")
    command.add_argument("--mode", choices=("default", "saved"), required=True)
    command.add_argument("--setup-name", help="exact existing PDF export setting name (saved mode only)")
    command.add_argument("--output-root", type=Path, required=True, help="local parent for a unique dev-pdf session directory")
    command.add_argument("--timeout", type=float, default=900, help="monitoring timeout in seconds, including model opening (default: 900)")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "dev-pdf":
        from . import dev_pdf
        import xml.etree.ElementTree as ET

        try:
            return dev_pdf.run(args)
        except (ValueError, OSError, subprocess.SubprocessError, ET.ParseError) as error:
            print(f"dev-pdf stopped: {error}", file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            print("dev-pdf interrupted; no Revit process was terminated.", file=sys.stderr)
            return 130
    if args.command == "build":
        try:
            build(args.version, args.runtime, args.revit_install_dir or default_install(args.version), args.configuration, args.manifest)
        except ValueError as error:
            raise SystemExit(str(error)) from error
    return 0


if __name__ == "__main__":
    sys.exit(main())
