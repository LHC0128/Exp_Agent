"""普通气室采集的稳定等待兼容规则；专用验证实验显式排除。"""

from __future__ import annotations

import math


ACQUISITION_EXCEPTIONS = frozenset({
    "z-aw-waveform-scope-check", "z-coil-inductance-frequency-response",
    "z-coil-current-frequency-response", "z-aw-current-waveform-scope-check",
    "z-aw-closed-loop-waveform-correction", "aw-waveform-scope-check",
    "dg-am-comp-transfer-check", "temperature-switch-pid-cycle",
})


def migrate_system_settle(values: dict, legacy_defaults: dict[str, float],
                          removed_temperature_keys=()) -> dict:
    """旧短等待取最大值；显式新键优先，移除已停用的温度判稳参数。"""
    migrated = dict(values)
    if "SYSTEM_SETTLE_S" not in migrated and any(key in migrated for key in legacy_defaults):
        durations = [migrated.get(key, default) for key, default in legacy_defaults.items()]
        for duration in durations:
            if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
                raise ValueError("旧系统稳定等待必须是有限非负秒数")
        migrated["SYSTEM_SETTLE_S"] = max(durations, default=0.0)
    for key in (*legacy_defaults, *removed_temperature_keys):
        migrated.pop(key, None)
    value = migrated.get("SYSTEM_SETTLE_S")
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
        raise ValueError("SYSTEM_SETTLE_S 必须是有限非负秒数")
    return migrated


# 旧模式仍使用 AST 参数入口，迁移信息不依赖导入采集脚本。
LEGACY_SETTLE_DEFAULTS = {'noise-spectrum-xy-v2': {'AM_DC_SETTLE_TIME': 0.5}, 'photon-shot-noise': {'PROBE_SETTLE_TIME': 0.5}, 'rf-sensitivity-aw-frequency': {'FREQ_SETTLE_TIME': 0.5, 'PHASE_SETTLE_TIME': 0.1, 'TEMP_SWITCH_OFF_LEAD': 0.1}, 'rf-sensitivity-constxy-frequency': {'FREQ_SETTLE_TIME': 0.5, 'TEMP_SWITCH_OFF_LEAD': 0.1}, 'z-field-bandwidth': {'FREQ_SETTLE_TIME': 0.5, 'TEMP_SWITCH_OFF_LEAD': 0.1, 'DEMOD_R_READ_SETTLE_TIME': 0.2}, 'bb-duty-scan-burst': {'TEMP_SWITCH_OFF_DELAY_S': 0.1}, 'bb-power-scan-burst': {'TEMP_SWITCH_OFF_DELAY_S': 0.1}, 't1-calibration': {}, 'xy-aw-voltage-calibration': {}, 'xy-dc-voltage-calibration': {}, 'aw-const-omega-check': {}, 'aw-const-omega-awscale-check': {}, 'static-sensitivity-optimize': {}, 'static-sensitivity-optimize-local': {}}

LEGACY_SETTLE_DEFAULTS.update({'aw-const-omega-check': {'AW_SETTLE_TIME_s': 0.5}, 'aw-const-omega-awscale-check': {'AW_SETTLE_TIME_s': 0.5}, 'xy-aw-voltage-calibration': {'XY_DC_SETTLE_TIME_s': 0.3}, 'xy-dc-voltage-calibration': {'XY_DC_SETTLE_TIME_s': 0.3}})
