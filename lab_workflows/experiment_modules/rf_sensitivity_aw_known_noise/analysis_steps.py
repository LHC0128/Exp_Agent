"""已知噪声的多控制分析入口，共用独立控制分析及审计产物。"""
from ...plotting import save_figure
from ..rf_sensitivity_direct_aw import control_analysis
from . import run_format


def control_inputs(directory, config):
    return control_analysis.control_inputs(directory, config, identity=run_format)


def load_inputs(directory, config):
    return control_analysis.load_inputs(directory, config, identity=run_format)


def response_fit(directory, config, *, component="X"):
    return control_analysis.response_fit(directory, config, component=component, identity=run_format)


def noise_psd(directory, config, nperseg):
    return control_analysis.noise_psd(directory, config, nperseg, identity=run_format)


def sensitivity_and_plots(directory, config, nperseg, *, identity=run_format):
    return control_analysis.sensitivity_and_plots(directory, config, nperseg, identity=identity, save_plot=save_figure)
