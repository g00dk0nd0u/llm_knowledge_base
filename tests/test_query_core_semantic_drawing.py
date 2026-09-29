from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tools.query_core.fixtures import build_synthetic_fixture
from tools.query_core.query import QueryCore


@pytest.fixture
def semantic_drawing_database(tmp_path: Path) -> Path:
    _, database = build_synthetic_fixture(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO entity_appearances SELECT 'appearance-sd03-same-page',"
            "entity_kind,entity_id,sheet_id,view_id,viewport_id,link_instance_id,"
            "pdf_page,x_min+1,y_min,x_max+1,y_max,coordinate_space,appearance_kind,"
            "bbox_quality,provenance FROM entity_appearances "
            "WHERE id='appearance-sd03-0'"
        )
        connection.execute(
            "INSERT INTO pdf_pages (id,document_id,page_number,width_points,"
            "height_points,rotation,coordinate_space,provenance) VALUES "
            "('pdf-page-9','doc-drawings',9,612,792,0,'pdf_points_top_left','test')"
        )
        connection.execute(
            "INSERT INTO evidence VALUES "
            "('ev-span','doc-drawings',NULL,NULL,9,10,20,40,30,'pdf_points_top_left')"
        )
        connection.execute(
            "INSERT INTO pdf_text_blocks VALUES "
            "('block-9','pdf-page-9',0,'D-101',10,20,40,30,"
            "'pdf_points_top_left','embedded_pdf_text','ev-span')"
        )
        connection.execute(
            "INSERT INTO pdf_text_lines VALUES "
            "('line-9','block-9',0,'D-101',10,20,40,30,"
            "'pdf_points_top_left','embedded_pdf_text')"
        )
        connection.execute(
            "INSERT INTO pdf_text_spans VALUES "
            "('span-9','line-9',0,'D-101',11,21,39,29,"
            "'pdf_points_top_left','embedded_pdf_text',NULL,NULL,NULL)"
        )
        connection.execute(
            "INSERT INTO pdf_tables VALUES "
            "('table-9','pdf-page-9',0,2,2,5,15,100,100,'pdf_points_top_left',"
            "'derived_pdf_table','pymupdf_lines_strict','1','PyMuPDF','1')"
        )
        connection.executemany(
            "INSERT INTO pdf_table_cells VALUES "
            "(?,'table-9',?,?,1,1,?,?,?,?,?,'pdf_points_top_left','derived_pdf_table')",
            [
                ("cell-9-header", 0, 0, "", 10, 16, 40, 19),
                ("cell-9-header-2", 0, 1, "", 41, 16, 80, 19),
                ("cell-9", 1, 0, "D-101", 10, 20, 40, 30),
                ("cell-9-type", 1, 1, "", 41, 20, 80, 30),
            ],
        )
        connection.execute(
            "INSERT INTO pdf_table_cell_spans VALUES ('cell-9','span-9',0)"
        )
        entities = [
            ("sem-element", "Door", "exact"),
            ("sem-cell", "DoorScheduleRow", "exact"),
            ("sem-span", "DrawingText", "exact"),
            ("sem-evidence", "DrawingEvidence", "exact"),
            ("sem-none", "Door", "unresolved"),
        ]
        connection.executemany(
            "INSERT INTO semantic_entities "
            "(id,entity_class,instance_or_type,resolution_state,provenance) "
            "VALUES (?,?,'instance',?,'test')",
            entities,
        )
        bindings = [
            ("binding-element-b", "sem-element", "element", "element-sd03", "exact"),
            ("binding-element-a", "sem-element", "element", "element-sd03", "resolved_deterministically"),
            ("binding-ambiguous", "sem-element", "element", "element-dl03", "ambiguous"),
            ("binding-cell", "sem-cell", "pdf_table_cell", "cell-9", "exact"),
            ("binding-span", "sem-span", "pdf_text_span", "span-9", "exact"),
            ("binding-evidence", "sem-evidence", "evidence", "ev-shutter", "exact"),
            ("binding-none", "sem-none", "element", None, "unresolved"),
        ]
        connection.executemany(
            "INSERT INTO semantic_bindings "
            "(id,semantic_entity_id,source_kind,source_id,resolution_state,provenance) "
            "VALUES (?,?,?,?,?,'test')",
            bindings,
        )
    return database


