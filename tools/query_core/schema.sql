PRAGMA foreign_keys = ON;

CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE documents (
  id TEXT PRIMARY KEY, identity TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
  source_filename TEXT NOT NULL, source_sha256 TEXT NOT NULL CHECK(length(source_sha256)=64)
) WITHOUT ROWID;
CREATE TABLE sheets (
  id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  number TEXT NOT NULL, name TEXT NOT NULL, pdf_page INTEGER NOT NULL CHECK(pdf_page >= 1),
  UNIQUE(document_id, number)
) WITHOUT ROWID;
CREATE TABLE views (
  id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  name TEXT NOT NULL, view_type TEXT
) WITHOUT ROWID;
CREATE TABLE viewports (
  id TEXT PRIMARY KEY, sheet_id TEXT NOT NULL REFERENCES sheets(id) ON DELETE CASCADE,
  view_id TEXT NOT NULL REFERENCES views(id) ON DELETE CASCADE,
  x_min REAL, y_min REAL, x_max REAL, y_max REAL,
  coordinate_space TEXT CHECK(coordinate_space IS NULL OR coordinate_space='pdf_points_top_left'),
  CHECK(x_min IS NULL OR (x_min <= x_max AND y_min <= y_max))
) WITHOUT ROWID;
CREATE TABLE levels (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, elevation REAL, unit TEXT,
  source_id TEXT UNIQUE, provenance TEXT NOT NULL,
  confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1)
) WITHOUT ROWID;
CREATE TABLE spaces (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('Room','Space','Zone','Corridor')),
  name TEXT NOT NULL, number TEXT, level_id TEXT REFERENCES levels(id), source_id TEXT UNIQUE,
  provenance TEXT NOT NULL, confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1)
) WITHOUT ROWID;
CREATE TABLE element_types (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, source_id TEXT UNIQUE,
  provenance TEXT NOT NULL, confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1)
) WITHOUT ROWID;
CREATE TABLE elements (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
  type_id TEXT REFERENCES element_types(id), space_id TEXT REFERENCES spaces(id),
  level_id TEXT REFERENCES levels(id), source_id TEXT UNIQUE, provenance TEXT NOT NULL,
  confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1)
) WITHOUT ROWID;
CREATE TABLE evidence (
  id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
  sheet_id TEXT REFERENCES sheets(id), view_id TEXT REFERENCES views(id),
  pdf_page INTEGER NOT NULL CHECK(pdf_page >= 1),
  x_min REAL, y_min REAL, x_max REAL, y_max REAL,
  coordinate_space TEXT CHECK(coordinate_space IS NULL OR coordinate_space='pdf_points_top_left'),
  CHECK(x_min IS NULL OR (x_min <= x_max AND y_min <= y_max))
) WITHOUT ROWID;
CREATE TABLE parameters (
  id TEXT PRIMARY KEY, entity_kind TEXT NOT NULL, entity_id TEXT NOT NULL,
  name TEXT NOT NULL, value_text TEXT, numeric_value REAL, unit TEXT,
  provenance TEXT NOT NULL, confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),
  evidence_id TEXT REFERENCES evidence(id),
  CHECK(value_text IS NOT NULL OR numeric_value IS NOT NULL),
  CHECK(numeric_value IS NULL OR unit IS NOT NULL)
) WITHOUT ROWID;
CREATE TABLE relationships (
  id TEXT PRIMARY KEY, source_kind TEXT NOT NULL, source_id TEXT NOT NULL,
  relation_type TEXT NOT NULL, target_kind TEXT NOT NULL, target_id TEXT NOT NULL,
  provenance TEXT NOT NULL, confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),
  evidence_id TEXT REFERENCES evidence(id)
) WITHOUT ROWID;
CREATE TABLE annotations (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('dimension','spot_elevation','text_annotation','tag','grid_reference')),
  semantic_type TEXT, display_text TEXT NOT NULL, numeric_value REAL, unit TEXT,
  related_entity_kind TEXT, related_entity_id TEXT,
  provenance TEXT NOT NULL, confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),
  evidence_id TEXT NOT NULL REFERENCES evidence(id),
  CHECK(numeric_value IS NULL OR unit IS NOT NULL)
) WITHOUT ROWID;
CREATE TABLE geometries (
  rowid INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, entity_kind TEXT NOT NULL, entity_id TEXT NOT NULL,
  geometry_type TEXT NOT NULL CHECK(geometry_type IN ('point','oriented_point','bbox2d','bbox3d','line','polyline','footprint')),
  geometry_json TEXT NOT NULL, coordinate_system TEXT NOT NULL, unit TEXT NOT NULL,
  min_x REAL NOT NULL, max_x REAL NOT NULL, min_y REAL NOT NULL, max_y REAL NOT NULL,
  min_z REAL NOT NULL, max_z REAL NOT NULL,
  provenance TEXT NOT NULL, confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),
  evidence_id TEXT REFERENCES evidence(id),
  CHECK(min_x <= max_x AND min_y <= max_y AND min_z <= max_z)
);
CREATE VIRTUAL TABLE geometry_rtree USING rtree(rowid, min_x, max_x, min_y, max_y, min_z, max_z);
CREATE TRIGGER geometry_insert AFTER INSERT ON geometries BEGIN
  INSERT INTO geometry_rtree VALUES (new.rowid,new.min_x,new.max_x,new.min_y,new.max_y,new.min_z,new.max_z);
END;
CREATE TRIGGER geometry_delete AFTER DELETE ON geometries BEGIN DELETE FROM geometry_rtree WHERE rowid=old.rowid; END;
CREATE TRIGGER geometry_update AFTER UPDATE OF min_x,max_x,min_y,max_y,min_z,max_z ON geometries BEGIN
  UPDATE geometry_rtree SET min_x=new.min_x,max_x=new.max_x,min_y=new.min_y,max_y=new.max_y,min_z=new.min_z,max_z=new.max_z WHERE rowid=new.rowid;
END;
CREATE TABLE search_content (
  rowid INTEGER PRIMARY KEY, record_kind TEXT NOT NULL, record_id TEXT NOT NULL,
  content TEXT NOT NULL, UNIQUE(record_kind, record_id)
);
-- unicode61 is available in standard FTS5 builds and tokenizes Japanese runs. QueryCore
-- additionally falls back to deterministic Unicode substring matching for unsegmented text.
CREATE VIRTUAL TABLE search_fts USING fts5(content, content='search_content', content_rowid='rowid', tokenize='unicode61');
CREATE TRIGGER search_ai AFTER INSERT ON search_content BEGIN INSERT INTO search_fts(rowid,content) VALUES(new.rowid,new.content); END;
CREATE TRIGGER search_ad AFTER DELETE ON search_content BEGIN INSERT INTO search_fts(search_fts,rowid,content) VALUES('delete',old.rowid,old.content); END;
CREATE TRIGGER search_au AFTER UPDATE ON search_content BEGIN
  INSERT INTO search_fts(search_fts,rowid,content) VALUES('delete',old.rowid,old.content);
  INSERT INTO search_fts(rowid,content) VALUES(new.rowid,new.content);
END;
