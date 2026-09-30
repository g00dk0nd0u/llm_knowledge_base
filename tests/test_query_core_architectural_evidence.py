from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from tools.query_core.fixtures import build_synthetic_fixture
from tools.query_core.query import QueryCore


def _semantic_database(tmp_path: Path) -> Path:
    _, database = build_synthetic_fixture(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO semantic_entities "
            "(id,entity_class,label,instance_or_type,resolution_state,provenance,evidence_id) "
            "VALUES ('door-1','Door','D-101','instance','exact','synthetic','ev-shutter')"
        )
        connection.executemany(
            "INSERT INTO semantic_bindings "
            "(id,semantic_entity_id,source_kind,source_id,resolution_state,provenance,evidence_id) "
            "VALUES (?,?,?,?,?,'synthetic','ev-shutter')",
            [
                ('b-exact', 'door-1', 'element', 'element-sd03', 'exact'),
                ('b-ambiguous', 'door-1', 'element', 'element-dl03', 'ambiguous'),
            ],
        )
    return database


def test_architectural_context_keeps_ambiguity_and_deduplicates_evidence(
    tmp_path: Path,
) -> None:
    database = _semantic_database(tmp_path)
    with QueryCore(database) as core:
        result = core.get_architectural_evidence_context('door-1')
        repeated = core.get_architectural_evidence_context('door-1')

    assert result is not None
    assert result == repeated
    assert result['entity']['label'] == 'D-101'
    assert [row['id'] for row in result['bindings']] == ['b-ambiguous', 'b-exact']
    assert result['resolution']['ambiguous_binding_ids'] == ['b-ambiguous']
    assert result['resolution']['binding_counts'] == {
        'exact': 1, 'resolved_deterministically': 0, 'ambiguous': 1, 'unresolved': 0,
    }
    assert result['coverage']['drawing_occurrence_count'] > 0
    assert len(result['evidence']) == 1
    assert result['evidence'][0]['evidence_id'] == 'ev-shutter'
    assert result['coverage']['source_document_count'] == 1


def test_architectural_context_cli_prints_api_json_and_missing_entity_is_null(
    tmp_path: Path,
) -> None:
    database = _semantic_database(tmp_path)
    completed = subprocess.run(
        [sys.executable, '-S', '-m', 'tools.query_core', 'architectural-context',
         str(database), 'door-1'],
        check=True, capture_output=True, text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload['entity']['id'] == 'door-1'
    assert payload['status'] == 'ok'
    missing = subprocess.run(
        [sys.executable, '-S', '-m', 'tools.query_core', 'architectural-context',
         str(database), 'absent'],
        check=True, capture_output=True, text=True,
    )
    assert json.loads(missing.stdout) is None
