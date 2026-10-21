# midas-gui MCP server (phase 1: Data Viewer + Calibrate)

Lets an LLM agent — this one, or any other framework that speaks MCP —
drive the live midas-gui application instead of a human clicking through
it. Scoped for now to the Data Viewer and Calibrate tabs.

```
Agent  →  MCP server (this folder, separate process)  →  control socket
       →  GuiController (inside the running midas_gui process)  →  tabs
```

The MCP server here is a thin proxy. All real logic — validation, dialog
suppression, job tracking — lives in `midas_gui/control_api.py` inside the
GUI process itself. The socket protocol below is the actual interoperability
contract: anything that can open a Unix domain socket and speak
newline-delimited JSON can be a client, with or without MCP, with or without
Python.

## Running it

1. Start midas-gui with the control socket enabled:

   ```
   MIDAS_GUI_CONTROL=1 python launch.py
   ```

   This opens a local control socket (off by default for every ordinary
   user — it's an opt-in, dev-facing capability) and publishes its path to
   `~/.midas_gui/midas_gui_control_v1.sock_path`. (Deliberately under
   `$HOME`, not a tempdir — an MCP client's stdio subprocess only inherits a
   small fixed env-var allowlist that excludes `TMPDIR`, so a tempdir-based
   path resolved to the wrong directory inside that subprocess on macOS.)

2. In a separate virtualenv (this package deliberately does **not** need
   midas-gui's own pinned scientific stack):

   ```
   cd midas_gui_mcp
   python -m venv .venv && .venv/bin/pip install -r requirements.txt
   .venv/bin/python server.py
   ```

3. Register `midas_gui_mcp/server.py` with your MCP client the normal way
   (stdio transport, e.g. `claude mcp add --transport stdio midas-gui -- 
   /path/to/.venv/bin/python /path/to/midas_gui_mcp/server.py`).

## Wiring this into APEXA

[APEXA](https://github.com/d-beniwal/APEXA-APS-Beamline-Assistant) (the
beamline-assistant orchestrator at
`~/ANL-research/github/APEXA-APS-Beamline-Assistant`) already speaks MCP
natively — every one of its existing capabilities (MIDAS CLI tools, GSAS-II,
tomography, EPICS motors, filesystem) is itself a stdio MCP server spawned
by `argo_mcp_client.py::APEXAClient` using the standard `mcp` SDK
(`StdioServerParameters` / `stdio_client` / `ClientSession`, the same
pattern this package's own `server.py` implements the other end of). There
is no APEXA-specific adapter to write — `midas_gui_mcp/server.py` is a
conformant MCP server and APEXA can spawn it exactly like any of its own
servers.

### 1. Add one line to APEXA's `servers.config`

APEXA's server registry is a plain `name:path/to/server.py` text file at
the repo root, parsed identically by `launch.py`, `web_server.py`,
`gradio_ui.py`, and `trame_ui.py`. Add:

```
midas_gui:/Users/dbeniwal/ANL-research/github/midas-gui/midas_gui_mcp/server.py
```

**Use `midas_gui`, not `midas`** — APEXA already has a `midas` entry
(`midas:midas_comprehensive_server.py`) wrapping the official MIDAS CLI
executables for FF/NF/PF-HEDM. That is a different, lower-level tool
surface than this package, which drives the *running GUI process*
(Data Viewer + Calibrate tabs) via its control socket. Keeping the names
distinct keeps the two apart in APEXA's tool list, where tools show up
prefixed by server name, e.g. `[MIDAS_GUI] data_viewer_load_file` vs.
`[MIDAS] ...`.

An absolute path is required (and works — `servers.config` lines resolve
relative paths against APEXA's own root, but accept absolute ones
unchanged), since `midas_gui_mcp/server.py` lives in a sibling repo, not
inside APEXA.

### 2. No extra dependency install needed in APEXA's venv

APEXA spawns every server script with its *own* interpreter —
`argo_mcp_client.py`'s `_server_command()` resolves `.venv/bin/python3`
relative to APEXA's own working directory (or `sys.executable` — the
uv-managed venv — when run via `uv run`), not a per-server venv referenced
in `servers.config`. That interpreter already has `mcp==2.2.0` (via APEXA's
own `uv.lock`), which satisfies `midas_gui_mcp/requirements.txt`'s
`mcp>=2.0.0`, and `client.py` (the only other import `server.py` makes) is
pure standard library (`socket`/`json`/`pathlib`) — zero extra dependencies.
So `midas_gui_mcp/.venv` (built in step 2 of "Running it" above) is **not**
used when APEXA is the one spawning `server.py`; it only matters for
standalone/manual runs or registration with a different MCP client.

If this package ever gains a real third-party dependency beyond `mcp`,
either `uv add <dep>` it into APEXA's own environment, or point
`servers.config` at a tiny wrapper script that `exec`s
`midas_gui_mcp/.venv/bin/python server.py` instead of the bare `.py` file —
`_server_command()` only special-cases files ending in `.py`, so a wrapper
with no `.py` suffix falls through to being run as `node`, which is wrong;
the actual escape hatch is to make the wrapper itself end in `.py` and
`os.execv()` the dedicated venv's interpreter on the real `server.py`.

### 3. Environment is fine as-is

`_connect_one_server()` spawns each server with `env=None`, which makes the
`mcp` SDK fall back to a minimal inherited environment
(`HOME, LOGNAME, PATH, SHELL, TERM, USER` on Unix) rather than APEXA's full
`.env`-loaded process environment. That would be a problem for a server
that reads a custom env var — but `client.py` only needs `HOME`, to locate
the discovery file at `~/.midas_gui/midas_gui_control_v1.sock_path`, and
`HOME` is one of the vars always passed through. No patch to
`argo_mcp_client.py` is required.

### 4. Startup order and failure mode

APEXA can spawn `midas_gui_mcp/server.py` and complete the MCP handshake
(`initialize`, `list_tools`) regardless of whether midas-gui itself is
running yet — the control-socket connection is made lazily, on the first
actual tool call (see `server.py`'s `_call()`). So:

- Start `midas-gui` with `MIDAS_GUI_CONTROL=1` (step 1 above) before asking
  the agent to actually drive it, but it's fine to start APEXA first.
- If a tool is called before midas-gui is up (or after it's been closed),
  every `data_viewer_*`/`calibrate_*` tool call returns a normal MCP tool
  result — not a crash — with
  `{"ok": false, "error": {"type": "connection_failed", "message": "..."}}`,
  which APEXA's orchestrator sees like any other tool error and can
  relay/retry.

### 5. What APEXA's agent sees

Once registered, APEXA's tool list gains one entry per `@mcp.tool()` in
`server.py` (`data_viewer_load_file`, `data_viewer_start_live`,
`calibrate_run`, `calibrate_wait`, etc. — see "Operations (phase 1)" below
for the authoritative list), each described with its own docstring and
prefixed `[MIDAS_GUI]` the same way APEXA prefixes every other server's
tools. From the agent's perspective it's just more tools in the same
registry as its MIDAS CLI, GSAS-II, and EPICS motor tools — nothing about
the orchestration loop, planning, or tool-choice logic needs to change.

## Wire protocol (the actual contract)

One JSON object per line, both directions, over the Unix domain socket
whose path is in the discovery file above.

**Request** (client → server):
```json
{"id": 1, "op": "data_viewer.load_file", "args": {"path": "/data/scan.h5"}}
```

**Response** (server → client, same `id`):
```json
{"id": 1, "ok": true, "result": {"path": "/data/scan.h5", "n_frames": 100}}
```
or
```json
{"id": 1, "ok": false, "error": {"message": "...", "type": "no_data"}}
```

**Event** (server → client, unsolicited, no `id`):
```json
{"event": "radial_profile_updated", "data": {"has_profile": true}}
```

`error.type` is one of: `unknown_op`, `bad_args`, `bad_request`, `no_data`,
`precondition_failed`, `live_start_failed`, `composite_failed`,
`connection_failed` (client-side only), `exception` (an unexpected traceback
from inside the GUI — see `message`).

### Large data

Frames, radial profiles, and residual maps are never inlined as JSON — a
response carries `{"path", "shape", "dtype"}` pointing at a temp `.npy` file
instead (same convention midas-gui's own batch jobs already use for their
`_bg_job_results.npz` sidecar). Read the file yourself; nothing in this
protocol transfers large binary payloads inline.

### Async operations (calibration runs)

`calibrate.run` (and the `calibrate.run_frozen_point_then_oneshot` composite)
return `{"job_id": ...}` immediately — the actual fit runs in a subprocess.
Poll with `calibrate.get_job` (non-blocking) or `calibrate.wait` (blocks, up
to a timeout, pumping the GUI event loop so the GUI stays responsive while
you wait).

### Operations (phase 1)

See `midas_gui/control_api.py`'s `op_*` methods for the authoritative list
and exact argument names — each MCP tool in `server.py` is named after one
(`data_viewer.load_file` → `data_viewer_load_file`, etc.) and its tool
docstring restates what it does.

## Known limitations (phase 1)

- **Scope**: only Data Viewer and Calibrate. Mask generation and Batch
  (radial) Integrate are a later phase.
- **No true live radial-integration stream**: midas-gui recomputes the
  radial profile per live frame rather than maintaining a separate stream,
  so `radial_profile_updated` is a lightweight "something changed, go pull
  it" notification, not a data-bearing stream.
- **Dialog bypass is narrow by design**: `CalibrationTab`/
  `DetectorGeometryCard` skip their blocking dialogs only on the specific
  call paths the control API uses (via `set_agent_mode`); any other dialog
  in the app is untouched and would still block a fully unattended run if
  reached some other way.
- **One request in flight per `ControlClient` connection** — the protocol
  itself supports pipelining (via `id`), but `client.py`'s `call()` doesn't;
  open a second connection for concurrent event-watching (`iter_events()`).
