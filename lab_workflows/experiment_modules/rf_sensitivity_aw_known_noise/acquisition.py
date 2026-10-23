"""多控制编排的兼容入口；实际目录、激活与阶段执行由公共内核实现。"""
from ..rf_sensitivity_direct_aw import control_runs as shared
from ..rf_sensitivity_direct_aw.control_runs import make_control_directory, inherit_control, seal_controls
from ..rf_sensitivity_direct_aw._helpers import wait


def activate(ctx, step):
    return shared.activate(ctx, step, settle_wait=wait)


def execute_controls(parent, step):
    return shared.execute_controls(parent, step, activate_control=activate)