def test_entity_appearances_are_preserved_and_navigation_is_reused(
    semantic_drawing_database: Path,
) -> None:
    with QueryCore(semantic_drawing_database) as query:
        before = query.connection.execute(
            "SELECT count(*) FROM semantic_relationships"
        ).fetchone()[0]
        result = query.get_semantic_drawing_context("sem-element")
        navigation = query.get_navigation_targets("element", "element-sd03")
        after = query.connection.execute(
            "SELECT count(*) FROM semantic_relationships"
        ).fetchone()[0]

    assert result["status"] == "ok"
    assert len(result["occurrences"]) == 8
    assert {row["binding"]["id"] for row in result["occurrences"]} == {
        "binding-element-a", "binding-element-b"
    }
    assert all(row["evidence_class"] == "source_fact" for row in result["occurrences"])
    ids = [row["appearance_id"] for row in result["occurrences"]]
    assert ids.count("appearance-sd03-0") == ids.count("appearance-sd03-same-page") == 2
    first = next(row for row in result["occurrences"] if row["appearance_id"] == "appearance-sd03-0")
    assert first["document"]["identity"] == "synthetic-drawings-v1"
    assert (first["sheet_number"], first["view_name"], first["view_type"], first["pdf_page"]) == (
        "A-312", "Loading Dock Elevation", "elevation", 1
    )
    assert first["bbox"] == [120.0, 150.0, 330.0, 240.0]
    assert first["navigation"] == next(
        row for row in navigation if row["appearance_id"] == first["appearance_id"]
    )
    assert before == after == 0


def test_viewport_context_is_shared_by_occurrence_and_navigation_apis(
    semantic_drawing_database: Path,
) -> None:
    with sqlite3.connect(semantic_drawing_database) as connection:
        connection.executemany(
            "INSERT INTO elements SELECT ?,name,category,type_id,space_id,level_id,"
            "source_model_id,?,provenance,confidence FROM elements "
            "WHERE id='element-sd03'",
            [
                ("element-viewport", "test-viewport"),
                ("element-priority", "test-priority"),
                ("element-unresolved", "test-unresolved"),
            ],
        )
        connection.executemany(
            "INSERT INTO entity_appearances VALUES "
            "(?, 'element', ?, ?, ?, ?, NULL, ?, NULL,NULL,NULL,NULL,"
            "'pdf_points_top_left','model','page_only','test')",
            [
                ("appearance-viewport-only", "element-viewport", None, None,
                 "vp-dock", 1),
                ("appearance-direct-priority", "element-priority", "sheet-a201",
                 "view-level2", "vp-dock", 3),
                ("appearance-unresolved-viewport", "element-unresolved", None,
                 None, None, 1),
            ],
        )
        connection.executemany(
            "INSERT INTO semantic_entities "
            "(id,entity_class,instance_or_type,resolution_state,provenance) "
            "VALUES (?,'Element','instance','exact','test')",
            [("sem-viewport",), ("sem-unresolved-viewport",)],
        )
        connection.executemany(
            "INSERT INTO semantic_bindings "
            "(id,semantic_entity_id,source_kind,source_id,resolution_state,provenance) "
            "VALUES (?,?,'element',?,'exact','test')",
            [
                ("binding-viewport", "sem-viewport", "element-viewport"),
                ("binding-unresolved-viewport", "sem-unresolved-viewport",
                 "element-unresolved"),
            ],
        )

    with QueryCore(semantic_drawing_database) as query:
        occurrences = query.get_occurrence_evidence("element", "element-viewport")
        targets = query.get_navigation_targets("element", "element-viewport")
        private_target = query._get_appearance_navigation("appearance-viewport-only")
        semantic = query.get_semantic_drawing_context("sem-viewport")
        priority = query.get_occurrence_evidence("element", "element-priority")
        priority_targets = query.get_navigation_targets("element", "element-priority")
        unresolved = query.get_occurrence_evidence("element", "element-unresolved")
        unresolved_targets = query.get_navigation_targets(
            "element", "element-unresolved"
        )
        unresolved_semantic = query.get_semantic_drawing_context(
            "sem-unresolved-viewport"
        )

    assert len(occurrences) == len(targets) == 1
    occurrence, target = occurrences[0], targets[0]
    assert occurrence["appearance_sheet_id"] is None
    assert occurrence["appearance_view_id"] is None
    assert occurrence["sheet_id"] == target["sheet_id"] == "sheet-a312"
    assert occurrence["view_id"] == target["view_id"] == "view-dock"
    assert occurrence["sheet_number"] == target["sheet_number"] == "A-312"
    assert occurrence["view_name"] == target["view_name"] == "Loading Dock Elevation"
    assert occurrence["document"] == target["document"] == private_target["document"]
    assert occurrence["pdf_page"] == target["pdf_page"] == 1
    assert semantic["status"] == "ok"
    assert semantic["occurrences"][0]["navigation"] == target

    assert len(priority) == len(priority_targets) == 1
    assert (
        priority[0]["sheet_id"]
        == priority[0]["appearance_sheet_id"]
        == "sheet-a201"
    )
    assert (
        priority[0]["view_id"]
        == priority[0]["appearance_view_id"]
        == "view-level2"
    )
    assert priority[0]["sheet_number"] == "A-201"
    assert priority[0]["view_name"] == "Level 2 Data Hall Plan"
    assert priority_targets[0]["sheet_id"] == "sheet-a201"
    assert priority_targets[0]["view_id"] == "view-level2"

    assert unresolved == unresolved_targets == []
    assert unresolved_semantic["status"] == "insufficient_data"
    assert unresolved_semantic["occurrences"] == []


