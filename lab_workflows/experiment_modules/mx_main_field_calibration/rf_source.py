"""Y 方向射频源：DG4000 rf_coil 通道与 MFLI MF-MOD 载波。

两种射频源都在共振频率附近给 rf_coil 提供正弦驱动，锁相解调 R 判读。
Demod n 对应 Osc n：MFLI 的 MF-MOD 载波由 Demod 1 / Osc 1 产生，原子读出固定
Demod 4 / Osc 4（索引 3/3）；DG4000 模式的读出同样使用 Demod 4 / Osc 4。
"""

from __future__ import annotations

import math
from typing import Any

from lockin_amplifier import OscillatorConfig, demod

from ...common import validate_safety_limit
from .models import (
    MFLI_CARRIER_OSC_IDX,
    MFLI_READOUT_DEMOD_IDX,
    MFLI_READOUT_OSC_IDX,
    MFLI_SIGNAL_OUTPUT_IDX,
    RF_SOURCE_DG4000,
    RF_SOURCE_MFLI,
)

MOD_IDX = 0
MFLI_OUTPUT_RANGES_V = (0.01, 0.1, 1.0, 10.0)
MFLI_MAX_OUTPUT_V = 10.0
_ABS_TOL_V = 1e-4
_REL_TOL_HZ = 1e-6


class RfSourceError(RuntimeError):
    """射频源配置、写入或回读不符合预期。"""


def installed_lockin_options(capabilities: dict | None) -> set[str]:
    """规范化锁相选件名称集合（移除 MF- 前缀）。"""
    options = (capabilities or {}).get("options") or ()
    return {str(item).strip().upper().removeprefix("MF-") for item in options if str(item).strip()}


def require_mfli_declared_capabilities(capabilities: dict | None) -> None:
    """离线预检：MFLI 射频源至少需要 MF-MD（④ 个解调器/振荡器中的 Demod 4 / Osc 4）。"""
    if "MD" not in installed_lockin_options(capabilities):
        raise ValueError("MFLI 射频源需要 MF-MD 选件（原子读出用 Demod 4 / Osc 4）")


def require_mfli_connected_capabilities(capabilities: dict | None) -> None:
    """连接后核对实机选件；MF-MOD 无法离线声明，必须在真机回读确认。"""
    installed = installed_lockin_options(capabilities)
    if "MD" not in installed:
        raise ValueError("MFLI 缺少 MF-MD 选件，无法分配 Demod 4 / Osc 4 原子读出")
    if "MOD" not in installed:
        raise ValueError("MFLI 缺少 MF-MOD 选件，无法用 Signal Output 1 产生 Y 向射频")


def mfli_signal_output_key(mapping: dict[str, dict[str, Any]]) -> str:
    """按锁相设备库 ID 生成 Signal Output 1 安全限值键名。"""
    config = mapping.get("lockin_r") or {}
    device_id = str(config.get("device_library_id") or config.get("device_id") or "").strip()
    if not device_id:
        raise ValueError("lockin_r 映射缺少有效的 device_library_id")
    return f"{device_id}_signal_output_0"


def select_mfli_output_range(vpk: float) -> float:
    """选择能完整容纳载波峰值的最小 Signal Output 1 量程。"""
    if not math.isfinite(vpk) or vpk < 0:
        raise ValueError("MFLI 载波峰值必须是非负有限电压")
    for option in MFLI_OUTPUT_RANGES_V:
        if vpk <= option + 1e-12:
            return float(option)
    raise ValueError(f"MFLI 载波峰值 {vpk:g} V 超出 Signal Output 1 最大 {MFLI_MAX_OUTPUT_V:g} V 量程")


