import sys

from .cli import main

try:
    raise SystemExit(main())
except ModuleNotFoundError as error:
    dependency = {"fitz": "PyMuPDF", "jsonschema": "jsonschema"}.get(error.name)
    if dependency is None:
        raise
    print(
        f"This command requires {dependency}. "
        "Querying an existing project.sqlite does not.",
        file=sys.stderr,
    )
    raise SystemExit(2) from None
