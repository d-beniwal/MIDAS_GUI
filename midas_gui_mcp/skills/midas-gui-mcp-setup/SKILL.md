---
name: midas-gui-mcp-setup
description: Use before driving the midas-gui app through its MCP server (tools named data_viewer_*, calibrate_*, solve_cell_*), or when those tools are missing or fail to connect. Covers the control socket (on by default, how to opt out), registering the server, and diagnosing a dead socket.
---

# midas-gui MCP: setup and connection

The MCP server (`midas_gui_mcp/server.py`) is a thin proxy to a **running**
midas-gui process over a Unix socket. No GUI with the socket enabled, no tools
that work.

## Checklist

1. **GUI running with the socket on:** it is ON by default — a plain
   `python launch.py` (or `midas-gui`) from the repo root, in the midas-gui env. The status
   bar says `Agent control: ready`. It is off only if the GUI was started with
   `--no-agent-control` or `MIDAS_GUI_CONTROL=0` (status bar: `off`).
2. **Socket is live:** `~/.midas_gui/midas_gui_control_v1.sock_path` holds the
   socket path; `ls -la` that path must show an `s` file. A path that does
   not exist means the GUI was started with `--no-agent-control` /
   `MIDAS_GUI_CONTROL=0`, or the file is left over from an older run. Restart the GUI without
   the flag. If the status bar says `Agent control: other window`, a second midas-gui is
   running and the agent drives the FIRST one; close it or the extra window.
3. **Server registered with the client:** `claude mcp list` should show
   `midas-gui`. If not, run `python midas_gui_mcp/install.py --register`
   (any OS; makes a venv under `~/.midas_gui/mcp-venv` and registers at
   `--scope user`). **Restart the client session** after registering; tools do
   not appear mid-session.
4. **New ops need a GUI restart.** Ops live in `midas_gui/control_api.py`
   inside the GUI process. After editing it, restart the GUI and the client.

## Fallback without MCP

`midas_gui_mcp/client.py` speaks the same protocol with the stdlib only:

```python
from client import ControlClient       # sys.path.insert the midas_gui_mcp dir
with ControlClient(timeout=60) as c:
    c.call("calibrate.get_state")      # args are a DICT: c.call(op, {...})
```

Op names are the tool names with the first `_` as `.`
(`calibrate_get_state` -> `calibrate.get_state`). Run scripts with
`python -I` from a scratch directory, not from a data folder.

## Rules

- **Setup is read-only for the repo.** Never edit, commit or stash tracked files
  in the midas-gui checkout to get the MCP server working (not `server.py`,
  `client.py`, the skills, the README, `.context/`), never use
  `claude mcp add --scope project` (writes `.mcp.json`), and do not append to
  `.context/STATE.md` / `DECISIONS.md` for a setup task. A user's `git pull`
  breaks on any locally modified tracked file. If something needs a code
  change, tell the user instead.

- Tools are not exposed to GUI-only controls: if a button has no tool, say so
  rather than hunting for a workaround. Offer to add an op instead.
- GUI state persists between calls. Read it (`*_get_state`) before assuming
  defaults.
