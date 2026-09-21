from __future__ import annotations

import argparse
import json
from pathlib import Path

from .fixtures import build_synthetic_fixture
from .package import cached_payload, extract_payload, inspect_pdf, package_pdf
from .pdf_adapter import build_pdf_database
from .project_pdf_adapter import build_pdf_project_database
from .project_pdf_update import (
    compare_pdf_project_database,
    update_pdf_project_database,
)
from .query import QueryCore
from .revit_snapshot import finalize_revit_export


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
    build_pdf = commands.add_parser(
        "build-pdf", help="build Query Core from one PDF Pipeline v1 document"
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
    finalize = commands.add_parser(
        "finalize-revit-export",
        help="validate a Revit export and create its Enhanced PDF",
    )
    finalize.add_argument("directory", type=Path)
    finalize.add_argument("--output", type=Path)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "build-fixture":
        drawing, database = build_synthetic_fixture(args.directory)
        print(
            json.dumps({"drawing": str(drawing), "database": str(database)}, indent=2)
        )
    elif args.command == "package":
        print(package_pdf(args.drawing, args.database, args.output))
    elif args.command == "inspect":
        print(json.dumps(inspect_pdf(args.pdf), indent=2, sort_keys=True))
    elif args.command == "extract":
        print(
            extract_payload(args.pdf, args.output)
            if args.output
            else cached_payload(args.pdf)
        )
    elif args.command == "search":
        database = (
            cached_payload(args.source)
            if args.source.suffix.lower() == ".pdf"
            else args.source
        )
        with QueryCore(database) as core:
            print(
                json.dumps(core.search_text(args.query), ensure_ascii=False, indent=2)
            )
    elif args.command == "build-pdf":
        print(build_pdf_database(args.repo_root, args.knowledge_directory, args.output))
    elif args.command == "build-pdf-project":
        print(
            build_pdf_project_database(
                args.repo_root, args.project_directory, args.output
            )
        )
    elif args.command == "compare-pdf-project":
        report = compare_pdf_project_database(
            args.repo_root, args.project_directory, args.database
        )
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    elif args.command == "update-pdf-project":
        result = update_pdf_project_database(
            args.repo_root, args.project_directory, args.database
        )
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    elif args.command == "finalize-revit-export":
        print(finalize_revit_export(args.directory, args.output))
    return 0
