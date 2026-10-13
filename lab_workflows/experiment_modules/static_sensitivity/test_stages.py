"""完整和分步共用假仪器，覆盖依赖、模式、目录及安全恢复。"""
from pathlib import Path
import json
import os
import sys
from unittest.mock import patch
import numpy as np
import pytest
import yaml

from ...common import WorkflowCancelled
from ...experiment_runtime import load_runtime_params
from . import workflow, preparation, acquisition
from .models import StaticSensitivityParams
from .stages import ACQUISITION, ANALYSIS, FINISH_ID, definition, invalidated_stages
from .test_support import fake_rig


@pytest.fixture
def rig(tmp_path):
    with fake_rig(tmp_path) as value:
        yield value


def through(last):
    return [stage.id for stage in ACQUISITION if stage.id <= last]


def test_complete_and_staged_share_measurement_and_never_write_timing2(tmp_path):
    datasets=[]
    for index in range(2):
        root=tmp_path / str(index)
        root.mkdir()
        with fake_rig(root) as rig:
            if index==0:
                directory=workflow.run(rig.params)
            else:
                ctx=workflow.Workflow(rig.params)
                for stage in ACQUISITION:
                    ctx.execute([stage.id])
                directory=ctx.directory.root
            with np.load(directory / "raw/dispersion_scan.npz") as raw:
                datasets.append({key:raw[key] for key in ("offsets_v","x_median","y_median","completed_mask","sample_counts")})
            for path in sorted((directory / "raw/noise_raw").rglob("*.npy")):
                datasets[-1][path.relative_to(directory / "raw").as_posix()]=np.load(path)
            timing=rig.mapping["Time_sequence_2"]
            assert not [event for event in rig.events if event[0]==timing["resource"] and event[2]==timing["channel"]]
            assert yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["completion_status"]=="completed"
            rig.tec.disconnect.assert_called_once()
    assert datasets[0].keys()==datasets[1].keys()
    assert sum(key.endswith(".npy") for key in datasets[0])==len(rig.params.gs200_current_ranges)*rig.params.noise_n_avg
    for key in datasets[0]:
        np.testing.assert_equal(datasets[0][key],datasets[1][key])


def test_dependency_selection_preflight_and_parameter_impacts(rig):
    ctx=workflow.Workflow(rig.params)
    with pytest.raises(ValueError,match="缺少前置"):
        ctx.execute(["A09"])
    assert not rig.events and ctx.directory is None
    ctx.execute(through("A08")[::-1])
    before=ctx.snapshot()["steps"]
    ctx.execute(["A01"])
    assert ctx.states==before
    ctx.update_parameters({"z_v_to_nt":1800.})
    assert ctx.states==before
    ctx.update_parameters({"dispersion_temp_switch_on_settle_s":0.7,"noise_n_avg":2})
    expected=set(definition()["parameter_impacts"]["acquisition"]["dispersion_temp_switch_on_settle_s"])
    expected.update(definition()["parameter_impacts"]["acquisition"]["noise_n_avg"])
    assert all(ctx.states[key]["status"]!="completed" for key in expected)
    assert ctx.states["A07"]["status"]=="completed"
    revision=ctx.revision
    with pytest.raises(ValueError,match="未知参数"):
        ctx.update_parameters({"not_a_parameter":1})
    assert ctx.revision==revision
    ctx.finish()


