"""真实单点扫描、温控门控和真实文件写入的无硬件验收。"""

from contextlib import ExitStack, contextmanager
from pathlib import Path
from time import perf_counter, sleep
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import yaml

from ...common import WorkflowCancelled
from . import acquisition, workflow
from .stages import ACQUISITION
from .test_sine_mode import copy_config, fake_hardware, scan_params


ACQUISITION_CASES = {
    "noise-spectrum-xy": {
        "mode": "single",
        "cases": {
            "order": ["test_single_point_order_and_slow_disk"],
            "slow_disk": ["test_single_point_order_and_slow_disk"],
            "cancel": ["test_cancel_keeps_previous_point_and_stops_scan"],
            "acquire_failure": ["test_failed_point_keeps_historical_nan_and_continues"],
            "restore_failure": ["test_restore_and_writer_failures_rescue_read_data"],
            "save_failure": ["test_restore_and_writer_failures_rescue_read_data"],
            "partial_save": ["test_cancel_keeps_previous_point_and_stops_scan",
                             "test_restore_and_writer_failures_rescue_read_data"],
            "entrypoints": ["test_single_point_order_and_slow_disk"],
        },
        "sources": [
            "lab_workflows/experiment_modules/noise_spectrum_xy/workflow.py",
            "lab_workflows/experiment_modules/noise_spectrum_xy/acquisition.py",
            "lab_workflows/experiment_modules/noise_spectrum_xy/_helpers.py",
            "lab_workflows/experiment_modules/noise_spectrum_xy/phase.py",
            "lab_workflows/experiment_modules/noise_spectrum_xy/preparation.py",
        ],
    },
}


@contextmanager
def traced_scan(root, *, prepare=True, slow=False):
    """仪器 I/O 使用假对象，配置、采集编排、温控步骤及 writer 执行真实函数。"""
    copy_config(root)
    with fake_hardware(root) as hw, ExitStack() as stack:
        params = scan_params()
        hw.rates.side_effect = lambda hfi, config, **kwargs: 800.0 if kwargs.get("for_acquisition") else 1200.0
        ctx = workflow.Workflow(params, project_root=root)
        if prepare:
            ctx.execute([stage.id for stage in ACQUISITION[:11]])
        trace = SimpleNamespace(ctx=ctx, hw=hw, events=[], active=False, voltage=5.0,
            output=True, attempts=0, acquired=[], slow_seconds=[], acquire_error=None,
            restore_error=None, restore_attempted=False, save_error=None, save_kind=None,
            save_failed=False)

        def dc(voltage, *, channel):
            if trace.active:
                trace.events.append(("temp", float(voltage)))
                if voltage == params.temp_switch:
                    trace.restore_attempted = True
                    if trace.restore_error:
                        raise trace.restore_error
            trace.voltage = float(voltage)

        def output(enabled, *, channel):
            trace.output = enabled
            if trace.active:
                trace.events.append(("output", enabled))
                assert enabled is True

        hw.devices["dg_temp"].setup_dc.side_effect = dc
        hw.devices["dg_temp"].set_output.side_effect = output
        original_read = hw.acquire.side_effect

        def read(hfi, cfg, **kwargs):
            trace.attempts += 1
            trace.events.append(("read", trace.attempts, trace.voltage))
            assert trace.voltage == 0.0 and trace.output
            if trace.attempts == 2 and trace.acquire_error:
                raise trace.acquire_error
            results = original_read(hfi, cfg, **kwargs)
            result = results[0]
            result.signal_name = "sample.y"
            result.time = np.arange(len(result.values)) / kwargs["actual_rate"]
            result.config_snapshot = dict(acquisition_module="daq", requested_rate=kwargs["requested_rate"],
                actual_rate=kwargs["actual_rate"], demod_stream_rate=kwargs["actual_rate"],
                actual_duration=cfg.duration, time_source="fake_instrument")
            trace.acquired.append(result.values.copy())
            return results

        hw.acquire.side_effect = read
        original_peak, original_wait = acquisition.set_xy_sine_peak, acquisition.wait

        def peak(*args, **kwargs):
            trace.events.append(("xy", float(args[1]), trace.voltage))
            assert trace.voltage == params.temp_switch and trace.output
            assert (ctx.run_dir / "experiment_config.yaml").is_file()
            return original_peak(*args, **kwargs)

        def wait(seconds):
            trace.events.append(("settle", seconds, trace.voltage))
            return original_wait(seconds)

        def writing(kind, path):
            if not trace.active:
                return
            name = Path(path).name
            trace.events.append(("write", name, trace.voltage))
            assert trace.output
            assert trace.voltage == params.temp_switch or (trace.restore_error and trace.restore_attempted)
            if trace.save_error and trace.save_kind == kind and not trace.save_failed:
                trace.save_failed = True
                raise trace.save_error
            if slow and name.startswith("waveform_C"):
                started = perf_counter()
                sleep(.01)
                trace.slow_seconds.append(perf_counter() - started)

        original_save, original_savez, original_text = np.save, np.savez, Path.write_text

        def save(path, *args, **kwargs):
            writing("waveform", path)
            return original_save(path, *args, **kwargs)

        def savez(path, *args, **kwargs):
            writing("metadata" if Path(path).stem.endswith("_acquisition") else "axes", path)
            return original_savez(path, *args, **kwargs)

        def text(path, *args, **kwargs):
            writing("config" if Path(path).suffix == ".tmp" else "state", path)
            return original_text(path, *args, **kwargs)

        original_scan = acquisition.acquire_scan

        def scan(context):
            trace.active = True
            return original_scan(context)

        for module, name, replacement in ((acquisition, "acquire_scan", scan),
                (acquisition, "set_xy_sine_peak", peak), (acquisition, "wait", wait),
                (np, "save", save), (np, "savez", savez), (Path, "write_text", text)):
            stack.enter_context(patch.object(module, name, replacement))
        yield trace


