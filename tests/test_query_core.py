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
    assert metadata["schema_version"] == "2"
    assert metadata["generator_version"] == "query-core/2.0"
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
                "source_model_id": "model-host",
                "source_unique_id": "synthetic-sd03",
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


def test_practical_opening_width_direct_indirect_and_deterministic(
    fixture: tuple[Path, Path],
) -> None:
    with QueryCore(fixture[1]) as core:
        indirect = core.get_opening_width("element-dl03")
        direct = core.get_opening_width("element-sd03")
        assert indirect == core.get_opening_width("element-dl03")

    assert indirect["status"] == "ok"
    assert indirect["resolved_target"]["id"] == "element-sd03"
    assert (indirect["numeric_value"], indirect["unit"]) == (4500, "mm")
    assert indirect["fact_source_kind"] == "parameter"
    assert indirect["relationship_path"][0]["relationship_id"] == "rel-dock-shutter"
    assert direct["fact_id"] == indirect["fact_id"] == "param-shutter-width"
    assert direct["evidence"] == indirect["evidence"]
    assert len(indirect["evidence"]) == 1
    assert indirect["evidence"][0]["sheet_number"] == "A-312"
    assert indirect["evidence"][0]["pdf_page"] == 1
    assert indirect["evidence"][0]["bbox"] == [120, 150, 330, 240]


def test_practical_relative_roof_elevation(fixture: tuple[Path, Path]) -> None:
    with QueryCore(fixture[1]) as core:
        result = core.get_relative_elevation("element-roof")
        assert result == core.get_relative_elevation("element-roof", reference="rfl")

    assert result["status"] == "ok"
    assert result["reference"] == "RFL"
    assert (result["numeric_value"], result["unit"]) == (1250, "mm")
    assert result["display_text"] == "RFL + 1250 mm"
    assert result["annotation_id"] == "ann-roof-rfl"
    assert result["provenance"] == "synthetic_fixture"
    assert result["evidence"][0]["sheet_number"] == "A-421"
    assert result["evidence"][0]["pdf_page"] == 2
    assert result["evidence"][0]["bbox"] == [410, 110, 560, 180]


def test_practical_opening_width_does_not_guess(fixture: tuple[Path, Path]) -> None:
    with QueryCore(fixture[1]) as core:
        result = core.get_opening_width("wall-north")
    assert result["status"] == "not_found"
    assert result["evidence"] == []


def test_practical_opening_width_reports_ambiguous_target(
    fixture: tuple[Path, Path], tmp_path: Path
) -> None:
    drawing, _database = fixture
    records = synthetic_records(drawing)
    second = dict(next(row for row in records["elements"] if row["id"] == "element-sd03"))
    second.update(id="element-sd04", name="Shutter SD-04", source_unique_id="synthetic-sd04")
    records["elements"].append(second)
    parameter = dict(records["parameters"][0])
    parameter.update(id="param-shutter-width-04", entity_id="element-sd04")
    records["parameters"].append(parameter)
    relation = dict(records["relationships"][0])
    relation.update(id="rel-dock-shutter-04", target_id="element-sd04")
    records["relationships"].append(relation)
    database = build_database(records, tmp_path / "ambiguous.sqlite")

    with QueryCore(database) as core:
        result = core.get_opening_width("element-dl03")
    assert result["status"] == "ambiguous"
    assert result["candidate_ids"] == ["element-sd03", "element-sd04"]
    assert len(result["evidence"]) == 1


def test_practical_opening_width_reports_conflicting_exact_facts(
    fixture: tuple[Path, Path], tmp_path: Path
) -> None:
    drawing, _database = fixture
    records = synthetic_records(drawing)
    conflicting = dict(records["parameters"][0])
    conflicting.update(
        id="param-shutter-width-conflict",
        numeric_value=4600,
        value_text="4600 mm",
        raw_numeric_value=15.0918635171,
        raw_value_text="15.0919 ft",
    )
    records["parameters"].append(conflicting)
    database = build_database(records, tmp_path / "conflict.sqlite")

    with QueryCore(database) as core:
        result = core.get_opening_width("element-sd03")
    assert result["status"] == "conflict"
    assert [item["numeric_value"] for item in result["candidates"]] == [4500, 4600]


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
            "geo-linked-a",
            "geo-linked-b",
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
    assert details["schema_version"] == 2
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
    connection.execute("UPDATE metadata SET value='3' WHERE key='schema_version'")
    connection.execute("PRAGMA user_version=3")
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


def test_schema_validation_rejects_incomplete_v2_payloads(
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
            ("schema_version", "2"),
            ("generator_version", "query-core/2.0"),
            ("project_id", "fake"),
            ("created_from", "test"),
            ("source_document_identity", "fake"),
            ("source_document_sha256", "0" * 64),
        ],
    )
    connection.execute("PRAGMA user_version=2")
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


