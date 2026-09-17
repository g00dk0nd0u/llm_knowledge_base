PRAGMA foreign_keys = ON;

CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE documents (id TEXT PRIMARY KEY, identity TEXT NOT NULL UNIQUE, title TEXT NOT NULL, source_filename TEXT NOT NULL, source_sha256 TEXT NOT NULL CHECK(length(source_sha256)=64)) WITHOUT ROWID;
CREATE TABLE source_models (
  id TEXT PRIMARY KEY, role TEXT NOT NULL CHECK(role IN ('host','link')), title TEXT NOT NULL,
  revit_version TEXT, model_identity_kind TEXT NOT NULL, model_identity TEXT NOT NULL,
  snapshot_version_guid TEXT, snapshot_save_number INTEGER,
  UNIQUE(model_identity_kind, model_identity)
) WITHOUT ROWID;
CREATE TABLE link_instances (
  id TEXT PRIMARY KEY,
  host_source_model_id TEXT NOT NULL REFERENCES source_models(id),
  linked_source_model_id TEXT NOT NULL REFERENCES source_models(id),
  source_unique_id TEXT NOT NULL CHECK(length(source_unique_id)>0),
  name TEXT, transform_to_host_json TEXT NOT NULL, provenance TEXT NOT NULL,
  UNIQUE(host_source_model_id, source_unique_id)
) WITHOUT ROWID;
CREATE TABLE sheets (
  id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  number TEXT NOT NULL, name TEXT NOT NULL, pdf_page INTEGER NOT NULL CHECK(pdf_page >= 1), export_order INTEGER NOT NULL CHECK(export_order >= 1),
  source_model_id TEXT REFERENCES source_models(id), source_unique_id TEXT,
  UNIQUE(document_id, number), UNIQUE(export_order), UNIQUE(source_model_id, source_unique_id)
) WITHOUT ROWID;
CREATE TABLE views (
  id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  name TEXT NOT NULL, view_type TEXT, export_order INTEGER CHECK(export_order >= 1),
  source_model_id TEXT REFERENCES source_models(id), source_unique_id TEXT,
  UNIQUE(source_model_id, source_unique_id)
) WITHOUT ROWID;
CREATE TABLE viewports (
  id TEXT PRIMARY KEY, sheet_id TEXT NOT NULL REFERENCES sheets(id) ON DELETE CASCADE,
  view_id TEXT NOT NULL REFERENCES views(id) ON DELETE CASCADE,
  placement_kind TEXT NOT NULL CHECK(placement_kind IN ('viewport','schedule')),
  sheet_x_min REAL, sheet_y_min REAL, sheet_x_max REAL, sheet_y_max REAL, sheet_coordinate_unit TEXT,
  pdf_x_min REAL, pdf_y_min REAL, pdf_x_max REAL, pdf_y_max REAL,
  pdf_coordinate_space TEXT CHECK(pdf_coordinate_space IS NULL OR pdf_coordinate_space='pdf_points_top_left'),
  sheet_to_pdf_transform_json TEXT, mapping_quality TEXT CHECK(mapping_quality IN ('unknown','approximate','calibrated','exact')),
  CHECK(sheet_x_min IS NULL OR (sheet_x_min<=sheet_x_max AND sheet_y_min<=sheet_y_max)),
  CHECK(pdf_x_min IS NULL OR (pdf_x_min<=pdf_x_max AND pdf_y_min<=pdf_y_max))
) WITHOUT ROWID;
CREATE TABLE levels (id TEXT PRIMARY KEY, name TEXT NOT NULL, elevation REAL, unit TEXT, source_model_id TEXT REFERENCES source_models(id), source_unique_id TEXT, provenance TEXT NOT NULL, confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1), UNIQUE(source_model_id,source_unique_id)) WITHOUT ROWID;
CREATE TABLE spaces (id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('Room','Space','Zone','Corridor')), name TEXT NOT NULL, number TEXT, level_id TEXT REFERENCES levels(id), source_model_id TEXT REFERENCES source_models(id), source_unique_id TEXT, phase_source_unique_id TEXT, provenance TEXT NOT NULL, confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1), UNIQUE(source_model_id,source_unique_id)) WITHOUT ROWID;
CREATE TABLE element_types (id TEXT PRIMARY KEY, family_name TEXT, type_name TEXT NOT NULL, category TEXT NOT NULL, source_model_id TEXT REFERENCES source_models(id), source_unique_id TEXT, provenance TEXT NOT NULL, confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1), UNIQUE(source_model_id,source_unique_id)) WITHOUT ROWID;
CREATE TABLE elements (id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, type_id TEXT REFERENCES element_types(id), space_id TEXT REFERENCES spaces(id), level_id TEXT REFERENCES levels(id), source_model_id TEXT REFERENCES source_models(id), source_unique_id TEXT, provenance TEXT NOT NULL, confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1), UNIQUE(source_model_id,source_unique_id)) WITHOUT ROWID;
CREATE TABLE evidence (id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id), sheet_id TEXT REFERENCES sheets(id), view_id TEXT REFERENCES views(id), pdf_page INTEGER NOT NULL CHECK(pdf_page>=1), x_min REAL,y_min REAL,x_max REAL,y_max REAL, coordinate_space TEXT CHECK(coordinate_space IS NULL OR coordinate_space='pdf_points_top_left'), CHECK(x_min IS NULL OR (x_min<=x_max AND y_min<=y_max))) WITHOUT ROWID;
CREATE TABLE parameters (
  id TEXT PRIMARY KEY, entity_kind TEXT NOT NULL, entity_id TEXT NOT NULL,
  scope TEXT CHECK(scope IN ('instance','type')), definition_name TEXT NOT NULL, definition_key TEXT,
  storage_type TEXT, data_type_id TEXT, parameter_type_id TEXT, shared_parameter_guid TEXT, unit_type_id TEXT,
  raw_value_text TEXT, raw_numeric_value REAL, numeric_value REAL, unit TEXT, value_text TEXT,
  provenance TEXT NOT NULL, confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1), evidence_id TEXT REFERENCES evidence(id),
  CHECK(value_text IS NOT NULL OR raw_value_text IS NOT NULL OR numeric_value IS NOT NULL OR raw_numeric_value IS NOT NULL), CHECK(numeric_value IS NULL OR unit IS NOT NULL)
) WITHOUT ROWID;
CREATE TABLE relationships (id TEXT PRIMARY KEY, source_kind TEXT NOT NULL, source_id TEXT NOT NULL, relation_type TEXT NOT NULL, target_kind TEXT NOT NULL, target_id TEXT NOT NULL, phase_source_unique_id TEXT, provenance TEXT NOT NULL, confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1), evidence_id TEXT REFERENCES evidence(id)) WITHOUT ROWID;
CREATE TABLE annotations (id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('dimension','spot_elevation','spot_coordinate','text_annotation','tag','grid_reference')), semantic_type TEXT, display_text TEXT NOT NULL, numeric_value REAL, unit TEXT, related_entity_kind TEXT, related_entity_id TEXT, source_model_id TEXT REFERENCES source_models(id), source_unique_id TEXT, view_id TEXT REFERENCES views(id), provenance TEXT NOT NULL, confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1), evidence_id TEXT REFERENCES evidence(id), CHECK(numeric_value IS NULL OR unit IS NOT NULL), UNIQUE(source_model_id,source_unique_id)) WITHOUT ROWID;
CREATE TABLE annotation_segments (id TEXT PRIMARY KEY, annotation_id TEXT NOT NULL REFERENCES annotations(id) ON DELETE CASCADE, segment_index INTEGER NOT NULL CHECK(segment_index>=0), numeric_value REAL, unit TEXT, display_text TEXT, value_override TEXT, prefix TEXT, suffix TEXT, above TEXT, below TEXT, origin_json TEXT, text_position_json TEXT, UNIQUE(annotation_id,segment_index), CHECK(numeric_value IS NULL OR unit IS NOT NULL)) WITHOUT ROWID;
CREATE TABLE annotation_references (id TEXT PRIMARY KEY, annotation_id TEXT NOT NULL REFERENCES annotations(id) ON DELETE CASCADE, reference_index INTEGER NOT NULL CHECK(reference_index>=0), target_source_model_id TEXT REFERENCES source_models(id), target_source_unique_id TEXT, target_link_instance_id TEXT REFERENCES link_instances(id), stable_reference TEXT, reference_type TEXT, is_linked INTEGER NOT NULL CHECK(is_linked IN (0,1)), resolution_state TEXT NOT NULL CHECK(resolution_state IN ('resolved','unresolved','orphan')), UNIQUE(annotation_id,reference_index)) WITHOUT ROWID;
CREATE TABLE entity_appearances (id TEXT PRIMARY KEY, entity_kind TEXT NOT NULL, entity_id TEXT NOT NULL, sheet_id TEXT REFERENCES sheets(id), view_id TEXT REFERENCES views(id), viewport_id TEXT REFERENCES viewports(id), link_instance_id TEXT REFERENCES link_instances(id), pdf_page INTEGER NOT NULL CHECK(pdf_page>=1), x_min REAL,y_min REAL,x_max REAL,y_max REAL, coordinate_space TEXT NOT NULL CHECK(coordinate_space='pdf_points_top_left'), appearance_kind TEXT NOT NULL CHECK(appearance_kind IN ('model','annotation','schedule','detail')), bbox_quality TEXT NOT NULL CHECK(bbox_quality IN ('page_only','may_be_visible','view_bbox','projected_bbox','exact')), provenance TEXT NOT NULL, CHECK((x_min IS NULL AND y_min IS NULL AND x_max IS NULL AND y_max IS NULL) OR (x_min IS NOT NULL AND y_min IS NOT NULL AND x_max IS NOT NULL AND y_max IS NOT NULL AND x_min<=x_max AND y_min<=y_max))) WITHOUT ROWID;
CREATE TABLE spatial_boundaries (id TEXT PRIMARY KEY, space_id TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE, link_instance_id TEXT REFERENCES link_instances(id), loop_index INTEGER NOT NULL CHECK(loop_index>=0), loop_kind TEXT NOT NULL CHECK(loop_kind IN ('outer','inner')), coordinate_system TEXT NOT NULL CHECK(coordinate_system='host_revit_internal_origin'), unit TEXT NOT NULL CHECK(unit='mm'), provenance TEXT NOT NULL, UNIQUE(space_id,link_instance_id,loop_index)) WITHOUT ROWID;
CREATE TABLE spatial_boundary_segments (id TEXT PRIMARY KEY, boundary_id TEXT NOT NULL REFERENCES spatial_boundaries(id) ON DELETE CASCADE, link_instance_id TEXT REFERENCES link_instances(id), segment_index INTEGER NOT NULL CHECK(segment_index>=0), start_x REAL NOT NULL,start_y REAL NOT NULL,start_z REAL NOT NULL,end_x REAL NOT NULL,end_y REAL NOT NULL,end_z REAL NOT NULL, source_model_id TEXT REFERENCES source_models(id), source_unique_id TEXT, UNIQUE(boundary_id,segment_index)) WITHOUT ROWID;
CREATE TABLE geometries (rowid INTEGER PRIMARY KEY,id TEXT NOT NULL UNIQUE,entity_kind TEXT NOT NULL,entity_id TEXT NOT NULL,link_instance_id TEXT REFERENCES link_instances(id),geometry_type TEXT NOT NULL CHECK(geometry_type IN ('point','oriented_point','bbox2d','bbox3d','line','polyline','footprint')),geometry_json TEXT NOT NULL,coordinate_system TEXT NOT NULL CHECK(coordinate_system='host_revit_internal_origin'),unit TEXT NOT NULL CHECK(unit='mm'),min_x REAL NOT NULL,max_x REAL NOT NULL,min_y REAL NOT NULL,max_y REAL NOT NULL,min_z REAL NOT NULL,max_z REAL NOT NULL,provenance TEXT NOT NULL,confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),evidence_id TEXT REFERENCES evidence(id),CHECK(min_x<=max_x AND min_y<=max_y AND min_z<=max_z));
CREATE VIRTUAL TABLE geometry_rtree USING rtree(rowid,min_x,max_x,min_y,max_y,min_z,max_z);
CREATE TRIGGER geometry_insert AFTER INSERT ON geometries BEGIN INSERT INTO geometry_rtree VALUES(new.rowid,new.min_x,new.max_x,new.min_y,new.max_y,new.min_z,new.max_z); END;
CREATE TRIGGER geometry_delete AFTER DELETE ON geometries BEGIN DELETE FROM geometry_rtree WHERE rowid=old.rowid; END;
CREATE TRIGGER geometry_update AFTER UPDATE OF min_x,max_x,min_y,max_y,min_z,max_z ON geometries BEGIN UPDATE geometry_rtree SET min_x=new.min_x,max_x=new.max_x,min_y=new.min_y,max_y=new.max_y,min_z=new.min_z,max_z=new.max_z WHERE rowid=new.rowid; END;
CREATE TABLE search_content (rowid INTEGER PRIMARY KEY,record_kind TEXT NOT NULL,record_id TEXT NOT NULL,content TEXT NOT NULL,UNIQUE(record_kind,record_id));
CREATE VIRTUAL TABLE search_fts USING fts5(content,content='search_content',content_rowid='rowid',tokenize='unicode61');
CREATE TRIGGER search_ai AFTER INSERT ON search_content BEGIN INSERT INTO search_fts(rowid,content) VALUES(new.rowid,new.content); END;
CREATE TRIGGER search_ad AFTER DELETE ON search_content BEGIN INSERT INTO search_fts(search_fts,rowid,content) VALUES('delete',old.rowid,old.content); END;
CREATE TRIGGER search_au AFTER UPDATE ON search_content BEGIN INSERT INTO search_fts(search_fts,rowid,content) VALUES('delete',old.rowid,old.content); INSERT INTO search_fts(rowid,content) VALUES(new.rowid,new.content); END;