@pytest.mark.parametrize("entrypoint,slow", [("steps", False), ("steps", True), ("full", False)])
def test_single_point_order_and_slow_disk(tmp_path, entrypoint, slow):
    with traced_scan(tmp_path, prepare=entrypoint == "steps", slow=slow) as trace:
        ctx = trace.ctx
        if entrypoint == "full":
            with patch.object(workflow, "Workflow", return_value=ctx):
                assert workflow.run(ctx.params, project_root=tmp_path) == ctx.run_dir
        else:
            ctx.execute(["A12", "A13", "A14"])
        assert ctx.closed and ctx.shutdown["completed"]
        starts = [index for index, event in enumerate(trace.events) if event[0] == "xy"]
        assert len(starts) == trace.attempts == 3
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(trace.events)
            events = trace.events[start:end]
            assert events[:5] == [("xy", float(ctx.axes[1][index]), 5.0),
                ("temp", 0.0), ("output", True), ("settle", ctx.params.system_settle_s, 0.0),
                ("read", index + 1, 0.0)]
            restored = events.index(("temp", 5.0))
            writes = [position for position, event in enumerate(events) if event[0] == "write"]
            assert 4 < restored < min(writes)
            assert [events[position][1] for position in writes if events[position][1].startswith("waveform_C")] == [
                f"waveform_C{index:04d}_acquisition.npz", f"waveform_C{index:04d}.npy"]
            np.testing.assert_array_equal(np.load(ctx.run_dir / f"raw/waveform_C{index:04d}.npy"), trace.acquired[index])
            with np.load(ctx.run_dir / f"raw/waveform_C{index:04d}_acquisition.npz") as metadata:
                assert metadata["acquisition_module"].item() == "daq"
                np.testing.assert_array_equal(metadata["time_s"], np.arange(160) / 800)
        if slow:
            assert len(trace.slow_seconds) == 6 and min(trace.slow_seconds) >= .009
        trace.hw.devices["tec"].set_enable.assert_not_called()
        trace.hw.devices["tec"].get_temperature.assert_not_called()


