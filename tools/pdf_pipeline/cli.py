"""Command-line interface for PDF processing and on-demand rendering."""

from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import PipelineError, process_all, render_pages


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.repo_root.resolve()
    try:
        if args.command == "process":
            result = process_all(root)
            print(
                f"processed={result.processed} unchanged={result.unchanged} "
                f"removed={result.removed}"
            )
        else:
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
    except PipelineError as exc:
        print(f"error: {exc}", file=__import__("sys").stderr)
        return 1
    return 0
