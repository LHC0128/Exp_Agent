"""新实验唯一的执行清单、分析产物、参数展示和失效规则。"""
from dataclasses import asdict

from ...workflow_stages import DependencyRules, Stage
from . import run_format
from .models import RfPhaseSensitivityParams

RUN_ID, FINISH_ID = "A03", "A04"
ANALYSIS_KEYS: tuple[str, ...] = ()
MEASUREMENT_KEYS = tuple(key for key in RfPhaseSensitivityParams.external_names() if key != "RUN_TAG")
ACQUISITION = (
    Stage("A01", "参数与映射预检", "准备", (), ("RUN_TAG",),
          quality_gates=("固定 Y 向 5 kHz / 50 mVpp；核对驱动能力、映射、电平、电流与实际脉冲功耗。",)),
    Stage("A02", "连接设备并同步公共参考时钟", "准备", ("A01",), (),
          quality_gates=("所有参与参考时钟的设备写后回读；DHO924 验证接线所需通道和面板状态。",)),
    Stage(RUN_ID, "同次标定、基带扫频与噪声", "测量", ("A02",), MEASUREMENT_KEYS,
          artifacts=("raw/",), quality_gates=(
              "整周扫描拟合最大切线；按 CH4 相对 CH3 实测角度、正交分量和对称局部扫拟合静态斜率。",
              "固定 1° 原生 PM 后才回调载波中心；扫频只改基带频率，不复用不合格的电学标定。",
              "无调制但保留载波，前后核查物理相位、载波幅值和同一锁相配置。关闭温控窗口只读不写。")),
    Stage(FINISH_ID, "安全结束会话", "结束", (), (), quality_gates=(
        "先关 1200B，再关 Y/X/Z 输出并归零、恢复温控、主场与示波器；共同触发保持。",)),
)
ANALYSIS = (
    Stage("B01", "相位频响与输入等效噪声分析", "独立分析", (), (),
          artifacts=(run_format.SUMMARY, run_format.TRANSFER_TABLE, run_format.NOISE_TABLE,
                     run_format.TRANSFER_PLOT, run_format.NOISE_PLOT),
          quality_gates=("核对同轮低频标度和锚点复测；仅在相邻合格频点覆盖的连续频段内折算 Welch 噪声。"
                         "质量不足时只导出可用诊断，不填补被拒频点或外推。",)),
)
_rules = DependencyRules(finish_id=FINISH_ID)
invalidated_stages = _rules.invalidated_stages
invalidate = _rules.invalidate
parameter_impacts = _rules.parameter_impacts
validate_selection = _rules.validate_selection


def definition():
    def public(stage):
        shown = stage.parameters if stage.display_parameters is None else stage.display_parameters
        return asdict(stage) | {"display_parameters": list(shown)}
    return dict(run_step_id=RUN_ID, finish_step_id=FINISH_ID,
                acquisition=[public(s) for s in ACQUISITION], analysis=[public(s) for s in ANALYSIS],
                parameter_impacts=dict(acquisition=parameter_impacts(ACQUISITION), analysis=parameter_impacts(ANALYSIS)),
                diagram_url=f"/api/experiments/{run_format.EXPERIMENT_ID}/workflow/diagram")
