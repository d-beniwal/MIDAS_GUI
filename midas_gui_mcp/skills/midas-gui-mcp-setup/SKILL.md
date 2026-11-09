---
name: midas-gui-mcp-setup
description: Use before driving the midas-gui app through its MCP server (tools named data_viewer_*, calibrate_*, solve_cell_*), or when those tools are missing or fail to connect. Covers launching the GUI with the control socket, registering the server, and diagnosing a dead socket.
---

# midas-gui MCP: setup and connection

The MCP server (`midas_gui_mcp/server.py`) is a thin proxy to a **running**
midas-gui process over a Unix socket. No GUI with the socket enabled, no tools
that work.

## Checklist

1. **GUI running with the socket on:** `MIDAS_GUI_CONTROL=1 python launch.py`
   (from the repo root, in the midas-gui env). Off by default.
2. **Socket is live:** `~/.midas_gui/midas_gui_control_v1.sock_path` holds the
   socket path; `ls -la` that path must show an `s` file. A path that does
   not exist means the GUI was started without `MIDAS_GUI_CONTROL=1`, or the
   file is left over from an older run. Restart the GUI.
3. **Server registered with the client:** `claude mcp list` should show
   `midas-gui`. If not:
   `claude mcp add --transport stdio midas-gui -- <venv>/bin/python <repo>/midas_gui_mcp/server.py`
   (venv from `midas_gui_mcp/requirements.txt`; it does not need the GUI's
   own scientific stack). **Restart the client session** after registering;
   tools do not appear mid-session.
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

- Tools are not exposed to GUI-only controls: if a button has no tool, say so
  rather than hunting for a workaround. Offer to add an op instead.
- GUI state persists between calls. Read it (`*_get_state`) before assuming
  defaults.
