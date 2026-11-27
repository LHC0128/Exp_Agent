"""Mx 标准射频相位灵敏度测量的注册定义；导入不连接设备。"""
from ...experiments.contracts import ExperimentDefinition, RunHistory
from ...experiments.typed import TypedWorkflowAdapter
from . import run_format
from .models import MxStandardRfPhaseSensitivityParams
from .preparation import preflight


class Adapter(TypedWorkflowAdapter):
    """预检与默认值保存都走本实验的无硬件预检，避免只校验字段不含映射与限值。"""

    def preflight(self, values):
        try:
            preflight(self._resolved(values), self.root)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            return [str(exc)]
        return []

    def save_defaults(self, values):
        params = self._resolved(values)
        preflight(params, self.root)
        params.save_yaml(self.defaults_path)


MODULE = "lab_workflows.experiment_modules.mx_standard_rf_phase_sensitivity"
ADAPTER = Adapter(run_format.EXPERIMENT_ID, run_format.DATA_TYPE, MxStandardRfPhaseSensitivityParams,
                  f"{MODULE}.workflow", f"{MODULE}.analysis")
DEFINITION = ExperimentDefinition(
    id=run_format.EXPERIMENT_ID, title="Mx 标准射频相位灵敏度测量",
    category="measurement", family="z-chain", variant="mx-standard-no-pulse",
    data_type=run_format.DATA_TYPE,
    description=(
        "标准 Mx（不打脉冲）构型：GS200 常量主场 + MFLI Signal Output 1 的 MF-MOD 连续 5 kHz 载波"
        "+ Z 双脉冲链关闭并回读 + Demod 4 读出。"
        "A02 按主磁场标定式反算 GS200 电流并回读，随后同步时钟、开温控、写 X 偏置与共同触发、配 Demod 4 "
        "窄带静态读出并启动连续载波（PM 边带关闭）；"
        "A03 先等原子响应稳定并采一段丢弃做预热，再按整周网格逐点采 XY、末尾回采首点量漂移并做圆拟合；"
        "A04 按圆模型取 |cos phi| 最大的相图点做 ±5° 细扫，用实测相位角回归 dY/dphi 并与 R|cos phi| 互校；"
        "A05 只对本段与 A06 把 Demod 4 切到高带宽档（DEMOD_DYNAMIC_TC / DEMOD_DYNAMIC_RATE），"
        "开启 MF-MOD 2° 峰值相干边带 PM 并按 PM_FREQUENCIES_HZ 逐点采 XY；"
        "A06 关闭边带、保持宽带档，按独立温控窗口分段采集无调制噪声，每帧体检。"
        "B01 输出圆拟合与逐段切线斜率、三条相位噪声路径（theta、y/K、切向投影）、A05 按工频与 5 kHz 载波泄漏模型"
        "拟合并以 √Lorentzian H0 归一化的频响，以及 A06 折算相位灵敏度（µrad/√Hz，含全频段 √Lorentzian 回补）；"
        "绘图与 Mx 脉冲射频相位灵敏度实验共用同一套样式与同一张频率表，便于直接对比。"
        "结束阶段关闭 PM 边带并清零载波、Z/X 归零回读、恢复温控与 GS200 原状态并断开设备；共同触发保持。"
        "A03–A06 依赖同一轮参数快照，重复运行会新建目录，不与旧帧拼接。"),
    documentation="docs/experiments/mx_standard_rf_phase_sensitivity.md",
    required_devices=("MFLI", "GS200", "DG4000/DG900", "TEC103（可选设温）"),
    required_mapping_keys=run_format.REQUIRED_MAPPING_KEYS,
    execution_mode="typed_workflow", schema_version=MxStandardRfPhaseSensitivityParams.schema_version,
    acquisition_program="experiments/MxStandardRfPhaseSensitivity.py",
    analysis_program="experiments/MxStandardRfPhaseSensitivity_plot.py",
    wiring_notes=(
        "MFLI Signal Output 1 经串联大电阻接 Y 方向射频线圈，输出 MF-MOD 载波；上下边带只在 A05 频响扫频期间"
        "按 2° 峰值开启，A06 前关闭并回读。"
        "MFLI Signal Input 1 接探测器，读出固定 Demod 4 / Osc 4，载波由 Demod 1 / Osc 1（Osc 1 索引 0）产生。",
        "Z_magnetic_field 通道与 1200B 功放属于 Z 双脉冲链，本实验保持输出 OFF 并回读，不参与激励；"
        "Time_sequence_2 提供 5 kHz 方波共同触发并全程保持输出与 SYNC。",
        "GS200 走 main_magnetic_field 映射（电流模式），电流只由主磁场标定式反算；"
        "温度开关 Temp_Switch 关 0 V ON、开 5 V ON，TEC 只下发界面目标温度。"),
    safety_notes=(
        "预检按 main_magnetic_field ±20 mA、MFLI Signal Output 1 ±10 V 包络（含 A05 的 2° 峰值边带）、"
        "X/共同触发/温控电平逐项校验；反算电流写入前再校验一次，写入后按 1% 回读核对。",
        "每帧先关温控并在内存采集，恢复 5 V DC + ON 并等待后才写盘；含 100 Hz 以上单音超过直流 20% 的帧直接作废"
        "（宽带档忽略 5 kHz 载波泄漏的已知寄生频点）。",
        "结束阶段先关闭 MF-MOD 上下边带，再关闭并清零 Out1 与 MF-MOD、Z/X 归零回读、恢复温控与 GS200 原状态、"
        "停解调传输并断开设备；收尾失败不记为安全完成，也不覆盖原始采集错误。"),
    run_history=RunHistory(run_format=run_format, historical_parameters=run_format.recorded_parameters),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults, preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive, experiment_runner=ADAPTER.run, analysis_runner=ADAPTER.analyze,
)
