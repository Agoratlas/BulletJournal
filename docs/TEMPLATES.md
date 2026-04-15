# Templates

BulletJournal supports built-in templates and dynamically discovered external template providers.

## Provider model

- built-ins ship from `src/bulletjournal/templates/`
- external providers are discovered from Python entry points in the `bulletjournal.templates` group
- BulletJournal does not import any private template package directly

External providers are expected to expose an object compatible with
`bulletjournal.templates.provider.TemplateProvider` and can return
`bulletjournal.templates.provider.TemplateAsset` objects.

The built-in filesystem implementation lives in
`bulletjournal.templates.builtin_provider`.

Provider objects should expose:

```python
class TemplateProvider:
    provider_name: str
    provider_revision: str

    def list_notebook_templates(self) -> list[TemplateAsset]: ...
    def list_pipeline_templates(self) -> list[TemplateAsset]: ...
    def load_notebook_template(self, name: str) -> str: ...
    def load_pipeline_template(self, name: str) -> str: ...
```

Template assets should include at least:

- `name`
- `ref`
- `path`
- `hidden`

Optional fields such as `title`, `description`, `provider`, `origin_revision`, and `file_name` are also supported.

## Template refs

Template refs are globally namespaced:

```text
{provider}/{name}
```

Examples:

- `builtin/empty_notebook`
- `builtin/example_iris_pipeline`
- `external/team_default`

## Template kinds

- notebook templates are copied into `project_root/notebooks/` when instantiated
- pipeline templates expand into graph operations when instantiated

Template metadata exposed by the API includes `provider`, `kind`, `name`, `ref`, `origin_revision`, and `hidden`.

Template providers can set `hidden=True` on notebook assets returned from
`list_notebook_templates()`.

When `hidden` is `True`, the notebook template is omitted from the Blocks panel
but still remains available for pipeline templates that reference it.

Providers may load content from package data rather than repository-relative
files. This matches the external `BulletJournal-Templates` provider pattern,
where templates are discovered from package data and loaded through
`load_notebook_template()` / `load_pipeline_template()`.

## Pipeline template format

Pipeline templates are JSON objects with three required top-level arrays:

- `nodes`
- `edges`
- `layout`

Minimal shape:

```json
{
  "title": "Example pipeline",
  "description": "Optional description",
  "nodes": [],
  "edges": [],
  "layout": []
}
```

### Nodes

Every node entry must define:

- `id`: unique graph node id
- `title`: non-empty display title
- `kind`: one of `notebook`, `file_input`, `organizer`, or `area`

Additional fields depend on `kind`.

#### Notebook node

Notebook nodes reference a notebook template:

```json
{
  "id": "clean",
  "kind": "notebook",
  "title": "Clean data",
  "template_ref": "builtin/clean_dataframe"
}
```

#### File input node

File input nodes expose a single file output. The artifact name defaults to `file`.

```json
{
  "id": "source",
  "kind": "file_input",
  "title": "CSV upload",
  "artifact_name": "csv"
}
```

`artifact_name` may also be supplied as `ui.artifact_name`, but providers should prefer the top-level field.

#### Organizer node

Organizer nodes are passthrough routing blocks. Each configured organizer port creates:

- one input port named by `key`
- one output port named by `key`
- a UI label taken from `name`

Provider format:

```json
{
  "id": "fanout",
  "kind": "organizer",
  "title": "Fan-out",
  "ui": {
    "organizer_ports": [
      { "key": "train", "name": "Train", "data_type": "dataframe" },
      { "key": "test", "name": "Test", "data_type": "dataframe" }
    ]
  }
}
```

Organizer rules:

- `ui.organizer_ports` must be a list of objects
- each entry must define non-empty `key`, `name`, and `data_type`
- `key` values must be unique within the organizer
- edges connect to the organizer by `key`, not by `name`

#### Area node

Area nodes are visual-only grouping blocks. They have no inputs, outputs, or assets, so providers should not create edges to or from them.

Provider format:

```json
{
  "id": "ingestion_area",
  "kind": "area",
  "title": "Ingestion",
  "ui": {
    "title_position": "top-left",
    "area_color": "blue",
    "area_filled": true
  }
}
```

Supported `title_position` values:

- `top-left`
- `top-center`
- `top-right`
- `right-center`
- `bottom-right`
- `bottom-center`
- `bottom-left`
- `left-center`

Supported `area_color` values:

- `red`
- `orange`
- `yellow`
- `green`
- `blue`
- `purple`
- `white`
- `black`

`area_filled` is a boolean. When omitted, it defaults to `true`.

Note: the interactive UI lets users clear an area title later, but pipeline templates currently still require a non-empty `title` field for every node, including `area` nodes.

### Layout

Each node must have a matching layout entry:

```json
{
  "node_id": "fanout",
  "x": 420,
  "y": 160,
  "w": 160,
  "h": 120
}
```

Layout rules:

- every node needs a matching `layout` row
- `node_id` must reference a node defined in `nodes`
- `x`, `y`, `w`, and `h` should be integers

For `area` nodes, `w` and `h` define the visible rectangle size.

### Edges

Edges connect provider-defined ports:

```json
{
  "source_node": "source",
  "source_port": "csv",
  "target_node": "fanout",
  "target_port": "train"
}
```

Edge validation uses the effective interface for each node kind:

- notebook: parsed from the referenced notebook template
- file input: a single file output
- organizer: synthetic input/output pairs from `ui.organizer_ports`
- area: no ports

### Complete example

```json
{
  "title": "Dataset review",
  "nodes": [
    {
      "id": "review_area",
      "kind": "area",
      "title": "Review",
      "ui": {
        "title_position": "top-left",
        "area_color": "purple",
        "area_filled": false
      }
    },
    {
      "id": "source",
      "kind": "file_input",
      "title": "CSV upload",
      "artifact_name": "csv"
    },
    {
      "id": "fanout",
      "kind": "organizer",
      "title": "Fan-out",
      "ui": {
        "organizer_ports": [
          { "key": "raw", "name": "Raw", "data_type": "file" },
          { "key": "clean", "name": "Clean", "data_type": "dataframe" }
        ]
      }
    }
  ],
  "edges": [
    {
      "source_node": "source",
      "source_port": "csv",
      "target_node": "fanout",
      "target_port": "raw"
    }
  ],
  "layout": [
    { "node_id": "review_area", "x": 80, "y": 80, "w": 640, "h": 320 },
    { "node_id": "source", "x": 140, "y": 180, "w": 320, "h": 220 },
    { "node_id": "fanout", "x": 500, "y": 190, "w": 160, "h": 120 }
  ]
}
```
