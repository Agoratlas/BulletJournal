# Agent Guide

## Documentation

High-level documentation of this project is available in `README.md`, and more technical details in `docs/` split into sub-documents by topic.

## Execution environment

The development environment to be used is in `.venv/`, which must be used for any development done (including tests and pre-commit checks) unless specified otherwise.

## Verify changes

- After performing any change: `pre-commit run --all-files`
- If the change affects the frontend: `npm run build`
- If the change affects the backend: `pytest`

If the changes are minor and unlikely to affect the entire codebase, it's acceptable to run a focused subset of tests relevant to the changes. In case of doubt, run the full test suite.

Do not perform changes on the git state (staging changes, committing, etc.) unless specifically asked.

## Fixing bugs

When asked to fix a bug on the backend:
- Write one or more tests that assert the correct behavior of the application
- Run pytest to ensure that at least one of the new tests fail
- Implement the fix
- Run pytest again to ensure that the tests now pass
