"""采集六步契约：假时钟、假仪器和真实保存调用，不连接硬件。"""

import ast
import math
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from .acquisition_policy import ACQUISITION_EXCEPTIONS, migrate_system_settle
from .common import WorkflowCancelled
from .experiments.registry import list_experiments
from .steps.acquisition_window import DeferredAcquisitionWrites, acquisition_window
from .steps.scope_waveform import temperature_gated_acquire
from .steps.temperature import configure_temperature_control


def _legacy_function(filename, name, namespace):
    """只执行真实业务函数定义，跳过旧脚本顶层的全部硬件连接。"""
    path = Path(__file__).resolve().parents[1] / "experiments" / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


class AcquisitionOrderTest(unittest.TestCase):
    def test_actual_gate_returns_before_slow_disk_write(self):
        events, clock = [], [0.0]
        enabled = [True]

        def switch(device, value, **kwargs):
            enabled[0] = value
            events.append(("on" if value else "off", clock[0]))

        def wait(seconds):
            events.append(("settle", seconds))
            clock[0] += seconds

        def capture():
            self.assertFalse(enabled[0])
            events.append(("acquire", clock[0]))
            clock[0] += 2
            return np.arange(4)

        def save(path, data):
            self.assertTrue(enabled[0], "温控关闭期间禁止写盘")
            events.append(("save", clock[0]))
            clock[0] += 30
            np.save(path, data)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "waveform.npy"
            data = temperature_gated_acquire(temp_switch=object(), temp_channel=2,
                off_settle_s=0.5, on_settle_s=0, acquire=capture,
                prepare=lambda: events.append(("configure", clock[0])),
                check_cancelled=lambda: None, sleep=wait,
                set_temperature_switch=switch, cancellation=None)
            save(path, data)
            np.testing.assert_array_equal(np.load(path), np.arange(4))
        self.assertEqual([item[0] for item in events], ["configure", "off", "settle", "acquire", "on", "save"])
        self.assertEqual(events[4][1] - events[1][1], 2.5)

    def test_pair_partial_data_restored_then_saved_on_cancel(self):
        events = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "off.npy"
            def save(path, values):
                self.assertEqual(events[-1], "on")
                np.save(path, values)
            values = np.arange(4)
            with self.assertRaises(WorkflowCancelled):
                with DeferredAcquisitionWrites() as pending:
                    with acquisition_window(lambda on: events.append("on" if on else "off"), lambda _: None, 0):
                        pending.add(save, path, values)
                        values[:] = -1
                        raise WorkflowCancelled("第二态取消")
            np.testing.assert_array_equal(np.load(path), np.arange(4))

    def test_restore_failure_stops_but_keeps_data(self):
        events = []
        def restore():
            events.append("restore")
            raise OSError("开关通信失败")
        with self.assertRaisesRegex(OSError, "开关通信失败"):
            with DeferredAcquisitionWrites(restore=restore) as pending:
                pending.add(lambda: events.append("save"))
        self.assertEqual(events, ["restore", "save"])

    def test_cleanup_and_disk_errors_do_not_replace_original(self):
        original = WorkflowCancelled("原始取消")
        with self.assertRaises(WorkflowCancelled) as caught:
            with DeferredAcquisitionWrites(restore=Mock(side_effect=OSError("恢复失败"))) as pending:
                pending.add(Mock(side_effect=OSError("磁盘失败")))
                raise original
        self.assertIs(caught.exception, original)

    def test_failed_off_also_attempts_on(self):
        events = []
        def switch(enabled):
            events.append(enabled)
            if not enabled:
                raise OSError("关断失败")
        with self.assertRaisesRegex(OSError, "关断失败"):
            with acquisition_window(switch, lambda _: None, 0):
                self.fail("关闭失败不能启动采集")
        self.assertEqual(events, [False, True])

    def test_settle_migration_uses_max_and_new_key_wins(self):
        defaults = {"OLD_CONFIG": 0.2, "OLD_OFF": 0.1}
        self.assertEqual(migrate_system_settle({"OLD_CONFIG": 0.4, "OLD_OFF": 0.7}, defaults), {"SYSTEM_SETTLE_S": 0.7})
        self.assertEqual(migrate_system_settle({"SYSTEM_SETTLE_S": 0, "OLD_OFF": 3}, defaults), {"SYSTEM_SETTLE_S": 0})
        for value in (-1, float("nan"), float("inf"), True, "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                migrate_system_settle({"SYSTEM_SETTLE_S": value}, defaults)

    def test_tec_only_sets_ui_target(self):
        tec = Mock()
        tec.set_enable.side_effect = AssertionError("不能启停 TEC")
        tec.get_temperature.side_effect = AssertionError("不能轮询温度")
        waiter = Mock(side_effect=AssertionError("不能判稳"))
        result = configure_temperature_control(tec, 92.5, stability_waiter=waiter)
        self.assertEqual(tec.method_calls, [("set_target_temperature", (92.5,), {"channel": 1})])
        self.assertEqual(result.target_temperature_c, 92.5)
        self.assertIsNone(result.actual_temperature_c)
        self.assertTrue(result.controlled_by_experiment)
        skipped = configure_temperature_control(None, 92.5)
        self.assertEqual(skipped.target_temperature_c, 92.5)
        self.assertIsNone(skipped.actual_temperature_c)

    def test_mx_real_point_restore_failure_still_saves_payload(self):
        from .experiment_modules.mx_y_rf_sensitivity import workflow as mx
        events = []
        params = SimpleNamespace(system_settle_s=0.7, temp_switch_on_lag_s=0,
            r_point_max_attempts=1, demod_idx=0, response_rate_sa_s=10,
            r_bad_point_std_threshold_v=1.0)
        payload = {"time_s": np.arange(4) / 10, "r": np.ones(4)}
        def switch(device, enabled, **kwargs):
            events.append("restore" if enabled else "off")
            if enabled:
                raise OSError("模拟温控恢复失败")
        original_save = np.savez
        def save(*args, **kwargs):
            self.assertEqual(events[-1], "restore")
            events.append("save")
            original_save(*args, **kwargs)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(mx, "set_temperature_switch", side_effect=switch), \
                patch.object(mx, "_sleep", side_effect=lambda seconds: events.append("settle")), \
                patch.object(mx, "acquire_r", side_effect=lambda *a, **k: events.append("acquire") or payload), \
                patch.object(mx.np, "savez", side_effect=save):
            with self.assertRaisesRegex(OSError, "模拟温控恢复失败"):
                mx._acquire_valid_r_point(params, SimpleNamespace(raw=Path(directory)),
                    {"temp_switch": object(), "hf2": object()}, {"temp_switch": 2},
                    file_stem="response", metadata={}, set_y_rf=lambda: events.append("configure"),
                    settle_time_s=0.7, duration_s=0.4, actual_rate=10, device_id="fake")
            with np.load(Path(directory) / "response_attempt_00.npz") as saved:
                np.testing.assert_array_equal(saved["r_v"], payload["r"])
        self.assertEqual(events, ["configure", "off", "settle", "acquire", "restore", "save"])

    def test_bb_shot_prepares_scope_before_off_and_keeps_frame_on_restore_failure(self):
        for filename in ("BB_Rpump_DutyScan_Burst.py", "BB_Rpump_PowerScan_Burst.py"):
            for fault in (None, "restore", "acquire"):
                with self.subTest(experiment=filename, fault=fault), tempfile.TemporaryDirectory() as directory:
                    run_dir = Path(directory)
                    (run_dir / "raw").mkdir()
                    enabled, events = [True], []
                    original = WorkflowCancelled("模拟采集取消")
                    def warm(*args, **kwargs):
                        self.assertTrue(enabled[0], "示波器准备与量程调整必须在温控开启时")
                        events.append("prepare")
                    def switch(value, **kwargs):
                        events.append("on" if value else "off")
                        if value and fault == "restore":
                            raise OSError("恢复失败")
                        enabled[0] = bool(value)
                    def read(*args, **kwargs):
                        self.assertFalse(enabled[0])
                        events.append("acquire")
                        if fault == "acquire":
                            raise original
                        return SimpleNamespace(time=np.arange(4), voltage=np.arange(4))
                    namespace = dict(np=np, devices={"dg_temp_sw": SimpleNamespace(setup_dc=switch)},
                        dg_rf=SimpleNamespace(set_burst_state=warm, set_pulse_dcycle=warm),
                        dg_laser=SimpleNamespace(setup_dc=warm), pump_cfg={"channel": 1},
                        TEMP_SWITCH_CHANNEL=2, TEMP_SWITCH_ON_V=5.0, TEMP_SWITCH_OFF_V=0,
                        SYSTEM_SETTLE_S=0.7, TEMP_SWITCH_ON_DELAY_S=0.1, BURST_BURST_PERIOD_S=0.1,
                        BURST_DURATION_S=0.1, SDS_PD_CHANNEL=1, actual_pts=4,
                        run_dir=run_dir, validate_safety_limit=lambda *a: None,
                        sds_inst=SimpleNamespace(trigger_run=warm, trigger_stop=lambda: None,
                            wait_for_trigger=lambda **kwargs: True, set_channel_scale=warm),
                        time=SimpleNamespace(sleep=lambda seconds: events.append(("wait", seconds)), time_ns=lambda: 123),
                        acquirer=SimpleNamespace(acquire_channel=read),
                        auto_scale_signal=lambda *args: (2.0, True),
                        acquisition_window=acquisition_window, DeferredAcquisitionWrites=DeferredAcquisitionWrites)
                    shot = _legacy_function(filename, "acquire_single_shot", namespace)
                    cfg = SimpleNamespace(acquire_delay=0, timebase_scale=0.1,
                        horizontal_divisions=10, channels=[SimpleNamespace(scale=1)])
                    if fault:
                        with self.assertRaises((OSError, WorkflowCancelled)) as caught:
                            shot(0.2, 1.0, cfg)
                        if fault == "acquire":
                            self.assertIs(caught.exception, original)
                    else:
                        result = shot(0.2, 1.0, cfg)
                        np.testing.assert_array_equal(result["signal_v"], np.arange(4))
                        self.assertTrue(enabled[0])
                    self.assertLess(events.index("prepare"), events.index("off"))
                    self.assertLess(events.index("off"), events.index(("wait", 0.7)))
                    self.assertLess(events.index(("wait", 0.7)), events.index("acquire"))
                    self.assertLess(events.index("acquire"), events.index("on"))
                    rescued = list((run_dir / "raw").glob("recovery_*.npz"))
                    self.assertEqual(bool(rescued), fault == "restore")
                    if rescued:
                        with np.load(rescued[0]) as data:
                            np.testing.assert_array_equal(data["signal_v"], np.arange(4))

    def test_t1_scale_retry_reopens_a_prepared_window(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            (run_dir / "raw").mkdir()
            enabled, captures = [True], []
            def warm(*args, **kwargs):
                self.assertTrue(enabled[0])
            def switch(value, **kwargs):
                enabled[0] = bool(value)
            def read(config):
                self.assertFalse(enabled[0])
                captures.append(len(captures))
                return SimpleNamespace(time=np.arange(4), voltage=np.ones(4))
            cfg = SimpleNamespace(channels=[SimpleNamespace(scale=1)])
            namespace = dict(np=np, devices={"dg_probe": SimpleNamespace(setup_dc=warm),
                "dg_ts": SimpleNamespace(setup_dc=switch)}, cfg_probe={"channel": 1}, cfg_ts={"channel": 2},
                PUMP_SWITCH_FREQ=1.0, SCOPE_VOLT_SCALE=1.0, ACQ_REPEATS=2, VERT_DIVS=8,
                PUMP_PREP_TIME=0.1, SYSTEM_SETTLE_S=0.7,
                time=SimpleNamespace(sleep=lambda seconds: None, time_ns=lambda: 123),
                validate_safety_limit=lambda *a: None, run_dir=run_dir,
                build_scope_config=lambda scale: cfg, _prepare_ch1_single=warm,
                _acquire_ch1_single=read, scope=SimpleNamespace(set_channel_scale=warm),
                auto_scale_signal=Mock(side_effect=[(2.0, True), (2.0, False), (2.0, False)]),
                _fit_T1=lambda *args: (1.0, 0.1, [1], np.eye(1)),
                acquisition_window=acquisition_window, DeferredAcquisitionWrites=DeferredAcquisitionWrites)
            measure = _legacy_function("T1_Calibration.py", "measure_T1_single", namespace)
            result = measure(0.2)
            self.assertEqual(result["n_acquired"], 2)
            self.assertEqual(len(captures), 3)
            self.assertTrue(enabled[0])
            def failed_restore(value, **kwargs):
                if value:
                    raise OSError("T1 温控恢复失败")
                enabled[0] = False
            namespace["devices"]["dg_ts"].setup_dc = failed_restore
            with self.assertRaisesRegex(OSError, "T1 温控恢复失败"):
                measure(0.2)
            self.assertEqual(len(captures), 4)
            with np.load(run_dir / "raw/recovery_t1_123.npz") as saved:
                np.testing.assert_array_equal(saved["signal_v"], np.ones(4))

    def test_legacy_xy_v2_group_prepares_first_state_and_reuses_system_wait(self):
        path = Path(__file__).resolve().parents[1] / "experiments/Noise_Spectrum_XY_Ctrl_v2.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        group = next(node for node in tree.body if isinstance(node, ast.With) and any(
            isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr == "set_phase_adjust" for call in ast.walk(node)))
        index = tree.body.index(group)
        enabled, events = [True], []
        def configure(*args, **kwargs):
            events.append(("configure", enabled[0]))
        def switch(value, **kwargs):
            enabled[0] = bool(value)
            events.append("on" if value else "off")
        def sample(*args, **kwargs):
            self.assertFalse(enabled[0])
            self.assertEqual(events[-1], ("settle", 0.7))
            events.append("acquire")
            return {"theta": 0.0}
        namespace = dict(acquisition_window=acquisition_window, SYSTEM_SETTLE_S=0.7,
            dg_comp=SimpleNamespace(set_output=configure, set_phase_adjust=configure),
            dg_am=SimpleNamespace(set_output=configure), dg_temp=SimpleNamespace(set_output=switch),
            time=SimpleNamespace(sleep=lambda seconds: events.append(("settle", seconds))),
            demod=SimpleNamespace(read_demod_sample=sample), hfi=object(), math=math,
            X_CARRIER_PHASE=0.0, Y_CARRIER_PHASE=90.0)
        # 保留真实 Cell 的首态准备和整个两通道校相组，跳过顶层连接。
        exec(compile(ast.Module(body=tree.body[index-3:index+1], type_ignores=[]), str(path), "exec"), namespace)
        self.assertEqual(events[:3], [("configure", True)] * 3)
        self.assertEqual([item for item in events if isinstance(item, tuple) and item[0] == "settle"],
                         [("settle", 0.7), ("settle", 0.7)])
        self.assertEqual(events[-1], "on")
        self.assertTrue(enabled[0])

    def test_registry_coverage_has_one_system_wait_per_ordinary_experiment(self):
        definitions = list_experiments()
        self.assertTrue(ACQUISITION_EXCEPTIONS.issubset({d.id for d in definitions}))
        for definition in definitions:
            if definition.id in ACQUISITION_EXCEPTIONS:
                continue
            with self.subTest(experiment=definition.id):
                schema = definition.schema()
                fields = schema["fields"]
                self.assertEqual(sum(f["name"] == "SYSTEM_SETTLE_S" for f in fields), 1)
                if definition.execution_mode == "typed_workflow":
                    cls = definition.experiment_runner.__self__.params_type
                    self.assertEqual(schema["schema_version"], cls.schema_version)
                    old = dict(cls.system_settle_legacy_defaults)
                    old.update({key: 99 for key in cls.removed_temperature_keys})
                    params = cls.from_external(old)
                    self.assertEqual(params.system_settle_s, max(cls.system_settle_legacy_defaults.values(), default=cls().system_settle_s))
                    self.assertFalse(set(params.to_external()).intersection(old))


if __name__ == "__main__":
    unittest.main()
