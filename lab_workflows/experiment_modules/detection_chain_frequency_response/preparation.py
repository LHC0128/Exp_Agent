"""预检、设备连接、参考时钟、初始输出与温控准备。"""

from __future__ import annotations

import time

from tec_controller import TECInstrument

from lab_workflows.devices import create_lockin
from lab_workflows.lockin_compatibility import require_lockin_compatibility
from lab_workflows.steps import initialize_lockin_inputs

from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...experiment_runtime import check_cancelled
from ...steps import (
    DeviceSession,
    configure_temperature_control,
    connect_signal_generator_routes,
    set_temperature_switch,
    synchronize_connected_clocks,
)
from .models import DetectionChainFrequencyResponseParams

EXPERIMENT_TYPE = "Detection_Chain_Frequency_Response"


class _RuntimeCancellation:
    """将 GUI 取消请求传给温度稳定等待。"""

    def raise_if_cancelled(self) -> None:
        check_cancelled()


def wait(seconds: float) -> None:
    """可取消的等待；分步会话与扫描循环共用。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, deadline - time.monotonic()))


def preflight(params: DetectionChainFrequencyResponseParams, root):
    """完整预检：参数、锁相兼容、安全限值与映射一致；返回 (params, mapping, limits)。"""
    errors = params.validate(root)
    if errors:
        raise ValueError("；".join(errors))
    mapping, limits = load_mapping(root), load_safety_limits(root)
    require_lockin_compatibility(
        "detection-chain-frequency-response", params.to_external(), mapping=mapping,
    )
    for key, value in (
        ("Pump_laser_power", params.pump_laser_power_v),
        ("Probe_laser_power", params.probe_laser_power_v),
        ("temperature", params.temperature_c),
        ("Probe_AOM_Carrier", params.probe_aom_carrier_amplitude_vpp),
        ("Probe_AOM_AM", params.probe_aom_am_amplitude_vpp / 2),
        ("Probe_AOM_AM", -params.probe_aom_am_amplitude_vpp / 2),
    ):
        validate_safety_limit(key, value, limits)
    if params.hf2_demod_idx != int(mapping["lockin_r"]["demod_idx"]):
        raise ValueError("HF2_DEMOD_IDX 与 lockin_r 映射不一致")
    return params, mapping, limits


def connect_instruments(mapping):
    """按映射连接设备；部分失败时释放已建立的连接。

    返回 (devices, connection, channels)：channels 为逻辑通道到仪器物理通道的映射。
    """
    session = DeviceSession()
    try:
        tec_cfg = mapping["temperature"]
        tec = session.connect_optional(
            "tec", str(tec_cfg["resource"]),
            lambda: TECInstrument(port=tec_cfg["resource"]),
            device_label="TEC103",
        )
        aom, aom_channels = connect_signal_generator_routes(
            session, "probe_aom", {
                "carrier": ("Probe_AOM_Carrier", mapping["Probe_AOM_Carrier"]),
                "am": ("Probe_AOM_AM", mapping["Probe_AOM_AM"]),
            }, logical_channels={"carrier": 1, "am": 2},
        )
        laser, laser_channels = connect_signal_generator_routes(
            session, "laser", {
                "pump": ("Pump_laser_power", mapping["Pump_laser_power"]),
                "probe": ("Probe_laser_power", mapping["Probe_laser_power"]),
            }, logical_channels={"pump": 1, "probe": 2},
        )
        temp_switch, temp_channels = connect_signal_generator_routes(
            session, "temp_switch", {"temp": ("Temp_Switch", mapping["Temp_Switch"])},
            logical_channels={"temp": 2},
        )
        channels = {**aom_channels, **laser_channels, "temp_switch": temp_channels["temp"]}
        hf2_cfg = mapping["lockin_r"]
        hf2 = session.connect(
            "hf2",
            f"hf2://{hf2_cfg['device_id']}@{hf2_cfg.get('host', '127.0.0.1')}:{hf2_cfg.get('port', 8005)}",
            lambda: create_lockin(hf2_cfg),
        )
        initialize_lockin_inputs(hf2)
    except BaseException:
        session.cleanup_connection_failure()
        raise
    devices = {"tec": tec, "aom": aom, "laser": laser, "temp_switch": temp_switch, "hf2": hf2}
    return devices, session, channels


def synchronize_devices(devices, mapping):
    """按设备库配置同步参考时钟并严格回读。"""
    clock_devices = {
        "probe_aom": devices["aom"],
        "laser": devices["laser"],
        "hf2": devices["hf2"],
        "temp_switch": devices["temp_switch"],
    }
    clock_keys = {
        "probe_aom": "Probe_AOM_Carrier",
        "laser": "Pump_laser_power",
        "hf2": "lockin_r",
        "temp_switch": "Temp_Switch",
    }
    return synchronize_connected_clocks(clock_devices, mapping, clock_keys)


def configure_initial_outputs(devices, channels, params, mapping, connection):
    """设置 Pump/Probe 光功率、温度开关并确认温控方式。

    返回 (temperature_control 快照, TEC 连接错误说明)。
    """
    laser = devices["laser"]
    laser.setup_dc(params.pump_laser_power_v, channel=channels["pump"])
    laser.set_output(True, channel=channels["pump"])
    laser.setup_dc(params.probe_laser_power_v, channel=channels["probe"])
    laser.set_output(True, channel=channels["probe"])
    set_temperature_switch(devices["temp_switch"], True, channel=channels["temp_switch"])
    temp = configure_temperature_control(
        devices["tec"], params.temperature_c,
        channel=int(mapping["temperature"]["channel"]),

    )
    return temp.to_dict(), connection.optional_connection_errors().get("tec")