class Dg4000RfSource:
    """DG4000 rf_coil 通道正弦驱动；读出振荡器逐点同步。"""

    kind = RF_SOURCE_DG4000
    safety_key = "rf_coil"

    def __init__(self, params, devices, channels):
        self.params = params
        self.device = devices["xy_field"]
        self.channel = int(channels["y_rf"])
        self.lockin = devices["hf2"]

    def describe(self) -> dict[str, Any]:
        return dict(kind=self.kind, safety_key=self.safety_key, mapping_key="rf_coil",
                    channel=self.channel, readout_demod_idx=self.params.demod_idx,
                    readout_osc_idx=self.params.demod_osc_idx)

    def initialize_safe_state(self) -> dict[str, Any]:
        """A04：把可能残留的 RF 通道归零关闭，不改变锁相配置。"""
        validate_safety_limit(self.safety_key, 0.0)
        self.device.set_burst_state(False, channel=self.channel)
        self.device.set_mod_state(False, channel=self.channel)
        self.device.setup_dc(0.0, channel=self.channel)
        self.device.set_output(False, channel=self.channel)
        return dict(kind=self.kind, output=False, dc_v=0.0)

    def prepare(self) -> dict[str, Any]:
        validate_safety_limit(self.safety_key, self.params.y_rf_amplitude_vpp)
        self.device.set_burst_state(False, channel=self.channel)
        self.device.set_mod_state(False, channel=self.channel)
        self.device.setup_sine(
            self.params.main_field_reference_frequency_hz,
            self.params.y_rf_amplitude_vpp,
            offset=0.0,
            phase=0.0,
            channel=self.channel,
        )
        self.device.set_output(False, channel=self.channel)
        return dict(kind=self.kind, frequency_hz=self.params.main_field_reference_frequency_hz,
                    amplitude_vpp=self.params.y_rf_amplitude_vpp, output=False)

    def set_frequency(self, frequency_hz: float) -> None:
        validate_safety_limit(self.safety_key, self.params.y_rf_amplitude_vpp)
        self.device.set_frequency(float(frequency_hz), channel=self.channel)
        self.device.set_phase_adjust(0.0, channel=self.channel)
        self.device.set_amplitude(self.params.y_rf_amplitude_vpp, channel=self.channel)
        demod.configure_oscillator(
            self.lockin,
            OscillatorConfig(osc_index=self.params.demod_osc_idx, frequency=float(frequency_hz)),
        )

    def set_output(self, enabled: bool) -> None:
        self.device.set_output(bool(enabled), channel=self.channel)

    def shutdown(self) -> dict[str, Any]:
        return self.initialize_safe_state()

    def snapshot(self) -> dict[str, Any]:
        return self.describe()