def test_revit_composite_identity_and_source_transform(
    fixture: tuple[Path, Path],
) -> None:
    with QueryCore(fixture[1]) as core:
        host = core.get_entity("element", "element-sd03")
        linked = core.get_entity("element", "element-linked-collision")
        assert host["source_unique_id"] == linked["source_unique_id"]
        assert host["source_model_id"] != linked["source_model_id"]
        assert core.get_source_model("model-link")["role"] == "link"
        transform = core.get_link_instance("link-instance-a")["transform_to_host"]
        assert transform["origin"] == [100, 200, 0]
        assert transform["source_unit"] == "revit_internal"


def test_multi_appearance_dimension_and_corridor_contract(
    fixture: tuple[Path, Path],
) -> None:
    with QueryCore(fixture[1]) as core:
        appearances = core.get_appearances("element", "element-sd03")
        assert [row["pdf_page"] for row in appearances] == [1, 2, 3]
        assert appearances[-1]["appearance_kind"] == "schedule"
        assert appearances[-1]["bbox_quality"] == "page_only"
        segments = core.get_annotation_segments("ann-opening-width")
        assert [row["segment_index"] for row in segments] == [0, 1]
        assert [row["numeric_value"] for row in segments] == [2100, 2400]
        references = core.get_annotation_references("ann-opening-width")
        assert [row["target_source_model_id"] for row in references] == [
            "model-host",
            "model-link",
        ]
        loops = core.get_spatial_boundaries("space-corridor")
        assert loops[0]["loop_kind"] == "outer"
        assert len(loops[0]["segments"]) == 4
        assert loops[0]["segments"][-1]["end_x"] == loops[0]["segments"][0]["start_x"]


def test_v2_revit_validation_errors(tmp_path: Path) -> None:
    drawing = tmp_path / "drawing.pdf"
    from tools.query_core.fixtures import create_synthetic_pdf

    create_synthetic_pdf(drawing)
    base = synthetic_records(drawing)

    bad = json.loads(json.dumps(base))
    bad["elements"].append({**bad["elements"][0], "id": "duplicate"})
    with pytest.raises(QueryCoreError, match="duplicate source identity"):
        build_database(bad, tmp_path / "bad.sqlite")

    bad = json.loads(json.dumps(base))
    bad["link_instances"][0]["transform_to_host"]["basis_x"] = [1, 0]
    with pytest.raises(QueryCoreError, match="invalid link transform"):
        build_database(bad, tmp_path / "bad.sqlite")

    for key, value in (("unit", "ft"), ("coordinate_system", "link_local")):
        bad = json.loads(json.dumps(base))
        bad["geometries"][0][key] = value
        with pytest.raises(QueryCoreError, match="indexed geometry"):
            build_database(bad, tmp_path / "bad.sqlite")

    bad = json.loads(json.dumps(base))
    bad["annotation_segments"][1]["segment_index"] = 2
    with pytest.raises(QueryCoreError, match="ordering error"):
        build_database(bad, tmp_path / "bad.sqlite")

    bad = json.loads(json.dumps(base))
    bad["annotation_references"][0]["target_source_model_id"] = "missing"
    with pytest.raises(QueryCoreError, match="nonexistent source model"):
        build_database(bad, tmp_path / "bad.sqlite")

    bad = json.loads(json.dumps(base))
    bad["spatial_boundary_segments"][-1]["end_x"] = 1
    with pytest.raises(QueryCoreError, match="malformed spatial boundary loop"):
        build_database(bad, tmp_path / "bad.sqlite")

    bad = json.loads(json.dumps(base))
    linked_boundary = next(
        row
        for row in bad["spatial_boundaries"]
        if row["link_instance_id"] is not None
    )
    linked_boundary["link_instance_id"] = "missing-link-instance"
    with pytest.raises(QueryCoreError, match="nonexistent link instance"):
        build_database(bad, tmp_path / "bad.sqlite")

    bad = json.loads(json.dumps(base))
    linked_boundary = next(
        row
        for row in bad["spatial_boundaries"]
        if row["link_instance_id"] is not None
    )
    linked_boundary["link_instance_id"] = None
    with pytest.raises(QueryCoreError, match="linked space boundary requires"):
        build_database(bad, tmp_path / "bad.sqlite")

    bad = json.loads(json.dumps(base))
    linked_space = next(row for row in bad["spaces"] if row["id"] == "space-linked-room")
    linked_space["source_model_id"] = "model-host"
    with pytest.raises(QueryCoreError, match="boundary link instance model mismatch"):
        build_database(bad, tmp_path / "bad.sqlite")

    bad = json.loads(json.dumps(base))
    linked_segment = next(
        row
        for row in bad["spatial_boundary_segments"]
        if row["link_instance_id"] is not None
    )
    linked_segment["link_instance_id"] = "link-instance-b"
    with pytest.raises(QueryCoreError, match="boundary segment link instance mismatch"):
        build_database(bad, tmp_path / "bad.sqlite")

    bad = json.loads(json.dumps(base))
    bad["link_instances"][1]["source_unique_id"] = bad["link_instances"][0][
        "source_unique_id"
    ]
    with pytest.raises(QueryCoreError, match="duplicate link instance identity"):
        build_database(bad, tmp_path / "bad.sqlite")

    bad = json.loads(json.dumps(base))
    linked_geometry = next(
        row for row in bad["geometries"] if row["link_instance_id"] is not None
    )
    linked_geometry["entity_id"] = "element-sd03"
    with pytest.raises(QueryCoreError, match="link instance model mismatch"):
        build_database(bad, tmp_path / "bad.sqlite")

    bad = json.loads(json.dumps(base))
    linked_appearance = next(
        row for row in bad["entity_appearances"] if row["link_instance_id"] is not None
    )
    linked_appearance["entity_id"] = "element-sd03"
    with pytest.raises(QueryCoreError, match="link instance model mismatch"):
        build_database(bad, tmp_path / "bad.sqlite")


