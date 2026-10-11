# Basic Memory MCP launcher

A temporary stdio compatibility launcher for Copilot CLI's modern-discovery
fallback failure. It runs the **installed Basic Memory server** using the
Python MCP SDK's handshake-only driver. It does not implement another memory
system, proxy requests to a second server, or change your data.

Copilot may send modern `server/discover`, stop waiting, then send legacy
`initialize` on the same stream. A dual-era server can already have selected
modern handling and reject initialization with `-32022`. This launcher rejects
modern discovery with `-32601` without selecting modern handling, so the
handshake fallback can succeed. Subsequent tools use MCP `2025-11-25`.

Tracked upstream:

- [Basic Memory #1713](https://github.com/basicmachines-co/basic-memory/issues/1713)
- [Copilot CLI #4888](https://github.com/github/copilot-cli/issues/4888)

This removes the mixed-era startup failure, not arbitrary hangs or database
errors. A supported modern-preserving upstream fix is preferable; this adapter
is deliberately temporary.

## Requirements

- Python 3.12 or newer, **from the installed Basic Memory environment**.
- Basic Memory already installed and configured.
- Python MCP SDK 2.x and FastMCP 4.x.

Verified development environment: Basic Memory distribution `0.23.3.dev8`
(`bm --version` reports `0.23.2`), MCP `2.2.0`, FastMCP `4.0.0b1`, Python
`3.14.3`, and Copilot CLI `1.0.95-0` on Windows. Major-version checks reject
unsupported environments; other versions within those majors still need
validation because the SDK APIs are experimental.

No dependency installation is required. The launcher uses FastMCP's internal
`_mcp_server` attachment because FastMCP exposes no public handshake-only
startup option. Revalidate after updating Basic Memory. It preserves registered
tools, prompts, resources, BM's lifespan, file indexing, and stdin-EOF shutdown.
It does not run BM's CLI background package auto-updater.

## Use

For a `uv tool install basic-memory` installation on Windows, locate its Python:

```powershell
$bmPython = Join-Path (uv tool dir) 'basic-memory\Scripts\python.exe'
& $bmPython 'C:\code\dev\basic-memory-mcp-launcher\launcher.py' --help
```

Use that interpreter, not an unrelated system Python or `uv run` that could
resolve a different Basic Memory version.

After approving the configuration change, replace **only** the existing
`basic-memory` entry's `command` and `args` in the global
`%USERPROFILE%\.copilot\mcp-config.json`:

```json
{
  "type": "local",
  "command": "C:\\path\\to\\basic-memory\\Scripts\\python.exe",
  "args": ["C:\\code\\dev\\basic-memory-mcp-launcher\\launcher.py"],
  "tools": ["*"]
}
```

Substitute the actual interpreter path. Preserve the entry's other fields,
including environment, tool filters, and timeouts; preserve unrelated servers.
The launcher does not edit MCP configuration itself. Keep the script in the
durable checkout, not a Copilot session-artifact or npm-cache directory.

Existing Basic Memory config and environment remain authoritative: no project
is pinned, defaults and project mappings are not changed, and local/cloud
routing is not overridden. Local SQLite operation is the verified deployment.
Do not run against an unverified cloud or Postgres configuration.

New Copilot sessions using this entry launch the adapter. Already-failed
connections need a Basic Memory-only reconnect through Copilot's MCP manager,
or a later CLI restart. Editing the file alone does not revive a failed
connection. **Do not reload extensions to repair MCP.**

## Verification

From this repository, use the installed BM interpreter:

```powershell
& $bmPython -m unittest discover -s tests -v
```

The tests launch real stdio BM processes against the existing configuration.
They initialize the local database/index in the same way as normal BM startup,
but do not write, edit, delete, or push notes. Run only against the verified
local configuration. Tests cover compatibility errors, direct handshake,
discovery rejection and fallback, pipelined requests, live tool discovery,
invalid tool calls, EOF shutdown, and delayed startup beyond the ten-second
discovery deadline. The delayed test intentionally adds a short startup delay;
the production launcher has no sleeps or retry loop.

After deployment, verify a live `tools/list` and read an existing note with an
explicit project in an actual Copilot session. A configured entry or cached
tool list alone is not proof of a working connection. Writes are not needed
for startup acceptance.

## Rollback and source control

Restore the original BM entry's executable and arguments (normally
`bm.exe` with `["mcp"]`) and reconnect only BM. No corpus migration, package
downgrade, or file deletion is needed.

The repository's `origin` is a local bare repository in the user's OneDrive
`Documents\code\remotes` directory. No GitHub publication is required. Source
control pushes and changes to global MCP configuration require separate
approval.
