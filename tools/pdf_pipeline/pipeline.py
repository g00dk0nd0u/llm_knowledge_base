"""Source-faithful, deterministic PDF knowledge generation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import fitz

from . import PIPELINE_VERSION

LOW_TEXT_THRESHOLD = 40
MANY_DRAWINGS_THRESHOLD = 200
MANAGED_MARKER = ".pdf-pipeline-v1"


class PipelineError(RuntimeError):
    """An actionable pipeline failure."""


@dataclass(frozen=True)
class ProcessResult:
    processed: int = 0
    unchanged: int = 0
    removed: int = 0


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _relative_source(root: Path, pdf: Path) -> str:
    return pdf.resolve().relative_to(root.resolve()).as_posix()


def document_id(source_file: str) -> str:
    """Return a stable identity based only on normalized repository-relative path."""
    normalized = PurePosixPath(source_file).as_posix()
    return "doc-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _slug(pdf: Path, doc_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", pdf.stem).strip("-.") or "document"
    return f"{safe}--{doc_id[4:16]}"


def _normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip()


def _status(char_count: int) -> str:
    if char_count == 0:
        return "no_text"
    if char_count < LOW_TEXT_THRESHOLD:
        return "minimal_text"
    return "extracted"


def _frontmatter(values: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in values.items():
        rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        lines.append(f"{key}: {rendered}")
    return "\n".join(lines) + "\n---\n"


def _metadata(document: fitz.Document) -> dict[str, str]:
    # PyMuPDF exposes standard embedded fields. Empty values are deliberately omitted.
    return {
        key: value
        for key, value in sorted((document.metadata or {}).items())
        if isinstance(value, str) and value.strip()
    }


def _build_document(root: Path, project: Path, pdf: Path, destination: Path) -> dict[str, Any]:
    source_file = _relative_source(root, pdf)
    source_hash = _sha256(pdf)
    doc_id = document_id(source_file)
    try:
        document = fitz.open(pdf)
    except Exception as exc:
        raise PipelineError(f"cannot open corrupt or invalid PDF {source_file}: {exc}") from exc
    try:
        if document.needs_pass:
            raise PipelineError(f"password-protected PDF is unsupported: {source_file}")
        page_count = document.page_count
        if page_count < 1:
            raise PipelineError(f"PDF has no pages: {source_file}")
        pages: list[dict[str, Any]] = []
        page_dir = destination / "pages"
        page_dir.mkdir(parents=True)
        project_id = project.name
        for number, page in enumerate(document, start=1):
            text = _normalize_text(page.get_text("text", sort=True))
            char_count = len(text)
            image_count = len(page.get_images(full=True))
            try:
                drawing_count = len(page.get_drawings())
            except Exception:
                drawing_count = 0
            status = _status(char_count)
            vision = (
                char_count < LOW_TEXT_THRESHOLD
                or image_count > 0
                or drawing_count >= MANY_DRAWINGS_THRESHOLD
            )
            page_data = {
                "page": number,
                "extraction_status": status,
                "text_char_count": char_count,
                "image_count": image_count,
                "drawing_count": drawing_count,
                "vision_recommended": vision,
            }
            pages.append(page_data)
            frontmatter = {
                "document_id": doc_id,
                "source_file": source_file,
                "source_sha256": source_hash,
                "project_id": project_id,
                "page": number,
                "page_count": page_count,
                **page_data,
            }
            body = text if text else "<!-- No embedded text was extracted from this page. -->"
            (page_dir / f"p{number:04d}.md").write_text(
                _frontmatter(frontmatter) + "\n" + body + "\n", encoding="utf-8"
            )
        data = {
            "document_id": doc_id,
            "source_file": source_file,
            "source_sha256": source_hash,
            "project_id": project_id,
            "page_count": page_count,
            "pipeline_version": PIPELINE_VERSION,
            "pdf_metadata": _metadata(document),
            "pages": pages,
        }
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError(f"failed to extract {source_file}: {exc}") from exc
    finally:
        document.close()

    (destination / "document.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    index_lines = [
        f"# {pdf.name}",
        "",
        "> Generated retrieval layer only. The source PDF and cited page are authoritative.",
        "> `vision_recommended` is a processing hint, not a legal or semantic judgment.",
        "",
        f"- Document ID: `{doc_id}`",
        f"- Source: `{source_file}`",
        f"- SHA-256: `{source_hash}`",
        f"- Pages: {page_count}",
        "",
        "## Pages",
        "",
    ]
    index_lines.extend(
        f"- [Page {p['page']}](pages/p{p['page']:04d}.md) — {p['extraction_status']}, "
        f"{p['text_char_count']} chars, vision: {str(p['vision_recommended']).lower()}"
        for p in pages
    )
    (destination / "index.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    (destination / MANAGED_MARKER).write_text("managed by pdf pipeline v1\n", encoding="utf-8")
    return data


def _load_manifest(path: Path, project_id: str) -> dict[str, Any]:
    if not path.exists():
        return {"project_id": project_id, "documents": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(f"invalid manifest {path}: {exc}") from exc
    if data.get("project_id") != project_id or not isinstance(data.get("documents"), list):
        raise PipelineError(f"manifest has invalid project identity or documents list: {path}")
    return data


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def process_all(root: Path) -> ProcessResult:
    projects = root / "projects"
    if not projects.is_dir():
        raise PipelineError(f"projects directory not found under repository root: {root}")
    processed = unchanged = removed = 0
    for project in sorted(p for p in projects.iterdir() if p.is_dir() and p.name != "_template"):
        source = project / "source"
        manifest_path = project / "manifest.json"
        if not source.is_dir() and not manifest_path.exists():
            continue
        manifest = _load_manifest(manifest_path, project.name)
        old_by_id = {item.get("document_id"): item for item in manifest["documents"]}
        pdfs = sorted(
            (p for p in source.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"),
            key=lambda p: p.relative_to(source).as_posix(),
        ) if source.is_dir() else []
        staged: list[tuple[Path, Path, dict[str, Any]]] = []
        entries: list[dict[str, Any]] = []
        temp_root = Path(tempfile.mkdtemp(prefix="pdf-pipeline-", dir=project))
        try:
            for pdf in pdfs:
                source_file = _relative_source(root, pdf)
                doc_id = document_id(source_file)
                output_rel = f"projects/{project.name}/knowledge/{_slug(pdf, doc_id)}"
                output = root / output_rel
                source_hash = _sha256(pdf)
                previous = old_by_id.get(doc_id)
                entry = {
                    "document_id": doc_id,
                    "source_file": source_file,
                    "source_sha256": source_hash,
                    "knowledge_path": output_rel,
                    "page_count": previous.get("page_count") if previous else None,
                    "pipeline_version": PIPELINE_VERSION,
                }
                if (
                    previous
                    and previous.get("source_sha256") == source_hash
                    and previous.get("pipeline_version") == PIPELINE_VERSION
                    and previous.get("knowledge_path") == output_rel
                    and (output / "document.json").is_file()
                    and (output / MANAGED_MARKER).is_file()
                ):
                    unchanged += 1
                else:
                    staged_output = temp_root / doc_id
                    data = _build_document(root, project, pdf, staged_output)
                    entry["page_count"] = data["page_count"]
                    staged.append((staged_output, output, data))
                    processed += 1
                entries.append(entry)

            # Extraction of every changed PDF succeeded; only now replace generated trees.
            for staged_output, output, _data in staged:
                output.parent.mkdir(parents=True, exist_ok=True)
                backup = temp_root / (staged_output.name + "-old")
                if output.exists():
                    os.replace(output, backup)
                os.replace(staged_output, output)

            current_paths = {entry["knowledge_path"] for entry in entries}
            for previous in manifest["documents"]:
                stale_rel = previous.get("knowledge_path")
                if not isinstance(stale_rel, str) or stale_rel in current_paths:
                    continue
                stale = root / stale_rel
                expected = project / "knowledge"
                if _inside(stale, expected) and (stale / MANAGED_MARKER).is_file():
                    shutil.rmtree(stale)
                    removed += 1

            updated = {key: value for key, value in manifest.items() if key != "documents"}
            updated["project_id"] = project.name
            updated["pipeline_version"] = PIPELINE_VERSION
            updated["documents"] = sorted(entries, key=lambda item: item["source_file"])
            if updated != manifest:
                _atomic_json(manifest_path, updated)
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)
    return ProcessResult(processed, unchanged, removed)


def _parse_pages(specification: str, page_count: int) -> list[int]:
    selected: set[int] = set()
    for item in specification.split(","):
        item = item.strip()
        if not item:
            raise PipelineError("empty page selector")
        try:
            if "-" in item:
                start_text, end_text = item.split("-", 1)
                start, end = int(start_text), int(end_text)
                if start > end:
                    raise ValueError
                selected.update(range(start, end + 1))
            else:
                selected.add(int(item))
        except ValueError as exc:
            raise PipelineError(f"invalid page selector: {item}") from exc
    if not selected or min(selected) < 1 or max(selected) > page_count:
        raise PipelineError(f"page selection must be within 1-{page_count}")
    return sorted(selected)


def render_pages(
    root: Path,
    pdf: Path,
    *,
    pages: str | None,
    all_pages: bool,
    dpi: int,
    output: Path,
) -> list[Path]:
    root = root.resolve()
    pdf = (root / pdf).resolve() if not pdf.is_absolute() else pdf.resolve()
    try:
        project_relative = pdf.relative_to(root / "projects")
    except ValueError:
        project_relative = None
    if (
        project_relative is None
        or len(project_relative.parts) < 3
        or project_relative.parts[1] != "source"
        or pdf.suffix.lower() != ".pdf"
    ):
        raise PipelineError("render input must be a PDF inside projects/<project-id>/source/")
    if not pdf.is_file():
        raise PipelineError(f"source PDF not found: {pdf}")
    if dpi < 36 or dpi > 1200:
        raise PipelineError("DPI must be between 36 and 1200")
    try:
        document = fitz.open(pdf)
    except Exception as exc:
        raise PipelineError(f"cannot open corrupt or invalid PDF {pdf}: {exc}") from exc
    try:
        chosen = list(range(1, document.page_count + 1)) if all_pages else _parse_pages(pages or "", document.page_count)
        output = (root / output).resolve() if not output.is_absolute() else output.resolve()
        # Knowledge trees are reviewable text; rendered binaries must never be placed there.
        for project_knowledge in (root / "projects").glob("*/knowledge"):
            if _inside(output, project_knowledge):
                raise PipelineError("render output must not be inside a project knowledge directory")
        output.mkdir(parents=True, exist_ok=True)
        results: list[Path] = []
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", pdf.stem).strip("-.") or "document"
        for number in chosen:
            target = output / f"{stem}-p{number:04d}-{dpi}dpi.png"
            temporary = target.with_name(f".{target.stem}.tmp.png")
            pixmap = document[number - 1].get_pixmap(matrix=matrix, alpha=False)
            pixmap.save(temporary)
            os.replace(temporary, target)
            results.append(target)
        return results
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError(f"failed to render {pdf}: {exc}") from exc
    finally:
        document.close()
