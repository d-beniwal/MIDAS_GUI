"""Every Solve Cell MCP tool must map to a real control-API op (and the other way round for the
workflows we promise agents), so the MCP server and ``GuiController`` cannot drift apart.
Source-level check: needs neither the ``mcp`` package nor Qt."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Each Solve Cell workflow/input an agent must be able to drive -> the MCP tool that does it.
REQUIRED_TOOLS = {
    # data inputs
    "solve_cell_panel_load_data", "solve_cell_panel_set_field", "solve_cell_panel_set_masks",
    "solve_cell_panel_load_calibration", "solve_cell_panel_set_omega",
    "solve_cell_panel_list_omega_fields", "solve_cell_panel_detect_first_idx",
    "solve_cell_panel_get_inputs",
    # parameter suggestion
    "solve_cell_suggest_parameters", "solve_cell_get_suggestion",
    "solve_cell_apply_suggested_parameters",
    # stages
    "solve_cell_set_ingest_params", "solve_cell_run_ingest", "solve_cell_set_material_filter",
    "solve_cell_run_diamond_filter", "solve_cell_set_index_params", "solve_cell_run_index",
    "solve_cell_run_refine", "solve_cell_set_remaining_params",
    "solve_cell_fill_remaining_from_latest_domain", "solve_cell_run_index_remaining",
    # sequential domain refinement
    "solve_cell_run_domain_loop", "solve_cell_cancel_domain_loop",
    # jobs / results / project
    "solve_cell_wait", "solve_cell_get_job", "solve_cell_get_state", "solve_cell_get_domains",
    "solve_cell_save_project",
}


def _server_tools():
    src = (REPO / "midas_gui_mcp" / "server.py").read_text()
    tools = re.findall(r"def (solve_cell_\w+)\(", src)
    ops = dict(zip(tools, re.findall(r'_call\("(solve_cell\.\w+)"', src)))
    return tools, src


def test_every_required_workflow_has_an_mcp_tool():
    tools, _ = _server_tools()
    assert REQUIRED_TOOLS <= set(tools), sorted(REQUIRED_TOOLS - set(tools))


def test_every_solve_cell_tool_calls_an_existing_control_op():
    from midas_gui.control_api import GuiController
    from midas_gui.control_server import _ASYNC_OPS
    _, src = _server_tools()
    ops = set(re.findall(r'_call\("(solve_cell\.\w+)"', src))
    assert ops, "no solve_cell ops found in the MCP server"
    for op in ops:
        method = "op_" + op.replace(".", "_")
        assert hasattr(GuiController, method) or op in _ASYNC_OPS, f"{op}: no {method} on GuiController"


def test_tool_arguments_are_accepted_by_the_control_op():
    """The keyword arguments an MCP tool forwards must exist on the op's signature."""
    import inspect
    from midas_gui.control_api import GuiController
    _, src = _server_tools()
    for m in re.finditer(r'return _call\("(solve_cell\.\w+)"((?:[^()]|\([^()]*\))*)\)', src):
        op, args = m.group(1), m.group(2)
        fn = getattr(GuiController, "op_" + op.replace(".", "_"), None)
        if fn is None:
            continue                       # async ops handled in control_server (checked above)
        sig = inspect.signature(fn)
        if any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values()):
            continue                       # op takes **kwargs (e.g. set_overlays flags)
        params = set(sig.parameters)
        for name in re.findall(r"(\w+)=", args):
            assert name in params, f"{op}: tool forwards '{name}' but the op takes {sorted(params)}"
