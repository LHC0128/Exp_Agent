"""保留已发布的相位对比导入及保存注入点；两种 RF 实验共用离线实现。"""
from ...plotting import save_figure
from ..rf_sensitivity_direct_aw import control_phase as shared
from ..rf_sensitivity_direct_aw.control_phase import control_legend, load_phase_scan


def plot_phase_responses(results, scans):
    return shared.plot_phase_responses(results, scans, save_plot=save_figure)
