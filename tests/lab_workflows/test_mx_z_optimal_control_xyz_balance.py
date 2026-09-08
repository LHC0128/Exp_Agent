"""Mx Z 最优控制 XYZ 平衡场实验测试。"""

from __future__ import annotations

from pathlib import Path
import shutil
from types import SimpleNamespace
from uuid import uuid4

import matplotlib
import numpy as np
import pytest
import yaml

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from lab_workflows.experiment_modules.mx_z_optimal_control_xyz_balance import (
    analysis as analysis_module,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_xyz_balance import (
    ADAPTER,
    DEFINITION,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_xyz_balance.analysis import (
    analyze,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_xyz_balance.models import (
    MxZOptimalControlXYZBalanceParams,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_xyz_balance.scan import (
    build_xyz_axes,
    first_acquired_minimum,
    iter_serpentine_grid,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_xyz_balance.workflow import (
    _XYZFieldController,
    _restore_dg_channel,
    _snapshot_dg_channel,
)
from lab_workflows.experiments import get_experiment


@pytest.fixture
def local_tmp_path() -> Path:
    root = Path(__file__).resolve().parents[2] / "data"
    path = root / f".test_mx_z_xyz_balance_{uuid4().hex}"
    path.mkdir(mode=0o777)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _params(**overrides) -> MxZOptimalControlXYZBalanceParams:
    values = {
        "x_field_start_v": -0.01,
        "x_field_stop_v": 0.01,
        "x_field_points": 2,
        "y_field_start_v": -0.02,
        "y_field_stop_v": 0.02,
        "y_field_points": 2,
        "z_field_start_ma": -0.03,
        "z_field_stop_ma": 0.03,
        "z_field_points": 2,
    }
    values.update(overrides)
    return MxZOptimalControlXYZBalanceParams(**values)


class _FakeDG:
    """模拟使用 OFFSET 命令设置 DC 电平的 DG4162。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.shape = {1: "SINusoid", 2: "DC"}
        self.frequency = {1: 1234.0, 2: 0.0}
        self.amplitude = {1: 0.4, 2: 0.0}
        self.offset = {1: 0.05, 2: -0.02}
        self.phase = {1: 20.0, 2: 0.0}
        self.burst = {1: True, 2: False}
        self.burst_mode = {1: "INFinity", 2: "TRIGgered"}
        self.burst_ncycles = {1: 8.0, 2: 2.0}
        self.burst_phase = {1: 30.0, 2: 0.0}
        self.burst_period = {1: 0.02, 2: 0.03}
        self.burst_delay = {1: 0.004, 2: 0.0}
        self.trigger_source = {1: "EXTernal", 2: "INTernal"}
        self.trigger_slope = {1: "POSitive", 2: "POSitive"}
        self.mod = {1: False, 2: True}
        self.output = {1: True, 2: False}

    def _record(self, name: str, channel: int, value) -> None:
        self.calls.append((name, channel, value))

    def get_shape(self, *, channel: int) -> str:
        return self.shape[channel]

    def set_shape(self, value: str, *, channel: int) -> None:
        self.shape[channel] = value
        self._record("shape", channel, value)

    def get_frequency(self, *, channel: int) -> float:
        return self.frequency[channel]

    def set_frequency(self, value: float, *, channel: int) -> None:
        self.frequency[channel] = value

    def get_amplitude(self, *, channel: int) -> float:
        return self.amplitude[channel]

    def set_amplitude(self, value: float, *, channel: int) -> None:
        self.amplitude[channel] = value

    def get_offset(self, *, channel: int) -> float:
        return self.offset[channel]

    def set_offset(self, value: float, *, channel: int) -> None:
        self.offset[channel] = value
        self._record("offset", channel, value)

    def get_dc_voltage(self, *, channel: int) -> float:
        return self.offset[channel]

    def set_dc_voltage(self, value: float, *, channel: int) -> None:
        self.set_offset(value, channel=channel)
        self._record("dc_voltage", channel, value)

    def get_phase_adjust(self, *, channel: int) -> float:
        return self.phase[channel]

    def set_phase_adjust(self, value: float, *, channel: int) -> None:
        self.phase[channel] = value

    def get_burst_state(self, *, channel: int) -> bool:
        return self.burst[channel]

    def set_burst_state(self, value: bool, *, channel: int) -> None:
        self.burst[channel] = value

    def get_burst_mode(self, *, channel: int) -> str:
        return self.burst_mode[channel]

    def set_burst_mode(self, value: str, *, channel: int) -> None:
        self.burst_mode[channel] = value

    def get_burst_ncycles(self, *, channel: int) -> float:
        return self.burst_ncycles[channel]

    def set_burst_ncycles(self, value: float, *, channel: int) -> None:
        self.burst_ncycles[channel] = value

    def get_burst_phase(self, *, channel: int) -> float:
        return self.burst_phase[channel]

    def set_burst_phase(self, value: float, *, channel: int) -> None:
        self.burst_phase[channel] = value

    def get_burst_period(self, *, channel: int) -> float:
        return self.burst_period[channel]

    def set_burst_period(self, value: float, *, channel: int) -> None:
        self.burst_period[channel] = value

    def get_burst_delay(self, *, channel: int) -> float:
        return self.burst_delay[channel]

    def set_burst_delay(self, value: float, *, channel: int) -> None:
        self.burst_delay[channel] = value

    def get_burst_trigger_source(self, *, channel: int) -> str:
        return self.trigger_source[channel]

    def set_burst_trigger_source(self, value: str, *, channel: int) -> None:
        self.trigger_source[channel] = value

    def get_burst_trigger_slope(self, *, channel: int) -> str:
        return self.trigger_slope[channel]

    def set_burst_trigger_slope(self, value: str, *, channel: int) -> None:
        self.trigger_slope[channel] = value

    def get_mod_state(self, *, channel: int) -> bool:
        return self.mod[channel]

    def set_mod_state(self, value: bool, *, channel: int) -> None:
        self.mod[channel] = value

    def get_output(self, *, channel: int) -> bool:
        return self.output[channel]

    def set_output(self, value: bool, *, channel: int) -> None:
        self.output[channel] = value
        self._record("output", channel, value)

    def setup_dc(self, value: float, *, channel: int) -> None:
        self.shape[channel] = "DC"
        self.set_dc_voltage(value, channel=channel)
        self.output[channel] = True
        self._record("dc", channel, value)


class _FakeGS200:
    def __init__(self) -> None:
        self.current_a = 0.0
        self.output_on = False
        self.calls: list[tuple] = []

    def set_current(self, value: float) -> None:
        self.current_a = value
        self.calls.append(("current", value))

    def set_output(self, value: bool) -> None:
        self.output_on = value
        self.calls.append(("output", value))


def test_definition_and_defaults_are_registered() -> None:
    definition = get_experiment(DEFINITION.id)
    assert definition.execution_mode == "typed_workflow"
    assert definition.data_type == "Mx_Z_Optimal_Control_XYZ_Balance"
    defaults = ADAPTER.defaults()
    assert defaults.x_field_points == 11
    assert defaults.y_field_points == 11
    assert defaults.z_field_points == 5
    assert defaults.demod_frequency_hz == pytest.approx(30000.0)


def test_single_point_axis_requires_equal_start_and_stop() -> None:
    params = _params(
        x_field_start_v=0.004,
        x_field_stop_v=0.004,
        x_field_points=1,
    )
    x_axis, _, _ = build_xyz_axes(params)
    assert x_axis.tolist() == [0.004]
    errors = _params(
        x_field_start_v=0.004,
        x_field_stop_v=0.005,
        x_field_points=1,
    )._validate_axis("X", 0.004, 0.005, 1)
    assert errors == ["X 点数为 1 时必须满足 START=STOP"]
    with pytest.raises(ValueError, match="START=STOP"):
        build_xyz_axes(
            _params(
                x_field_start_v=0.004,
                x_field_stop_v=0.005,
                x_field_points=1,
            )
        )


def test_xyz_serpentine_grid_keeps_zyx_indices() -> None:
    points = list(iter_serpentine_grid(_params()))
    assert [(item[1], item[2], item[3]) for item in points] == [
        (0, 0, 0),
        (0, 0, 1),
        (0, 1, 1),
        (0, 1, 0),
        (1, 1, 0),
        (1, 1, 1),
        (1, 0, 1),
        (1, 0, 0),
    ]


def test_first_acquired_minimum_breaks_ties_by_acquisition_order() -> None:
    best = first_acquired_minimum(
        [
            {"r_mean_v": 1.0, "acquisition_index": 0},
            {"r_mean_v": 0.5, "acquisition_index": 4},
            {"r_mean_v": 0.5, "acquisition_index": 2},
        ]
    )
    assert best["acquisition_index"] == 2


def test_field_controller_keeps_zero_outputs_on_and_changes_z_by_plane() -> None:
    dg = _FakeDG()
    gs200 = _FakeGS200()
    controller = _XYZFieldController(
        dg,
        gs200,
        {"x_field": 1, "y_rf": 2},
    )
    controller.apply(0.0, 0.0, -0.01)
    controller.apply(0.001, -0.002, -0.01)
    assert dg.output[1] is True
    assert dg.output[2] is True
    assert ("dc", 1, 0.0) in dg.calls
    assert ("dc", 2, 0.0) in dg.calls
    assert gs200.calls.count(("current", -0.01 / 1000.0)) == 1
    assert gs200.output_on is True


def test_dg_channel_state_is_restored_after_dc_scan() -> None:
    dg = _FakeDG()
    state = _snapshot_dg_channel(dg, 1)
    controller = _XYZFieldController(
        dg,
        _FakeGS200(),
        {"x_field": 1, "y_rf": 2},
    )
    controller.apply(0.001, 0.002, 0.0)
    _restore_dg_channel(dg, 1, state)
    assert dg.shape[1] == "SINusoid"
    assert dg.frequency[1] == pytest.approx(1234.0)
    assert dg.amplitude[1] == pytest.approx(0.4)
    assert dg.offset[1] == pytest.approx(0.05)
    assert dg.phase[1] == pytest.approx(20.0)
    assert dg.burst[1] is True
    assert dg.burst_ncycles[1] == pytest.approx(8.0)
    assert dg.burst_period[1] == pytest.approx(0.02)
    assert dg.burst_delay[1] == pytest.approx(0.004)
    assert dg.output[1] is True


def test_dc_snapshot_uses_model_specific_dc_query() -> None:
    dg = _FakeDG()
    assert dg.get_dc_voltage(channel=2) == pytest.approx(-0.02)
    state = _snapshot_dg_channel(dg, 2)
    assert state.shape == "DC"
    assert state.offset_v == pytest.approx(-0.02)
    assert state.frequency_hz is None
    assert state.amplitude_vpp is None
    assert state.output_on is False


def test_dc_restore_uses_model_specific_dc_setter() -> None:
    dg = _FakeDG()
    state = _snapshot_dg_channel(dg, 2)
    assert state.offset_v == pytest.approx(-0.02)

    # 扫描把通道改为别的 DC 电平。
    controller = _XYZFieldController(
        dg,
        _FakeGS200(),
        {"x_field": 1, "y_rf": 2},
    )
    controller.apply(0.0, 0.123, 0.0)
    assert dg.get_dc_voltage(channel=2) == pytest.approx(0.123)

    _restore_dg_channel(dg, 2, state)

    # 恢复型号专用 DC 设置值和原输出状态。
    assert dg.shape[2] == "DC"
    assert dg.get_dc_voltage(channel=2) == pytest.approx(-0.02)
    dc_writes = [
        value
        for name, channel, value in dg.calls
        if name == "dc_voltage" and channel == 2
    ]
    assert dc_writes
    assert any(value == pytest.approx(-0.02) for value in dc_writes)
    # 输出状态是恢复序列的最后一步。
    assert dg.calls[-1] == ("output", 2, False)
    assert dg.output[2] is False


def test_analysis_reports_measured_minimum_and_writes_outputs(
    local_tmp_path: Path,
) -> None:
    run_dir = local_tmp_path / "run"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "completion_status": "completed",
                "control_source": {"version": "v1"},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    x_axis = np.array([-0.01, 0.0, 0.01])
    y_axis = np.array([-0.01, 0.0, 0.01])
    z_axis = np.array([-0.01, 0.0, 0.01])
    z_grid, x_grid, y_grid = np.meshgrid(
        z_axis,
        x_axis,
        y_axis,
        indexing="ij",
    )
    r_mean = (
        (x_grid - 0.0) ** 2
        + (y_grid - 0.01) ** 2
        + (z_grid + 0.01) ** 2
    )
    np.savez(
        raw_dir / "xyz_balance_scan.npz",
        x_field_v=x_axis,
        y_field_v=y_axis,
        z_field_ma=z_axis,
        r_mean_v=r_mean,
        r_std_v=np.full(r_mean.shape, 0.001),
        acquisition_order=np.arange(r_mean.size).reshape(r_mean.shape),
        actual_rate_sa_s=np.float64(1000.0),
    )
    result = analyze(run_dir)
    best = result["measured_grid_minimum"]
    assert best["x_field_v"] == pytest.approx(0.0)
    assert best["y_field_v"] == pytest.approx(0.01)
    assert best["z_field_ma"] == pytest.approx(-0.01)
    assert best["remeasured"] is False
    assert (run_dir / "results" / "analysis.yaml").is_file()
    assert (run_dir / "results" / "balance_results.npz").is_file()
    assert (run_dir / "results" / "xyz_balance_grid.csv").is_file()
    assert (run_dir / "results" / "xyz_balance_slices.png").is_file()


def test_analysis_plot_contains_one_xy_plane_for_each_z_current(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = {
        "x_field_v": np.array([-0.01, 0.01]),
        "y_field_v": np.array([-0.02, 0.02]),
        "z_field_ma": np.array([-0.03, 0.0, 0.03]),
        "r_mean_v": np.arange(12, dtype=float).reshape(3, 2, 2),
    }
    captured: dict[str, object] = {}

    def capture_figure(fig, path, **_kwargs):
        captured["figure"] = fig
        return Path(path)

    monkeypatch.setattr(analysis_module, "save_figure", capture_figure)
    filename = analysis_module._plot_xy_planes(
        local_tmp_path,
        data,
        (1, 0, 1),
    )
    figure = captured["figure"]
    try:
        plane_axes = [axis for axis in figure.axes if axis.get_title()]
        assert filename == "xyz_balance_slices.png"
        assert [axis.get_title() for axis in plane_axes] == [
            "Z current = -0.03 mA",
            "Z current = +0 mA",
            "Z current = +0.03 mA",
        ]
        assert all(axis.get_xlabel() == "X field setting (V)" for axis in plane_axes)
        assert all(axis.get_ylabel() == "Y field setting (V)" for axis in plane_axes)
        marked_axes = [
            axis
            for axis in plane_axes
            if any(line.get_label() == "Measured minimum" for line in axis.lines)
        ]
        assert marked_axes == [plane_axes[1]]
    finally:
        plt.close(figure)


def test_analysis_supports_all_axes_fixed(local_tmp_path: Path) -> None:
    run_dir = local_tmp_path / "fixed"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    (run_dir / "experiment_config.yaml").write_text(
        "completion_status: completed\n",
        encoding="utf-8",
    )
    np.savez(
        raw_dir / "xyz_balance_scan.npz",
        x_field_v=np.array([0.001]),
        y_field_v=np.array([-0.002]),
        z_field_ma=np.array([0.003]),
        r_mean_v=np.array([[[0.4]]]),
        r_std_v=np.array([[[0.01]]]),
        acquisition_order=np.array([[[0]]]),
        actual_rate_sa_s=np.float64(1000.0),
    )
    result = analyze(run_dir)
    assert result["scan_shape_zyx"] == [1, 1, 1]
    assert result["measured_grid_minimum"]["r_mean_v"] == pytest.approx(0.4)


def _write_vshape_run(
    run_dir: Path,
    *,
    x0_offset: float = 0.0,
    inject_outliers: bool = False,
) -> None:
    """写入 V 形响应数据：零点随 z 线性漂移，可注入孤立异常点。"""
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "completion_status": "completed",
                "control_source": {"version": "v1"},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    x_axis = np.linspace(-0.03, 0.03, 13)
    y_axis = np.linspace(-0.03, 0.03, 13)
    z_axis = np.array([-0.02, -0.01, 0.0, 0.01, 0.02])
    z_grid, x_grid, y_grid = np.meshgrid(z_axis, x_axis, y_axis, indexing="ij")
    # 零点随 z 线性漂移：x0 = -z + x0_offset, y0 = 0.5 z + 0.01
    x0 = -z_grid + x0_offset
    y0 = 0.5 * z_grid + 0.01
    r_mean = (
        np.abs(2.0 * (x_grid - x0))
        + np.abs(1.5 * (y_grid - y0))
        + 0.02 * np.abs(z_grid - 0.01)
        + 0.002
    )
    if inject_outliers:
        # 注入在高值角落，邻域中位数偏差 >0.15 V 可被默认阈值标记；
        # 同时会被 clean 网格排除，不影响各层最小点判定。
        r_mean[0, 0, 0] = 0.5
        r_mean[4, 12, 12] = 0.4
    np.savez(
        raw_dir / "xyz_balance_scan.npz",
        x_field_v=x_axis,
        y_field_v=y_axis,
        z_field_ma=z_axis,
        r_mean_v=r_mean,
        r_std_v=np.full(r_mean.shape, 0.0005),
        acquisition_order=np.arange(r_mean.size).reshape(r_mean.shape),
        actual_rate_sa_s=np.float64(1000.0),
    )


def test_analysis_linear_fit_recovers_workpoint_and_coupling(
    local_tmp_path: Path,
) -> None:
    run_dir = local_tmp_path / "vshape"
    _write_vshape_run(run_dir)
    result = analyze(run_dir)
    linear = result["linear_fit"]
    workpoint = linear["fitted_workpoint"]
    # e 最小层 z=+0.01，零点 x0=-0.01, y0=+0.015
    assert workpoint["z_field_ma"] == pytest.approx(0.01, abs=0.001)
    assert workpoint["x_field_v"] == pytest.approx(-0.01, abs=0.002)
    assert workpoint["y_field_v"] == pytest.approx(0.015, abs=0.002)
    assert workpoint["extrapolated"] is False
    assert workpoint["x_from_fit"] is True
    assert workpoint["y_from_fit"] is True
    assert linear["coupling"]["x0_slope_v_per_ma"] == pytest.approx(-1.0, abs=0.05)
    assert linear["coupling"]["y0_slope_v_per_ma"] == pytest.approx(0.5, abs=0.05)
    assert linear["next_scan_suggestion"] is None
    assert result["interpretation"]["continuous_fit_performed"] is True
    assert (run_dir / "results" / "balance_linear_fit.npz").is_file()
    assert (run_dir / "results" / "xyz_balance_linear_fit.png").is_file()
    saved = yaml.safe_load(
        (run_dir / "results" / "analysis.yaml").read_text(encoding="utf-8")
    )
    assert saved["linear_fit"]["fitted_workpoint"]["x_field_v"] == pytest.approx(
        -0.01, abs=0.002
    )


def test_analysis_linear_fit_marks_outliers_and_stays_robust(
    local_tmp_path: Path,
) -> None:
    run_dir = local_tmp_path / "outliers"
    _write_vshape_run(run_dir, inject_outliers=True)
    result = analyze(run_dir)
    linear = result["linear_fit"]
    assert linear["outlier_detection"]["count"] == 2
    workpoint = linear["fitted_workpoint"]
    assert workpoint["x_field_v"] == pytest.approx(-0.01, abs=0.002)
    assert workpoint["y_field_v"] == pytest.approx(0.015, abs=0.002)
    assert any("邻域孤立点" in warning for warning in result["warnings"])


def test_analysis_linear_fit_suggests_rescan_when_zero_at_edge(
    local_tmp_path: Path,
) -> None:
    run_dir = local_tmp_path / "edge"
    # x0 平移 +0.06 V：所有层的 x 零点都在扫描窗口外
    _write_vshape_run(run_dir, x0_offset=0.06)
    result = analyze(run_dir)
    linear = result["linear_fit"]
    suggestion = linear["next_scan_suggestion"]
    assert suggestion is not None
    assert suggestion["suggested_center"]["x_field_v"] == pytest.approx(
        0.05, abs=0.01
    )
    # 所有层的谷都在窗口外时，z 选层被"离窗口距离"主导，只断言在扫描范围内
    assert -0.02 <= suggestion["suggested_center"]["z_field_ma"] <= 0.02


def _write_complex_run(
    run_dir: Path,
) -> None:
    """复线性 y 响应 + e(z) 抛物线（顶点在层间）的数据。"""
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "completion_status": "completed",
                "control_source": {"version": "v1"},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    x_axis = np.linspace(-0.03, 0.03, 13)
    y_axis = np.linspace(-0.03, 0.03, 13)
    z_axis = np.array([-0.02, -0.01, 0.0, 0.01, 0.02])
    z_grid, x_grid, y_grid = np.meshgrid(z_axis, x_axis, y_axis, indexing="ij")
    x0 = -z_grid + 0.004
    y0 = 0.5 * z_grid + 0.01
    # y 响应为复线性模：|Jy*(y-y0) + C|，C 与 Jy 有相位差
    jy = 1.5 + 0.9j
    c_val = 0.01 - 0.008j
    r_mean = (
        np.abs(2.0 * (x_grid - x0))
        + np.abs(jy * (y_grid - y0) + c_val)
        + 0.02 * (z_grid - 0.005) ** 2
        + 0.002
    )
    np.savez(
        raw_dir / "xyz_balance_scan.npz",
        x_field_v=x_axis,
        y_field_v=y_axis,
        z_field_ma=z_axis,
        r_mean_v=r_mean,
        r_std_v=np.full(r_mean.shape, 0.0005),
        acquisition_order=np.arange(r_mean.size).reshape(r_mean.shape),
        actual_rate_sa_s=np.float64(1000.0),
    )


def test_analysis_complex_mod_recovers_y_zero_and_quadratic_z_vertex(
    local_tmp_path: Path,
) -> None:
    run_dir = local_tmp_path / "complex"
    _write_complex_run(run_dir)
    result = analyze(run_dir)
    linear = result["linear_fit"]
    # z 层选择：e(z) 顶点在 z=+0.005（层间），应使用二次拟合顶点
    assert linear["z_layer_fit"]["method"] == "quadratic_vertex"
    assert linear["z_layer_fit"]["vertex_z_ma"] == pytest.approx(0.005, abs=0.002)
    # 工作点：z=+0.005 顶点，x0 ≈ 0.004-0.005=-0.001，y0 ≈ 0.5*0.005+0.01=0.0125
    wp = linear["fitted_workpoint"]
    assert wp["z_field_ma"] == pytest.approx(0.005, abs=0.002)
    # x0 取顶点最近层（z=0.01 层，x0=-0.006）或 z=0 层（x0=0.004）
    # y0 复线性拟合应恢复 |Jy*(y-y0)+C| 的顶点
    work_layer = next(
        layer
        for layer in linear["per_layer"]
        if abs(layer["z_field_ma"] - 0.01) < 1e-9
    )
    y_fit = work_layer["y_fit"]
    assert y_fit["mode"] == "complex_linear_mod"
    # 理论顶点：|Jy*(y-y0)+C| 的模平方最小点
    jy = 1.5 + 0.9j
    c_val = 0.01 - 0.008j
    y0_layer = 0.5 * 0.01 + 0.01
    c_prime = c_val - jy * y0_layer
    y_expected = -np.real(np.conj(jy) * c_prime) / abs(jy) ** 2
    assert y_fit["s0"] == pytest.approx(y_expected, abs=0.002)
