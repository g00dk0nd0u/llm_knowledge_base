from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import fitz
import pytest

from tools.query_core.build import build_database
from tools.query_core.errors import QueryCoreError
from tools.query_core.fixtures import build_synthetic_fixture, synthetic_records
from tools.query_core.package import (
    cached_payload,
    extract_payload,
    inspect_pdf,
    package_pdf,
)
from tools.query_core.query import QueryCore, validate_database


@pytest.fixture
def fixture(tmp_path: Path) -> tuple[Path, Path]:
    return build_synthetic_fixture(tmp_path)


def test_schema_build_rebuild_update_delete_and_stable_ids(
    fixture: tuple[Path, Path], tmp_path: Path
) -> None:
    drawing, database = fixture
    metadata = validate_database(database)
    assert metadata["schema_version"] == "1"
    assert metadata["generator_version"] == "query-core/1.0"
    before = (
        sqlite3.connect(database)
        .execute("SELECT id FROM elements ORDER BY id")
        .fetchall()
    )
    rebuilt = tmp_path / "rebuilt.sqlite"
    records = synthetic_records(drawing)
    build_database(records, rebuilt)
    assert (
        sqlite3.connect(rebuilt)
        .execute("SELECT id FROM elements ORDER BY id")
        .fetchall()
        == before
    )
    records["elements"] = [
        row for row in records["elements"] if row["id"] != "element-dl03"
    ]
    records["elements"][0]["name"] = "Shutter SD-03 Updated"
    build_database(records, rebuilt)
    connection = sqlite3.connect(rebuilt)
    assert (
        connection.execute(
            "SELECT count(*) FROM elements WHERE id='element-dl03'"
        ).fetchone()[0]
        == 0
    )
    assert (
        connection.execute(
            "SELECT name FROM elements WHERE id='element-sd03'"
        ).fetchone()[0]
        == "Shutter SD-03 Updated"
    )


def test_loading_dock_and_evidence(fixture: tuple[Path, Path]) -> None:
    with QueryCore(fixture[1]) as core:
        assert core.find_entities("SD-03") == [
            {
                "kind": "element",
                "id": "element-sd03",
                "name": "Shutter SD-03",
                "category": "Door",
                "type_id": "type-shutter",
                "space_id": None,
                "level_id": "level-2",
                "source_id": "synthetic-sd03",
                "provenance": "synthetic_fixture",
                "confidence": None,
            }
        ]
        relationships = core.get_related_entities("element", "element-dl03")
        assert relationships[0]["target_id"] == "element-sd03"
        fact = core.get_numeric_facts("element", "element-sd03")[0]
        assert (
            fact["numeric_value"],
            fact["unit"],
            fact["provenance"],
            fact["confidence"],
        ) == (4500, "mm", "synthetic_fixture", None)
        assert (fact["evidence"]["sheet_number"], fact["evidence"]["pdf_page"]) == (
            "A-312",
            1,
        )
        assert [
            fact["evidence"][key] for key in ("x_min", "y_min", "x_max", "y_max")
        ] == [120, 150, 330, 240]
        assert (
            core.get_dimensions("element-sd03")[0]["semantic_type"] == "opening_width"
        )


def test_roof_spot_elevation_is_numeric(fixture: tuple[Path, Path]) -> None:
    with QueryCore(fixture[1]) as core:
        elevation = core.get_spot_elevations("element-roof")[0]
        assert (
            elevation["numeric_value"],
            elevation["unit"],
            elevation["display_text"],
        ) == (1250, "mm", "RFL + 1250 mm")
        assert (
            elevation["evidence"]["sheet_number"],
            elevation["evidence"]["pdf_page"],
        ) == ("A-421", 2)


def test_fts_japanese_relationships_and_spatial_exact_geometry(
    fixture: tuple[Path, Path],
) -> None:
    with QueryCore(fixture[1]) as core:
        assert core.search_text("Shutter")[0]["record_id"] == "element-sd03"
        assert core.search_text("データホール")[0]["record_id"] == "space-corridor"
        related = core.get_related_entities("space", "space-corridor")
        assert {row["target_id"] for row in related} == {
            "space-hall-a",
            "space-hall-b",
            "wall-north",
            "wall-south",
        }
        candidates = core.get_spatial_candidates((-100, 10100, -100, 1900, -1, 3001))
        assert {row["id"] for row in candidates} == {
            "geo-corridor",
            "geo-wall-n",
            "geo-wall-s",
        }
        corridor = next(row for row in candidates if row["id"] == "geo-corridor")
        assert corridor["geometry"]["points"] == [
            [0, 0, 0],
            [10000, 0, 0],
            [10000, 1800, 0],
            [0, 1800, 0],
        ]


