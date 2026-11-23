"""Every Mask Builder control must have an MCP tool, and every tool must map to a real control-API op
with arguments it accepts, so the MCP server and ``GuiController`` cannot drift apart.
Source-level check (needs neither the ``mcp`` package nor Qt for the tool lists)."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Mask Builder control group (section of the tab) -> the MCP tool that drives it.
REQUIRED_TOOLS = {
    "mask_set_image",                # Image field + dataset + frame navigator
    "mask_set_frame",
    "mask_set_threshold",            # 1 · pixel <=, pixel >, projection, skip frames
    "mask_set_statistical",          # 2 · spatial / temporal, K_sigma, hot, dead, frozen
    "mask_set_stack",                # 2 · stack path / dataset / stride / explicit files
    "mask_set_spike",                # 3 · spike rejection
    "mask_set_cosmic_ray",           # 3b · cosmic-ray rejection
    "mask_set_calibration",          # 4 · calibration source
    "mask_set_calibration_methods",  # 4 · azimuthal clip, learnable mask
    "mask_set_postprocess",          # 5 · dilation
    "mask_set_display",              # overlay, draw toggle, log, colormap
    "mask_compute", "mask_wait", "mask_get_job",
    "mask_load_existing", "mask_save", "mask_log_to_project",
    "mask_add_shape", "mask_add_points", "mask_list_shapes", "mask_remove_shape",
    "mask_clear_shapes", "mask_apply_shapes",
    "mask_get_state", "mask_get_array",
}


def _src():
    return (REPO / "midas_gui_mcp" / "server.py").read_text()


def test_every_mask_control_has_an_mcp_tool():
    tools = set(re.findall(r"def (mask_\w+)\(", _src()))
    assert REQUIRED_TOOLS <= tools, sorted(REQUIRED_TOOLS - tools)
    assert tools <= REQUIRED_TOOLS, f"tool not in the coverage list: {sorted(tools - REQUIRED_TOOLS)}"


def test_every_mask_tool_calls_an_existing_control_op():
    from midas_gui.control_api import GuiController
    from midas_gui.control_server import _ASYNC_OPS
    ops = set(re.findall(r'_call\("(mask\.\w+)"', _src()))
    assert len(ops) == len(REQUIRED_TOOLS)
    for op in ops:
        assert hasattr(GuiController, "op_" + op.replace(".", "_")) or op in _ASYNC_OPS, op


def test_every_widget_of_the_mask_tab_is_reachable():
    """Each user-editable state widget of the tab (``_state_widgets``) is set by some mask op."""
    ops_src = (REPO / "midas_gui" / "control_api.py").read_text()
    ops_src = ops_src[ops_src.index("class _MaskOps"):ops_src.index("class GuiController")]
    widgets = ["_lower", "_upper", "_thresh_proj_combo", "_thresh_skip", "_spatial_check",
               "_temporal_check", "_k_sigma", "_hot_factor", "_dead_factor", "_frozen_frac", "_gap_guard", "_gap_factor",
               "_stack_ed", "_stack_ds_combo", "_stride_spin", "_spike_check", "_spike_sigma",
               "_cosmic_check", "_cosmic_sigma", "_azim_check", "_azim_sigma", "_learn_check",
               "_learn_steps", "_learn_lr", "_learn_sparsity", "_dilation_spin", "_save_edit",
               "_load_mask_edit", "_show_overlay_check", "_draw_check", "_img_edit", "_h5loc_edit"]
    missing = [w for w in widgets if f"t.{w}." not in ops_src]
    assert not missing, missing


def test_tool_arguments_are_accepted_by_the_control_op():
    import inspect
    from midas_gui.control_api import GuiController
    for m in re.finditer(r'return _call\("(mask\.\w+)"((?:[^()]|\([^()]*\))*)\)', _src()):
        op, args = m.group(1), m.group(2)
        fn = getattr(GuiController, "op_" + op.replace(".", "_"), None)
        if fn is None:
            continue
        params = set(inspect.signature(fn).parameters)
        for name in re.findall(r"(\w+)=", args):
            assert name in params, f"{op}: tool forwards '{name}' but the op takes {sorted(params)}"
