# Troubleshooting

## Notebook run fails with parser errors

Check `validation_issues` in the snapshot or the bundled UI.
The strict parser rejects aliased or indirect artifact calls.

## Artifact stays stale

Stale artifacts keep their last value by design.
Run upstream notebooks or use the run-all queue to refresh them.

## Project opens but web page is minimal

The bundled page is a lightweight MVP shell over the backend APIs.
It is not yet a full ReactFlow editor.

## Marimo import visibility

BulletJournal injects the runtime artifact API into notebook execution so `artifacts` is available during managed runs.

## Rebuild from disk

If notebook interfaces or validation look out of sync:

```bash
bulletjournal rebuild-state .
```
