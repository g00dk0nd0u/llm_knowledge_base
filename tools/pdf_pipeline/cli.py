"""Command-line interface for PDF processing and on-demand rendering."""

from __future__ import annotations

import argparse
from pathlib import Path


def _configure_utf8_output() -> None:
    """Keep human-readable report output portable across console encodings."""
    import sys

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.pdf_pipeline")
    parser.add_argument(
        "--repo-root", type=Path, default=Path.cwd(), help=argparse.SUPPRESS
    )
    commands = parser.add_subparsers(dest="command", required=True)

    process = commands.add_parser("process", help="generate knowledge from source PDFs")
    process.add_argument("--all", action="store_true", required=True)

    render = commands.add_parser("render", help="render selected source pages to PNG")
    render.add_argument("pdf", type=Path)
    selection = render.add_mutually_exclusive_group(required=True)
    selection.add_argument("--pages", help="one-based pages, e.g. 1,3-5")
    selection.add_argument("--all", action="store_true", dest="all_pages")
    render.add_argument("--dpi", type=int, default=300)
    render.add_argument("--output", type=Path, required=True)
    candidate = commands.add_parser(
        "render-candidate", help="verify and render an explicitly selected Vision candidate"
    )
    candidate.add_argument("database", type=Path)
    candidate.add_argument("semantic_entity_id")
    candidate.add_argument("candidate_id")
    candidate.add_argument("--pdf", type=Path, required=True)
    candidate.add_argument("--dpi", type=int, default=300)
    candidate.add_argument("--output", type=Path, required=True)
    context = commands.add_parser(
        "render-context-candidate", help="render fixed-margin V1 context for an explicit region candidate"
    )
    context.add_argument("database", type=Path)
    context.add_argument("semantic_entity_id")
    context.add_argument("candidate_id")
    context.add_argument("--pdf", type=Path, required=True)
    context.add_argument("--dpi", type=int, default=300)
    context.add_argument("--output", type=Path, required=True)
    report = commands.add_parser("report", help="summarize existing Pipeline v2 knowledge")
    report.add_argument("project_directory", type=Path)
    report.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.repo_root.resolve()
    if args.command == "report":
        _configure_utf8_output()
        from .report import ReportError, build_report, format_text

        try:
            project_directory = (
                args.project_directory
                if args.project_directory.is_absolute()
                else root / args.project_directory
            )
            report = build_report(project_directory)
            if args.format == "json":
                import json

                print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(format_text(report))
        except ReportError as exc:
            print(f"error: {exc}", file=__import__("sys").stderr)
            return 1
        return 0
    from .pipeline import PipelineError, process_all, render_pages

    try:
        if args.command == "process":
            result = process_all(root)
            print(
                f"processed={result.processed} unchanged={result.unchanged} "
                f"removed={result.removed}"
            )
        elif args.command == "render":
            outputs = render_pages(
                root,
                args.pdf,
                pages=args.pages,
                all_pages=args.all_pages,
                dpi=args.dpi,
                output=args.output,
            )
            for output in outputs:
                print(output)
        elif args.command == "render-candidate":
            import json

            from .vision_render import render_vision_candidate

            _configure_utf8_output()
            result = render_vision_candidate(
                root, args.database, args.semantic_entity_id, args.candidate_id,
                args.pdf, dpi=args.dpi, output=args.output,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True,
                             allow_nan=False))
        elif args.command == "render-context-candidate":
            import json

            from .vision_render import render_vision_context_candidate

            _configure_utf8_output()
            result = render_vision_context_candidate(
                root, args.database, args.semantic_entity_id, args.candidate_id,
                args.pdf, dpi=args.dpi, output=args.output,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True,
                             allow_nan=False))
    except PipelineError as exc:
        print(f"error: {exc}", file=__import__("sys").stderr)
        return 1
    return 0
