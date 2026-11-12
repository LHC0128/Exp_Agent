"""升级助手的纯函数契约：依赖闭包、锁文件分段、development.txt 同步；不建环境、不联网。"""

import argparse
from types import SimpleNamespace

import pytest

from tools import upgrade_lock


def _dist(name, version, requires=()):
    return SimpleNamespace(metadata={"Name": name}, version=version, requires=list(requires))


FAKE = {
    "exp-agent": _dist("exp-agent", "0.1.0", [
        "tqdm>=4",
        'numpy>=1; extra == "runtime"',
        'uvicorn[standard]>=0.3; extra == "gui"',
        'pytest>=8; extra == "dev"',
        'jupyter>=1; extra == "notebook"',
    ]),
    "tqdm": _dist("tqdm", "4.70.1", ['colorama; platform_system == "Windows"']),
    "colorama": _dist("colorama", "0.4.6"),
    "numpy": _dist("numpy", "2.5.4"),
    "uvicorn": _dist("uvicorn", "0.54.0", [
        "h11>=0.8",
        'websockets>=10; extra == "standard"',
        'uvloop; sys_platform != "win32" and extra == "standard"',
    ]),
    "h11": _dist("h11", "0.16.0"),
    "websockets": _dist("websockets", "16.1.1"),
    "pytest": _dist("pytest", "9.1.1", ["pluggy<2", 'colorama>=0.4; sys_platform == "win32"']),
    "pluggy": _dist("pluggy", "1.7.0"),
}


def _lookup(name):
    return FAKE[upgrade_lock.canonical(name)]


def test_closure_follows_extras_and_windows_markers_only():
    runtime = upgrade_lock.dependency_closure([("exp-agent", ("runtime", "gui"))], _lookup)
    assert set(runtime) == {"exp-agent", "tqdm", "colorama", "numpy", "uvicorn", "h11", "websockets"}
    full = upgrade_lock.dependency_closure([("exp-agent", ("runtime", "gui", "dev"))], _lookup)
    assert set(full) - set(runtime) == {"pytest", "pluggy"}
    assert full["websockets"] == ("websockets", "16.1.1")


def test_render_lock_splits_sections_and_round_trips_through_parse():
    runtime = upgrade_lock.dependency_closure([("exp-agent", ("runtime", "gui"))], _lookup)
    full = upgrade_lock.dependency_closure([("exp-agent", ("runtime", "gui", "dev"))], _lookup)
    text = upgrade_lock.render_lock(runtime, full, "测试环境", "2026-11-12")
    head, dev = text.split(upgrade_lock.SECTION_DEV)
    assert "numpy==2.5.4" in head and "pytest==9.1.1" not in head
    assert "pytest==9.1.1" in dev and "pluggy==1.7.0" in dev and "numpy" not in dev
    assert "exp-agent" not in text
    pins = upgrade_lock.parse_pins(text)
    assert set(pins) == set(full) - {"exp-agent"}


def test_diff_and_hardware_sdk_detection():
    old = upgrade_lock.parse_pins("numpy==2.5.4\nzhinst-core==26.7.2.5\nsix==1.17.0\n")
    new = upgrade_lock.parse_pins("numpy==2.6.0\nzhinst-core==26.10.0\nidna==3.20\n")
    diff = upgrade_lock.diff_pins(old, new)
    assert diff == {"changed": ["numpy: 2.5.4 -> 2.6.0", "zhinst-core: 26.7.2.5 -> 26.10.0"],
                    "added": ["idna==3.20"], "removed": ["six==1.17.0"]}
    assert upgrade_lock.hardware_sdk_changes(diff) == ["zhinst-core: 26.7.2.5 -> 26.10.0"]


def test_sync_development_updates_only_target_python_exact_pins():
    text = ("# 注释保留\n"
            "numpy==2.5.4; python_version >= '3.12'\n"
            "numpy==2.4.4; python_version == '3.11'\n"
            "numpy>=1.26,<2.3; python_version < '3.11'\n"
            "pytest==9.1.1\n"
            "pyserial==3.5\n")
    pins = upgrade_lock.parse_pins("numpy==2.6.0\npytest==9.2.0\npyserial==3.5\n")
    assert upgrade_lock.sync_development(text, pins) == (
        "# 注释保留\n"
        "numpy==2.6.0; python_version >= '3.12'\n"
        "numpy==2.4.4; python_version == '3.11'\n"
        "numpy>=1.26,<2.3; python_version < '3.11'\n"
        "pytest==9.2.0\n"
        "pyserial==3.5\n")


@pytest.mark.parametrize("env", [
    upgrade_lock.ROOT / "agent_exp_env",
    upgrade_lock.ROOT / ".cache" / "dev-env",
    upgrade_lock.WORK,
])
def test_prepare_refuses_environment_outside_work_dir(monkeypatch, env):
    def forbidden(*args, **kwargs):
        raise AssertionError("越界时不得执行任何命令")

    monkeypatch.setattr(upgrade_lock.subprocess, "run", forbidden)
    monkeypatch.setattr(upgrade_lock.shutil, "rmtree", forbidden)
    args = argparse.Namespace(env=str(env), python="python", packages=[])
    with pytest.raises(SystemExit, match="upgrade-lock"):
        upgrade_lock.prepare_command(args)


def test_current_lock_and_development_are_already_in_sync():
    pins = upgrade_lock.parse_pins(upgrade_lock.LOCK.read_text(encoding="utf-8"))
    text = upgrade_lock.DEVELOPMENT.read_text(encoding="utf-8")
    assert upgrade_lock.sync_development(text, pins) == text
