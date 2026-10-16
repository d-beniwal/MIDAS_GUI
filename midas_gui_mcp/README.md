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
