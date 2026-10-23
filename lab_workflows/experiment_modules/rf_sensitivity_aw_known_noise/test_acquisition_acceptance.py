"""已知噪声实验真实采集时序、故障与多控制边界(假仪器+真实写盘)。"""
from dataclasses import replace
from pathlib import Path
import numpy as np
import pytest
import yaml

from ..rf_sensitivity_direct_aw import acquisition as rf_acquisition
from ..rf_sensitivity_direct_aw import _helpers
from ..rf_sensitivity_direct_aw import phase as rf_phase
from ...common import WorkflowCancelled
from . import workflow
from .stages import ACQUISITION
from .test_workflow import known_rig, rig, control_source, assert_shutdown


ACQUISITION_CASES = {
    "rf-sensitivity-aw-known-noise": {
        "mode": "grouped",
        "cases": {
            "order": ["test_real_group_settle_restore_slow_disk_and_control_order"],
            "slow_disk": ["test_real_group_settle_restore_slow_disk_and_control_order"],
            "group_change": ["test_real_group_settle_restore_slow_disk_and_control_order"],
            "last_group": ["test_real_group_settle_restore_slow_disk_and_control_order"],
            "cancel": ["test_capture_failure_in_second_group_preserves_first_group"],
            "acquire_failure": ["test_capture_failure_in_second_group_preserves_first_group"],
            "partial_save": ["test_capture_failure_in_second_group_preserves_first_group",
                             "test_restore_or_save_failure_stops_next_group_and_salvages"],
            "restore_failure": ["test_restore_or_save_failure_stops_next_group_and_salvages"],
            "save_failure": ["test_restore_or_save_failure_stops_next_group_and_salvages"],
            "pair_second_failure": ["test_phase_pair_second_failure_keeps_first_sample_and_noise"],
        },
        "sources": [
            "lab_workflows/experiment_modules/rf_sensitivity_aw_known_noise/workflow.py",
            "lab_workflows/experiment_modules/rf_sensitivity_aw_known_noise/acquisition.py",
            "lab_workflows/experiment_modules/rf_sensitivity_aw_known_noise/excitation.py",
            "lab_workflows/experiment_modules/rf_sensitivity_aw_known_noise/session.py",
            "experiments/RF_Field_Sensitivity_AW_Known_Noise.py",
        ],
    },
}


