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