def test_synthetic_revit_snapshot_import_and_version(tmp_path: Path) -> None:
    from tools.query_core.fixtures import (
        create_synthetic_pdf,
        write_synthetic_revit_snapshot,
    )
    from tools.query_core.revit_snapshot import import_snapshot, load_snapshot

    drawing = create_synthetic_pdf(tmp_path / "drawing.pdf")
    snapshot = write_synthetic_revit_snapshot(drawing, tmp_path / "snapshot.json")
    database = import_snapshot(snapshot, tmp_path / "snapshot.sqlite")
    with QueryCore(database) as core:
        assert core.get_source_model("model-host")["role"] == "host"
        assert len(core.get_appearances("element", "element-sd03")) == 3
    payload = json.loads(snapshot.read_text())
    payload["snapshot_version"] = 2
    snapshot.write_text(json.dumps(payload))
    with pytest.raises(QueryCoreError, match="unsupported snapshot version"):
        load_snapshot(snapshot)


def test_finalize_revit_export_and_reject_hash_mismatch(tmp_path: Path) -> None:
    from tools.query_core.fixtures import (
        create_synthetic_pdf,
        write_synthetic_revit_snapshot,
    )
    from tools.query_core.revit_snapshot import finalize_revit_export

    export = tmp_path / "export"
    export.mkdir()
    drawing = create_synthetic_pdf(export / "drawing.pdf")
    write_synthetic_revit_snapshot(drawing, export / "revit_snapshot.json")
    diagnostic = export / "project.sqlite"
    diagnostic.write_bytes(b"existing diagnostic")
    with pytest.raises(QueryCoreError, match="project.sqlite"):
        finalize_revit_export(export, diagnostic)
    assert diagnostic.read_bytes() == b"existing diagnostic"

    enhanced = finalize_revit_export(export)
    assert enhanced == export / "enhanced.pdf"
    assert (export / "project.sqlite").is_file()
    assert inspect_pdf(enhanced)["source_document_sha256"] == hashlib.sha256(
        drawing.read_bytes()
    ).hexdigest()

    drawing.write_bytes(drawing.read_bytes() + b"changed")
    with pytest.raises(QueryCoreError, match="SHA-256 does not match"):
        finalize_revit_export(export, export / "must-not-exist.pdf")
    assert not (export / "must-not-exist.pdf").exists()


@pytest.mark.parametrize(
    ("version", "runtime", "project", "framework"),
    [
        ("2025", "net8", "LlmKnowledgeBase.Revit2025", "net8.0-windows"),
        ("2026", "net8", "LlmKnowledgeBase.Revit2026", "net8.0-windows"),
        ("2026", "net10", "LlmKnowledgeBase.Revit2026Net10", "net10.0-windows"),
        ("2027", "net10", "LlmKnowledgeBase.Revit2027", "net10.0-windows"),
    ],
)
def test_revit_runtime_profiles(version: str, runtime: str, project: str, framework: str) -> None:
    from tools.revit_exporter.cli import select_profile

    assert select_profile(version, runtime)[:2] == (project, framework)


@pytest.mark.parametrize(("version", "runtime"), [("2027", "net8"), ("2026", "invalid")])
def test_invalid_revit_runtime_profiles_rejected(version: str, runtime: str) -> None:
    from tools.revit_exporter.cli import select_profile

    with pytest.raises(ValueError, match="unsupported Revit/runtime profile"):
        select_profile(version, runtime)


