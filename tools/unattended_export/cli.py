from __future__ import annotations

import argparse

from .publish import finalize_and_publish, load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Finalize and publish an unattended Revit export")
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("finalize-publish")
    publish.add_argument("--config", required=True)
    publish.add_argument("--run", required=True)
    args = parser.parse_args(argv)
    destination = finalize_and_publish(load_config(args.config), args.run)
    print(destination)
    return 0
