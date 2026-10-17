"""本实验真实配对采集与 writer 的无硬件回归。"""
from contextlib import contextmanager, ExitStack
import json
from pathlib import Path
from time import perf_counter, sleep
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import yaml

from ...common import WorkflowCancelled
from . import acquisition, stages, workflow
from .test_workflow import copy_known_config, rig, scan_params


ACQUISITION_CASES = {
    "noise-spectrum-xy-known-noise": {
        "mode": "paired",
        "cases": {
            "order": ["test_pair_order_and_slow_disk"],
            "slow_disk": ["test_pair_order_and_slow_disk"],
            "cancel": ["test_second_state_failure_keeps_first_state"],
            "acquire_failure": ["test_second_state_failure_keeps_first_state"],
            "restore_failure": ["test_restore_and_save_failures_keep_original_error"],
            "save_failure": ["test_restore_and_save_failures_keep_original_error"],
            "partial_save": ["test_second_state_failure_keeps_first_state",
                             "test_restore_and_save_failures_keep_original_error"],
            "pair_second_failure": ["test_second_state_failure_keeps_first_state"],
            "entrypoints": ["test_pair_order_and_slow_disk"],
        },
        "sources": [
            "lab_workflows/experiment_modules/noise_spectrum_xy_known_noise/workflow.py",
            "lab_workflows/experiment_modules/noise_spectrum_xy_known_noise/acquisition.py",
            "lab_workflows/experiment_modules/noise_spectrum_xy_known_noise/excitation.py",
            "lab_workflows/experiment_modules/noise_spectrum_xy/acquisition.py",
            "lab_workflows/experiment_modules/noise_spectrum_xy/_helpers.py",
        ],
    },
}


@contextmanager
def traced_scan(root, *, prepare=True, slow=False):
    """只替换仪器 I/O；配置、窗口、采集编排与文件写入仍执行真实函数。"""
    copy_known_config(root)
    with rig(root) as hw, ExitStack() as stack:
        ctx = workflow.Workflow(scan_params(), project_root=root)
        if prepare:
            ctx.execute([stage.id for stage in stages.ACQUISITION[:12]])
        trace = SimpleNamespace(ctx=ctx, hw=hw, events=[], active=False, voltage=5.0,
            output=True, z=False, acquired=[], read_attempts=0, slow_seconds=[],
            acquire_error=None, restore_error=None, restore_attempted=False,
            save_error=None, save_kind=None, save_failed=False)

        def dc(value, *, channel):
            if trace.active:
                trace.events.append(("temp", float(value)))
                if value == 5.0:
                    trace.restore_attempted = True
                    if trace.restore_error:
                        raise trace.restore_error
            trace.voltage = float(value)

        def output(enabled, *, channel):
            trace.output = bool(enabled)
            if trace.active:
                assert enabled is True
                trace.events.append(("temp_output", enabled))

        hw.devices["dg_temp"].setup_dc.side_effect = dc
        hw.devices["dg_temp"].set_output.side_effect = output

        def z_output(enabled, *, channel):
            if channel == 1:
                trace.z = bool(enabled)
                if trace.active:
                    trace.events.append(("z", bool(enabled), trace.voltage))

        hw.devices["dg_sweep"].set_output.side_effect = z_output
        original_read = hw.acquire.side_effect

        def read(hfi, cfg, **kwargs):
            trace.read_attempts += 1
            trace.events.append(("read", trace.z, trace.voltage))
            assert trace.voltage == 0.0 and trace.output
            if trace.read_attempts == 2 and trace.acquire_error:
                raise trace.acquire_error
            results = original_read(hfi, cfg, **kwargs)
            result = results[0]
            result.signal_name = "sample.y"
            result.time = np.arange(len(result.values)) / kwargs["actual_rate"]
            result.config_snapshot = dict(acquisition_module="daq", requested_rate=kwargs["requested_rate"],
                actual_rate=kwargs["actual_rate"], demod_stream_rate=kwargs["actual_rate"],
                actual_duration=cfg.duration, time_source="test_instrument")
            trace.acquired.append(result.values.copy())
            return results

        hw.acquire.side_effect = read
        original_peak, original_wait = acquisition.set_xy_sine_peak, acquisition.wait_for_settle

        def peak(*args, **kwargs):
            trace.events.append(("xy", float(args[1]), trace.voltage))
            assert trace.voltage == 5.0 and trace.output
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
            assert trace.voltage == 5.0 or (trace.restore_error and trace.restore_attempted)
            if trace.save_error and trace.save_kind == kind and not trace.save_failed:
                trace.save_failed = True
                raise trace.save_error
            if slow and name.startswith("waveform_C"):
                started = perf_counter()
                sleep(.01)
                trace.slow_seconds.append(perf_counter() - started)

        original_save, original_savez = np.save, np.savez
        original_text, original_timing = Path.write_text, acquisition._append_pair_timing

        def save(path, *args, **kwargs):
            writing("waveform" if Path(path).name.startswith("waveform_C") else "initial", path)
            return original_save(path, *args, **kwargs)

        def savez(path, *args, **kwargs):
            writing("metadata" if Path(path).stem.endswith("_acquisition") else "initial", path)
            return original_savez(path, *args, **kwargs)

        def text(path, *args, **kwargs):
            writing("config" if Path(path).suffix == ".tmp" else "state", path)
            return original_text(path, *args, **kwargs)

        def timing(path, *args, **kwargs):
            writing("timing", path)
            return original_timing(path, *args, **kwargs)

        original_scan = acquisition.acquire_scan

        def scan(context):
            trace.active = True
            return original_scan(context)

        for module, name, replacement in (
            (acquisition, "acquire_scan", scan), (acquisition, "set_xy_sine_peak", peak),
            (acquisition, "wait_for_settle", wait), (acquisition, "_append_pair_timing", timing),
            (np, "save", save), (np, "savez", savez), (Path, "write_text", text),
        ):
            stack.enter_context(patch.object(module, name, replacement))
        yield trace


