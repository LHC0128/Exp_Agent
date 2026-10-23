"""已移出维护主目录的实验卡片；不导入归档工作流。"""

from __future__ import annotations

from .contracts import ExperimentDefinition

ARCHIVE_PATH = "old/experiments"

ARCHIVED_EXPERIMENTS = {
    item.id: item for item in (
        ExperimentDefinition(
            id="mx-keithley-6221-main-field-calibration",
            title="Mx Keithley 6221 主磁场频率标定",
            category="calibration",
            family="z-field-calibration",
            variant="mx-keithley-6221",
            description="已归档，GUI 仅保留卡片；代码位于 old/experiments。原方案：Keithley 6221 独占 Z 主线圈并按可配置电流轴扫描，以 Y RF 共振中心标定自由斜率和截距。",
            data_type="Mx_Keithley_6221_Main_Field_Calibration",
            required_devices=("Keithley 6221", "GS200", "DG900", "DG4000", "锁相放大器"),
            execution_mode="typed_workflow",
            acquisition_program="old/experiments/experiments/Mx_Keithley_6221_Main_Field_Calibration.py",
            analysis_program="old/experiments/experiments/Mx_Keithley_6221_Main_Field_Calibration_plot.py",
            schema_version=2, supports_cancel=False, auto_analyze=False,
        ),
        ExperimentDefinition(
            id="mx-keithley-6221-optimal-control-rf-sensitivity",
            title="Mx Keithley 6221 最优控制 RF 灵敏度",
            category="measurement",
            family="rf-sensitivity",
            variant="mx-keithley-6221-optimal-control",
            description="已归档，GUI 仅保留卡片；代码位于 old/experiments。原方案：使用 Keithley 6221 在 Z 小磁场线圈输出每个触发对应的一周期最优控制任意波，同时由 GS200 驱动独立的 Z 主磁场线圈。",
            data_type="Mx_Keithley_6221_Optimal_Control_RF_Sensitivity",
            required_devices=("Keithley 6221", "GS200", "DG900", "DG4000", "锁相放大器"),
            execution_mode="typed_workflow",
            acquisition_program="old/experiments/experiments/Mx_Keithley_6221_Optimal_Control_RF_Sensitivity.py",
            analysis_program="old/experiments/experiments/Mx_Keithley_6221_Optimal_Control_RF_Sensitivity_plot.py",
            schema_version=4, supports_cancel=False, auto_analyze=False,
        ),
        ExperimentDefinition(
            id="mx-keithley-6221-optimal-control-xyz-balance",
            title="Mx Keithley 6221 最优控制 XYZ 平衡场",
            category="calibration",
            family="field-balance",
            variant="mx-keithley-6221-optimal-control",
            description="已归档，GUI 仅保留卡片；代码位于 old/experiments。原方案：保持 Keithley 6221 外部触发 Z 最优控制，以 DG4000 DC 扫描 X/Y、GS200 电流扫描 Z，记录 锁相放大器 Demod0 R 并报告实测三维网格最小点。",
            data_type="Mx_Keithley_6221_Optimal_Control_XYZ_Balance",
            required_devices=("Keithley 6221", "GS200", "DG900", "DG4000", "锁相放大器"),
            execution_mode="typed_workflow",
            acquisition_program="old/experiments/experiments/Mx_Keithley_6221_Optimal_Control_XYZ_Balance.py",
            analysis_program="old/experiments/experiments/Mx_Keithley_6221_Optimal_Control_XYZ_Balance_plot.py",
            schema_version=2, supports_cancel=False, auto_analyze=False,
        ),
        ExperimentDefinition(
            id="mx-keithley-6221-optimal-control-balanced-sensitivity",
            title="Mx Keithley 6221 最优控制 平衡-灵敏度一体化",
            category="measurement",
            family="sensitivity",
            variant="mx-keithley-6221-optimal-control-balanced",
            description="已归档，GUI 仅保留卡片；代码位于 old/experiments。原方案：保持 Keithley 6221 外部触发 Z 最优控制，先做 XYZ 平衡粗扫（可选细扫）并由逐层复线性模拟合定位工作点，随即用拟合工作点配置 X/Y/GS200 并直接完成 Y RF 相位校准、带符号幅度扫描与零 Y RF 噪声采集，最小化平衡与灵敏度测量之间的剩磁漂移失配。",
            data_type="Mx_Keithley_6221_Optimal_Control_Balanced_Sensitivity",
            required_devices=("Keithley 6221", "GS200", "DG900", "DG4000", "锁相放大器"),
            execution_mode="typed_workflow",
            acquisition_program="old/experiments/experiments/Mx_Keithley_6221_Optimal_Control_Balanced_Sensitivity.py",
            analysis_program="old/experiments/experiments/Mx_Keithley_6221_Optimal_Control_Balanced_Sensitivity_plot.py",
            schema_version=2, supports_cancel=False, auto_analyze=False,
        ),
        ExperimentDefinition(
            id="mx-main-field-noise-spectrum",
            title="Mx 主磁场控制噪声谱",
            category="measurement",
            family="noise",
            variant="mx-main-field",
            description="已归档，GUI 仅保留卡片；代码位于 old/experiments。原方案：固定锁相放大器解调频率为 90 kHz，按实测标定降低 GS200 主场电流，并从 Demod0 R 的 PSD 分离 S_beta 与 N_S1。",
            data_type="Mx_Main_Field_Noise_Spectrum",
            required_devices=("GS200", "DG900", "DG4000", "锁相放大器"),
            execution_mode="typed_workflow",
            acquisition_program="old/experiments/experiments/Mx_Main_Field_Noise_Spectrum.py",
            analysis_program="old/experiments/experiments/Mx_Main_Field_Noise_Spectrum_plot.py",
            schema_version=2, supports_cancel=False, auto_analyze=False,
        ),
        ExperimentDefinition(
            id="mx-z-noise-spectrum",
            title="Mx Z 直流控制噪声谱",
            category="measurement",
            family="noise",
            variant="mx-z-dc",
            description="已归档，GUI 仅保留卡片；代码位于 old/experiments。原方案：在 Mx 高主场构型下扫描 Z 直流偏置，固定锁相放大器的零偏参考频率并从 Demod0 R 的 PSD 分离 S_beta 与 N_S1。",
            data_type="Mx_Z_Noise_Spectrum",
            required_devices=("GS200", "DG900", "DG4000", "锁相放大器"),
            execution_mode="typed_workflow",
            acquisition_program="old/experiments/experiments/Mx_Z_Noise_Spectrum.py",
            analysis_program="old/experiments/experiments/Mx_Z_Noise_Spectrum_plot.py",
            schema_version=2, supports_cancel=False, auto_analyze=False,
        ),
        ExperimentDefinition(
            id="noise-spectrum-xy-v2",
            title="XY 控制噪声谱 v2",
            category="measurement",
            family="noise",
            variant="v2",
            description="已归档，GUI 仅保留卡片；代码位于 old/experiments。原方案：第二版 XY 控制噪声谱流程。",
            data_type="Noise_Spectrum_XY_Ctrl_v2",
            required_devices=("DG900", "DG4000", "HF2"),
            execution_mode="legacy_script",
            acquisition_program="old/experiments/experiments/Noise_Spectrum_XY_Ctrl_v2.py",
            analysis_program="old/experiments/experiments/Noise_Spectrum_XY_Ctrl_v2_plot.py",
            schema_version=2, supports_cancel=False, auto_analyze=False,
        ),
        ExperimentDefinition(
            id="rf-sensitivity-aw-r",
            title="RF 灵敏度（DirectAW R）",
            category="measurement",
            family="rf-sensitivity",
            variant="direct-aw-r",
            description="已归档，GUI 仅保留卡片；代码位于 old/experiments。原方案：复用 DirectAW 准备与 XY 校相，保持共同触发和 AW 整周期，跳过 A11。",
            data_type="RF_Field_Sensitivity_AW_R",
            required_devices=("GS200", "DG900", "DG4000", "lockin_amplifier"),
            execution_mode="typed_workflow",
            acquisition_program="old/experiments/experiments/RF_Field_Sensitivity_AW_R.py",
            analysis_program="old/experiments/experiments/RF_Field_Sensitivity_AW_R_plot.py",
            schema_version=3, supports_cancel=False, auto_analyze=False,
        ),
    )
}


def load_archived_experiment(experiment_id: str) -> ExperimentDefinition:
    """仅显式的旧 Python 调用按需加载归档实现；GUI 只读取上面的卡片。"""
    from importlib import import_module

    card = ARCHIVED_EXPERIMENTS[experiment_id]
    if card.execution_mode == "typed_workflow":
        module = ("rf_sensitivity_direct_aw_r" if experiment_id == "rf-sensitivity-aw-r"
                  else experiment_id.replace("-", "_"))
        return import_module(f"lab_workflows.experiment_modules.{module}.definition").DEFINITION

    from dataclasses import replace
    from ..common import find_project_root
    from .legacy import LegacyScriptAdapter

    root = find_project_root()
    adapter = LegacyScriptAdapter(experiment_id, str(root / card.acquisition_program), card.data_type,
                                  str(root / card.analysis_program) if card.analysis_program else None)
    return replace(card, schema_provider=adapter.schema, defaults_saver=adapter.save_defaults,
                   preflight_runner=adapter.preflight, experiment_runner=adapter.run,
                   analysis_runner=adapter.analyze, supports_cancel=True, auto_analyze=True)
