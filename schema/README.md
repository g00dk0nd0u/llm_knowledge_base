# Metadata Schema

The processing pipeline should produce enough metadata to resolve every derived knowledge item back to its original source.

Minimum document metadata:

- `document_id`
- `source_file`
- `source_sha256`
- `project_id`
- `title` when known
- `document_type` when known
- `discipline` when known
- `language` when known
- `page_count`
- `pipeline_version`

Minimum derived Markdown metadata:

- source PDF
- source page or page range
- stable document ID
- project ID

Unknown values must remain null/empty rather than being inferred without evidence.

`document.schema.json` validates each generated `document.json` file.
`manifest.schema.json` validates reconciled project manifests. Both use JSON Schema draft
2020-12. `python -m pytest` validates synthetic generated examples without real documents.