def test_appearance_context_never_combines_documents(
    semantic_drawing_database: Path,
) -> None:
    with sqlite3.connect(semantic_drawing_database) as connection:
        connection.execute(
            "INSERT INTO documents VALUES "
            "('doc-foreign','foreign-v1','Foreign','foreign.pdf',?)",
            ("f" * 64,),
        )
        connection.execute(
            "INSERT INTO sheets (id,document_id,number,name,pdf_page,export_order) "
            "VALUES ('sheet-foreign','doc-foreign','B-101','Foreign Sheet',1,99)"
        )
        connection.execute(
            "INSERT INTO views (id,document_id,name,view_type) VALUES "
            "('view-foreign','doc-foreign','Foreign View','plan')"
        )
        connection.executemany(
            "INSERT INTO viewports "
            "(id,sheet_id,view_id,placement_kind,sheet_x_min,sheet_y_min,"
            "sheet_x_max,sheet_y_max,sheet_coordinate_unit) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [
                ("vp-foreign", "sheet-foreign", "view-foreign", "viewport", None,
                 None, None, None, "sheet_points"),
                ("vp-cross", "sheet-a201", "view-foreign", "viewport", None,
                 None, None, None, "sheet_points"),
            ],
        )
        cases = [
            ("direct-view", None, "view-level2", "vp-foreign"),
            ("direct-sheet", "sheet-a201", None, "vp-foreign"),
            ("direct-same", "sheet-a201", "view-level2", "vp-foreign"),
            ("viewport-only", None, None, "vp-dock"),
            ("viewport-cross", None, None, "vp-cross"),
            ("direct-conflict", "sheet-a201", "view-foreign", None),
        ]
        connection.executemany(
            "INSERT INTO elements SELECT ?,name,category,type_id,space_id,level_id,"
            "source_model_id,?,provenance,confidence FROM elements "
            "WHERE id='element-sd03'",
            [(f"element-{name}", f"test-context-{name}") for name, *_ in cases],
        )
        connection.executemany(
            "INSERT INTO entity_appearances VALUES "
            "(?, 'element', ?, ?, ?, ?, NULL, 1, NULL,NULL,NULL,NULL,"
            "'pdf_points_top_left','model','page_only','test')",
            [
                (f"appearance-{name}", f"element-{name}", sheet, view, viewport)
                for name, sheet, view, viewport in cases
            ],
        )
        connection.execute(
            "INSERT INTO semantic_entities "
            "(id,entity_class,instance_or_type,resolution_state,provenance) "
            "VALUES ('sem-direct-conflict','Element','instance','exact','test')"
        )
        connection.execute(
            "INSERT INTO semantic_bindings "
            "(id,semantic_entity_id,source_kind,source_id,resolution_state,provenance) "
            "VALUES ('binding-direct-conflict','sem-direct-conflict','element',"
            "'element-direct-conflict','exact','test')"
        )

    expected = {
        "direct-view": ("doc-drawings", None, "view-level2"),
        "direct-sheet": ("doc-drawings", "sheet-a201", None),
        "direct-same": ("doc-drawings", "sheet-a201", "view-level2"),
        "viewport-only": ("doc-drawings", "sheet-a312", "view-dock"),
        "viewport-cross": ("doc-drawings", "sheet-a201", None),
        "direct-conflict": ("doc-drawings", "sheet-a201", None),
    }
    with QueryCore(semantic_drawing_database) as query:
        for name, (document_id, sheet_id, view_id) in expected.items():
            occurrence = query.get_occurrence_evidence(
                "element", f"element-{name}"
            )[0]
            target = query.get_navigation_targets("element", f"element-{name}")[0]
            private_target = query._get_appearance_navigation(f"appearance-{name}")
            assert private_target is not None
            assert (
                occurrence["document"]["id"],
                occurrence["sheet_id"],
                occurrence["view_id"],
            ) == (document_id, sheet_id, view_id)
            for navigation in (target, private_target):
                assert (
                    navigation["document"]["id"],
                    navigation["sheet_id"],
                    navigation["view_id"],
                ) == (document_id, sheet_id, view_id)

        conflict = query.get_occurrence_evidence(
            "element", "element-direct-conflict"
        )[0]
        semantic = query.get_semantic_drawing_context("sem-direct-conflict")

    assert conflict["appearance_sheet_id"] == "sheet-a201"
    assert conflict["appearance_view_id"] == "view-foreign"
    assert semantic["occurrences"][0]["document"]["id"] == "doc-drawings"
    assert semantic["occurrences"][0]["sheet_id"] == "sheet-a201"
    assert semantic["occurrences"][0]["view_id"] is None


