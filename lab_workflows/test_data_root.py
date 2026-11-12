"""数据根解析：运行目录与其他根为 <根>/data；开发目录本身只认 LAB_DATA_ROOT。不连接硬件。"""

from pathlib import Path
from unittest.mock import patch

import pytest

import hardware_gate
from lab_workflows.common import DATA_ROOT_ENV, DataRootNotConfigured, data_root

CHECKOUT = hardware_gate.runtime_root()
DEV = patch("hardware_gate.denial_reason", return_value="开发")
LAB = patch("hardware_gate.denial_reason", return_value=None)


def test_other_root_uses_its_own_data_directory(tmp_path, monkeypatch):
    monkeypatch.setenv(DATA_ROOT_ENV, str(tmp_path / "elsewhere"))
    with DEV:
        assert data_root(tmp_path) == tmp_path / "data"


def test_lab_checkout_ignores_environment(monkeypatch):
    monkeypatch.setenv(DATA_ROOT_ENV, r"D:\elsewhere")
    with LAB:
        assert data_root(CHECKOUT) == CHECKOUT / "data"


def test_dev_checkout_uses_configured_root(tmp_path, monkeypatch):
    monkeypatch.setenv(DATA_ROOT_ENV, str(tmp_path))
    with DEV:
        assert data_root(CHECKOUT) == tmp_path


def test_dev_checkout_without_configuration_refuses(monkeypatch):
    monkeypatch.delenv(DATA_ROOT_ENV, raising=False)
    with DEV, pytest.raises(DataRootNotConfigured, match="未配置数据根"):
        data_root(CHECKOUT)
    assert not (CHECKOUT / "data").exists()


def test_no_project_code_builds_data_path_by_hand():
    # 除 data_root() 外不再出现 <根> / "data" 拼接；新代码应调用 data_root()。
    offenders = []
    for folder in ("lab_workflows", "GUI/backend"):
        for path in (CHECKOUT / folder).rglob("*.py"):
            if path.name.startswith("test_") or path.name == "common.py":
                continue
            text = path.read_text(encoding="utf-8")
            if '/ "data"' in text or '/"data' in text or '/ "data/' in text:
                offenders.append(path.relative_to(CHECKOUT).as_posix())
    assert offenders == []