def test_validation_rejects_bad_numeric_bbox_and_bounds(
    fixture: tuple[Path, Path], tmp_path: Path
) -> None:
    drawing, database = fixture
    records = synthetic_records(drawing)
    records["parameters"][0]["unit"] = None
    with pytest.raises(QueryCoreError, match="requires unit"):
        build_database(records, tmp_path / "bad.sqlite")
    records = synthetic_records(drawing)
    records["evidence"][0]["x_min"] = 999
    with pytest.raises(QueryCoreError, match="malformed evidence bbox"):
        build_database(records, tmp_path / "bad.sqlite")
    with (
        QueryCore(database) as core,
        pytest.raises(QueryCoreError, match="malformed spatial"),
    ):
        core.get_spatial_candidates((2, 1, 0, 1, 0, 1))


def test_pdf_round_trip_and_ordinary_pdf_behavior(
    fixture: tuple[Path, Path], tmp_path: Path
) -> None:
    drawing, database = fixture
    source = fitz.open(drawing)
    source_pages = [
        (page.rect.width, page.rect.height, page.get_text(), len(page.get_images()))
        for page in source
    ]
    enhanced = package_pdf(drawing, database, tmp_path / "enhanced.pdf")
    packaged = fitz.open(enhanced)
    assert len(packaged) == len(source)
    assert [
        (page.rect.width, page.rect.height, page.get_text(), len(page.get_images()))
        for page in packaged
    ] == source_pages
    assert "project.sqlite" in packaged.embfile_names()
    assert "description" in packaged.embfile_info("project.sqlite")
    catalog = packaged.xref_object(packaged.pdf_catalog(), compressed=True)
    assert "/AF[" in catalog
    assert any(
        "/AFRelationship/Data" in packaged.xref_object(xref, compressed=True)
        for xref in range(1, packaged.xref_length())
    )
    assert any(
        "/Type/EmbeddedFile" in packaged.xref_object(xref, compressed=True)
        and "/Subtype/application#2Fvnd.sqlite3"
        in packaged.xref_object(xref, compressed=True)
        for xref in range(1, packaged.xref_length())
    )
    extracted = extract_payload(enhanced, tmp_path / "extracted.sqlite")
    assert extracted.read_bytes() == database.read_bytes()
    details = inspect_pdf(enhanced)
    assert details["schema_version"] == 1
    assert details["af_relationship"] == "Data"
    assert details["mime_type"] == "application/vnd.sqlite3"
    assert (
        details["payload_sha256"] == hashlib.sha256(database.read_bytes()).hexdigest()
    )


def test_package_rejects_database_for_different_drawing(
    fixture: tuple[Path, Path], tmp_path: Path
) -> None:
    drawing_a, database_for_a = fixture
    drawing_b = tmp_path / "drawing-b.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "A different synthetic drawing")
    document.save(drawing_b)
    document.close()

    rejected_output = tmp_path / "must-not-exist.pdf"
    with pytest.raises(QueryCoreError, match="different source drawing"):
        package_pdf(drawing_b, database_for_a, rejected_output)
    assert not rejected_output.exists()
    assert package_pdf(drawing_a, database_for_a, tmp_path / "valid.pdf").exists()


def test_cache_reuse_invalidation_and_corruption(
    fixture: tuple[Path, Path], tmp_path: Path
) -> None:
    drawing, database = fixture
    enhanced = package_pdf(drawing, database, tmp_path / "enhanced.pdf")
    cache_root = tmp_path / "cache"
    first = cached_payload(enhanced, cache_root)
    assert cached_payload(enhanced, cache_root) == first
    first.write_bytes(b"corrupt")
    assert cached_payload(enhanced, cache_root).read_bytes() == database.read_bytes()
    changed = tmp_path / "changed.pdf"
    document = fitz.open(enhanced)
    document.set_metadata({**document.metadata, "subject": "changed enhanced document"})
    document.save(changed)
    document.close()
    second = cached_payload(changed, cache_root)
    assert second != first
    assert second.read_bytes() == database.read_bytes()


