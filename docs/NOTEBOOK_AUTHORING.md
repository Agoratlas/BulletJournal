# Notebook Authoring

## Required import

Import `artifacts` explicitly in the notebook source. A good place is
`with app.setup:` so every cell can reference it.

```python
with app.setup:
    from bulletjournal.runtime import artifacts
```

## Supported patterns

```python
count = artifacts.pull(name='count', data_type=int, default=10)
frame = artifacts.pull(name='frame', data_type=pd.DataFrame)

artifacts.push(result, name='summary', data_type=str)
artifacts.push(df, name='clean_df', data_type=pd.DataFrame, is_output=True)

with artifacts.push_file(name='plot', extension='.png', is_output=True) as path:
    plt.savefig(path)
```

## Parser rules

- artifact calls must be direct top-level calls
- no aliasing, wrappers, loops, or conditionals around artifact declarations
- names, descriptions, and `is_output` values must be literals
- unsupported type expressions normalize to `object` with a warning
- runtime pushes must match the parsed contract exactly; undeclared outputs or type/role mismatches fail the run

## Rejected examples

These patterns are intentionally invalid:

```python
puller = artifacts.pull
count = puller(name='count', data_type=int)
```

```python
if enabled:
    artifacts.push(result, name='summary', data_type=str, is_output=True)
```

```python
artifacts.push(result, name=dynamic_name, data_type=str, is_output=True)
```

## Docs extraction

The first Markdown-style Marimo cell is used as the notebook documentation in the graph view.

## Interactive caveat

Interactive `Edit & Run` artifacts are marked with heuristic lineage.

## Stale behavior

- if an upstream artifact is stale and the run proceeds with `use_stale`, the downstream outputs are persisted as `stale`
- if a notebook source changes, its existing outputs are marked stale
- if port changes remove graph edges, a durable warning is stored in backend validation issues

## Standalone execution

You can also run a notebook directly as a Python script from inside an
BulletJournal project root:

```python
if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
```

This preserves `artifacts.pull(...)` and `artifacts.push(...)` behavior
without going through the BulletJournal server.
