"""公共安全校验的有限数值边界；不连接仪器。"""

from unittest.mock import Mock

import pytest
import yaml

from . import common


NONFINITE = [float("nan"), float("inf"), float("-inf")]

# 覆盖 YAML 1.1 隐式类型、锚点/合并键、显式标签等容易在解析器实现间出现差异的写法。
YAML_EDGE_CASES = """\
bools: [yes, No, on, OFF, true, False, y, n]
nulls: [~, null, Null, ""]
ints: [0, -17, 0o17, 017, 0x1F, 0b101, 1_000, +5, 190:20:30]
floats: [1.0, -0.5, 1e3, 1.0e-3, 6.02E+23, .inf, -.Inf, 1_0.5, 190:20:30.15]
strings: [1e-3, "007", '0x1F', 2026-11-11x, 中文, "\\u03bcT", text with: colon]
dates: [2026-11-11, 2026-11-11T08:30:00Z, 2026-11-11 08:30:00.5+08:00]
base: &base {min: -1.0, max: 2.0, unit: V}
derived: {<<: *base, max: 3.5}
alias_list: [*base, *base]
multiline: |
  第一行
  第二行
folded: >
  a
  b
binary: !!binary aGVsbG8=
pairs: !!omap [{a: 1}, {b: 2}]
set: !!set {x, y}
"""


def _safe_reference(text):
    return yaml.load(text, Loader=yaml.SafeLoader)


@pytest.mark.parametrize(
    "path",
    sorted((common.find_project_root() / "params").rglob("*.yaml")),
    ids=lambda path: path.relative_to(common.find_project_root()).as_posix(),
)
def test_load_yaml_matches_pure_python_safe_loader_on_project_params(path):
    # repr 同时比较值与类型（1 / 1.0 / True）以及映射顺序，比 == 更严格。
    expected = _safe_reference(path.read_text(encoding="utf-8")) or {}
    assert repr(common.load_yaml(path)) == repr(expected)


def test_load_yaml_matches_pure_python_safe_loader_on_yaml_edge_cases(tmp_path):
    path = tmp_path / "edge.yaml"
    path.write_text(YAML_EDGE_CASES, encoding="utf-8")
    assert repr(common.load_yaml(path)) == repr(_safe_reference(YAML_EDGE_CASES))


@pytest.mark.parametrize("text", ["", "# 仅注释\n", "~\n"])
def test_load_yaml_empty_documents_still_return_empty_mapping(tmp_path, text):
    path = tmp_path / "empty.yaml"
    path.write_text(text, encoding="utf-8")
    assert common.load_yaml(path) == {}


@pytest.mark.parametrize("text", ["!!python/object/apply:os.system ['echo unsafe']\n",
                                  "value: !!python/name:os.system\n"])
def test_load_yaml_keeps_safe_semantics_for_python_tags(tmp_path, text):
    path = tmp_path / "unsafe.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(yaml.constructor.ConstructorError):
        common.load_yaml(path)


def test_load_yaml_uses_libyaml_safe_loader_when_available(tmp_path, monkeypatch):
    used = []
    original = yaml.load

    def spy(stream, Loader):
        used.append(Loader)
        return original(stream, Loader=Loader)

    monkeypatch.setattr(common.yaml, "load", spy)
    path = tmp_path / "small.yaml"
    path.write_text("a: 1\n", encoding="utf-8")
    assert common.load_yaml(path) == {"a": 1}
    assert used == [getattr(yaml, "CSafeLoader", yaml.SafeLoader)]
    # 无 libyaml 的环境回退到纯 Python SafeLoader，语义不变。
    monkeypatch.delattr(common.yaml, "CSafeLoader", raising=False)
    used.clear()
    assert common.load_yaml(path) == {"a": 1}
    assert used == [yaml.SafeLoader]


@pytest.mark.parametrize("value", NONFINITE, ids=["nan", "pos_inf", "neg_inf"])
def test_nonfinite_rejected_before_rules_or_config_io(monkeypatch, value):
    reader = Mock(return_value={})
    monkeypatch.setattr(common, "load_safety_limits", reader)
    for limits in ({"rf_coil": {"min": 0.0, "max": 2.0}},
                   {"rf_coil": {"min": None, "max": None}}, {"other": {"max": 2.0}}, None):
        with pytest.raises(ValueError, match="rf_coil.*有限"):
            common.validate_safety_limit("rf_coil", value, limits)
    reader.assert_not_called()


def test_finite_values_keep_existing_bounds_and_return_types():
    limits = {"rf_coil": {"min": 0.0, "max": 2.0}}
    for value in (0, 0.25, 2.0, True):
        result = common.validate_safety_limit("rf_coil", value, limits)
        assert result is value and type(result) is type(value)
    for value in (-0.01, 2.01):
        with pytest.raises(ValueError, match="安全拦截"):
            common.validate_safety_limit("rf_coil", value, limits)
    # 本轮只补非有限值检查，不改变缺规则或无上下限的有限值透传策略。
    for limits in ({"other": {"max": 2.0}}, {"unbounded": {"min": None, "max": None}}):
        value = -123.5
        assert common.validate_safety_limit("unbounded", value, limits) is value


def test_output_load_markers_still_use_their_own_normalizer():
    from signal_generator.types import normalize_output_load

    assert normalize_output_load("INF") == "INF"
    assert normalize_output_load(9.9e37) == "INF"


@pytest.mark.parametrize("value", NONFINITE, ids=["nan", "pos_inf", "neg_inf"])
def test_original_y_rf_boundary_blocks_nonfinite_before_any_output(monkeypatch, value):
    from .experiment_modules.mx_z_optimal_control_rf_sensitivity import excitation

    monkeypatch.setattr(common, "load_safety_limits", lambda: {
        "Y_magnetic_field": {"min": -1.0, "max": 1.0}, "rf_coil": {"min": 0.0, "max": 2.0}})
    for field in ("amplitude_vpp", "offset_v"):
        device = Mock()
        values = dict(amplitude_vpp=0.2, offset_v=0.0, phase_deg=0.0)
        values[field] = value
        with pytest.raises(ValueError, match="有限"):
            excitation._rearm_y_rf(device, 2, **values)
        assert device.method_calls == []
