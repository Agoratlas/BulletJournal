# MCP

BulletJournal provides a local Model Context Protocol (MCP) endpoint when
`BULLETJOURNAL_ENABLE_MCP` is explicitly enabled. The default local endpoint is
`http://127.0.0.1:8765/mcp`.

Only `1`, `true`, `yes`, and `on` enable MCP. `0`, `false`, `no`, and `off`
disable it. Invalid nonempty values fail startup. When the server is deliberately
bound beyond loopback, set `BULLETJOURNAL_MCP_TOKEN` and send it as a bearer token.

The server validates browser Origins and permits no-Origin local MCP clients.
It exposes compact template and project resources plus tools for template lookup,
project inspection, graph changes, constants, and managed runs. Graph writes use
the existing graph-version and request-id concurrency controls. Template source
and pipeline definitions are opt-in. Project paths, full snapshots, and generic
pickle-backed artifact data are not exposed.

When `--base-path /p/<project-id>` is used, the endpoint is
`/p/<project-id>/mcp`.