def test_repeated_link_model_placements(fixture: tuple[Path, Path]) -> None:
    with QueryCore(fixture[1]) as core:
        element = core.get_entity("element", "element-linked-collision")
        assert element["source_model_id"] == "model-link"
        instances = core.get_link_instances("model-link")
        assert [row["id"] for row in instances] == [
            "link-instance-a",
            "link-instance-b",
        ]
        assert [row["transform_to_host"]["origin"] for row in instances] == [
            [100, 200, 0],
            [10100, 200, 0],
        ]
        geometries = [
            row
            for row in core.get_spatial_candidates((0, 11000, 0, 1000, 0, 1100))
            if row["entity_id"] == element["id"]
        ]
        assert {row["link_instance_id"] for row in geometries} == {
            "link-instance-a",
            "link-instance-b",
        }
        appearances = core.get_appearances("element", element["id"])
        assert {row["link_instance_id"] for row in appearances} == {
            "link-instance-a",
            "link-instance-b",
        }
        boundaries = core.get_spatial_boundaries("space-linked-room")
        assert len(boundaries) == 2
        assert {row["link_instance_id"] for row in boundaries} == {
            "link-instance-a",
            "link-instance-b",
        }
        assert len({row["id"] for row in boundaries}) == 2
        assert all(
            {segment["link_instance_id"] for segment in row["segments"]}
            == {row["link_instance_id"]}
            for row in boundaries
        )
        assert boundaries[0]["segments"][0]["start_x"] != boundaries[1][
            "segments"
        ][0]["start_x"]


def test_snapshot_json_schema_is_enforced_before_build(tmp_path: Path) -> None:
    from tools.query_core.fixtures import (
        create_synthetic_pdf,
        write_synthetic_revit_snapshot,
    )
    from tools.query_core.revit_snapshot import load_snapshot

    drawing = create_synthetic_pdf(tmp_path / "drawing.pdf")
    source = write_synthetic_revit_snapshot(drawing, tmp_path / "snapshot.json")
    valid = json.loads(source.read_text())
    assert load_snapshot(source)["snapshot_version"] == 1

    mutations = [
        (
            "unknown property",
            lambda value: value["records"]["documents"][0].update({"titel": "typo"}),
            "records.documents.0",
        ),
        (
            "missing field",
            lambda value: value["records"]["elements"][0].pop("category"),
            "records.elements.0",
        ),
        (
            "wrong enum",
            lambda value: value["records"]["source_models"][0].update(role="primary"),
            "role",
        ),
        (
            "wrong type",
            lambda value: value["records"]["parameters"][0].update(
                numeric_value="4500"
            ),
            "numeric_value",
        ),
        (
            "invalid page",
            lambda value: value["records"]["evidence"][0].update(pdf_page="1"),
            "pdf_page",
        ),
        (
            "bad vector",
            lambda value: value["records"]["link_instances"][0][
                "transform_to_host"
            ].update(origin=[1, 2]),
            "origin",
        ),
        (
            "coordinate system",
            lambda value: value.update(coordinate_system="link_local"),
            "coordinate_system",
        ),
        ("unit", lambda value: value.update(unit="ft"), "unit"),
    ]
    for name, mutate, expected_path in mutations:
        payload = json.loads(json.dumps(valid))
        mutate(payload)
        candidate = tmp_path / f"bad-{name.replace(' ', '-')}.json"
        candidate.write_text(json.dumps(payload))
        with pytest.raises(QueryCoreError, match=expected_path):
            load_snapshot(candidate)


def test_sqlite_v2_structural_validation_is_complete(
    fixture: tuple[Path, Path], tmp_path: Path
) -> None:
    _drawing, valid_database = fixture
    damages = [
        ("DROP TABLE link_instances", "missing tables: link_instances"),
        (
            "ALTER TABLE link_instances RENAME COLUMN linked_source_model_id TO broken",
            "link_instances missing columns: linked_source_model_id",
        ),
        (
            "ALTER TABLE entity_appearances RENAME COLUMN link_instance_id TO broken",
            "entity_appearances missing columns: link_instance_id",
        ),
        (
            "ALTER TABLE viewports RENAME COLUMN pdf_y_max TO broken",
            "viewports missing columns: pdf_y_max",
        ),
        (
            "ALTER TABLE annotation_references RENAME COLUMN target_link_instance_id TO broken",
            "annotation_references missing columns: target_link_instance_id",
        ),
        (
            "ALTER TABLE geometries RENAME COLUMN link_instance_id TO broken",
            "geometries missing columns: link_instance_id",
        ),
    ]
    for index, (statement, message) in enumerate(damages):
        damaged = tmp_path / f"damaged-{index}.sqlite"
        shutil.copyfile(valid_database, damaged)
        connection = sqlite3.connect(damaged)
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(statement)
        connection.commit()
        connection.close()
        with pytest.raises(QueryCoreError, match=message):
            validate_database(damaged)