def test_pdf_native_bindings_retain_exact_traceability(
    semantic_drawing_database: Path,
) -> None:
    with QueryCore(semantic_drawing_database) as query:
        cell = query.get_semantic_drawing_context("sem-cell")["occurrences"][0]
        span = query.get_semantic_drawing_context("sem-span")["occurrences"][0]
        evidence = query.get_semantic_drawing_context("sem-evidence")["occurrences"][0]

    assert (cell["table"]["table_id"], cell["cell_id"], cell["pdf_page"]) == (
        "table-9", "cell-9", 9
    )
    assert cell["bbox"] == [10.0, 20.0, 40.0, 30.0]
    assert cell["source_spans"][0]["span_id"] == "span-9"
    assert span["bbox"] == [11.0, 21.0, 39.0, 29.0]
    assert span["source_ref"]["block_id"] == "block-9"
    assert span["evidence_id"] == "ev-span"
    assert evidence["evidence_id"] == "ev-shutter"
    assert evidence["pdf_page"] == 1
    assert evidence["bbox"] == [120.0, 150.0, 330.0, 240.0]


def test_unsupported_or_unresolved_data_is_explicit_and_legacy_is_readable(
    semantic_drawing_database: Path, tmp_path: Path,
) -> None:
    with QueryCore(semantic_drawing_database) as query:
        result = query.get_semantic_drawing_context("sem-none")
    assert result["status"] == "insufficient_data"
    assert result["occurrences"] == []

    legacy = tmp_path / "legacy.sqlite"
    legacy.write_bytes(semantic_drawing_database.read_bytes())
    with sqlite3.connect(legacy) as connection:
        connection.execute("DROP TABLE semantic_relationships")
        connection.execute("DROP TABLE semantic_properties")
        connection.execute("DROP TABLE semantic_bindings")
        connection.execute("DROP TABLE semantic_entities")
    with QueryCore(legacy) as query:
        assert query.get_semantic_drawing_context("sem-element") is None


def test_semantic_drawing_projection_is_stdlib_only(
    semantic_drawing_database: Path,
) -> None:
    code = (
        "import sys; from tools.query_core import QueryCore; "
        f"q=QueryCore({str(semantic_drawing_database)!r}); "
        "v=q.get_semantic_drawing_context('sem-cell'); q.close(); "
        "assert v['occurrences'][0]['cell_id']=='cell-9'; "
        "assert 'fitz' not in sys.modules and 'jsonschema' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