def test_modes_reruns_copy_dispersion_and_preserve_sealed_runs(rig):
    ctx=workflow.Workflow(rig.params)
    ctx.execute(through("A12"))
    first=ctx.directory.root
    snapshot={path.relative_to(first):path.read_bytes() for path in first.rglob("*") if path.is_file()}
    assert ctx.demod_mode=="dispersion" and not ctx.closed
    with pytest.raises(ValueError,match="缺少前置"):
        ctx.execute(["A11"])
    ctx.execute(["A10","A11","A12"])
    second=ctx.directory.root
    assert first!=second
    config=yaml.safe_load((second / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["dispersion_source_run_id"]==first.name
    for path,data in snapshot.items():
        assert (first/path).read_bytes()==data
    assert (second / "raw/dispersion_scan.npz").read_bytes()==snapshot[Path("raw/dispersion_scan.npz")]
    assert len(list((second / "raw/dispersion_steps").glob("point_*.npz")))==rig.params.dispersion_steps
    ctx.update_parameters({"ramp_low":-0.3})
    with pytest.raises(ValueError,match="缺少前置"):
        ctx.execute(["A10"])
    ctx.execute(["A09","A10","A11","A12"])
    assert ctx.directory.root not in (first,second)
    assert "dispersion_source_run_id" not in ctx.state
    sealed=ctx.directory.config_path.read_bytes()
    ctx.finish()
    assert ctx.directory.config_path.read_bytes()==sealed


def test_noise_only_rerun_before_sealing_has_valid_mode(rig):
    ctx=workflow.Workflow(rig.params)
    ctx.execute(through("A11"))
    first=ctx.directory.root
    ctx.execute(["A11","A12"])
    assert ctx.directory.root!=first
    assert ctx.state["dispersion_source_run_id"]==first.name
    ctx.finish()


def test_actual_mode_drift_and_cleanup_keep_original_error(rig):
    ctx=workflow.Workflow(rig.params)
    ctx.execute(through("A08"))
    rig.hf2.nodes[rig.hf2.demod_path(rig.params.hf2_demod_idx)+"/timeconstant"]=0.2
    with pytest.raises(ValueError,match="实际配置") as caught:
        ctx.execute(["A09"])
    rig.shutdown_error=True
    ctx.finish(caught.value)
    config=yaml.safe_load(ctx.directory.config_path.read_text(encoding="utf-8"))
    assert "实际配置" in config["failure_reason"]
    assert not config["safety_shutdown"]["completed"]
    assert ctx.states[FINISH_ID]["status"]=="failed"


def test_mapping_changes_allow_safe_finish_and_clock_failure_closes_outputs(rig):
    ctx=workflow.Workflow(rig.params)
    ctx.execute(through("A08"))
    with patch.object(preparation,"synchronize_clocks",side_effect=RuntimeError("时钟失败")):
        with pytest.raises(RuntimeError,match="时钟失败") as caught:
            ctx.execute(["A03"])
    ctx.finish(caught.value)
    assert ctx.state["safety_shutdown"]["completed"]
    assert any(event[1]=="disconnect" for event in rig.events)
    fresh=workflow.Workflow(rig.params)
    fresh.execute(["A01"])
    (rig.root / "params/safety_limits.yaml").write_text("changed",encoding="utf-8")
    fresh.execute([FINISH_ID])
    assert fresh.closed


def test_last_divisible_point_has_no_extra_temperature_wait(tmp_path):
    from .test_dc_scan import FakeGenerator
    from ...steps.test_dispersion import FakeHF2
    params=StaticSensitivityParams(dispersion_steps=10, dispersion_temp_switch_interval_points=5,
                                  hf2_demod_rate=30.)
    with patch.object(acquisition,"wait_for_settle") as wait:
        acquisition.acquire_dispersion(params,FakeGenerator(),1,FakeGenerator(),2,FakeHF2(),tmp_path / "scan.npz")
    assert sum(call.args==(params.dispersion_temp_switch_on_settle_s,) for call in wait.call_args_list)==1


def test_manifest_covers_visible_parameters_and_finish_has_no_dependencies():
    fields={field["name"] for field in StaticSensitivityParams().schema()["fields"] if field.get("visible",True)}
    flow=definition()
    shown={name for stage in flow["acquisition"]+flow["analysis"] for name in stage["display_parameters"]}
    assert fields<=shown
    assert not ACQUISITION[-1].dependencies
    assert set(flow["parameter_impacts"]["analysis"]["z_v_to_nt"])=={"B02","B04"}
    assert set(stage.id for stage in invalidated_stages(ANALYSIS,"B02"))=={"B02","B04"}


def fake_session_main():
    payload=json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id=payload.pop("session_id")
    os.environ["LAB_TYPED_PARAMETERS"]=json.dumps(payload)
    with fake_rig(Path(os.environ["STATIC_TEST_ROOT"]),load_runtime_params(StaticSensitivityParams)) as rig:
        rig.block_noise=os.environ.get("STATIC_TEST_BLOCK_NOISE")=="1"
        from . import session
        # 执行正式入口，只有仪器实现被夹具替换。
        os.environ["LAB_TYPED_PARAMETERS"]=json.dumps(payload|{"session_id":session_id})
        session.main()


def test_finish_uses_last_applied_dispersion_configuration(rig):
    ctx=workflow.Workflow(rig.params)
    ctx.execute(through("A08"))
    old_idx=ctx.params.hf2_demod_idx
    ctx.update_parameters({"hf2_demod_idx":2,"hf2_demod_tc":.2})
    before=rig.hf2.get_double(rig.hf2.demod_path(2)+"/timeconstant")
    ctx.finish()
    assert rig.hf2.get_double(rig.hf2.demod_path(old_idx)+"/timeconstant")==rig.params.hf2_demod_tc
    assert rig.hf2.get_double(rig.hf2.demod_path(2)+"/timeconstant")==before


def test_analysis_parameter_edit_does_not_start_empty_round_on_restore(rig):
    ctx=workflow.Workflow(rig.params)
    ctx.execute(through("A11"))
    directory=ctx.directory.root
    ctx.update_parameters({"z_v_to_nt":1800.})
    ctx.execute(["A12"])
    assert ctx.directory.root==directory
    config=yaml.safe_load(ctx.directory.config_path.read_text(encoding="utf-8"))
    assert config["completion_status"]=="completed"
    assert config["parameters"]["z_v_to_nt"]==rig.params.z_v_to_nt
    ctx.finish()


if __name__=="__main__":
    fake_session_main()
