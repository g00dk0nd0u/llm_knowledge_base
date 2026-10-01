from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .query import QueryCore


def _configure_utf8_output() -> None:
    """Keep human-readable JSON portable across platform console encodings."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m tools.query_core")
    commands = root.add_subparsers(dest="command", required=True)
    fixture = commands.add_parser("build-fixture")
    fixture.add_argument("directory", type=Path)
    package = commands.add_parser("package")
    package.add_argument("drawing", type=Path)
    package.add_argument("database", type=Path)
    package.add_argument("output", type=Path)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("pdf", type=Path)
    extract = commands.add_parser("extract")
    extract.add_argument("pdf", type=Path)
    extract.add_argument("output", type=Path, nargs="?")
    search = commands.add_parser("search")
    search.add_argument("source", type=Path)
    search.add_argument("query")
    architectural = commands.add_parser(
        "architectural-context", help="retrieve one-hop context for a semantic entity"
    )
    architectural.add_argument("source", type=Path)
    architectural.add_argument("semantic_entity_id")
    build_pdf = commands.add_parser(
        "build-pdf", help="build Query Core from one PDF Pipeline v1 or v2 document"
    )
    build_pdf.add_argument("--repo-root", type=Path, default=Path("."))
    build_pdf.add_argument("knowledge_directory", type=Path)
    build_pdf.add_argument("output", type=Path)
    build_project = commands.add_parser(
        "build-pdf-project", help="build Query Core from a PDF Pipeline project manifest"
    )
    build_project.add_argument("--repo-root", type=Path, default=Path("."))
    build_project.add_argument("project_directory", type=Path)
    build_project.add_argument("output", type=Path)
    compare_project = commands.add_parser(
        "compare-pdf-project",
        help="report project document changes and duplicate-byte groups without mutation",
    )
    compare_project.add_argument("--repo-root", type=Path, default=Path("."))
    compare_project.add_argument("project_directory", type=Path)
    compare_project.add_argument("database", type=Path)
    update_project = commands.add_parser(
        "update-pdf-project",
        help="incrementally update an existing PDF project Query Core",
    )
    update_project.add_argument("--repo-root", type=Path, default=Path("."))
    update_project.add_argument("project_directory", type=Path)
    update_project.add_argument("database", type=Path)
    package_project = commands.add_parser(
        "package-pdf-project", help="create a portable project Query Bundle directory"
    )
    package_project.add_argument("--repo-root", type=Path, default=Path("."))
    package_project.add_argument("project_directory", type=Path)
    package_project.add_argument("database", type=Path)
    package_project.add_argument("output", type=Path)
    inspect_project = commands.add_parser(
        "inspect-pdf-project-bundle", help="strictly verify a project Query Bundle"
    )
    inspect_project.add_argument("bundle_directory", type=Path)
    finalize = commands.add_parser(
        "finalize-revit-export",
        help="validate a Revit export and create its Enhanced PDF",
    )
    finalize.add_argument("directory", type=Path)
    finalize.add_argument("--output", type=Path)
    return root


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_output()
    args = parser().parse_args(argv)
    if args.command == "build-fixture":
        from .fixtures import build_synthetic_fixture

        drawing, database = build_synthetic_fixture(args.directory)
        print(
            json.dumps({"drawing": str(drawing), "database": str(database)}, indent=2)
        )
    elif args.command == "package":
        from .package import package_pdf

        print(package_pdf(args.drawing, args.database, args.output))
    elif args.command == "inspect":
        from .package import inspect_pdf

        print(json.dumps(inspect_pdf(args.pdf), indent=2, sort_keys=True))
    elif args.command == "extract":
        from .package import cached_payload, extract_payload

        print(
            extract_payload(args.pdf, args.output)
            if args.output
            else cached_payload(args.pdf)
        )
    elif args.command in {"search", "architectural-context"}:
        database = args.source
        if args.source.suffix.lower() == ".pdf":
            from .package import cached_payload

            database = cached_payload(args.source)
        elif args.source.is_dir():
            from .project_bundle import project_bundle_database

            database = project_bundle_database(args.source)
        with QueryCore(database) as core:
            result = (
                core.search_text(args.query)
                if args.command == "search"
                else core.get_architectural_evidence_context(args.semantic_entity_id)
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "build-pdf":
        from .pdf_adapter import build_pdf_database

        print(build_pdf_database(args.repo_root, args.knowledge_directory, args.output))
    elif args.command == "build-pdf-project":
        from .project_pdf_adapter import build_pdf_project_database

        print(
            build_pdf_project_database(
                args.repo_root, args.project_directory, args.output
            )
        )
    elif args.command == "compare-pdf-project":
        from .project_pdf_update import compare_pdf_project_database

        report = compare_pdf_project_database(
            args.repo_root, args.project_directory, args.database
        )
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    elif args.command == "update-pdf-project":
        from .project_pdf_update import update_pdf_project_database

        result = update_pdf_project_database(
            args.repo_root, args.project_directory, args.database
        )
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    elif args.command == "package-pdf-project":
        from .project_bundle import package_pdf_project_bundle

        print(package_pdf_project_bundle(
            args.repo_root, args.project_directory, args.database, args.output
        ))
    elif args.command == "inspect-pdf-project-bundle":
        from .project_bundle import inspect_pdf_project_bundle

        print(json.dumps(
            inspect_pdf_project_bundle(args.bundle_directory),
            ensure_ascii=False, indent=2, sort_keys=True,
        ))
    elif args.command == "finalize-revit-export":
        from .revit_snapshot import finalize_revit_export

        print(finalize_revit_export(args.directory, args.output))
    return 0
