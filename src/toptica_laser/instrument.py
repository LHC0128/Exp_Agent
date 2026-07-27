"""基于 TOPTICA Laser SDK 低层客户端的 DLC pro 安全驱动。"""

from __future__ import annotations

import math
from typing import Any, Callable

from toptica.lasersdk.client import Client, NetworkConnection


class DLCProInstrument:
    """DLC pro Laser 1 的受限控制接口。

    驱动仅开放实验需要的写操作；扫描频率、电流限值和维修参数保持只读。
    """

    CURRENT_MIN_MA = 200.0
    CURRENT_MAX_MA = 390.0
    TEMPERATURE_MIN_C = 19.0
    TEMPERATURE_MAX_C = 21.0
    PZT_MIN_V = 0.0
    PZT_MAX_V = 140.0
    SCAN_AMPLITUDE_MIN_VPP = 0.0
    SCAN_AMPLITUDE_MAX_VPP = 50.0

    def __init__(
        self,
        host: str,
        *,
        laser_channel: int = 1,
        command_port: int = 1998,
        monitoring_port: int = 1999,
        timeout: float = 5.0,
        connection_factory: Callable[..., Any] = NetworkConnection,
        client_factory: Callable[[Any], Any] = Client,
    ) -> None:
        if not str(host).strip():
            raise ValueError("DLC pro 主机地址不能为空")
        if int(laser_channel) < 1:
            raise ValueError("DLC pro 激光通道必须为正整数")
        self.host = str(host).strip()
        self.laser_channel = int(laser_channel)
        self.command_port = int(command_port)
        self.monitoring_port = int(monitoring_port)
        self.timeout = float(timeout)
        self._connection_factory = connection_factory
        self._client_factory = client_factory
        self._client: Any | None = None

    @property
    def connected(self) -> bool:
        return self._client is not None

    @property
    def prefix(self) -> str:
        return f"laser{self.laser_channel}"

    def connect(self) -> None:
        if self.connected:
            return
        connection = self._connection_factory(
            self.host,
            command_line_port=self.command_port,
            monitoring_line_port=self.monitoring_port,
            timeout=self.timeout,
        )
        client = self._client_factory(connection)
        client.open()
        self._client = client

    def disconnect(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            client.close()

    def __enter__(self) -> DLCProInstrument:
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.disconnect()

    def _require_client(self):
        if self._client is None:
            raise RuntimeError("DLC pro 尚未连接")
        return self._client

    def _get(self, parameter: str) -> Any:
        return self._require_client().get(parameter)

    def _set(self, parameter: str, value: Any) -> None:
        self._require_client().set(parameter, value)

    def _laser_parameter(self, suffix: str) -> str:
        return f"{self.prefix}:{suffix}"

    @staticmethod
    def _finite(name: str, value: Any) -> float:
        try:
            converted = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} 必须是数值") from exc
        if not math.isfinite(converted):
            raise ValueError(f"{name} 必须是有限数值")
        return converted

    @classmethod
    def _bounded(
        cls,
        name: str,
        value: Any,
        minimum: float,
        maximum: float,
    ) -> float:
        converted = cls._finite(name, value)
        if not minimum <= converted <= maximum:
            raise ValueError(
                f"{name} {converted:g} 超出安全范围 {minimum:g}～{maximum:g}"
            )
        return converted

    @staticmethod
    def _verify_numeric(
        name: str,
        requested: float,
        actual: Any,
        *,
        tolerance: float = 1e-3,
    ) -> float:
        readback = DLCProInstrument._finite(f"{name}回读值", actual)
        if not math.isclose(readback, requested, rel_tol=0.0, abs_tol=tolerance):
            raise RuntimeError(
                f"{name}回读超差：请求 {requested:g}，回读 {readback:g}"
            )
        return readback

    @classmethod
    def _validate_scan_envelope(
        cls,
        pzt_voltage_v: Any,
        scan_amplitude_vpp: Any,
    ) -> tuple[float, float]:
        pzt = cls._bounded(
            "PZT 电压",
            pzt_voltage_v,
            cls.PZT_MIN_V,
            cls.PZT_MAX_V,
        )
        amplitude = cls._bounded(
            "扫描幅度",
            scan_amplitude_vpp,
            cls.SCAN_AMPLITUDE_MIN_VPP,
            cls.SCAN_AMPLITUDE_MAX_VPP,
        )
        low = pzt - amplitude / 2.0
        high = pzt + amplitude / 2.0
        if low < cls.PZT_MIN_V or high > cls.PZT_MAX_V:
            raise ValueError(
                "扫描包络越界："
                f"{pzt:g} V ± {amplitude:g} Vpp / 2 = "
                f"{low:g}～{high:g} V，允许范围为 "
                f"{cls.PZT_MIN_V:g}～{cls.PZT_MAX_V:g} V"
            )
        return pzt, amplitude

    def get_parameter(self, parameter: str) -> Any:
        """只读查询任意参数，用于诊断；不提供对应的通用写接口。"""
        return self._get(str(parameter))

    def get_controller_serial(self) -> str:
        return str(self._get("serial-number"))

    def get_system_type(self) -> str:
        return str(self._get("system-type"))

    def get_system_label(self) -> str:
        return str(self._get("system-label"))

    def get_firmware_version(self) -> str:
        return str(self._get("fw-ver"))

    def get_system_health_code(self) -> int:
        return int(self._get("system-health"))

    def get_system_health(self) -> str:
        return str(self._get("system-health-txt"))

    def get_interlock_open(self) -> bool:
        return bool(self._get("interlock-open"))

    def get_front_key_locked(self) -> bool:
        return bool(self._get("frontkey-locked"))

    def get_emission(self) -> bool:
        return bool(self._get("emission"))

    def get_laser_type(self) -> str:
        return str(self._get(self._laser_parameter("type")))

    def get_laser_product_name(self) -> str:
        return str(self._get(self._laser_parameter("product-name")))

    def get_laser_enabled(self) -> bool:
        return bool(self._get(self._laser_parameter("enabled")))

    def get_laser_health_code(self) -> int:
        return int(self._get(self._laser_parameter("health")))

    def get_laser_health(self) -> str:
        return str(self._get(self._laser_parameter("health-txt")))

    def get_laser_emission(self) -> bool:
        return bool(self._get(self._laser_parameter("emission")))

    def get_laser_head_model(self) -> str:
        return str(self._get(self._laser_parameter("dl:model")))

    def get_laser_head_serial(self) -> str:
        return str(self._get(self._laser_parameter("dl:serial-number")))

    def get_laser_current_set_ma(self) -> float:
        return self._finite(
            "激光电流设定值",
            self._get(self._laser_parameter("dl:cc:current-set")),
        )

    def get_laser_current_actual_ma(self) -> float:
        return self._finite(
            "激光电流实际值",
            self._get(self._laser_parameter("dl:cc:current-act")),
        )

    def get_laser_current_clip_ma(self) -> float:
        return self._finite(
            "激光电流 clip",
            self._get(self._laser_parameter("dl:cc:current-clip")),
        )

    def get_laser_current_clip_limit_ma(self) -> float:
        return self._finite(
            "激光电流 clip limit",
            self._get(self._laser_parameter("dl:cc:current-clip-limit")),
        )

    def get_laser_temperature_set_c(self) -> float:
        return self._finite(
            "激光温度设定值",
            self._get(self._laser_parameter("dl:tc:temp-set")),
        )

    def get_laser_temperature_actual_c(self) -> float:
        return self._finite(
            "激光温度实际值",
            self._get(self._laser_parameter("dl:tc:temp-act")),
        )

    def get_pzt_voltage_v(self) -> float:
        """返回 Scan Offset，即用户定义的 PZT 绝对电压。"""
        return self._finite(
            "PZT 电压",
            self._get(self._laser_parameter("scan:offset")),
        )

    def get_pzt_voltage_actual_v(self) -> float:
        return self._finite(
            "PZT 实际电压",
            self._get(self._laser_parameter("dl:pc:voltage-act")),
        )

    def get_scan_amplitude_vpp(self) -> float:
        return self._finite(
            "扫描幅度",
            self._get(self._laser_parameter("scan:amplitude")),
        )

    def get_scan_frequency_hz(self) -> float:
        return self._finite(
            "扫描频率",
            self._get(self._laser_parameter("scan:frequency")),
        )

    def get_scan_enabled(self) -> bool:
        return bool(self._get(self._laser_parameter("scan:enabled")))

    def get_scan_unit(self) -> str:
        return str(self._get(self._laser_parameter("scan:unit")))

    def get_scan_output_channel(self) -> int:
        return int(self._get(self._laser_parameter("scan:output-channel")))

    def set_laser_current_ma(self, value: Any) -> float:
        requested = self._bounded(
            "激光电流",
            value,
            self.CURRENT_MIN_MA,
            self.CURRENT_MAX_MA,
        )
        current_clip = self.get_laser_current_clip_ma()
        if not math.isfinite(current_clip):
            raise ValueError("设备 current-clip 缺失或无效")
        if requested > current_clip:
            raise ValueError(
                f"激光电流 {requested:g} mA 超过设备 current-clip "
                f"{current_clip:g} mA"
            )
        parameter = self._laser_parameter("dl:cc:current-set")
        self._set(parameter, requested)
        return self._verify_numeric(
            "激光电流",
            requested,
            self._get(parameter),
            tolerance=0.01,
        )

    def set_laser_temperature_c(self, value: Any) -> float:
        requested = self._bounded(
            "激光温度",
            value,
            self.TEMPERATURE_MIN_C,
            self.TEMPERATURE_MAX_C,
        )
        parameter = self._laser_parameter("dl:tc:temp-set")
        self._set(parameter, requested)
        return self._verify_numeric(
            "激光温度",
            requested,
            self._get(parameter),
            tolerance=0.01,
        )

    def set_pzt_voltage_v(self, value: Any) -> float:
        requested, _ = self._validate_scan_envelope(
            value,
            self.get_scan_amplitude_vpp(),
        )
        parameter = self._laser_parameter("scan:offset")
        self._set(parameter, requested)
        return self._verify_numeric(
            "PZT 电压",
            requested,
            self._get(parameter),
            tolerance=0.01,
        )

    def set_scan_amplitude_vpp(self, value: Any) -> float:
        _, requested = self._validate_scan_envelope(
            self.get_pzt_voltage_v(),
            value,
        )
        parameter = self._laser_parameter("scan:amplitude")
        self._set(parameter, requested)
        return self._verify_numeric(
            "扫描幅度",
            requested,
            self._get(parameter),
            tolerance=0.01,
        )

    def set_scan_enabled(self, enabled: bool) -> bool:
        if not isinstance(enabled, bool):
            raise TypeError("扫描启停值必须为布尔值")
        parameter = self._laser_parameter("scan:enabled")
        self._set(parameter, enabled)
        readback = bool(self._get(parameter))
        if readback is not enabled:
            raise RuntimeError(
                f"扫描状态回读不一致：请求 {enabled}，回读 {readback}"
            )
        return readback

    def set_emission(
        self,
        enabled: bool,
        *,
        remote_enable_allowed: bool = False,
    ) -> bool:
        """设置 Emission；开启必须显式解锁并通过硬件前置检查。"""
        if not isinstance(enabled, bool):
            raise TypeError("Emission 状态必须为布尔值")
        if enabled:
            if not remote_enable_allowed:
                raise PermissionError("远程 Emission ON 门禁未开放")
            if self.get_system_health_code() != 0:
                raise RuntimeError("系统健康状态异常，拒绝开启 Emission")
            if self.get_laser_health_code() != 0:
                raise RuntimeError("激光头健康状态异常，拒绝开启 Emission")
            if self.get_interlock_open():
                raise RuntimeError("联锁回路断开，拒绝开启 Emission")
            if not self.get_laser_enabled():
                raise RuntimeError("Laser Enabled 为 OFF，拒绝开启 Emission")

        self._set("emission-button-enabled", enabled)
        readback = self.get_emission()
        if readback is not enabled:
            raise RuntimeError(
                f"Emission 回读不一致：请求 {enabled}，回读 {readback}"
            )
        return readback
