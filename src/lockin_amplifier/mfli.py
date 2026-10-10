"""MFLI 驱动：连接校验、选件能力、节点单位与 Scope 设置。"""

from .base import LockinInstrument


class MFLIInstrument(LockinInstrument):
    """MFLI：通过网口或 USB 使用 Python Core API Level 6。"""

    model = "MFLI"
    demod_input_sources = {"voltage0": 0, "current0": 1, "aux_input0": 8, "aux_input1": 9}
    scope_input_sources = {"voltage0": 0, "aux_input0": 8, "aux_input1": 9}
    scope_fixed_length = None

    def __init__(self, host: str = "127.0.0.1", port: int = 8004,
                 api_level: int = 6, device_id: str = "", interface: str = "1GbE"):
        if not device_id:
            raise ValueError("MFLI 必须指定真实设备 ID")
        if api_level != 6:
            raise ValueError("MFLI 接入使用 API Level 6")
        super().__init__(host, port, api_level, device_id, interface)

    @staticmethod
    def _capabilities(options: list[str] | None = None) -> dict:
        installed = {item.upper().removeprefix("MF-") for item in (options or [])}
        demods = 4 if "MD" in installed else 1
        return {
            "model": "MFLI", "options": sorted(options or []),
            "demodulators": demods, "oscillators": demods, "voltage_inputs": 1,
            "signal_outputs": 1, "aux_outputs": 4,
            "max_frequency_hz": 5e6 if "F5M" in installed else 500e3,
            "scope_channels": 2 if "DIG" in installed else 1,
            "scope_max_samples": 5_000_000 if "DIG" in installed else 2048,
            "scope_max_rate": 60e6, "aux_input_feedback_verified": False,
        }

    def _read_clockbase(self) -> float:
        return float(self.get_double(f"/{self.device_id}/clockbase"))

    def _output_voltage_factor(self, output_idx: int, output_range: float | None = None) -> float:
        return 1.0

    def _scope_settings(self, config, divider: int) -> dict:
        settings = {"time": divider, "channel": (1 << len(config.sources)) - 1,
                    "length": config.length, "single": 0, "trigenable": 0}
        if self.capabilities["scope_channels"] > 1:
            settings["segments/enable"] = 0
        for channel, source in enumerate(config.sources):
            settings[f"channels/{channel}/inputselect"] = MFLIInstrument.scope_input_sources[source]
        return settings

    def _scope_module_settings(self, config) -> dict:
        return {}