def test_missing_corrupt_and_unsupported_payload_fail(
    fixture: tuple[Path, Path], tmp_path: Path
) -> None:
    drawing, database = fixture
    with pytest.raises(QueryCoreError, match="no embedded"):
        inspect_pdf(drawing)
    broken = tmp_path / "broken.sqlite"
    broken.write_bytes(b"not sqlite")
    with pytest.raises(QueryCoreError, match="invalid SQLite"):
        validate_database(broken)
    unsupported = tmp_path / "unsupported.sqlite"
    unsupported.write_bytes(database.read_bytes())
    connection = sqlite3.connect(unsupported)
    connection.execute("UPDATE metadata SET value='2' WHERE key='schema_version'")
    connection.execute("PRAGMA user_version=2")
    connection.commit()
    connection.close()
    with pytest.raises(QueryCoreError, match="unsupported schema"):
        validate_database(unsupported)

    # A self-consistent attachment hash cannot disguise a non-SQLite payload.
    corrupt_pdf = tmp_path / "corrupt-payload.pdf"
    document = fitz.open(drawing)
    document.embfile_add(
        "project.sqlite",
        b"not sqlite",
        filename="project.sqlite",
        ufilename="project.sqlite",
        desc=json.dumps(
            {
                "payload_sha256": hashlib.sha256(b"not sqlite").hexdigest(),
                "af_relationship": "Data",
                "mime_type": "application/vnd.sqlite3",
            }
        ),
    )
    document.save(corrupt_pdf)
    document.close()
    with pytest.raises(QueryCoreError, match="invalid SQLite"):
        inspect_pdf(corrupt_pdf)

    ambiguous_pdf = tmp_path / "ambiguous.pdf"
    document = fitz.open(drawing)
    for key in ("first", "second"):
        document.embfile_add(
            key,
            database.read_bytes(),
            filename="project.sqlite",
            ufilename="project.sqlite",
        )
    document.save(ambiguous_pdf)
    document.close()
    with pytest.raises(QueryCoreError, match="multiple ambiguous"):
        inspect_pdf(ambiguous_pdf)


def test_schema_validation_rejects_incomplete_v1_payloads(
    fixture: tuple[Path, Path], tmp_path: Path
) -> None:
    _drawing, valid_database = fixture

    metadata_only = tmp_path / "metadata-only.sqlite"
    connection = sqlite3.connect(metadata_only)
    connection.execute(
        "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.executemany(
        "INSERT INTO metadata VALUES (?,?)",
        [
            ("schema_version", "1"),
            ("generator_version", "query-core/1.0"),
            ("project_id", "fake"),
            ("created_from", "test"),
            ("source_document_identity", "fake"),
            ("source_document_sha256", "0" * 64),
        ],
    )
    connection.execute("PRAGMA user_version=1")
    connection.commit()
    connection.close()
    with pytest.raises(QueryCoreError, match="missing tables"):
        validate_database(metadata_only)
    with pytest.raises(QueryCoreError, match="missing tables"):
        QueryCore(metadata_only)

    missing_table = tmp_path / "missing-table.sqlite"
    shutil.copyfile(valid_database, missing_table)
    connection = sqlite3.connect(missing_table)
    connection.execute("ALTER TABLE annotations RENAME TO removed_annotations")
    connection.commit()
    connection.close()
    with pytest.raises(QueryCoreError, match="missing tables: annotations"):
        validate_database(missing_table)

    missing_metadata = tmp_path / "missing-metadata.sqlite"
    shutil.copyfile(valid_database, missing_metadata)
    connection = sqlite3.connect(missing_metadata)
    connection.execute("DELETE FROM metadata WHERE key='generator_version'")
    connection.commit()
    connection.close()
    with pytest.raises(
        QueryCoreError, match="missing required metadata: generator_version"
    ):
        validate_database(missing_metadata)

    missing_column = tmp_path / "missing-column.sqlite"
    shutil.copyfile(valid_database, missing_column)
    connection = sqlite3.connect(missing_column)
    connection.execute(
        "ALTER TABLE documents RENAME COLUMN title TO incompatible_title"
    )
    connection.commit()
    connection.close()
    with pytest.raises(QueryCoreError, match="documents missing columns: title"):
        validate_database(missing_column)


def test_query_connection_is_read_only(fixture: tuple[Path, Path]) -> None:
    with (
        QueryCore(fixture[1]) as core,
        pytest.raises(sqlite3.OperationalError, match="readonly"),
    ):
        core.connection.execute("DELETE FROM elements")
