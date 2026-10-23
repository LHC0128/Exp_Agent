"""真实子进程入口导入回归；在预检处停止，严禁连接硬件。"""
import os
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.parametrize("entry", ["workflow", "session"])
def test_module_main_reaches_preflight_without_circular_import_or_hardware(entry):
    source = f'''
import runpy
from lab_workflows import experiment_runtime
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw import preparation
class ExpectedPreflightStop(Exception):
    pass
def stop(*args, **kwargs):
    raise ExpectedPreflightStop()
def reject_hardware(*args, **kwargs):
    raise AssertionError("必须在任何硬件连接前停止")
experiment_runtime.load_runtime_params = lambda model: model()
preparation.preflight = stop
preparation.connect_devices = reject_hardware
try:
    runpy.run_module("lab_workflows.experiment_modules.rf_sensitivity_direct_aw.{entry}", run_name="__main__")
except ExpectedPreflightStop:
    print("IMPORT_REACHED_PREFLIGHT_WITHOUT_HARDWARE")
else:
    raise AssertionError("未到达预检边界")
'''
    result = subprocess.run([sys.executable, "-c", source], cwd=Path(__file__).resolve().parents[3],
        env=os.environ | {"LAB_TYPED_PARAMETERS": '{"session_id":"import-regression"}'},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "IMPORT_REACHED_PREFLIGHT_WITHOUT_HARDWARE" in result.stdout
