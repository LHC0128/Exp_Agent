"""新模式实验使用的稳定物理量键。"""

from __future__ import annotations


FULL_HF2 = (
    "main_magnetic_field", "Z_magnetic_field", "X_magnetic_field",
    "Y_magnetic_field", "rf_coil", "Pump_laser_power",
    "Probe_laser_power", "Pump_modulation", "Time_sequence",
    "Temp_Switch", "temperature", "lockin_r",
)
KEITHLEY_MAIN_FIELD = (*FULL_HF2, "keithley_6221_main_field")
KEITHLEY_OPTIMAL_CONTROL = (
    "main_magnetic_field", "keithley_6221_main_field", "Time_sequence_2",
    "X_magnetic_field", "rf_coil", "Pump_laser_power", "Probe_laser_power",
    "Pump_modulation", "Time_sequence", "Temp_Switch", "temperature", "lockin_r",
)
KEITHLEY_OPTIMAL_CONTROL_XYZ_BALANCE = (
    "main_magnetic_field", "keithley_6221_main_field", "Time_sequence_2",
    "X_magnetic_field", "Y_magnetic_field", "Pump_laser_power",
    "Probe_laser_power", "Pump_modulation", "Time_sequence", "Temp_Switch",
    "temperature", "lockin_r",
)
KEITHLEY_OPTIMAL_CONTROL_BALANCED_SENSITIVITY = (
    "main_magnetic_field", "keithley_6221_main_field", "Time_sequence_2",
    "X_magnetic_field", "Y_magnetic_field", "rf_coil", "Pump_laser_power",
    "Probe_laser_power", "Pump_modulation", "Time_sequence", "Temp_Switch",
    "temperature", "lockin_r",
)
SCOPE = (
    "main_magnetic_field", "Z_magnetic_field", "X_magnetic_field",
    "Y_magnetic_field", "Pump_laser_power", "Probe_laser_power",
    "Pump_modulation", "Time_sequence", "Temp_Switch", "temperature",
    "scope_waveform",
)
DIRECT_AW = (
    *FULL_HF2,
    "Time_sequence_2",
    "X_magnetic_field_AM",
    "Y_magnetic_field_AM",
)
OPTIMAL_CONTROL = (*FULL_HF2, "Time_sequence_2")
T2 = (
    "main_magnetic_field", "X_magnetic_field", "Y_magnetic_field",
    "Z_magnetic_field", "Time_sequence_2",
    "Pump_laser_power", "Probe_laser_power", "Pump_modulation",
    "Time_sequence", "Temp_Switch", "temperature", "lockin_xy",
    "scope_waveform",
)
STATIC_SENSITIVITY = (
    "main_magnetic_field", "X_magnetic_field", "Y_magnetic_field",
    "Z_magnetic_field", "Time_sequence_2", "Pump_laser_power",
    "Probe_laser_power", "Pump_modulation", "Time_sequence",
    "Temp_Switch", "temperature", "lockin_r",
)


TYPED_MAPPING_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "static-sensitivity": STATIC_SENSITIVITY,
    "mx-main-field-calibration": FULL_HF2,
    "mx-keithley-6221-main-field-calibration": KEITHLEY_MAIN_FIELD,
    "mx-keithley-6221-optimal-control-rf-sensitivity": KEITHLEY_OPTIMAL_CONTROL,
    "mx-keithley-6221-optimal-control-xyz-balance": KEITHLEY_OPTIMAL_CONTROL_XYZ_BALANCE,
    "mx-keithley-6221-optimal-control-balanced-sensitivity": KEITHLEY_OPTIMAL_CONTROL_BALANCED_SENSITIVITY,
    "mx-main-field-noise-spectrum": FULL_HF2,
    "mx-main-field-scope-noise-spectrum": SCOPE,
    "mx-xy-residual-field-calibration": FULL_HF2,
    "mx-y-rf-power-optimization": FULL_HF2,
    "mx-y-rf-probe-detuning-optimization": (*FULL_HF2, "probe_laser"),
    "mx-y-rf-sensitivity": FULL_HF2,
    "mx-y-rf-frequency-response": FULL_HF2,
    "mx-y-rf-sensitivity-drift": FULL_HF2,
    "mx-z-field-calibration": FULL_HF2,
    "mx-z-noise-spectrum": FULL_HF2,
    "mx-z-optimal-control-rf-sensitivity": OPTIMAL_CONTROL,
    "mx-z-optimal-control-xy-leakage-response": OPTIMAL_CONTROL,
    "mx-z-optimal-control-xyz-balance": OPTIMAL_CONTROL,
    "noise-spectrum-xy": DIRECT_AW,
    "noise-spectrum-xy-demod3-r": DIRECT_AW,
    "projection-noise": SCOPE,
    "rf-sensitivity-direct-aw-frequency": DIRECT_AW,
    "t2-calibration": T2,
    "xy-direct-aw-dc-calibration": DIRECT_AW,
}


ARBITRARY_MAPPING_KEYS: dict[str, tuple[str, ...]] = {
    experiment_id: ("X_magnetic_field", "Y_magnetic_field")
    for experiment_id in (
        "noise-spectrum-xy",
        "noise-spectrum-xy-demod3-r",
        "rf-sensitivity-direct-aw-frequency",
        "xy-direct-aw-dc-calibration",
    )
}
ARBITRARY_MAPPING_KEYS.update({
    experiment_id: ("Z_magnetic_field",)
    for experiment_id in (
        "mx-z-optimal-control-rf-sensitivity",
        "mx-z-optimal-control-xy-leakage-response",
        "mx-z-optimal-control-xyz-balance",
    )
})


INFINITE_BURST_MAPPING_KEYS: dict[str, tuple[str, ...]] = {
    experiment_id: ARBITRARY_MAPPING_KEYS[experiment_id]
    for experiment_id in ARBITRARY_MAPPING_KEYS
}


MAPPING_INSTRUMENTS = {
    "main_magnetic_field": "gs200",
    "keithley_6221_main_field": "keithley_6221",
    "temperature": "tec_controller",
    "lockin_r": "lockin_amplifier",
    "lockin_xy": "lockin_amplifier",
    "scope_waveform": "sds_acquisition",
    "probe_laser": "toptica_dlc_pro",
}


MAPPING_ENDPOINT_FIELDS = {
    "lockin_r": "demod_idx",
    "lockin_xy": "demod_idx",
    "probe_laser": "laser_channel",
    "temperature": "channel",
}