def test_temperature_metadata_matches_actual_zero_dc_output_on(tmp_path):
    with traced_scan(tmp_path) as trace:
        trace.ctx.execute(["A12", "A14"])
        config = yaml.safe_load((trace.ctx.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
        assert config["scan_params"]["temperature_switch_off_mode"] == "dc_zero_output_on"


@pytest.mark.parametrize("kind", ["sine", "known", "probe_am"])
def test_initial_config_marks_only_sine_raw_format(tmp_path, kind):
    from ..noise_spectrum_xy_known_noise import workflow as known_workflow, test_workflow as known_tests
    from ..noise_spectrum_xy_probe_am import workflow as probe_workflow, test_workflow as probe_tests
    module, params, rig = {
        "sine": (workflow, scan_params, fake_hardware),
        "known": (known_workflow, known_tests.scan_params, known_tests.rig),
        "probe_am": (probe_workflow, probe_tests.scan_params, probe_tests.rig),
    }[kind]
    copy_config(tmp_path)
    with rig(tmp_path) as hw:
        ctx = module.Workflow(params(), project_root=tmp_path)
        try:
            ctx.execute([stage.id for stage in ctx.acquisition_stages[:4]])
            config = yaml.safe_load((ctx.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
            assert config["experiment_id"] == ctx.experiment_id
            assert config["completion_status"] == "initializing"
            if kind == "sine":
                assert config["raw_format_version"] == 1
            else:
                assert "raw_format_version" not in config
            hw.acquire.assert_not_called()
        finally:
            ctx.finish()


@pytest.mark.parametrize("error_type", [WorkflowCancelled, KeyboardInterrupt])
def test_cancel_keeps_previous_point_and_stops_scan(tmp_path, error_type):
    with traced_scan(tmp_path) as trace:
        failure = error_type("第二点取消")
        trace.acquire_error = failure
        with pytest.raises(error_type) as caught:
            trace.ctx.execute(["A12", "A13"])
        assert caught.value is failure
        trace.ctx.finish(failure)
        assert trace.attempts == 2 and trace.ctx.shutdown["completed"]
        np.testing.assert_array_equal(np.load(trace.ctx.run_dir / "raw/waveform_C0000.npy"), trace.acquired[0])
        assert not (trace.ctx.run_dir / "raw/waveform_C0001.npy").exists()
        config = yaml.safe_load((trace.ctx.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
        assert config["completion_status"] == "cancelled" and config["failure_reason"] == str(failure)


def test_failed_point_keeps_historical_nan_and_continues(tmp_path):
    with traced_scan(tmp_path) as trace:
        trace.acquire_error = OSError("第二点 DAQ 失败")
        trace.ctx.execute(["A12", "A13", "A14"])
        raw = trace.ctx.run_dir / "raw"
        assert trace.attempts == 3 and np.isnan(np.load(raw / "waveform_C0001.npy")).all()
        assert not (raw / "waveform_C0001_acquisition.npz").exists()
        np.testing.assert_array_equal(np.load(raw / "waveform_C0002.npy"), trace.acquired[1])


@pytest.mark.parametrize("sample", [dict(r=0.0, x=0.0, y=0.0, theta=0.0),
    dict(r=1.0, x=0.0, y=1.0, theta=np.pi / 2), dict(r=1.0, x=np.nan, y=0.0, theta=0.0)])
def test_demod_phase_quality_blocks_successors(tmp_path, sample):
    copy_config(tmp_path)
    with fake_hardware(tmp_path) as hw:
        ctx = workflow.Workflow(scan_params(), project_root=tmp_path)
        ctx.execute([stage.id for stage in ACQUISITION[:7]])
        hw.samples.side_effect = [sample] * 6
        with pytest.raises((RuntimeError, ValueError)) as caught:
            ctx.execute(["A08", "A09"])
        assert ctx.states["A08"]["status"] == "failed"
        assert ctx.states["A09"]["status"] == "pending"
        with pytest.raises(ValueError, match="A08"):
            ctx.execute(["A09"])
        ctx.finish(caught.value)
        assert ctx.shutdown["completed"]


@pytest.mark.parametrize("restore,save_kind", [(True, None), (False, "metadata"),
                                               (False, "waveform"), (True, "metadata")])
def test_restore_and_writer_failures_rescue_read_data(tmp_path, restore, save_kind):
    with traced_scan(tmp_path) as trace:
        restore_error, save_error = OSError("恢复温控失败"), OSError("真实 writer 失败")
        trace.restore_error = restore_error if restore else None
        trace.save_error, trace.save_kind = save_error, save_kind
        primary = restore_error if restore else save_error
        with pytest.raises(OSError) as caught:
            trace.ctx.execute(["A12", "A13"])
        assert caught.value is primary
        assert trace.attempts == 1 and trace.restore_attempted
        raw = trace.ctx.run_dir / "raw"
        if save_kind != "waveform":
            np.testing.assert_array_equal(np.load(raw / "waveform_C0000.npy"), trace.acquired[0])
        if save_kind != "metadata":
            assert (raw / "waveform_C0000_acquisition.npz").is_file()
        assert not (raw / "waveform_C0001.npy").exists()
        config = yaml.safe_load((trace.ctx.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
        assert config["completion_status"] == "failed" and config["failure_reason"] == str(primary)
        trace.restore_error = None
        trace.ctx.finish(primary)
        assert trace.ctx.shutdown["completed"]
