# Templates

BulletJournal supports built-in templates and dynamically discovered external template providers.

## Provider model

- built-ins ship from `src/bulletjournal/templates/`
- external providers are discovered from Python entry points in the `bulletjournal.templates` group
- BulletJournal does not import any private template package directly

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

Template metadata exposed by the API includes `provider`, `kind`, `name`, `ref`, and `origin_revision`.
