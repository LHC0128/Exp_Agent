"""只读校验续测来源，复制完整点；失败点始终重新校相并整点重测。"""
from copy import deepcopy
from pathlib import Path
import hashlib
import shutil
import numpy as np
import yaml
from ...experiment_runtime import check_cancelled
from ...steps.run_directory import write_run_config
from ...instrument_config import instrument_config_snapshot
from ...control_sources import resolve_control_results_root
from .analysis_steps import load_inputs
from .models import RFControlScaleParams
from . import run_format

OPERATIONAL_KEYS = {"RESUME_RUN_ID", "TEC_EXTERNAL_CONTROL", "RUN_TAG"}
COUNTERS = ("response_points_completed", "noise_records_completed", "bandwidth_points_completed")


def resume_plan(params, root):
    """不连接仪器、不改文件；任何完整点损坏或配置变化均拒绝续测。"""
    run_id = params.resume_run_id
    base = (Path(root) / "data" / run_format.DATA_TYPE).resolve()
    source = (base / run_id).resolve()
    if not run_id or "/" in run_id or "\\" in run_id or source.parent != base or source.name != run_id:
        raise ValueError("续测来源必须是本实验目录下的单个运行 ID")
    config = yaml.safe_load((source / "experiment_config.yaml").read_text(encoding="utf-8"))
    if reason := run_format.format_block_reason(config):
        raise ValueError(reason)
    if config["raw_format_version"] != run_format.RAW_FORMAT_VERSION:
        raise ValueError("续测仅接受每点独立四级校相的 v2 数据")
    if config.get("completion_status") not in ("failed", "cancelled", "completed"):
        raise ValueError("续测来源尚未结束，不能与存活采集并发")
    original = RFControlScaleParams.from_external(config["parameters"])
    original.validate_model()
    if {k: v for k, v in original.acquisition_parameters().items() if k not in OPERATIONAL_KEYS} != {
            k: v for k, v in params.acquisition_parameters().items() if k not in OPERATIONAL_KEYS}:
        raise ValueError("续测的控制、倍率、采集参数或标定与来源运行不一致")
    current = instrument_config_snapshot(root)
    for key in ("device_library_revision", "physical_mapping_revision"):
        if config.get(key) != current[key]:
            raise ValueError("设备库或映射已变化，不能自动合并续测")
    points = load_inputs(source, config, acquired_only=True)
    expected_counts = (params.rf_amp_points, params.noise_n_avg if params.include_noise else 0, params.bw_freq_points)
    for record, path, child in points:
        check_cancelled()
        for key, count in zip(COUNTERS, expected_counts):
            if child.get(key) != count or record.get(key) != count:
                raise ValueError(f"{path.name} 完整点计数不一致：{key}")
        waveform = resolve_control_results_root("oc_sens") / child["parameters"]["ARB_WAVEFORM_FILE"]
        if hashlib.sha256(waveform.read_bytes()).hexdigest() != child.get("control_source_sha256"):
            raise ValueError("理论控制源已改变，不能沿用旧点")
        for file in path.rglob("*"):
            if file.is_symlink():
                raise ValueError("续测点包含符号链接，拒绝复制")
        for name, fields, count, grid in (
            ("response_data.npz", ("amplitudes_V", "demod3_x_V", "demod3_y_V", "demod3_r_V"),
             params.rf_amp_points, np.linspace(params.rf_amp_start, params.rf_amp_stop, params.rf_amp_points)),
            ("bandwidth_data.npz", ("frequency_Hz", "demod3_r_V"),
             params.bw_freq_points, np.linspace(params.bw_freq_start, params.bw_freq_stop, params.bw_freq_points)),
        ):
            with np.load(path / "raw" / name, allow_pickle=False) as data:
                if any(data[key].shape != (count,) or not np.isfinite(data[key]).all() for key in fields):
                    raise ValueError(f"完整点原始数组不完整：{name}")
                if not np.allclose(data[fields[0]], grid):
                    raise ValueError(f"完整点扫描轴与参数不一致：{name}")
        for file in (path / "raw").glob("noise_D3_*.npz"):
            check_cancelled()
            with np.load(file, allow_pickle=False) as data:
                values = data["noise_x_V"]
                rate = float(data["actual_rate_Sa_s"])
                if values.ndim != 1 or values.size < 2 or not np.isfinite(values).all() or not np.isfinite(rate) or rate <= 0:
                    raise ValueError(f"完整点噪声记录无效：{file.name}")
    if len(points) == len(params.control_waveform_files) * len(params.control_scales):
        raise ValueError("来源运行已采齐全部点，无需补测")
    return source, config, points


def copy_completed_points(parent):
    source, config, points = resume_plan(parent.params, parent.root)
    records = []
    for record, path, child in points:
        check_cancelled()
        target = parent.directory.root / record["path"]
        shutil.copytree(path, target)
        origin = dict(run_id=source.name, path=record["path"])
        # 原始参数、时间和校相结果保持原值，只在副本注明来源。
        child = deepcopy(child)
        child["resumed_from"] = origin
        write_run_config(target / "experiment_config.yaml", child)
        records.append(dict(record, status="acquired", resumed_from=origin))
    parent.state.update(resume_source_run_id=source.name, reused_points=len(records),
        resume_source_temperature_control=deepcopy(config.get("temperature_control")),
        resume_source_safety_shutdown=deepcopy(config.get("safety_shutdown")),
        calibration_points_completed=len(records))
    for key in COUNTERS:
        parent.state[key] = sum(record[key] for record in records)
    parent.save()
    return records
