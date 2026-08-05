"""可由多个实验组合使用的安全实验步骤。"""

from .arbitrary import (
    AWScaleStrategy,
    ArbitraryWaveformSpec,
    DirectAWStrategy,
    ExternalAMStrategy,
    upload_arbitrary,
)
from .clock import (
    ClockSyncRecord,
    clock_device_id,
    load_clock_profile,
    normalize_clock_source,
    synchronize_clock_device,
    synchronize_connected_clocks,
)
from .direct_aw_phase import (
    DirectAWPhaseCalibrationConfig,
    DirectAWPhaseCalibrationResult,
    DirectAWPhaseMeasurement,
    calibrate_direct_aw_phase,
    wrap_phase_deg,
)
from .phase import (
    PhaseCalibrationConfig,
    PhaseCalibrationResult,
    calibrate_demod_phase,
    phase_calibration_guard,
)
from .main_field import (
    MainFieldState,
    restore_main_field_state,
    snapshot_main_field_state,
)
from .magnetic_field import configure_fixed_dc_field
from .mx_z_optimal_control import (
    configure_main_field,
    configure_mx_z_optimal_control_workpoint,
    configure_optimal_control_trigger,
    configure_z_optimal_control_output,
    save_optimal_control_source_snapshot,
    validate_z_trigger_mapping,
)
from .run_directory import RunDirectory, create_run_directory
from .scope_waveform import (
    ScopeAutoRangeState,
    ScopeCaptureSettings,
    acquire_autoranged_waveform,
    configure_fixed_rate_scope,
    next_auto_offset,
    next_auto_range_scale,
    read_complete_scope_record,
    temperature_gated_acquire,
)
from .safety_shutdown import (
    DGChannelShutdown,
    DisconnectTarget,
    STANDARD_PRESERVED_OUTPUTS,
    SafetyShutdownReport,
    ShutdownAction,
    TemperatureSwitchRestore,
    disconnect_device_mapping,
    disconnect_devices,
    run_safety_shutdown,
)
from .session import DeviceSession
from .temperature import (
    TemperatureControlStatus,
    configure_temperature_control,
    set_temperature_switch,
    wait_for_temperature_stable,
)

__all__ = [
    "AWScaleStrategy",
    "ArbitraryWaveformSpec",
    "DeviceSession",
    "DirectAWStrategy",
    "DirectAWPhaseCalibrationConfig",
    "DirectAWPhaseCalibrationResult",
    "DirectAWPhaseMeasurement",
    "DGChannelShutdown",
    "DisconnectTarget",
    "ExternalAMStrategy",
    "MainFieldState",
    "PhaseCalibrationConfig",
    "PhaseCalibrationResult",
    "RunDirectory",
    "ScopeAutoRangeState",
    "ScopeCaptureSettings",
    "STANDARD_PRESERVED_OUTPUTS",
    "SafetyShutdownReport",
    "ShutdownAction",
    "TemperatureSwitchRestore",
    "TemperatureControlStatus",
    "calibrate_demod_phase",
    "calibrate_direct_aw_phase",
    "acquire_autoranged_waveform",
    "configure_fixed_rate_scope",
    "configure_fixed_dc_field",
    "configure_main_field",
    "configure_mx_z_optimal_control_workpoint",
    "configure_optimal_control_trigger",
    "configure_temperature_control",
    "configure_z_optimal_control_output",
    "create_run_directory",
    "disconnect_device_mapping",
    "disconnect_devices",
    "phase_calibration_guard",
    "run_safety_shutdown",
    "save_optimal_control_source_snapshot",
    "restore_main_field_state",
    "set_temperature_switch",
    "upload_arbitrary",
    "validate_z_trigger_mapping",
    "ClockSyncRecord",
    "clock_device_id",
    "load_clock_profile",
    "normalize_clock_source",
    "next_auto_offset",
    "next_auto_range_scale",
    "read_complete_scope_record",
    "synchronize_clock_device",
    "synchronize_connected_clocks",
    "snapshot_main_field_state",
    "temperature_gated_acquire",
    "wait_for_temperature_stable",
    "wrap_phase_deg",
]
