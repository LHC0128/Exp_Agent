"""共享噪声扫描引擎的延迟建目录、终态与历史目录契约；真实工作流 + 假仪器。"""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from ...staged_workflow import StagedWorkflow
from ...steps.noise_xy_scan import NoiseXYScan
from . import workflow
from .stages import ACQUISITION
from .test_sine_mode import copy_config, fake_hardware, scan_params

DATA_TYPE = "Noise_Spectrum_XY_Ctrl"


def run_directories(root: Path) -> list[Path]:
    base = root / "data" / DATA_TYPE
    return sorted(base.iterdir()) if base.is_dir() else []


def data_hashes(directory: Path) -> dict[str, str]:
    """历史运行数据的整目录内容哈希。

    acquisition_state.json 是会话状态镜像：切到新目录前仍属于当前会话，会被 publish
    更新；历史配置与 raw 数据不得重写。
    """
    return {str(path.relative_to(directory)): sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.rglob("*"))
            if path.is_file() and path.name != "acquisition_state.json"}


def test_engine_hooks_keep_noise_family_semantics(tmp_path):
    """G8：骨架目录轮换不参与；父类模块型 after_step 不得覆盖节点终态。"""
    copy_config(tmp_path)
    ctx = workflow.Workflow(scan_params(), project_root=tmp_path)
    assert isinstance(ctx, NoiseXYScan)
    assert ctx._needs_new_directory([stage.id for stage in ACQUISITION]) is False
    assert NoiseXYScan.after_step is not StagedWorkflow.after_step
    assert not hasattr(ctx, "directory") and not hasattr(ctx, "state")
    ctx.finish()


def test_sub_experiments_reuse_nodes_without_inheriting_shell_class():
    """两个子实验与正弦实验是兄弟：只复用共享节点函数，不继承本实验壳类。"""
    from ..noise_spectrum_xy_known_noise.workflow import Workflow as KnownWorkflow
    from ..noise_spectrum_xy_probe_am.workflow import Workflow as ProbeWorkflow

    for child in (KnownWorkflow, ProbeWorkflow):
        assert not issubclass(child, workflow.Workflow)
        assert issubclass(child, NoiseXYScan)
        # 分派仍由各实验自己声明，不搬进基类或 provider。
        assert "_execute_step" in child.__dict__


@pytest.mark.parametrize("item", ["A01", "A02", "A03"])
def test_failure_before_output_setup_creates_no_run_directory(tmp_path, item):
    """A01–A03 失败时不得产生运行目录；目录只在 A04 节点内惰性创建。"""
    copy_config(tmp_path)
    error = RuntimeError(f"{item} 失败哨兵")
    with fake_hardware(tmp_path) as hw:
        ctx = workflow.Workflow(scan_params(), project_root=tmp_path)
        if item == "A01":
            # execute 的入口复检通过，A01 节点内的复检失败：节点记为 failed 且不建目录。
            original = ctx._preflight_state
            calls = []

            def flaky(params):
                calls.append(1)
                if len(calls) > 1:
                    raise error
                return original(params)

            ctx._preflight_state = flaky
        elif item == "A02":
            ctx.execute(["A01"])
            hw.devices["hf2"].connect.side_effect = error
        else:
            ctx.execute(["A01", "A02"])
            hw.clocks.side_effect = error
        with pytest.raises(type(error)) as caught:
            ctx.execute([item])
        assert caught.value is error
        assert ctx.states[item]["status"] == "failed"
        assert run_directories(tmp_path) == []
        ctx.finish()
    assert run_directories(tmp_path) == []


def test_quality_failed_keeps_terminal_state_and_initializing_config(tmp_path):
    """A10 校相未收敛：节点终态保留、后继不执行、初始配置不被写成 completed。"""
    copy_config(tmp_path)
    with fake_hardware(tmp_path) as hw:
        ctx = workflow.Workflow(replace(scan_params(), xy_phase_cal_max_iter=2), project_root=tmp_path)
        ctx.execute([stage.id for stage in ACQUISITION[:9]])
        hw.samples.side_effect = [dict(r=0.0, x=0.0, y=0.0, theta=0.0)] * 2
        ctx.execute(["A10", "A11", "A12"])
        assert ctx.states["A10"] == {"status": "quality_failed", "error": "XY 校相未收敛，禁止扫描；可以调整参数后重试"}
        assert ctx.states["A11"]["status"] == "pending" and ctx.states["A12"]["status"] == "pending"
        config = yaml.safe_load((ctx.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
        # 父类的模块型 after_step 会写 completed；本族保留节点声明的终态。
        assert config["completion_status"] == "initializing"
        ctx.finish()
    config = yaml.safe_load((ctx.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == "failed"
    assert config["failure_reason"] == "本轮未开始扫描"


def test_rerun_creates_new_directory_and_keeps_previous_data(tmp_path):
    """扫描重跑换新目录，旧目录的配置与 raw 字节逐文件不变。"""
    copy_config(tmp_path)
    with fake_hardware(tmp_path) as hw:
        hw.rates.side_effect = lambda hfi, config, **kwargs: (
            800.0 if config.time_constant == scan_params().hf2_daq_tc else 1200.0)
        ctx = workflow.Workflow(scan_params(), project_root=tmp_path)
        ctx.execute([stage.id for stage in ACQUISITION[:11]])
        ctx.execute(["A12", "A13"])
        first = ctx.run_dir
        previous = data_hashes(first)
        ctx.execute(["A11", "A12", "A13"])
        assert ctx.run_dir != first
        assert data_hashes(first) == previous
        assert run_directories(tmp_path) == sorted([first, ctx.run_dir])
        ctx.finish()