class MfliRfSource:
    """MFLI Signal Output 1 的 MF-MOD 载波驱动；Demod 4 / Osc 4 读出。"""

    kind = RF_SOURCE_MFLI

    def __init__(self, params, mapping, devices):
        self.params = params
        self.lockin = devices["hf2"]
        self.device_id = self.lockin.device_id
        self.safety_key = mfli_signal_output_key(mapping)
        self.vpk = float(params.y_rf_amplitude_vpk)
        self.range_v = select_mfli_output_range(self.vpk)
        self.frequency_hz = float(params.main_field_reference_frequency_hz)
        self.output_on = False
        self.last_readback: dict[str, Any] = {}

    def _sigout(self, node: str) -> str:
        return f"/{self.device_id}/sigouts/{MFLI_SIGNAL_OUTPUT_IDX}/{node}"

    def _mod(self, node: str) -> str:
        return f"/{self.device_id}/mods/{MOD_IDX}/{node}"

    def _osc(self, index: int, node: str = "freq") -> str:
        return f"{self.lockin.osc_path(index)}/{node}"

    def _validate(self) -> None:
        for value in (0.0, self.vpk, -self.vpk):
            validate_safety_limit(self.safety_key, value)
        if self.vpk > MFLI_MAX_OUTPUT_V:
            raise ValueError(f"MFLI 载波峰值 {self.vpk:g} V 超出 ±{MFLI_MAX_OUTPUT_V:g} V")

    def describe(self) -> dict[str, Any]:
        return dict(kind=self.kind, safety_key=self.safety_key, output_index=MFLI_SIGNAL_OUTPUT_IDX,
                    carrier_osc_idx=MFLI_CARRIER_OSC_IDX, readout_demod_idx=MFLI_READOUT_DEMOD_IDX,
                    readout_osc_idx=MFLI_READOUT_OSC_IDX, range_v=self.range_v,
                    carrier_vpk=self.vpk, frequency_hz=self.frequency_hz)

    def initialize_safe_state(self) -> dict[str, Any]:
        """A04：先关闭并清零可能残留的 Signal Output 1 / MF-MOD。"""
        self._validate()
        device = self.lockin
        device.set_int(self._sigout("on"), 0)
        device.set_int(self._mod("enable"), 0)
        device.set_int(self._mod("output"), 0)
        device.set_double(self._mod("carrier/amplitude"), 0.0)
        device.set_double(self._sigout("offset"), 0.0)
        device.set_int(self._sigout("add"), 0)
        device.sync()
        readback = dict(
            output=bool(device.get_int(self._sigout("on"))),
            mod_enable=bool(device.get_int(self._mod("enable"))),
            carrier_vpk=float(device.get_double(self._mod("carrier/amplitude"))),
            offset_v=float(device.get_double(self._sigout("offset"))),
        )
        if (readback["output"] or readback["mod_enable"] or abs(readback["carrier_vpk"]) > 1e-6
                or abs(readback["offset_v"]) > _ABS_TOL_V):
            raise RfSourceError(f"MFLI Y 射频未回读为安全关闭状态：{readback}")
        self.output_on = False
        self.last_readback = readback
        return dict(kind=self.kind, **readback)

    def prepare(self) -> dict[str, Any]:
        self._validate()
        device = self.lockin
        if int(device.get_int(self._sigout("add"))) != 0:
            raise RfSourceError("MFLI Signal Output 1 开启了 Add 叠加，无法保证总电压安全")
        device.set_int(self._sigout("on"), 0)
        device.set_int(self._mod("enable"), 0)
        device.set_double(self._sigout("range"), self.range_v)
        device.set_double(self._sigout("offset"), 0.0)
        device.set_int(self._mod("mode"), 1)
        device.set_int(self._mod("freqdevenable"), 0)
        device.set_int(self._mod("carrier/oscselect"), MFLI_CARRIER_OSC_IDX)
        device.set_double(self._osc(MFLI_CARRIER_OSC_IDX), self.frequency_hz)
        device.set_double(self._osc(self.params.demod_osc_idx), self.frequency_hz)
        device.set_int(self._mod("sidebands/0/enable"), 0)
        device.set_int(self._mod("sidebands/1/enable"), 0)
        device.set_double(self._mod("carrier/amplitude"), self.vpk)
        device.set_double(self._mod("carrier/phaseshift"), 0.0)
        device.set_int(self._mod("carrier/enable"), 1)
        device.set_int(self._mod("output"), 1)
        device.set_int(self._mod("enable"), 1)
        device.sync()
        return self._verify(output=False, expect_readout=False)

    def verify_readout(self) -> dict[str, Any]:
        """锁相输入/解调配置完成后核对读出振荡器与解调器。"""
        return self._verify(output=self.output_on, expect_readout=True)

    def _verify(self, *, output: bool, expect_readout: bool = True) -> dict[str, Any]:
        device = self.lockin
        demod_path = device.demod_path(self.params.demod_idx)
        got = dict(
            output=bool(device.get_int(self._sigout("on"))),
            range_v=float(device.get_double(self._sigout("range"))),
            offset_v=float(device.get_double(self._sigout("offset"))),
            add=bool(device.get_int(self._sigout("add"))),
            mod_enable=bool(device.get_int(self._mod("enable"))),
            mod_mode=int(device.get_int(self._mod("mode"))),
            mod_output=int(device.get_int(self._mod("output"))),
            carrier_enable=bool(device.get_int(self._mod("carrier/enable"))),
            carrier_osc=int(device.get_int(self._mod("carrier/oscselect"))),
            carrier_hz=float(device.get_double(self._osc(MFLI_CARRIER_OSC_IDX))),
            carrier_vpk=float(device.get_double(self._mod("carrier/amplitude"))),
            carrier_phase_deg=float(device.get_double(self._mod("carrier/phaseshift"))),
            sideband0=bool(device.get_int(self._mod("sidebands/0/enable"))),
            sideband1=bool(device.get_int(self._mod("sidebands/1/enable"))),
            readout_osc_hz=float(device.get_double(self._osc(self.params.demod_osc_idx))),
            readout_demod_oscselect=int(device.get_int(f"{demod_path}/oscselect")),
            readout_demod_enable=bool(device.get_int(f"{demod_path}/enable")),
        )
        valid = (
            got["output"] is output
            and not got["add"]
            and got["range_v"] >= self.vpk - 1e-9
            and math.isclose(got["offset_v"], 0.0, abs_tol=_ABS_TOL_V)
            and got["mod_enable"]
            and got["mod_mode"] == 1
            and got["mod_output"] == 1
            and got["carrier_enable"]
            and got["carrier_osc"] == MFLI_CARRIER_OSC_IDX
            and math.isclose(got["carrier_hz"], self.frequency_hz, rel_tol=_REL_TOL_HZ, abs_tol=1e-3)
            and math.isclose(got["carrier_vpk"], self.vpk, rel_tol=_REL_TOL_HZ, abs_tol=1e-6)
            and math.isclose(got["carrier_phase_deg"], 0.0, abs_tol=0.02)
            and not got["sideband0"] and not got["sideband1"]
            and math.isclose(got["readout_osc_hz"], self.frequency_hz, rel_tol=_REL_TOL_HZ, abs_tol=1e-3)
        )
        if expect_readout:
            valid = valid and got["readout_demod_oscselect"] == self.params.demod_osc_idx
        if not valid:
            raise RfSourceError(f"MFLI Y 射频写入回读不符，拒绝继续采集：{got}")
        self.output_on = bool(output)
        self.last_readback = got
        return got

    def set_frequency(self, frequency_hz: float) -> None:
        self._validate()
        self.frequency_hz = float(frequency_hz)
        device = self.lockin
        device.set_double(self._osc(MFLI_CARRIER_OSC_IDX), self.frequency_hz)
        device.set_double(self._osc(self.params.demod_osc_idx), self.frequency_hz)
        device.sync()
        self._verify(output=self.output_on)

    def set_output(self, enabled: bool) -> None:
        self._validate()
        self.lockin.set_int(self._sigout("on"), 1 if enabled else 0)
        self.lockin.sync()
        self._verify(output=bool(enabled))

    def shutdown(self) -> dict[str, Any]:
        """关闭 Signal Output 1 并清零 MF-MOD 载波，逐项回读；失败抛出。"""
        self._validate()
        device = self.lockin
        record: dict[str, Any] = {}
        errors: list[str] = []
        try:
            device.set_int(self._sigout("on"), 0)
            device.sync()
            if int(device.get_int(self._sigout("on"))) != 0:
                raise RfSourceError("MFLI Out1 未回读为 OFF，禁止归零")
            device.set_int(self._mod("sidebands/0/enable"), 0)
            device.set_int(self._mod("sidebands/1/enable"), 0)
            device.set_double(self._mod("carrier/amplitude"), 0.0)
            device.set_int(self._mod("output"), 0)
            device.set_int(self._mod("enable"), 0)
            validate_safety_limit(self.safety_key, 0.0)
            device.set_double(self._sigout("offset"), 0.0)
            record["offset_commanded_v"] = 0.0
        except Exception as exc:
            errors.append(f"Y 射频收尾命令: {exc}")
        finally:
            for path, value in ((self._sigout("on"), 0), (self._mod("enable"), 0)):
                try:
                    device.set_int(path, value)
                except Exception as exc:
                    errors.append(f"最终关闭 {path}: {exc}")
            try:
                device.sync()
            except Exception as exc:
                errors.append(f"MFLI sync 异常: {exc}")

        checks = {
            "output": (lambda: bool(device.get_int(self._sigout("on"))), lambda value: value is False),
            "mod_enable": (lambda: int(device.get_int(self._mod("enable"))), lambda value: value == 0),
            "mod_output": (lambda: int(device.get_int(self._mod("output"))), lambda value: value == 0),
            "sideband0_enable": (lambda: int(device.get_int(self._mod("sidebands/0/enable"))), lambda value: value == 0),
            "sideband1_enable": (lambda: int(device.get_int(self._mod("sidebands/1/enable"))), lambda value: value == 0),
            "offset_v": (lambda: float(device.get_double(self._sigout("offset"))),
                         lambda value: math.isclose(value, 0.0, abs_tol=_ABS_TOL_V)),
            "carrier_amplitude_vpk": (lambda: float(device.get_double(self._mod("carrier/amplitude"))),
                                      lambda value: math.isclose(value, 0.0, abs_tol=1e-5)),
        }
        for field, (query, expected) in checks.items():
            try:
                value = record[field] = query()
                if not expected(value):
                    errors.append(f"{field} 回读不符: {value!r}")
            except Exception as exc:
                errors.append(f"{field} 回读异常: {exc}")
        if errors:
            record["errors"] = errors
            raise RfSourceError("MFLI Y 射频收尾未通过: " + "；".join(errors))
        self.output_on = False
        self.last_readback = record
        return record

    def snapshot(self) -> dict[str, Any]:
        return dict(self.describe(), readback=dict(self.last_readback))


def create_rf_source(params, mapping, devices, channels):
    """按参数选择射频源实现；未知取值直接拒绝。"""
    if params.rf_source == RF_SOURCE_MFLI:
        return MfliRfSource(params, mapping, devices)
    if params.rf_source == RF_SOURCE_DG4000:
        return Dg4000RfSource(params, devices, channels)
    raise ValueError(f"未知射频源：{params.rf_source}")
