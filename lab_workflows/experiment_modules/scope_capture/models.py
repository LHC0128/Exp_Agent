"""单通道示波器采集参数。"""

from dataclasses import dataclass
import math
import re

from ...experiment_params import ExperimentParams, parameter


@dataclass(slots=True)
class ScopeCaptureParams(ExperimentParams):
    schema_version = 2

    mode: str = parameter(default="time", external_name="mode", label="模式",
                          group="basic", options=(("time", "时间"), ("frequency", "频域")))
    sampling_rate_sa_s: float = parameter(
        default=500000.0, external_name="sampling_rate_sa_s", label="采样率",
        unit="Sa/s", group="basic", minimum=1.0)
    duration_s: float = parameter(default=1.0, external_name="duration_s",
                                  label="采集时间", unit="s", group="basic", minimum=1e-9)
    channel: int = parameter(default=1, external_name="channel", label="采集通道",
                             group="basic", options=tuple((i, f"C{i}") for i in range(1, 5)))
    trigger_channel: int = parameter(
        default=None, external_name="trigger_channel", label="触发通道", group="basic",
        options=tuple((i, f"C{i}") for i in range(1, 5)),
    )
    trigger_mode: str = parameter(
        default="AUTO", external_name="trigger_mode", label="触发模式", group="basic",
        options=(("AUTO", "Auto"), ("NORMal", "Normal"), ("SINGle", "Single")),
    )
    trigger_slope: str = parameter(
        default="RISing", external_name="trigger_slope", label="触发边沿", group="basic",
        options=(("RISing", "Rising"), ("FALLing", "Falling")),
    )
    trigger_level_v: float = parameter(
        default=0.0, external_name="trigger_level_v", label="触发电平", unit="V",
        group="basic",
    )
    vertical_scale_v_div: float = parameter(
        default=1.0, external_name="vertical_scale_v_div", label="垂直档位",
        unit="V/div", group="basic", minimum=1e-6,
    )
    vertical_offset_v: float = parameter(
        default=0.0, external_name="vertical_offset_v", label="垂直偏置", unit="V",
        group="basic",
    )
    disable_temperature_control: bool = parameter(
        default=True,
        external_name="disable_temperature_control",
        label="关闭温控采集",
        group="basic",
        description="采集前关闭温度开关，波形读完后立即恢复为开启状态。",
    )
    temperature_switch_off_settle_s: float = parameter(
        default=0.1,
        external_name="temperature_switch_off_settle_s",
        label="温控关闭后等待",
        unit="s",
        group="advanced",
        minimum=0.0,
    )
    temperature_switch_on_settle_s: float = parameter(
        default=1.0,
        external_name="temperature_switch_on_settle_s",
        label="温控恢复后等待",
        unit="s",
        group="advanced",
        minimum=0.0,
    )
    run_tag: str = parameter(default="scope", external_name="run_tag",
                             label="运行标签", group="basic")
    psd_window: str = parameter(default="hann", external_name="psd_window",
                                label="PSD 窗", visible=False, options=(("hann", "Hann"),))
    psd_nperseg: int = parameter(default=10000, external_name="psd_nperseg",
                                label="PSD 最大分段点数", visible=False, minimum=10000, maximum=10000)
    psd_detrend: str = parameter(default="constant", external_name="psd_detrend",
                                 label="PSD 去直流", visible=False, options=(("constant", "去直流"),))

    def __post_init__(self) -> None:
        if self.trigger_channel is None:
            self.trigger_channel = self.channel

    def validate_model(self) -> list[str]:
        errors = []
        for name in ("vertical_scale_v_div", "vertical_offset_v", "trigger_level_v"):
            if not math.isfinite(getattr(self, name)):
                errors.append(f"{name} 必须为有限数")
        if not re.fullmatch(r"[\w-]{1,64}", self.run_tag):
            errors.append("运行标签须为 1 到 64 个字母、数字、汉字、下划线或连字符")
        points = self.sampling_rate_sa_s * self.duration_s
        if not math.isfinite(points) or not 2 <= points <= 50_000_000:
            errors.append("第一版单次记录须为 2 到 50,000,000 点，请调整采样率或采集时间")
        return errors

    @classmethod
    def migrate_external(cls, values: dict[str, object], schema_version: int) -> dict[str, object]:
        migrated = ExperimentParams.migrate_external.__func__(cls, values, schema_version)
        if schema_version < 2:
            migrated.setdefault("trigger_channel", migrated.get("channel", 1))
            migrated.setdefault("trigger_mode", "AUTO")
            migrated.setdefault("trigger_slope", "RISing")
            migrated.setdefault("trigger_level_v", 0.0)
            migrated.setdefault("vertical_scale_v_div", 1.0)
            migrated.setdefault("vertical_offset_v", 0.0)
        return migrated
