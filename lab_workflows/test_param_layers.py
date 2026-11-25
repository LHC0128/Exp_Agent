"""默认参数分层：基线 + 本机覆盖的加载、保存、提升与删除。"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import yaml

from lab_workflows.experiment_params import ExperimentParams, parameter
from lab_workflows.experiments.legacy import LegacyScriptAdapter
from lab_workflows.experiments.param_layers import ParamLayers, override_path
from lab_workflows.experiments.typed import TypedWorkflowAdapter


@dataclasses.dataclass
class ProbeParams(ExperimentParams):
    amplitude: float = parameter(
        default=1.0, external_name="amplitude", label="幅度", unit="V", minimum=0.0, maximum=10.0
    )
    repeats: int = parameter(
        default=3, external_name="repeats", label="重复次数", minimum=1, maximum=5
    )


def _adapter(tmp_path: Path) -> TypedWorkflowAdapter:
    adapter = TypedWorkflowAdapter("probe", "probe", ProbeParams, "probe.workflow")
    adapter.defaults_path = tmp_path / "probe.yaml"
    return adapter


def _write_baseline(adapter: TypedWorkflowAdapter, **values: object) -> None:
    ProbeParams(**values).save_yaml(adapter.defaults_path)


def test_missing_files_keep_model_defaults(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)

    params = adapter.defaults()

    assert (params.amplitude, params.repeats) == (1.0, 3)
    assert adapter.param_state() == {"overrides": {}}


def test_override_merges_over_baseline_and_stores_only_delta(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_baseline(adapter, amplitude=2.5, repeats=4)

    adapter.save_overrides({"amplitude": 7.0})

    params = adapter.defaults()
    assert (params.amplitude, params.repeats) == (7.0, 4)
    payload = yaml.safe_load(override_path(adapter.defaults_path).read_text(encoding="utf-8"))
    assert payload["parameters"] == {"amplitude": 7.0}
    assert adapter.param_state() == {"overrides": {"amplitude": 7.0}}


def test_saving_overrides_does_not_touch_baseline(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_baseline(adapter, amplitude=2.5)
    before = adapter.defaults_path.read_bytes()

    adapter.save_overrides({"amplitude": 5.0})

    assert adapter.defaults_path.read_bytes() == before


def test_override_equal_to_baseline_is_removed(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_baseline(adapter, amplitude=2.5)

    adapter.save_overrides({"amplitude": 2.5})

    assert not override_path(adapter.defaults_path).exists()
    assert adapter.param_state() == {"overrides": {}}


def test_save_overrides_rejects_out_of_range_values(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_baseline(adapter)

    with pytest.raises(ValueError):
        adapter.save_overrides({"amplitude": 99.0})

    assert not override_path(adapter.defaults_path).exists()


def test_unknown_override_key_is_rejected(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_baseline(adapter)
    override_path(adapter.defaults_path).write_text(
        yaml.safe_dump({"schema_version": 1, "parameters": {"bogus": 1}}), encoding="utf-8"
    )

    with pytest.raises(ValueError):
        adapter.defaults()


def test_promote_moves_override_into_baseline(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_baseline(adapter, amplitude=2.5)
    adapter.save_overrides({"amplitude": 8.0, "repeats": 2})

    adapter.promote_defaults()

    assert not override_path(adapter.defaults_path).exists()
    payload = yaml.safe_load(adapter.defaults_path.read_text(encoding="utf-8"))
    assert payload["parameters"] == {"amplitude": 8.0, "repeats": 2}
    assert adapter.defaults().amplitude == 8.0


def test_discard_restores_baseline_values(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_baseline(adapter, amplitude=2.5)
    adapter.save_overrides({"amplitude": 8.0})

    ParamLayers(adapter.defaults_path).discard()

    assert adapter.defaults().amplitude == 2.5
    assert adapter.param_state() == {"overrides": {}}


class _ProbeLegacy(LegacyScriptAdapter):
    """兼容适配器的探针：基线来自脚本体字面量，预检不连接任何设备。"""

    def __init__(self, tmp_path: Path) -> None:
        super().__init__("probe-legacy", "probe_legacy.py", "probe_legacy")
        self.script = tmp_path / "probe_legacy.py"
        self.script.write_text("AMPLITUDE = 1.0\nN_AVG = 3\n", encoding="utf-8")
        self.defaults_path = tmp_path / "probe-legacy.yaml"

    def preflight(self, values: dict[str, object]) -> list[str]:
        return []


def test_legacy_defaults_come_from_script_literals(tmp_path: Path) -> None:
    adapter = _ProbeLegacy(tmp_path)

    assert adapter.defaults() == {"AMPLITUDE": 1.0, "N_AVG": 3}
    assert adapter.param_state() == {"overrides": {}}


def test_legacy_baseline_yaml_wins_over_script_literal(tmp_path: Path) -> None:
    adapter = _ProbeLegacy(tmp_path)
    adapter.defaults_path.write_text(
        yaml.safe_dump({"schema_version": 1, "parameters": {"N_AVG": 9}}), encoding="utf-8"
    )

    assert adapter.defaults()["N_AVG"] == 9


def test_legacy_override_stores_only_delta_and_merges(tmp_path: Path) -> None:
    adapter = _ProbeLegacy(tmp_path)

    adapter.save_overrides({"AMPLITUDE": 2.5, "N_AVG": 3})

    payload = yaml.safe_load(override_path(adapter.defaults_path).read_text(encoding="utf-8"))
    assert payload["parameters"] == {"AMPLITUDE": 2.5}
    assert adapter.defaults() == {"AMPLITUDE": 2.5, "N_AVG": 3}
    assert adapter.param_state() == {"overrides": {"AMPLITUDE": 2.5}}


def test_legacy_promote_moves_override_into_baseline(tmp_path: Path) -> None:
    adapter = _ProbeLegacy(tmp_path)
    adapter.save_overrides({"AMPLITUDE": 4.0})

    adapter.promote_defaults()

    assert not override_path(adapter.defaults_path).exists()
    payload = yaml.safe_load(adapter.defaults_path.read_text(encoding="utf-8"))
    assert payload["parameters"]["AMPLITUDE"] == 4.0
    assert payload["parameters"]["N_AVG"] == 3
    assert adapter.defaults()["AMPLITUDE"] == 4.0