@pytest.mark.parametrize("stage", ["A13", "A14"])
def test_real_group_settle_restore_slow_disk_and_control_order(known_rig, monkeypatch, stage):
    """实际业务:组首配置先于关温控,关闭窗口内只 settle+采集,慢盘不占用关闭窗口。"""
    points = dict(A13=dict(temp_group_size=2, rf_amp_points=5), A14=dict(temp_group_size=2, noise_n_avg=3))[stage]
    known_rig.params = replace(known_rig.params, control_scale_factors={"v1": 1.25, "v2": .8}, **points)
    ctx = workflow.Workflow(known_rig.params, project_root=known_rig.tmp_path)
    ids = [item.id for item in ACQUISITION]
    ctx.execute(ids[:ids.index(stage)])
    enabled, clock, events, saved = [True], [0.0], [], []
    windows = []
    real_switch = _helpers.set_temperature_switch
    real_configure = rf_acquisition.set_z_rf

    def configure(ctx, amplitude, phase_base=None):
        events.append("configure")
        return real_configure(ctx, amplitude, phase_base)

    def switch(device, value, **kwargs):
        real_switch(device, value, **kwargs)
        if not value:
            if stage == "A13":
                assert events[-1] == "configure", "关温控前必须完成本组 Z 叠加上传"
            elif not windows:
                # A14 的零 RF 噪声上传在分组循环外完成，其 z_sum 快照在温控开启时落盘。
                assert "configure" in events, "首个采集窗口前必须完成零 RF 噪声上传"
            windows.append([clock[0], None])
        elif not enabled[0]:
            # 仅在 off→on 沿记录窗口结束；退出缓冲上下文的防御性重复恢复不得延长窗口。
            windows[-1][1] = clock[0]
        enabled[0] = value
        events.append("on" if value else "off")

    def wait(seconds):
        if not enabled[0]:
            assert seconds == known_rig.params.system_settle_s
            events.append("settle")
        clock[0] += seconds

    real_acquire = rf_acquisition.daq.acquire_data

    def acquire(*args, **kwargs):
        assert not enabled[0] and events[-1] == "settle", "采集必须在关闭窗口的稳定等待之后"
        events.append("acquire")
        result = real_acquire(*args, **kwargs)
        clock[0] += 1
        return result

    real_save = np.savez

    def save(path, *args, **kwargs):
        assert enabled[0], "关闭温控窗口内不能保存波形或快照"
        events.append("save")
        clock[0] += 30  # 模拟慢磁盘,不得挤占关闭窗口
        result = real_save(path, *args, **kwargs)
        target = Path(path.name if hasattr(path, "write") else path)
        saved.append(target.with_suffix("") if target.suffix == ".tmp" else target)
        return result

    real_update = type(ctx.directory).update_config

    def update(directory, **kwargs):
        assert enabled[0], "关闭温控窗口内不能写采集元数据"
        return real_update(directory, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(rf_acquisition, "set_temperature_switch", switch)
        scoped.setattr(_helpers, "set_temperature_switch", switch)
        scoped.setattr(rf_acquisition, "set_z_rf", configure)
        scoped.setattr(rf_acquisition, "wait", wait)
        scoped.setattr(rf_acquisition.daq, "acquire_data", acquire)
        scoped.setattr(np, "savez", save)
        scoped.setattr(type(ctx.directory), "update_config", update)
        ctx.execute([stage])
    expected = {"A13": (5, 3), "A14": (3, 3)}[stage]
    assert events.count("acquire") == expected[0] * 2, "两个控制版本都必须完整执行"
    assert len(windows) == expected[1] * 2, "每个控制版本各自分组关温控"
    sizes = ([min(points["temp_group_size"], expected[0] - start)
              for start in range(0, expected[0], points["temp_group_size"])]
             if stage == "A13" else [1] * points["noise_n_avg"])
    assert [end - start for start, end in windows] == pytest.approx(
        [size * (known_rig.params.system_settle_s + 1) for size in sizes] * 2)
    assert enabled[0] and "save" in events and saved
    for version in ("v1", "v2"):
        child = ctx.directory.root / "controls" / version
        names = [path.name for path in saved if path.is_relative_to(child)]
        assert names, f"{version} 必须真实落盘"
        from ..rf_sensitivity_direct_aw.waveform import load_envelope
        control = ctx.controls[version]
        nominal = load_envelope(replace(control.params, control_scale_factors={}), 16384)
        with np.load(child / "raw/xy_aw_waveforms.npz", allow_pickle=False) as data:
            np.testing.assert_allclose(data["omega_ctrl_Hz"],
                nominal["omega_ctrl_Hz"] * control.params.control_scale_factors[version])
            assert float(data["control_scale_factor"]) == control.params.control_scale_factors[version]
    for path in saved:
        with np.load(path, allow_pickle=False) as data:
            assert data.files
    ctx.finish()
    assert_shutdown(known_rig)


def test_phase_pair_second_failure_keeps_first_sample_and_noise(known_rig, monkeypatch):
    """A11 的 phi/phi+180 顺序仍保留；第二相失败也保存第一相，噪声分量不随 RF 相位平移。"""
    known_rig.params = replace(known_rig.params, temp_group_size=2)
    ctx = workflow.Workflow(known_rig.params, project_root=known_rig.tmp_path)
    ctx.execute([f"A{i:02d}" for i in range(1, 11)])
    real_acquire, real_save, real_switch = rf_phase.daq.acquire_data, np.savez, _helpers.set_temperature_switch
    captures, phases, events, enabled = [], [], [], [True]

    def switch(device, value, **kwargs):
        real_switch(device, value, **kwargs)
        enabled[0] = value
        events.append("on" if value else "off")

    def acquire(*args, **kwargs):
        assert not enabled[0]
        captures.append(1)
        if len(captures) == 2:
            raise RuntimeError("第二相采集失败")
        return real_acquire(*args, **kwargs)

    def save(path, *args, **kwargs):
        assert enabled[0], "配对部分数据须先恢复温控再保存"
        events.append("save")
        if "rf_phase_deg" in kwargs:
            phases.append(float(kwargs["rf_phase_deg"]))
        return real_save(path, *args, **kwargs)

    failure = None
    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(rf_phase, "set_temperature_switch", switch)
            scoped.setattr(_helpers, "set_temperature_switch", switch)
            scoped.setattr(rf_phase.daq, "acquire_data", acquire)
            scoped.setattr(np, "savez", save)
            with pytest.raises(RuntimeError, match="第二相采集失败") as error:
                ctx.execute(["A11"])
            failure = error.value
        assert captures == [1, 1]
        assert phases[:2] == [known_rig.params.z_phase_scan_start_deg,
                              (known_rig.params.z_phase_scan_start_deg + 180) % 360]
        child = ctx.directory.root / "controls/v1"
        with np.load(child / "raw/z_phase_scan.npz", allow_pickle=False) as saved:
            assert len(saved["x_mean_v"]) == 1
        sums = sorted((child / "raw").glob("z_sum_A11_*.npz"))
        assert len(sums) == 2
        with np.load(sums[0]) as first, np.load(sums[1]) as second:
            np.testing.assert_array_equal(first["noise_V"], second["noise_V"])
            np.testing.assert_allclose(first["rf_V"], -second["rf_V"], atol=1e-13)
        assert not (ctx.directory.root / "controls/v2/raw/z_phase_scan.npz").exists()
        assert "on" in events and "save" in events and enabled[0]
    finally:
        ctx.finish(failure)
    assert_shutdown(known_rig)


@pytest.mark.parametrize("failure", ["acquire", "cancel"])
def test_capture_failure_in_second_group_preserves_first_group(known_rig, monkeypatch, failure):
    """v1 第二组首点采集失败/取消:第一组数据已恢复温控后落盘,v2 不启动,原始错误保留。"""
    known_rig.params = replace(known_rig.params, temp_group_size=2, rf_amp_points=5)
    ctx = workflow.Workflow(known_rig.params, project_root=known_rig.tmp_path)
    ids = [item.id for item in ACQUISITION]
    ctx.execute(ids[:ids.index("A13")])
    count = [0]
    real = rf_acquisition.daq.acquire_data
    error = RuntimeError("模拟采集失败") if failure == "acquire" else WorkflowCancelled("模拟取消")

    def acquire(*args, **kwargs):
        if count[0] == 2:
            raise error
        count[0] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(rf_acquisition.daq, "acquire_data", acquire)
    expected = RuntimeError if failure == "acquire" else WorkflowCancelled
    with pytest.raises(expected, match="模拟") as caught:
        ctx.execute(["A13"])
    primary = caught.value
    assert count[0] == 2, "失败后不能继续采集"
    child = ctx.directory.root / "controls" / "v1"
    with np.load(child / "raw" / "response_data.npz", allow_pickle=False) as data:
        assert data["amplitudes_V"].shape == (2,), "第一组已读数据必须恢复温控后保存"
        assert np.all(np.isfinite(data["demod3_x_V"]))
    assert not (ctx.directory.root / "controls" / "v2" / "raw" / "response_data.npz").exists()
    config = yaml.safe_load((ctx.directory.root / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["controls"]["v1"]["steps"]["A13"]["status"] == "failed"
    assert config["controls"]["v1"]["steps"]["A13"]["error"] == str(primary)
    assert "v2" not in config["controls"] or config["controls"]["v2"]["steps"]["A13"]["status"] != "completed"
    ctx.finish(primary)
    assert_shutdown(known_rig)


@pytest.mark.parametrize("fault", ["restore", "save"])
def test_restore_or_save_failure_stops_next_group_and_salvages(known_rig, monkeypatch, fault):
    """温控恢复或写盘失败:停止后继组,已读样本尽力抢救,恢复尝试先于文件保存。"""
    known_rig.params = replace(known_rig.params, temp_group_size=2, rf_amp_points=5)
    ctx = workflow.Workflow(known_rig.params, project_root=known_rig.tmp_path)
    ids = [item.id for item in ACQUISITION]
    ctx.execute(ids[:ids.index("A13")])
    attempted, captured, saved = [], [], []
    real_switch, real_save, real_acquire = _helpers.set_temperature_switch, np.savez, rf_acquisition.daq.acquire_data
    disk_failed = False

    def switch(device, enabled, **kwargs):
        real_switch(device, enabled, **kwargs)
        attempted.append(enabled)
        if enabled and fault == "restore" and attempted.count(True) == 1:
            raise OSError("模拟温控恢复故障")

    def acquire(*args, **kwargs):
        captured.append(1)
        return real_acquire(*args, **kwargs)

    def save(path, *args, **kwargs):
        nonlocal disk_failed
        assert attempted and attempted[-1] is True, "必须先尝试恢复温控再保存"
        if fault == "save" and not disk_failed:
            disk_failed = True
            raise OSError("模拟写盘故障")
        result = real_save(path, *args, **kwargs)
        target = Path(path.name if hasattr(path, "write") else path)
        saved.append(target.with_suffix("") if target.suffix == ".tmp" else target)
        return result

    primary = None
    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(rf_acquisition, "set_temperature_switch", switch)
            scoped.setattr(_helpers, "set_temperature_switch", switch)
            scoped.setattr(rf_acquisition.daq, "acquire_data", acquire)
            scoped.setattr(np, "savez", save)
            with pytest.raises(OSError, match="模拟温控恢复故障|模拟写盘故障") as caught:
                ctx.execute(["A13"])
            primary = caught.value
        assert len(captured) == 2, "恢复或写盘失败后不能继续下一组"
        assert saved, "剩余待存样本仍须尽力保存"
        for path in saved:
            with np.load(path, allow_pickle=False) as data:
                assert data.files
        assert not (ctx.directory.root / "controls" / "v2" / "raw" / "response_data.npz").exists()
    finally:
        ctx.finish(primary)
    assert_shutdown(known_rig)