@pytest.mark.parametrize("entrypoint,slow", [("steps", False), ("steps", True), ("full", False)])
def test_pair_order_and_slow_disk(tmp_path, entrypoint, slow):
    with traced_scan(tmp_path, prepare=entrypoint == "steps", slow=slow) as trace:
        ctx = trace.ctx
        if entrypoint == "full":
            with patch.object(workflow, "Workflow", return_value=ctx):
                assert workflow.run(ctx.params, project_root=tmp_path) == ctx.run_dir
        else:
            ctx.execute(["A13", "A14", "A15"])
        assert ctx.closed and ctx.shutdown["completed"]
        assert trace.read_attempts == 6
        starts = [index for index, event in enumerate(trace.events) if event[0] == "xy"]
        for point, start in enumerate(starts):
            end = starts[point + 1] if point + 1 < len(starts) else len(trace.events)
            events = trace.events[start:end]
            assert events[0][2] == 5.0
            assert events[1] == ("z", bool(point % 2), 5.0)
            assert events[2:5] == [("temp", 0.0), ("temp_output", True),
                                  ("settle", ctx.params.system_settle_s, 0.0)]
            reads = [index for index, event in enumerate(events) if event[0] == "read"]
            assert [events[index][1:] for index in reads] == [(bool(point % 2), 0.0), (not bool(point % 2), 0.0)]
            assert events[reads[1] - 1] == ("settle", ctx.params.system_settle_s, 0.0)
            assert events[reads[1] - 2] == ("z", not bool(point % 2), 0.0)
            restored = events.index(("temp", 5.0))
            writes = [index for index, event in enumerate(events) if event[0] == "write"]
            assert reads[1] < restored < min(writes)
            pair_writes = [events[index][1] for index in writes
                           if events[index][1].startswith("waveform_C") or events[index][1] == "pair_timing.jsonl"]
            assert len(pair_writes) == 6
        assert len(starts) == 3
        raw = ctx.run_dir / "raw"
        timing = [json.loads(line) for line in (raw / "pair_timing.jsonl").read_text().splitlines()]
        assert [item["state"] for item in timing] == ["off", "on", "on", "off", "off", "on"]
        for record, expected in zip(timing, trace.acquired, strict=True):
            np.testing.assert_array_equal(np.load(raw / record["file"]), expected)
            with np.load(raw / record["file"].replace(".npy", "_acquisition.npz")) as metadata:
                assert metadata["acquisition_module"].item() == "daq"
                assert len(metadata["time_s"]) == len(expected)
        config = yaml.safe_load((ctx.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
        assert config["scan_params"]["temperature_switch_off_mode"] == "dc_zero_output_on"
        assert "raw_format_version" not in config
        trace.hw.devices["tec"].set_enable.assert_not_called()
        trace.hw.devices["tec"].get_temperature.assert_not_called()
        if slow:
            assert len(trace.slow_seconds) == 12 and min(trace.slow_seconds) >= .009


@pytest.mark.parametrize("error_type", [OSError, WorkflowCancelled, KeyboardInterrupt])
def test_second_state_failure_keeps_first_state(tmp_path, error_type):
    with traced_scan(tmp_path) as trace:
        failure = error_type("配对第二态中断")
        trace.acquire_error = failure
        with pytest.raises(error_type) as caught:
            trace.ctx.execute(["A13", "A14"])
        assert caught.value is failure
        trace.ctx.finish(failure)
        assert trace.read_attempts == 2 and trace.ctx.shutdown["completed"]
        assert trace.ctx.states["A14"]["status"] == "pending"
        raw = trace.ctx.run_dir / "raw"
        np.testing.assert_array_equal(np.load(raw / "waveform_C0000_off.npy"), trace.acquired[0])
        assert (raw / "waveform_C0000_off_acquisition.npz").is_file()
        assert not (raw / "waveform_C0000_on.npy").exists()
        records = [json.loads(line) for line in (raw / "pair_timing.jsonl").read_text().splitlines()]
        assert [item["state"] for item in records] == ["off"]
        config = yaml.safe_load((trace.ctx.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
        assert config["completion_status"] == ("failed" if error_type is OSError else "cancelled")
        assert config["failure_reason"] == str(failure)
        assert "raw/waveform_C0000_off.npy" in config["data_files"]
        first_write = next(index for index, event in enumerate(trace.events)
                           if event[:2] == ("write", "waveform_C0000_off.npy"))
        assert trace.events.index(("temp", 5.0)) < first_write


@pytest.mark.parametrize("restore,save_kind,acquire_fails", [
    (True, None, False), (False, "waveform", False), (False, "metadata", False),
    (False, "timing", False), (True, None, True), (False, "waveform", True),
    (True, "waveform", True),
])
def test_restore_and_save_failures_keep_original_error(tmp_path, caplog, restore, save_kind, acquire_fails):
    with traced_scan(tmp_path) as trace:
        trace.restore_error = RuntimeError("温控恢复失败") if restore else None
        trace.save_error = OSError("真实 writer 写盘失败") if save_kind else None
        trace.save_kind = save_kind
        trace.acquire_error = OSError("原始第二态采集错误") if acquire_fails else None
        expected = trace.acquire_error or trace.restore_error or trace.save_error
        with pytest.raises(type(expected)) as caught:
            trace.ctx.execute(["A13", "A14"])
        assert caught.value is expected
        trace.ctx.finish(expected)
        assert trace.read_attempts == 2 and trace.ctx.closed
        assert trace.ctx.states["A14"]["status"] == "pending"
        assert trace.ctx.shutdown["completed"] is not restore
        if restore:
            assert "温控恢复失败" in "；".join(trace.ctx.shutdown["errors"])
            assert trace.ctx.states["A15"]["status"] == "failed"
            saved = json.loads((trace.ctx.run_dir / "acquisition_state.json").read_text(encoding="utf-8"))
            assert saved["safety_shutdown"] == trace.ctx.shutdown
        if save_kind:
            assert trace.save_failed and "采集数据保存失败" in caplog.text
        raw = trace.ctx.run_dir / "raw"
        assert (raw / "waveform_C0000_off.npy").exists() is (save_kind != "waveform")
        assert (raw / "waveform_C0000_off_acquisition.npz").exists() is (save_kind != "metadata")
        if not acquire_fails:
            np.testing.assert_array_equal(np.load(raw / "waveform_C0000_on.npy"), trace.acquired[1])
            assert (raw / "waveform_C0000_on_acquisition.npz").is_file()
        config = yaml.safe_load((trace.ctx.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
        assert config["completion_status"] == "failed" and config["failure_reason"] == str(expected)
        assert len([event for event in trace.events if event[0] == "xy"]) == 1
        first_write = next(index for index, event in enumerate(trace.events)
                           if event[:2] == ("write", "waveform_C0000_off.npy"))
        assert trace.events.index(("temp", 5.0)) < first_write
