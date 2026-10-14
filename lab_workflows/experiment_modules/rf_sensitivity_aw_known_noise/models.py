from typing import ClassVar
"""固定噪声配置与同频控制版本多选。"""
from dataclasses import dataclass, replace
from pathlib import Path
import re
import math

from ...experiment_params import parameter
from ...control_sources import resolve_control_results_root, load_theory_control
from ..rf_sensitivity_direct_aw.models import RFSensitivityDirectAWParams


@dataclass(slots=True)
class RFSensitivityKnownNoiseParams(RFSensitivityDirectAWParams):
    schema_version: ClassVar[int] = 3
    system_settle_legacy_defaults: ClassVar[dict[str, float]] = {'RF_STEP_SETTLE_S': 0.3, 'TEMP_OFF_SETTLE_S': 0.5}
    removed_temperature_keys: ClassVar[tuple[str, ...]] = ()
    system_settle_s: float = parameter(default=0.5, external_name="SYSTEM_SETTLE_S", label="系统稳定时间", unit="s", group="basic", minimum=0, description="配置和温控门控共同稳定的采集前等待；组内每次换点或换态后重新等待。")
    arb_waveform_file: str = parameter(default="", external_name="ARB_WAVEFORM_FILE", label="当前控制文件", visible=False)
    control_waveform_files: list[str] = parameter(default_factory=list, external_name="CONTROL_WAVEFORM_FILES", label="控制波形版本（可多选）", group="basic")
    z_burst_phase_init: float = parameter(default=0.0, external_name="Z_BURST_PHASE_INIT", label="RF 正弦分量初始相位", unit="°", description="相位仅作用于 RF 分量，整体 Z 任意波 Burst 相位固定为 0°。")
    noise_band_starts_hz: list[float] = parameter(default_factory=lambda: [8000.0], external_name="NOISE_BAND_STARTS_HZ", label="噪声频带起点", unit="Hz", group="basic")
    noise_band_stops_hz: list[float] = parameter(default_factory=lambda: [10000.0], external_name="NOISE_BAND_STOPS_HZ", label="噪声频带终点", unit="Hz", group="basic")
    noise_band_psds: list[float] = parameter(default_factory=lambda: [1e-11], external_name="NOISE_BAND_PSDS_V2_PER_HZ", label="噪声设计 PSD", unit="V²/Hz")
    noise_repeat_freq_hz: float = parameter(default=10.0, external_name="NOISE_REPEAT_FREQ_HZ", label="噪声重复频率", unit="Hz", minimum=1e-6)
    noise_amplitude_vpp: float = parameter(default=0.0034, external_name="NOISE_AMPLITUDE_VPP", label="实际噪声幅度", unit="Vpp", group="basic", minimum=0.001, description="固定噪声的满量程幅度；实际 PSD 按缩放后的波形计算，不将设计 PSD 当作实际注入谱。")
    noise_seed: int = parameter(default=20260922, external_name="NOISE_SEED", label="噪声随机种子", minimum=0)

    @staticmethod
    def _options(item, root):
        if item.name != "control_waveform_files":
            return RFSensitivityDirectAWParams._options(item, root)
        base = resolve_control_results_root("oc_sens")
        choices = []
        for directory in sorted(base.glob("v*"), key=lambda p: p.name):
            if not re.fullmatch(r"v[1-9]\d*", directory.name):
                continue
            try:
                source = load_theory_control(base, directory.name)
                choices.append((source.waveform_path.relative_to(base).as_posix(), f"{directory.name} · RF {source.theory_rf_frequency_hz:g} Hz"))
            except (OSError, ValueError):
                continue
        return choices

    def validate_model(self):
        self.arb_waveform_file = self.control_waveform_files[0] if self.control_waveform_files else ""
        errors = super(RFSensitivityKnownNoiseParams, self).validate_model()
        if not self.control_waveform_files or len(set(self.control_waveform_files)) != len(self.control_waveform_files):
            errors.append("请选择至少一个不重复的控制波形版本")
        lengths = [len(values) for values in (self.noise_band_starts_hz, self.noise_band_stops_hz, self.noise_band_psds)]
        if not lengths[0] or len(set(lengths)) != 1:
            errors.append("噪声谱段三个数组须非空且等长")
        elif any(not (0 < start < stop and level > 0) for start, stop, level in self.noise_bands):
            errors.append("噪声谱段须满足 0 < 起点 < 终点，PSD > 0")
        return errors

    @property
    def noise_bands(self):
        return list(zip(self.noise_band_starts_hz, self.noise_band_stops_hz, self.noise_band_psds))

    def validate(self, project_root: Path | None = None):
        errors = super(RFSensitivityKnownNoiseParams, self).validate(project_root)
        if errors:
            return errors
        base = resolve_control_results_root("oc_sens").resolve()
        for relative in self.control_waveform_files:
            try:
                path = (base / relative).resolve()
                if base not in path.parents or path.name != "optimal_control_waveform.csv" or path.parent.name != "waveforms":
                    raise ValueError("控制文件必须为 oc_sens/vN/waveforms/optimal_control_waveform.csv")
                source = load_theory_control(base, path.parent.parent.name)
                if not math.isclose(source.theory_rf_frequency_hz, self.z_rf_freq, rel_tol=1e-6):
                    raise ValueError(f"{source.version} 的设计 RF 频率 {source.theory_rf_frequency_hz:g} Hz 与本轮 {self.z_rf_freq:g} Hz 不一致")
                single = replace(self, arb_waveform_file=relative, control_waveform_files=[relative])
                errors.extend(RFSensitivityDirectAWParams.validate(single, project_root))
            except (OSError, TypeError, ValueError) as exc:
                errors.append(str(exc))
        return errors
