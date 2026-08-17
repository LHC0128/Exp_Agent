"""Mx Keithley 6221 最优控制 XYZ 平衡场采集工作流。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from gs200 import GS200Instrument
from keithley_6221 import Keithley6221Instrument
from lockin_amplifier import HF2Instrument
from tec_controller import TECInstrument

from ...common import find_project_root, load_mapping, validate_safety_limit
from ...devices import create_signal_generator
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import (
    DeviceSession,
    DisconnectTarget,
    SafetyShutdownReport,
    ShutdownAction,
    STANDARD_PRESERVED_OUTPUTS,
    TemperatureSwitchRestore,
    connect_signal_generator_routes,
    configure_temperature_control,
    create_run_directory,
    run_safety_shutdown,
    set_temperature_switch,
    synchronize_connected_clocks,
    wait_for_temperature_stable,
)
from ...steps.keithley_6221 import (
    Keithley6221CurrentRangeCheck,
    configure_keithley_6221_optimal_control,
    require_keithley_6221_current_range,
)
from ...steps.main_field import (
    restore_main_field_state,
    snapshot_main_field_state,
)
from ..mx_keithley_6221_optimal_control_rf_sensitivity.sources import (
    AppliedCurrentWaveform,
    KeithleyCalibrationSource,
    TheoryControlSource,
    build_applied_current,
    load_keithley_calibration,
    load_theory_control,
)
from ..mx_keithley_6221_optimal_control_rf_sensitivity.workflow import (
    _configure_hf2,
    _configure_trigger,
    _install_compliance_checker,
)
from ..mx_y_rf_sensitivity.workflow import (
    _acquire_valid_r_point,
    _set_pump_gate_on,
)
from .models import MxKeithley6221OptimalControlXYZBalanceParams
from .scan import build_xyz_axes, first_acquired_minimum, iter_serpentine_grid


EXPERIMENT_ID = "mx-keithley-6221-optimal-control-xyz-balance"
DATA_TYPE = "Mx_Keithley_6221_Optimal_Control_XYZ_Balance"
EXECUTION_MODE = "typed_workflow"


class _RuntimeCancellation:
    @staticmethod
    def raise_if_cancelled() -> None:
        check_cancelled()


@dataclass(frozen=True, slots=True)
class _DGChannelState:
    shape: str
    frequency_hz: float | None
    amplitude_vpp: float | None
    offset_v: float
    phase_deg: float | None
    burst_state: bool
    burst_mode: str | None
    burst_ncycles: float | str | None
    burst_phase_deg: float | None
    burst_period_s: float | None
    burst_delay_s: float | None
    burst_trigger_source: str | None
    burst_trigger_slope: str | None
    mod_state: bool
    output_on: bool


def _snapshot_dg_channel(device: Any, channel: int) -> _DGChannelState:
    """在改为 DC 前读取足以恢复通道工作模式的状态。"""
    shape = str(device.get_shape(channel=channel))
    is_dc = shape.strip().upper() == "DC"
    burst_state = bool(device.get_burst_state(channel=channel))
    return _DGChannelState(
        shape=shape,
        frequency_hz=(
            None if is_dc else float(device.get_frequency(channel=channel))
        ),
        amplitude_vpp=(
            None if is_dc else float(device.get_amplitude(channel=channel))
        ),
        offset_v=float(device.get_offset(channel=channel)),
        phase_deg=(
            None if is_dc else float(device.get_phase_adjust(channel=channel))
        ),
        burst_state=burst_state,
        burst_mode=(
            str(device.get_burst_mode(channel=channel))
            if burst_state
            else None
        ),
        burst_ncycles=(
            device.get_burst_ncycles(channel=channel)
            if burst_state
            else None
        ),
        burst_phase_deg=(
            float(device.get_burst_phase(channel=channel))
            if burst_state
            else None
        ),
        burst_period_s=(
            float(device.get_burst_period(channel=channel))
            if burst_state
            else None
        ),
        burst_delay_s=(
            float(device.get_burst_delay(channel=channel))
            if burst_state
            else None
        ),
        burst_trigger_source=(
            str(device.get_burst_trigger_source(channel=channel))
            if burst_state
            else None
        ),
        burst_trigger_slope=(
            str(device.get_burst_trigger_slope(channel=channel))
            if burst_state
            else None
        ),
        mod_state=bool(device.get_mod_state(channel=channel)),
        output_on=bool(device.get_output(channel=channel)),
    )


def _restore_dg_channel(
    device: Any,
    channel: int,
    state: _DGChannelState,
) -> None:
    """恢复扫描前的波形、Burst/调制开关和输出状态。"""
    device.set_output(False, channel=channel)
    device.set_burst_state(False, channel=channel)
    device.set_mod_state(False, channel=channel)
    device.set_shape(state.shape, channel=channel)
    if state.frequency_hz is not None:
        device.set_frequency(state.frequency_hz, channel=channel)
    if state.amplitude_vpp is not None:
        device.set_amplitude(state.amplitude_vpp, channel=channel)
    device.set_offset(state.offset_v, channel=channel)
    if state.phase_deg is not None:
        device.set_phase_adjust(state.phase_deg, channel=channel)
    if state.burst_state:
        assert state.burst_mode is not None
        assert state.burst_ncycles is not None
        assert state.burst_phase_deg is not None
        assert state.burst_period_s is not None
        assert state.burst_delay_s is not None
        assert state.burst_trigger_source is not None
        assert state.burst_trigger_slope is not None
        device.set_burst_mode(state.burst_mode, channel=channel)
        device.set_burst_ncycles(state.burst_ncycles, channel=channel)
        device.set_burst_phase(state.burst_phase_deg, channel=channel)
        device.set_burst_period(state.burst_period_s, channel=channel)
        device.set_burst_delay(state.burst_delay_s, channel=channel)
        device.set_burst_trigger_source(
            state.burst_trigger_source,
            channel=channel,
        )
        device.set_burst_trigger_slope(
            state.burst_trigger_slope,
            channel=channel,
        )
    device.set_burst_state(state.burst_state, channel=channel)
    device.set_mod_state(state.mod_state, channel=channel)
    device.set_output(state.output_on, channel=channel)


def _connect_devices(
    mapping: dict[str, dict[str, Any]],
    session: DeviceSession,
    devices: dict[str, Any] | None = None,
    channels: dict[str, int] | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    devices = {} if devices is None else devices
    channels = {} if channels is None else channels
    gs = mapping["main_magnetic_field"]
    devices["gs200"] = session.connect(
        "gs200",
        gs["resource"],
        lambda: GS200Instrument(gs["resource"]),
    )
    kcfg = mapping["keithley_6221_main_field"]
    devices["keithley"] = session.connect(
        "keithley",
        kcfg["resource"],
        lambda: Keithley6221Instrument(kcfg["resource"]),
    )
    trigger = mapping["Time_sequence_2"]
    devices["trigger"] = session.connect(
        "trigger",
        trigger["resource"],
        lambda: create_signal_generator(trigger),
    )
    channels["trigger"] = int(trigger["channel"])

    xcfg = mapping["X_magnetic_field"]
    ycfg = mapping["Y_magnetic_field"]
    devices["xy_field"], routed = connect_signal_generator_routes(
        session,
        "xy_field",
        {
            "x_field": ("X_magnetic_field", xcfg),
            "y_rf": ("Y_magnetic_field", ycfg),
        },
    )
    channels.update(routed)
    lcfg = mapping["Pump_laser_power"]
    pcfg = mapping["Probe_laser_power"]
    devices["laser"], routed = connect_signal_generator_routes(
        session,
        "laser",
        {
            "pump_laser": ("Pump_laser_power", lcfg),
            "probe_laser": ("Probe_laser_power", pcfg),
        },
    )
    channels.update(routed)
    ccfg = mapping["Pump_modulation"]
    gcfg = mapping["Time_sequence"]
    devices["pump_rf"], routed = connect_signal_generator_routes(
        session,
        "pump_rf",
        {
            "pump_carrier": ("Pump_modulation", ccfg),
            "pump_gate": ("Time_sequence", gcfg),
        },
    )
    channels.update(routed)
    tcfg = mapping["Temp_Switch"]
    devices["temp_switch"] = session.connect(
        "temp_switch",
        tcfg["resource"],
        lambda: create_signal_generator(tcfg),
    )
    channels["temp_switch"] = int(tcfg["channel"])
    hcfg = mapping["lockin_r"]
    devices["hf2"] = session.connect(
        "hf2",
        f"hf2://{hcfg.get('host', '127.0.0.1')}/{hcfg['device_id']}",
        lambda: HF2Instrument(
            host=hcfg.get("host", "127.0.0.1"),
            port=int(hcfg.get("port", 8005)),
            api_level=1,
            device_id=hcfg["device_id"],
        ),
    )
    tcfg = mapping["temperature"]
    devices["tec"] = session.connect_optional(
        "tec",
        tcfg["resource"],
        lambda: TECInstrument(port=tcfg["resource"]),
        device_label="TEC103",
    )
    return devices, channels


def _configure_clocks(
    devices: dict[str, Any],
    mapping: dict[str, dict[str, Any]],
) -> dict[str, dict[str, str]]:
    records = synchronize_connected_clocks(
        devices,
        mapping,
        {
            "trigger": "Time_sequence_2",
            "xy_field": "Y_magnetic_field",
            "laser": "Pump_laser_power",
            "pump_rf": "Pump_modulation",
            "temp_switch": "Temp_Switch",
            "hf2": "lockin_r",
        },
    )
    return {
        name: {"target": str(record["target"]), "actual": str(record["actual"])}
        for name, record in records.items()
    }


def _configure_xy_idle(
    devices: dict[str, Any],
    channels: dict[str, int],
) -> None:
    """启动最优控制前把 X/Y 安全置为 0 V 且关闭。"""
    xy = devices["xy_field"]
    for channel_name, safety_key in (
        ("x_field", "X_magnetic_field"),
        ("y_rf", "Y_magnetic_field"),
    ):
        validate_safety_limit(safety_key, 0.0)
        channel = channels[channel_name]
        xy.set_output(False, channel=channel)
        xy.set_burst_state(False, channel=channel)
        xy.set_mod_state(False, channel=channel)
        xy.setup_dc(0.0, channel=channel)
        xy.set_output(False, channel=channel)


class _XYZFieldController:
    """设置 X/Y DG4000 DC 与 GS200 Z 电流。"""

    def __init__(
        self,
        xy_device: Any,
        gs200: Any,
        channels: dict[str, int],
    ) -> None:
        self.xy_device = xy_device
        self.gs200 = gs200
        self.channels = channels
        self._last_z_ma: float | None = None

    def apply(self, x_v: float, y_v: float, z_ma: float) -> None:
        x_v = float(validate_safety_limit("X_magnetic_field", x_v))
        y_v = float(validate_safety_limit("Y_magnetic_field", y_v))
        z_ma = float(validate_safety_limit("main_magnetic_field", z_ma))
        if self._last_z_ma is None or z_ma != self._last_z_ma:
            self.gs200.set_current(z_ma / 1000.0)
            self.gs200.set_output(True)
            self._last_z_ma = z_ma
        for channel_name, value in (("x_field", x_v), ("y_rf", y_v)):
            channel = self.channels[channel_name]
            self.xy_device.set_burst_state(False, channel=channel)
            self.xy_device.set_mod_state(False, channel=channel)
            self.xy_device.setup_dc(value, channel=channel)
            # 扫描零点也保持 Output ON，避免零点引入输出状态跳变。
            self.xy_device.set_output(True, channel=channel)


def _source_snapshot(
    run_dir: Any,
    theory: TheoryControlSource,
    calibration: KeithleyCalibrationSource,
    applied: AppliedCurrentWaveform,
    current_range: Keithley6221CurrentRangeCheck,
) -> list[str]:
    """保存理论波形、标定分析和实际电流波形的快照。"""
    files: list[str] = []
    for source, name in (
        (theory.waveform_path, "source_optimal_control_waveform.csv"),
        (theory.parameter_path, "source_optimal_control_params.csv"),
        (calibration.analysis_path, "source_keithley_calibration_analysis.yaml"),
    ):
        target = run_dir.raw / name
        target.write_bytes(source.read_bytes())
        files.append(f"raw/{name}")
    np.savez(
        run_dir.raw / "applied_control_waveform.npz",
        time_s=theory.time_s,
        omega_ctrl_hz=theory.omega_ctrl_hz,
        current_ma=applied.current_ma,
        normalized=applied.normalized,
        repeat_frequency_hz=np.float64(theory.repeat_frequency_hz),
        theory_rf_frequency_hz=np.float64(theory.theory_rf_frequency_hz),
        rf_periods_per_waveform=np.int64(theory.rf_periods_per_waveform),
        calibration_slope_hz_per_ma=np.float64(calibration.slope_hz_per_ma),
        calibration_intercept_hz=np.float64(calibration.intercept_hz),
        current_minimum_ma=np.float64(applied.minimum_ma),
        current_maximum_ma=np.float64(applied.maximum_ma),
        current_amplitude_peak_ma=np.float64(applied.amplitude_peak_ma),
        current_offset_ma=np.float64(applied.offset_ma),
        selected_current_range_ma=np.float64(current_range.selected_range_ma),
        required_current_range_ma=np.float64(current_range.required_peak_ma),
        current_range_margin_ma=np.float64(current_range.margin_ma),
        current_range_utilization_fraction=np.float64(
            current_range.utilization_fraction
        ),
        current_range_satisfied=np.bool_(current_range.satisfies),
    )
    files.append("raw/applied_control_waveform.npz")
    return files


def _save_scan_aggregate(
    run_dir: Any,
    params: MxKeithley6221OptimalControlXYZBalanceParams,
    records: list[dict[str, Any]],
    actual_rate: float,
    best: dict[str, Any],
) -> None:
    x_axis, y_axis, z_axis = build_xyz_axes(params)
    shape = (z_axis.size, x_axis.size, y_axis.size)
    r_mean = np.full(shape, np.nan, dtype=float)
    r_scalar_mean = np.full(shape, np.nan, dtype=float)
    r_std = np.full(shape, np.nan, dtype=float)
    accepted_attempt = np.full(shape, -1, dtype=int)
    acquisition_order = np.full(shape, -1, dtype=int)
    accepted_file = np.full(shape, "", dtype="<U160")
    for record in records:
        index = (
            int(record["z_index"]),
            int(record["x_index"]),
            int(record["y_index"]),
        )
        r_mean[index] = float(record["r_mean_v"])
        r_scalar_mean[index] = float(record["r_scalar_mean_v"])
        r_std[index] = float(record["r_std_v"])
        accepted_attempt[index] = int(record["accepted_attempt_index"])
        acquisition_order[index] = int(record["acquisition_index"])
        accepted_file[index] = str(record["accepted_file"])
    np.savez(
        run_dir.raw / "xyz_balance_scan.npz",
        x_field_v=x_axis,
        y_field_v=y_axis,
        z_field_ma=z_axis,
        r_mean_v=r_mean,
        r_scalar_mean_v=r_scalar_mean,
        r_std_v=r_std,
        accepted_attempt_index=accepted_attempt,
        acquisition_order=acquisition_order,
        accepted_file=accepted_file,
        actual_rate_sa_s=np.float64(actual_rate),
        best_z_index=np.int64(best["z_index"]),
        best_x_index=np.int64(best["x_index"]),
        best_y_index=np.int64(best["y_index"]),
        best_z_field_ma=np.float64(best["z_field_ma"]),
        best_x_field_v=np.float64(best["x_field_v"]),
        best_y_field_v=np.float64(best["y_field_v"]),
        best_r_mean_v=np.float64(best["r_mean_v"]),
        best_r_std_v=np.float64(best["r_std_v"]),
        best_combination_remeasured=np.uint8(False),
    )


def _acquire_grid(
    params: MxKeithley6221OptimalControlXYZBalanceParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    controller: _XYZFieldController,
    actual_rate: float,
    device_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    total = params.x_field_points * params.y_field_points * params.z_field_points
    for (
        acquisition_index,
        z_index,
        x_index,
        y_index,
        z_ma,
        x_v,
        y_v,
    ) in iter_serpentine_grid(params):
        check_cancelled()
        print(
            f"XYZ 网格 {acquisition_index + 1}/{total}: "
            f"X={x_v:+.6f} V, Y={y_v:+.6f} V, Z={z_ma:+.6f} mA"
        )
        metadata = {
            "acquisition_index": np.int64(acquisition_index),
            "z_index": np.int64(z_index),
            "x_index": np.int64(x_index),
            "y_index": np.int64(y_index),
            "z_field_ma": np.float64(z_ma),
            "x_field_v": np.float64(x_v),
            "y_field_v": np.float64(y_v),
            "x_output_on": np.uint8(True),
            "y_output_on": np.uint8(True),
            "gs200_output_on": np.uint8(True),
            "keithley_output_on": np.uint8(True),
        }
        summary, attempt, filename = _acquire_valid_r_point(
            params,
            run_dir,
            devices,
            channels,
            file_stem=(
                f"grid_Z{z_index:03d}_X{x_index:03d}_Y{y_index:03d}"
            ),
            metadata=metadata,
            set_y_rf=lambda x=x_v, y=y_v, z=z_ma: controller.apply(x, y, z),
            settle_time_s=params.response_settle_time_s,
            duration_s=params.response_duration_s,
            actual_rate=actual_rate,
            device_id=device_id,
        )
        records.append(
            {
                "acquisition_index": acquisition_index,
                "z_index": z_index,
                "x_index": x_index,
                "y_index": y_index,
                "z_field_ma": z_ma,
                "x_field_v": x_v,
                "y_field_v": y_v,
                **summary,
                "accepted_attempt_index": attempt,
                "accepted_file": filename,
            }
        )
    best = dict(first_acquired_minimum(records))
    _save_scan_aggregate(run_dir, params, records, actual_rate, best)
    return records, best


def safe_shutdown(
    devices: dict[str, Any],
    channels: dict[str, int],
    params: MxKeithley6221OptimalControlXYZBalanceParams,
) -> SafetyShutdownReport:
    """保持 6221 最优控制与共同触发运行，恢复温控和 Pump 门控。

    6221 仅在 Compliance 故障时由合规检查器执行紧急关断；其余结束路径
    保持 6221 已加载 + ARM + Output ON，并保持 Time_sequence_2 触发输出。
    """
    extra_actions: list[ShutdownAction] = []
    pump_rf = devices.get("pump_rf")
    if pump_rf is not None and "pump_gate" in channels:
        extra_actions.append(
            ShutdownAction(
                "保持 Pump RF 开关 5V 常开失败",
                lambda: _set_pump_gate_on(
                    pump_rf,
                    channels["pump_gate"],
                    params.pump_gate_voltage_v,
                ),
            )
        )
    if pump_rf is not None and "pump_carrier" in channels:

        def keep_pump_carrier_on() -> None:
            validate_safety_limit(
                "Pump_modulation",
                params.pump_carrier_amplitude_vpp,
            )
            pump_rf.setup_sine(
                params.pump_carrier_frequency_hz,
                params.pump_carrier_amplitude_vpp,
                offset=0.0,
                phase=0.0,
                channel=channels["pump_carrier"],
            )
            pump_rf.set_output(True, channel=channels["pump_carrier"])

        extra_actions.append(
            ShutdownAction(
                "保持 Pump 100MHz 载波开启失败",
                keep_pump_carrier_on,
            )
        )
    temperature_restore = None
    if devices.get("temp_switch") is not None and "temp_switch" in channels:
        temperature_restore = TemperatureSwitchRestore(
            devices["temp_switch"],
            channels["temp_switch"],
        )
    return run_safety_shutdown(
        temperature_switch=temperature_restore,
        extra_actions=extra_actions,
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),),
        preserved_outputs=(
            *STANDARD_PRESERVED_OUTPUTS,
            "Time_sequence",
            "Time_sequence_2",
            "keithley_6221_main_field",
        ),
    )


def run(params: MxKeithley6221OptimalControlXYZBalanceParams) -> Path:
    """执行 6221 外部触发最优控制下的 XYZ DC 三维网格扫描。"""
    root = find_project_root()
    if not params.confirm_gs200_connected:
        raise ValueError("必须确认 GS200 已接入 Z 主磁场线圈（6221 接 Z 小磁场线圈）")
    mapping = load_mapping(root)
    theory = load_theory_control(
        Path(params.control_results_root),
        params.control_version,
    )
    calibration = load_keithley_calibration(
        root,
        params.keithley_calibration_source_run,
    )
    applied = build_applied_current(theory, calibration, params.control_scale)
    current_range = require_keithley_6221_current_range(
        params.keithley_current_range_ma,
        applied.minimum_ma,
        applied.maximum_ma,
    )
    run_dir = create_run_directory(
        DATA_TYPE,
        params.run_tag,
        params.to_external(),
        schema_version=params.schema_version,
        project_root=root,
    )
    source_files = _source_snapshot(
        run_dir,
        theory,
        calibration,
        applied,
        current_range,
    )
    x_axis, y_axis, z_axis = build_xyz_axes(params)
    inactive_normalized = float(-applied.offset_ma / applied.amplitude_peak_ma)
    run_dir.update_config(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        scan_mode="nested_scan",
        geometry={
            "optimal_control": "Keithley 6221 Z small-field coil ARB0 current",
            "x_balance": "X_magnetic_field DG4000 DC",
            "y_balance": "Y_magnetic_field DG4000 DC",
            "z_balance": "main_magnetic_field GS200 current",
            "gs200_physical_connection_confirmed": params.confirm_gs200_connected,
            "control_current_min_ma": applied.minimum_ma,
            "control_current_max_ma": applied.maximum_ma,
        },
        scan={
            "x_field_v": x_axis.tolist(),
            "y_field_v": y_axis.tolist(),
            "z_field_ma": z_axis.tolist(),
            "canonical_shape": "Z, X, Y",
            "order": "Z outer, continuous X/Y serpentine",
            "points": int(x_axis.size * y_axis.size * z_axis.size),
            "single_axis_rule": "POINTS=1 requires START=STOP",
            "zero_output": "X/Y DC and GS200 output remain ON at zero",
        },
        control_source={
            "version": theory.version,
            "waveform_sha256": theory.waveform_sha256,
            "parameter_sha256": theory.parameter_sha256,
            "repeat_frequency_hz": theory.repeat_frequency_hz,
            "theory_rf_frequency_hz": theory.theory_rf_frequency_hz,
            "rf_periods_per_waveform": theory.rf_periods_per_waveform,
            "scale": params.control_scale,
            "formula": (
                "I_mA(t) = CONTROL_SCALE * "
                "(Omega_ctrl_Hz(t) - f_0mA_Hz) / K_f_Hz_per_mA"
            ),
        },
        keithley_calibration={
            "source_run": calibration.run_name,
            "slope_hz_per_ma": calibration.slope_hz_per_ma,
            "intercept_hz": calibration.intercept_hz,
            "r_squared": calibration.r_squared,
            "analysis_sha256": calibration.analysis_sha256,
        },
        keithley_source_configuration={
            "coil": "Z small-field coil",
            "range_ma": params.keithley_current_range_ma,
            "range_a": params.keithley_current_range_ma / 1000.0,
            "autorange": False,
            "response": params.keithley_output_response,
            "analog_filter": False,
            "compliance_v": params.keithley_compliance_v,
        },
        trigger={
            "source": "Time_sequence_2",
            "frequency_hz": theory.repeat_frequency_hz,
            "amplitude_vpp": params.trigger_amplitude_vpp,
            "offset_v": params.trigger_offset_v,
            "duty_percent": params.trigger_duty_percent,
            "slope": "NEGative",
            "keithley_line": params.keithley_trigger_line,
            "ignore": "OFF",
            "inactive_value_normalized": inactive_normalized,
            "inactive_current_ma": 0.0,
            "wiring": (
                "Time_sequence_2 CH2 to 6221 Trigger Link Line 1; "
                "6221 Line 2 unused"
            ),
            "physical_wiring_verified_by_software": False,
        },
        applied_control={
            "minimum_ma": applied.minimum_ma,
            "maximum_ma": applied.maximum_ma,
            "amplitude_peak_ma": applied.amplitude_peak_ma,
            "offset_ma": applied.offset_ma,
            "points": int(theory.time_s.size),
            "current_range_check": current_range.to_dict(),
        },
        final_output_policy={
            "xyz_fields": "restore complete pre-run X/Y/GS200 state",
            "optimal_control": "6221 stays armed with output ON",
            "trigger": "Time_sequence_2 keeps running",
            "best_combination_remeasured": False,
        },
        acquisition_signals=["HF2 Demod0 R"],
        warnings=[],
    )

    session = DeviceSession()
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}
    main_field_state = None
    xy_states: dict[str, _DGChannelState] = {}
    completion_status = "failed"
    failure_reason: str | None = None
    best: dict[str, Any] | None = None
    connection_complete = False
    try:
        check_cancelled()
        try:
            devices, channels = _connect_devices(mapping, session)
            connection_complete = True
        except Exception:
            session.cleanup_connection_failure()
            raise
        _install_compliance_checker(devices)
        main_field_state = snapshot_main_field_state(devices["gs200"])
        xy_states = {
            "x_field": _snapshot_dg_channel(
                devices["xy_field"],
                channels["x_field"],
            ),
            "y_rf": _snapshot_dg_channel(
                devices["xy_field"],
                channels["y_rf"],
            ),
        }
        run_dir.update_config(
            device_snapshot={
                "main_field_before_configuration": asdict(main_field_state),
                "xy_channels_before_configuration": {
                    name: asdict(state) for name, state in xy_states.items()
                },
            }
        )
        _configure_xy_idle(devices, channels)
        _configure_trigger(
            params,
            devices["trigger"],
            channels["trigger"],
            theory.repeat_frequency_hz,
            output=False,
        )
        laser = devices["laser"]
        laser.setup_dc(params.pump_laser_power_v, channel=channels["pump_laser"])
        laser.set_output(True, channel=channels["pump_laser"])
        laser.setup_dc(params.probe_laser_power_v, channel=channels["probe_laser"])
        laser.set_output(True, channel=channels["probe_laser"])
        pump = devices["pump_rf"]
        pump.setup_sine(
            params.pump_carrier_frequency_hz,
            params.pump_carrier_amplitude_vpp,
            offset=0.0,
            phase=0.0,
            channel=channels["pump_carrier"],
        )
        pump.set_output(True, channel=channels["pump_carrier"])
        _set_pump_gate_on(pump, channels["pump_gate"], params.pump_gate_voltage_v)
        set_temperature_switch(
            devices["temp_switch"],
            True,
            channel=channels["temp_switch"],
        )
        configure_temperature_control(
            devices.get("tec"),
            params.temperature_c,
            channel=1,
            tolerance_c=params.temperature_tolerance_c,
            stable_reads=params.temperature_stable_reads,
            poll_interval_s=params.temperature_poll_interval_s,
            timeout_s=params.temperature_timeout_s,
            cancellation=_RuntimeCancellation(),
            stability_waiter=wait_for_temperature_stable,
        )
        clocks = _configure_clocks(devices, mapping)
        configured_inactive = configure_keithley_6221_optimal_control(
            devices["keithley"],
            theory,
            applied,
            current_range_ma=params.keithley_current_range_ma,
            compliance_v=params.keithley_compliance_v,
        )
        _configure_trigger(
            params,
            devices["trigger"],
            channels["trigger"],
            theory.repeat_frequency_hz,
            output=True,
        )
        actual_rate, hf2_snapshot = _configure_hf2(
            params,
            devices,
            theory.theory_rf_frequency_hz,
        )
        run_dir.update_config(
            clock_sources=clocks,
            hf2_configuration=hf2_snapshot,
            trigger_inactive_value_normalized=configured_inactive,
            trigger_inactive_current_ma=0.0,
        )
        controller = _XYZFieldController(
            devices["xy_field"],
            devices["gs200"],
            channels,
        )
        _, best = _acquire_grid(
            params,
            run_dir,
            devices,
            channels,
            controller,
            actual_rate,
            str(mapping["lockin_r"]["device_id"]),
        )
        completion_status = "completed"
        run_dir.update_config(
            completion_status=completion_status,
            failure_reason=None,
            actual_rates={"response_sa_s": actual_rate},
            measured_grid_minimum={
                "z_index": int(best["z_index"]),
                "x_index": int(best["x_index"]),
                "y_index": int(best["y_index"]),
                "z_field_ma": float(best["z_field_ma"]),
                "x_field_v": float(best["x_field_v"]),
                "y_field_v": float(best["y_field_v"]),
                "r_mean_v": float(best["r_mean_v"]),
                "r_std_v": float(best["r_std_v"]),
                "acquisition_index": int(best["acquisition_index"]),
                "remeasured": False,
            },
            data_files=[*source_files, "raw/xyz_balance_scan.npz"],
        )
        print(f"Mx Keithley 6221 最优控制 XYZ 平衡场采集完成: {run_dir.root}")
        return run_dir.root
    except Exception as exc:
        failure_reason = str(exc)
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status="failed",
                failure_reason=failure_reason,
            )
        raise
    finally:
        shutdown_report = safe_shutdown(devices, channels, params)
        if not connection_complete:
            session.cleanup_connection_failure()
        xy_restore_errors: list[str] = []
        if devices.get("xy_field") is not None:
            for channel_name in ("y_rf", "x_field"):
                state = xy_states.get(channel_name)
                channel = channels.get(channel_name)
                if state is None or channel is None:
                    continue
                try:
                    _restore_dg_channel(
                        devices["xy_field"],
                        channel,
                        state,
                    )
                except Exception as exc:
                    xy_restore_errors.append(f"{channel_name}: {exc}")
        main_field_restore_errors: list[str] = []
        if main_field_state is not None and devices.get("gs200") is not None:
            try:
                restore_main_field_state(devices["gs200"], main_field_state)
            except Exception as exc:
                main_field_restore_errors.append(str(exc))
        restore_errors = [
            *shutdown_report.errors,
            *xy_restore_errors,
            *main_field_restore_errors,
        ]
        if restore_errors:
            print("安全恢复警告: " + "；".join(restore_errors))
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status=completion_status,
                failure_reason=failure_reason,
                safety_shutdown=shutdown_report.to_dict(),
                xy_restore={
                    "attempted": bool(xy_states),
                    "success": not xy_restore_errors,
                    "errors": xy_restore_errors,
                },
                main_field_restore={
                    "attempted": main_field_state is not None,
                    "success": not main_field_restore_errors,
                    "errors": main_field_restore_errors,
                },
                device_disconnect={
                    "tec_disconnected": not shutdown_report.disconnect_errors,
                    "other_devices_preserved": True,
                    "errors": list(shutdown_report.disconnect_errors),
                },
            )


def main() -> int:
    params = load_runtime_params(MxKeithley6221OptimalControlXYZBalanceParams)
    run(params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
