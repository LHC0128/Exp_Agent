"""真实静磁场原始曲线的来源资格与检测增益，不改写来源目录。"""
import hashlib

import numpy as np
import pytest
import yaml

from . import calibration_source
from .test_analysis import static_source


@pytest.mark.parametrize("schema,raw", [(3, None), (4, None), (4, 3), (99, 3)])
def test_parameter_schema_is_independent_of_static_raw_format(tmp_path, schema, raw):
    directory = static_source(tmp_path, schema_version=schema)
    config_path = directory / "experiment_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if raw is not None:
        config["raw_format_version"] = raw
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    before = {path: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
              for path in directory.rglob("*") if path.is_file()}
    fit = calibration_source.load_source(tmp_path, "source", 5373.638)
    assert fit["is_valid"] and fit["n_points"] == 41
    assert fit["slope_v_per_hz"] == pytest.approx(.001 / .025**2 / 5373.638)
    assert fit["fingerprints"] == {str(path.relative_to(directory)).replace("\\", "/"): data[1]
                                    for path, data in before.items()}
    assert [choice["value"] for choice in calibration_source.source_choices(tmp_path)] == ["source"]
    assert before == {path: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
                      for path in directory.rglob("*") if path.is_file()}


@pytest.mark.parametrize("change", [
    {"schema_version": 99}, {"raw_format_version": 99}, {"raw_format_version": True},
    {"acquisition_method": "ramp"}, {"experiment_id": "noise-spectrum-xy"},
    {"experiment_type": "Other"}, {"completion_status": "running"},
])
def test_unverified_static_format_identity_and_running_sources_are_rejected(tmp_path, change):
    directory = static_source(tmp_path)
    path = directory / "experiment_config.yaml"
    path.write_text(yaml.safe_dump(yaml.safe_load(path.read_text()) | change), encoding="utf-8")
    with pytest.raises(ValueError):
        calibration_source.load_source(tmp_path, "source", 5373.638)
    assert calibration_source.source_choices(tmp_path) == []
    assert not (directory / "results").exists()


@pytest.mark.parametrize("key,value", [
    ("completed_mask", np.full(41, False)), ("offsets_v", np.full(41, np.nan)),
    ("x_median", np.full(41, np.inf)), ("y_median", np.ones(41)),
    ("r_median", np.full(41, np.nan)), ("sample_counts", np.zeros(41)),
    ("actual_rate_sa_s", np.zeros(41)), ("actual_duration_s", np.full(41, np.nan)),
    ("schema_version", np.array(4)),
])
def test_source_data_quality_is_not_relaxed(tmp_path, key, value):
    directory = static_source(tmp_path, schema_version=4)
    path = directory / "raw/dispersion_scan.npz"
    with np.load(path, allow_pickle=False) as stored:
        data = {name: stored[name] for name in stored.files}
    np.savez(path, **(data | {key: value}))
    with pytest.raises(ValueError):
        calibration_source.load_source(tmp_path, "source", 5373.638)
    assert not (directory / "results").exists()
