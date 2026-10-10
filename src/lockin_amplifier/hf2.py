"""HF2 驱动：连接默认值、硬件能力、节点单位与 Scope 设置。"""

from .base import LockinInstrument


class HF2Instrument(LockinInstrument):
    """HF2：保留原构造参数和 API Level 1。"""

    model = "HF2"
    demod_input_sources = {"voltage0": 0, "voltage1": 1}
    scope_input_sources = {"voltage0": 0, "voltage1": 1}
    scope_fixed_length = 2048

    def __init__(self, host: str = "127.0.0.1", port: int = 8005,
                 api_level: int = 1, device_id: str = "dev18246", interface: str = "PCIe"):
        super().__init__(host, port, api_level, device_id, interface)

    @staticmethod
    def _capabilities(options: list[str] | None = None) -> dict:
        return {
            "model": "HF2", "options": sorted(options or []),
            "demodulators": 6, "oscillators": 6, "voltage_inputs": 2,
            "signal_outputs": 2, "aux_outputs": 4, "max_frequency_hz": 50e6,
            "scope_channels": 1, "scope_max_samples": 2048, "scope_max_rate": 210e6,
            "aux_input_feedback_verified": False,
        }

    def _read_clockbase(self) -> float:
        return float(self.get_int(f"/{self.device_id}/clockbase"))

    def _output_voltage_factor(self, output_idx: int, output_range: float | None = None) -> float:
        return self.get_double(f"{self.sig_out_path(output_idx)}/range") if output_range is None else output_range

    def _scope_settings(self, config, divider: int) -> dict:
        return {"time": divider, "channel": HF2Instrument.scope_input_sources[config.sources[0]],
                "trigchannel": -2}

    def _scope_module_settings(self, config) -> dict:
        input_idx = HF2Instrument.scope_input_sources[config.sources[0]]
        return {"externalscaling": self.get_double(f"{self.sig_in_path(input_idx)}/range")}
