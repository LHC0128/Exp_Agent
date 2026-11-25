"""``tools.params`` 的分层查看、对比与提升行为。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tools import params


def _write(path: Path, values: dict, schema_version: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {"schema_version": schema_version, "parameters": values},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _read(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    _write(tmp_path / "params" / "experiments" / "demo.yaml", {"A": 10, "B": 0.5})
    _write(tmp_path / "params" / "experiments" / "demo.local.yaml", {"A": 12}, schema_version=2)
    return tmp_path


def test_list_reports_each_experiment_and_is_empty_without_overrides(root: Path, tmp_path: Path) -> None:
    lines = params.list_lines(root)
    assert "共 1 个实验有本机覆盖" in lines[0]
    assert "demo" in lines[1]
    assert "A" in lines[1]
    assert params.list_lines(tmp_path / "empty") == ["没有实验使用本机覆盖层。"]


def test_diff_shows_only_changed_keys_with_all_three_values(root: Path) -> None:
    text = "\n".join(params.diff_lines(root, "demo"))
    assert "本机覆盖（1 项）" in text
    assert "A" in text
    assert "12" in text
    assert " B " not in text  # 未改动的键不出现


def test_diff_without_override_says_so(root: Path) -> None:
    (root / "params" / "experiments" / "demo.local.yaml").unlink()
    assert params.diff_lines(root, "demo") == ["demo 没有本机覆盖层。"]


def test_diff_accepts_missing_baseline_for_legacy_scripts(root: Path) -> None:
    (root / "params" / "experiments" / "demo.yaml").unlink()
    text = "\n".join(params.diff_lines(root, "demo"))
    assert "基线文件不存在" in text


def test_promote_merges_into_baseline_and_removes_override(root: Path) -> None:
    directory = root / "params" / "experiments"
    lines = params.promote(root, "demo")
    assert "已把 1 项并入基线" in lines[0]
    assert not (directory / "demo.local.yaml").exists()
    payload = _read(directory / "demo.yaml")
    assert payload["schema_version"] == 2
    assert payload["parameters"] == {"A": 12, "B": 0.5}


def test_promote_dry_run_writes_nothing(root: Path) -> None:
    directory = root / "params" / "experiments"
    lines = params.promote(root, "demo", dry_run=True)
    assert "将把 1 项并入" in lines[0]
    assert (directory / "demo.local.yaml").exists()
    assert _read(directory / "demo.yaml")["parameters"]["A"] == 10


def test_promote_rejects_values_failing_validation(root: Path) -> None:
    def validator(_values: dict) -> list[str]:
        return ["A 超出范围"]

    with pytest.raises(ValueError, match="A 超出范围"):
        params.promote(root, "demo", validator=validator)
    assert (root / "params" / "experiments" / "demo.local.yaml").exists()


def test_discard_removes_override(root: Path) -> None:
    lines = params.discard(root, "demo")
    assert "恢复为基线默认值" in lines[0]
    assert not (root / "params" / "experiments" / "demo.local.yaml").exists()


def test_missing_override_is_reported(root: Path) -> None:
    (root / "params" / "experiments" / "demo.local.yaml").unlink()
    with pytest.raises(FileNotFoundError):
        params.promote(root, "demo")
    with pytest.raises(FileNotFoundError):
        params.discard(root, "demo")


def test_main_promotes_unregistered_experiment_with_force(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(params, "find_project_root", lambda: root)
    assert params.main(["promote", "demo", "--force"]) == 0
    assert "已把 1 项并入基线" in capsys.readouterr().out
    assert not (root / "params" / "experiments" / "demo.local.yaml").exists()


def test_main_reports_unknown_experiment(root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _write(root / "params" / "experiments" / "not-registered.local.yaml", {"A": 3})
    monkeypatch.setattr(params, "find_project_root", lambda: root)
    assert params.main(["promote", "not-registered"]) == 1
    assert "未注册的实验" in capsys.readouterr().err
