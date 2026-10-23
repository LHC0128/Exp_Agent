"""每个控制 × Scale 原子执行校相、RF 相位设置与测量，外层只共享固定准备。"""
from dataclasses import replace
from .run_format import POINT_CALIBRATION_STAGES
from ..rf_sensitivity_direct_aw import stages as shared
from ..rf_sensitivity_direct_aw.stages import (
    Stage, FINISH_ID, initial_states, invalidated_stages, invalidate, validate_selection, parameter_impacts)

SCAN_KEYS = ("CONTROL_SCALES", "CONTROL_SCALE_START", "CONTROL_SCALE_STOP", "CONTROL_SCALE_POINTS",
    "CONTROL_SCALE_FACTORS", "INCLUDE_NOISE", "RUN_TAG", "RESUME_RUN_ID", "RF_AMP_START", "RF_AMP_STOP",
    "RF_AMP_POINTS", "Z_V_TO_NT", "NOISE_N_AVG", "NOISE_DURATION", "NOISE_RATE",
    "DEMOD3_NOISE_TC", "DEMOD3_NOISE_ORDER", "BW_FREQ_START", "BW_FREQ_STOP", "BW_FREQ_POINTS")
POINT_PREPARATION = tuple(stage for stage in shared.SINGLE_ACQUISITION if stage.id in POINT_CALIBRATION_STAGES)
POINT_PARAMETERS = tuple(dict.fromkeys("CONTROL_WAVEFORM_FILES" if key == "ARB_WAVEFORM_FILE" else key
    for key in (*SCAN_KEYS, *(key for stage in POINT_PREPARATION for key in stage.parameters))))
POINT_DISPLAY = tuple(dict.fromkeys("CONTROL_WAVEFORM_FILES" if key == "ARB_WAVEFORM_FILE" else key
    for key in (*POINT_PARAMETERS, *shared.SCAN,
        *(key for stage in POINT_PREPARATION for key in (stage.parameters if stage.display_parameters is None else stage.display_parameters)))))
ACQUISITION = tuple(replace(stage, artifacts=(),
                    parameters=stage.parameters + (("TEC_EXTERNAL_CONTROL",) if stage.id in ("A02", "A05") else ())) for stage in shared.SINGLE_ACQUISITION
                    if stage.id in ("A01", "A02", "A03", "A04", "A05", "A06", "A07")) + (
    Stage("A13", "逐控制 × Scale：校相、RF 相位与灵敏度测量", "逐点完整测量与结束", ("A07",),
        POINT_PARAMETERS, ("raw/point_manifest.json",), display_parameters=POINT_DISPLAY,
        quality_gates=("每点完成 Demod0、当前倍率 XY、RF 相位扫描或手动设置及 RF 解调器校相，失败停止后继测量",
                       "手动 RF 相位为本实验共用设定；其余校相不跨点继承，所有采集恢复温控后保存",)),
    Stage("A15", "响应恢复与本轮安全收尾", "逐点完整测量与结束", ("A13",)),
    Stage(FINISH_ID, "安全收尾 / 结束会话", "逐点完整测量与结束", ()),
)
ANALYSIS = (
    Stage("B01", "校验已封存的控制 × Scale 原始数据", "独立离线分析", (),
          quality_gates=("实验身份及完整清单有效；v2 逐点校相和扫描／手动 RF 相位一致，v1 按历史标称校相规则校验",)),
    Stage("B02", "色散斜率/线宽与洛伦兹中心频率拟合", "独立离线分析", ("B01",),
          artifacts=("response_fits.json", "fit_source_data.npz")),
    Stage("B03", "各 Scale 噪声质量检查与 Welch PSD", "独立离线分析", ("B02",),
          ("NOISE_NPERSEG", "NOISE_OUTLIER_POLICY", "SENSITIVITY_BAND_MIN_HZ", "SENSITIVITY_BAND_MAX_HZ"),
          ("psd_index.json", "psd_data.npz", "noise_record_quality.csv", "noise_rejection_summary.json")),
    Stage("B04", "斜率、色散线宽、中心频率与灵敏度比较", "独立离线分析", ("B03",),
          ("SENSITIVITY_BAND_MIN_HZ", "SENSITIVITY_BAND_MAX_HZ"),
          ("analysis.json", "analysis.yaml", "metrics.csv", "sensitivity.npz",
           "full_analysis.png", "full_analysis.pdf", "response_curves.png", "response_curves.pdf",
           "frequency_curves.png", "frequency_curves.pdf", "sensitivity_spectra.png", "sensitivity_spectra.pdf")),
)


def definition():
    return shared.definition(acquisition=ACQUISITION, analysis=ANALYSIS,
                             experiment_id="rf-control-scale-sensitivity")
