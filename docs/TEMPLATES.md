# Templates

BulletJournal ships two kinds of built-in templates:

- Notebook templates in `src/bulletjournal/templates/builtin/`
- Pipeline templates in `src/bulletjournal/templates/pipelines/`

Notebook templates are copied into project notebooks when instantiated.

Pipeline templates describe a small graph made of notebook-template and file-input nodes. Instantiating one creates all referenced nodes, layouts, and edges in a single graph operation. File input nodes start pending and uninitialized.

Built-ins include:

- `empty_notebook.py`
- `value_input.py`
- `example_1.py` to `example_4.py`
- `example_iris_pipeline.json`

If a pipeline template would reuse an existing node ID, the frontend asks for a prefix and the backend enforces that requirement.

Template validation is available through:

```bash
bulletjournal validate-templates
```

The validator now checks notebook templates recursively and verifies pipeline-template graph integrity, referenced notebook templates, edge ports, edge type compatibility, and DAG validity.
