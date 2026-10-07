"""CLI selection tests and real MSBuild graph checks without Autodesk binaries."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tools.revit_exporter import cli


PROFILES = [
    ("2025", "net8", "LlmKnowledgeBase.Revit2025", "net8.0-windows", 8),
    ("2026", "net8", "LlmKnowledgeBase.Revit2026", "net8.0-windows", 8),
    ("2026", "net10", "LlmKnowledgeBase.Revit2026Net10", "net10.0-windows", 10),
    ("2027", "net10", "LlmKnowledgeBase.Revit2027", "net10.0-windows", 10),
]
ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "revit_exporter/src/LlmKnowledgeBase.Revit.Core/LlmKnowledgeBase.Revit.Core.csproj"


@pytest.mark.parametrize("version,runtime,project,framework,sdk", PROFILES)
def test_selected_profile_build_command_and_manifest(
    tmp_path, monkeypatch, version, runtime, project, framework, sdk,
):
    assert cli.select_profile(version, runtime) == (project, framework, sdk)
    install = tmp_path / "installed Revit"
    install.mkdir()
    for name in ("RevitAPI.dll", "RevitAPIUI.dll"):
        (install / name).touch()
    # These files support a CLI unit test, not a simulated host compilation.
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "sdk_majors", lambda: {sdk})
    project_path = tmp_path / "revit_exporter/src" / project / f"{project}.csproj"
    assembly = project_path.parent / "bin/Release" / framework / f"{project}.dll"
    assembly.parent.mkdir(parents=True)
    assembly.touch()
    template = ROOT / "revit_exporter/manifests" / version / "LlmKnowledgeBase.Revit.addin.template"
    local_template = tmp_path / template.relative_to(ROOT)
    local_template.parent.mkdir(parents=True)
    shutil.copyfile(template, local_template)
    commands = []
    monkeypatch.setattr(cli.subprocess, "run", lambda command, **kwargs: commands.append((command, kwargs)))
    manifest = tmp_path / "Addins" / version / "LlmKnowledgeBase.Revit.addin"

    assert cli.build(version, runtime, install, "Release", manifest) == assembly
    assert commands == [([
        "dotnet", "build", str(project_path), "--configuration", "Release",
        f"-p:RevitInstallDir={install}",
        f"-p:RevitCoreTargetFramework={framework.removesuffix('-windows')}",
    ], {"check": True})]
    assert manifest.read_text(encoding="utf-8") == template.read_text(encoding="utf-8").replace(
        "{{ASSEMBLY_PATH}}", str(assembly.resolve()),
    )


@pytest.mark.parametrize("version", ["2026", "2027"])
def test_net10_preflight_fails_before_build_with_only_sdk8(tmp_path, monkeypatch, version):
    for name in ("RevitAPI.dll", "RevitAPIUI.dll"):
        (tmp_path / name).touch()
    monkeypatch.setattr(cli, "sdk_majors", lambda: {8})
    commands = []
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: commands.append(args))
    manifest = tmp_path / "must-not-exist.addin"
    with pytest.raises(SystemExit, match=r"A \.NET 10 SDK is required"):
        cli.build(version, "net10", tmp_path, "Release", manifest)
    assert commands == []
    assert not manifest.exists()


@pytest.mark.parametrize("version,runtime", [("2025", "net10"), ("2027", "net8"), ("2026", "invalid")])
def test_unsupported_profile_never_builds(tmp_path, monkeypatch, version, runtime):
    commands = []
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: commands.append(args))
    with pytest.raises(ValueError, match="unsupported Revit/runtime profile"):
        cli.build(version, runtime, tmp_path, "Release", None)
    assert commands == []


def msbuild_with_sdk(tmp_path: Path, major: int):
    """Pin the real installed SDK, including SDK 8 when SDK 10 is also present."""
    dotnet = shutil.which("dotnet")
    if not dotnet:
        pytest.skip("real MSBuild graph check requires an installed .NET SDK")
    installed = subprocess.run([dotnet, "--list-sdks"], check=True, capture_output=True, text=True)
    versions = [line.split()[0] for line in installed.stdout.splitlines() if line.startswith(f"{major}.")]
    if not versions:
        pytest.skip(f"real MSBuild graph check requires .NET SDK {major}")
    (tmp_path / "global.json").write_text(json.dumps({
        "sdk": {"version": versions[-1], "rollForward": "disable"},
    }), encoding="utf-8")
    env = dict(os.environ, DOTNET_NOLOGO="1", DOTNET_CLI_TELEMETRY_OPTOUT="1")
    selected = subprocess.run([dotnet, "--version"], cwd=tmp_path, env=env, check=True, capture_output=True, text=True)
    assert selected.stdout.strip() == versions[-1]

    def run(*arguments):
        return subprocess.run(
            [dotnet, "msbuild", *map(str, arguments), "-nologo"],
            cwd=tmp_path, env=env, capture_output=True, text=True,
        )

    return run


@pytest.mark.parametrize("version,runtime,project,framework,sdk", PROFILES)
def test_real_host_restore_graph_contains_only_selected_framework(
    tmp_path, version, runtime, project, framework, sdk,
):
    run = msbuild_with_sdk(tmp_path, sdk)
    host = ROOT / "revit_exporter/src" / project / f"{project}.csproj"
    graph_path = tmp_path / "restore-graph.json"
    selected = framework.removesuffix("-windows")
    result = run(host, "-t:GenerateRestoreGraphFile", "-p:EnableWindowsTargeting=true",
                 f"-p:RevitCoreTargetFramework={selected}", f"-p:RestoreGraphOutputPath={graph_path}")
    assert result.returncode == 0, result.stdout + result.stderr
    projects = json.loads(graph_path.read_text(encoding="utf-8"))["projects"]
    records = {record["restore"]["projectName"]: record for record in projects.values()}
    assert set(records) == {project, "LlmKnowledgeBase.Revit.Core"}
    assert records[project]["restore"]["originalTargetFrameworks"] == [framework]
    core = records["LlmKnowledgeBase.Revit.Core"]
    assert core["restore"]["originalTargetFrameworks"] == [selected]
    assert set(core["frameworks"]) == {selected}
    references = next(iter(records[project]["restore"]["frameworks"].values()))["projectReferences"]
    assert {Path(key).resolve() for key in references} == {CORE.resolve()}


def test_core_default_multitarget_contract_and_sdk8_failure_without_selection(tmp_path):
    run = msbuild_with_sdk(tmp_path, 8)
    default = run(CORE, "-getProperty:TargetFrameworks")
    assert default.returncode == 0, default.stdout + default.stderr
    assert default.stdout.strip() == "net8.0;net10.0"
    host = ROOT / "revit_exporter/src/LlmKnowledgeBase.Revit2025/LlmKnowledgeBase.Revit2025.csproj"
    unselected = run(host, "-t:GenerateRestoreGraphFile", "-p:EnableWindowsTargeting=true",
                     f"-p:RestoreGraphOutputPath={tmp_path / 'unselected.json'}")
    assert unselected.returncode != 0
    assert "NETSDK1045" in unselected.stdout + unselected.stderr
