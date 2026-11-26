"""Z 脉冲链实验共用的映射键与安全限值键。

键名属于 `params/mapping.yaml` 与 `params/safety_limits.yaml` 的稳定外部约定，
实验包只引用，不自行改写。功放物理旁路后 DG4000 直驱 Z 线圈的实验不再要求
``coil_drive_amplifier`` 映射，使用 ``REQUIRED_MAPPING_KEYS_NO_AMPLIFIER``。
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
# 功放旁路、DG 直驱的实验：同一份映射里可以有 coil_drive_amplifier，但不要求它存在。
REQUIRED_MAPPING_KEYS_NO_AMPLIFIER = tuple(key for key in REQUIRED_MAPPING_KEYS if key != AMPLIFIER_KEY)


def required_mapping_keys(amplifier_enabled: bool = True) -> tuple[str, ...]:
    """按驱动链路是否经功放返回必需映射键。"""
    return REQUIRED_MAPPING_KEYS if amplifier_enabled else REQUIRED_MAPPING_KEYS_NO_AMPLIFIER
