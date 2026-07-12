"""Tests for the per-user configuration system (settings + constants overlay)."""
import json
import os
import subprocess
import sys

from midas_gui import settings


# ── settings unit tests (no Qt) ──────────────────────────────────────────────
def test_user_config_path_per_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/xdg_home_test")
    p = settings.user_config_path()
    assert p.name == "config.json" and p.parent.name == "midas_gui"
    assert str(p).startswith("/tmp/xdg_home_test")


def test_save_reload_reset_roundtrip(tmp_path, monkeypatch):
    cfgfile = tmp_path / "midas_gui" / "config.json"
    monkeypatch.setattr(settings, "user_config_path", lambda: cfgfile)
    settings.save_user_config({"geometry": {"wavelength_A": 0.271}})
    assert cfgfile.is_file()
    assert settings.reload()["geometry"]["wavelength_A"] == 0.271
    settings.reset_user_config()
    assert not cfgfile.is_file()
    assert settings.reload() == {}


# ── overlay in a fresh interpreter (constants applies overlay at import) ──────
def _env_with_config_home(tmp_path):
    """Env that redirects the per-user config dir to a temp location on all OSes."""
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    env["HOME"] = str(tmp_path)              # macOS: ~/Library/Application Support
    env["XDG_CONFIG_HOME"] = str(tmp_path / ".config")   # Linux
    env["APPDATA"] = str(tmp_path / "AppData")           # Windows
    return env


def _write_user_config(env, cfg):
    """Compute the user path under the temp env and write the config there."""
    snippet = ("import json,sys;from midas_gui import settings as s;"
               "p=s.user_config_path();p.parent.mkdir(parents=True,exist_ok=True);"
               "p.write_text(sys.stdin.read());print(p)")
    r = subprocess.run([sys.executable, "-c", snippet], input=json.dumps(cfg),
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_overlay_applies_and_replaces(tmp_path):
    env = _env_with_config_home(tmp_path)
    _write_user_config(env, {
        "geometry": {"wavelength_A": 0.222},
        "materials": {"OnlyPhase": {"a": 3.6, "b": 3.6, "c": 3.6,
                                    "alpha": 90, "beta": 90, "gamma": 90, "sg": 225}},
        "ui": {"integration_kernel": "hard", "calibration_pipeline": "four_stage"},
    })
    check = (
        "import midas_gui.constants as c;"
        "assert abs(c.DEFAULT_WAVELENGTH-0.222)<1e-9, c.DEFAULT_WAVELENGTH;"
        "assert list(c.MATERIALS)==['OnlyPhase'], list(c.MATERIALS);"   # replace, not merge
        "assert c.DEFAULT_KERNEL=='hard';"
        "assert c.DEFAULT_PIPELINE=='four_stage';"
        "print('ok')"
    )
    r = subprocess.run([sys.executable, "-c", check], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout


def test_malformed_config_does_not_break(tmp_path):
    env = _env_with_config_home(tmp_path)
    # write invalid JSON directly at the computed path
    snippet = ("from midas_gui import settings as s;"
               "p=s.user_config_path();p.parent.mkdir(parents=True,exist_ok=True);"
               "p.write_text('{ not valid json ,,,');print(p)")
    r = subprocess.run([sys.executable, "-c", snippet], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    check = ("import midas_gui.constants as c;"
             "assert c.DEFAULT_WAVELENGTH==0.39, c.DEFAULT_WAVELENGTH;print('ok')")
    r = subprocess.run([sys.executable, "-c", check], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout
