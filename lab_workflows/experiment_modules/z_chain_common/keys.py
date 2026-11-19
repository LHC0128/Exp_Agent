"""Z 脉冲链实验共用的映射键与安全限值键。

键名属于 `params/mapping.yaml` 与 `params/safety_limits.yaml` 的稳定外部约定，
实验包只引用，不自行改写。
"""
from __future__ import annotations

SOURCE_KEY = "Z_magnetic_field"
X_KEY = "X_magnetic_field"
RF_KEY = "Y_magnetic_field"
TRIGGER_KEY = "Time_sequence_2"
TEMP_SWITCH_KEY = "Temp_Switch"
LOCKIN_KEY = "lockin_r"
AMPLIFIER_KEY = "coil_drive_amplifier"
SCOPE_KEY = "coil_current_scope"
MAIN_FIELD_KEY = "main_magnetic_field"
TEC_KEY = "temperature"
CURRENT_LIMIT_KEY = "z_pulse_coil_current"
REQUIRED_MAPPING_KEYS = (SOURCE_KEY, X_KEY, RF_KEY, TRIGGER_KEY, TEMP_SWITCH_KEY, LOCKIN_KEY,
                         AMPLIFIER_KEY, SCOPE_KEY, MAIN_FIELD_KEY, TEC_KEY)
