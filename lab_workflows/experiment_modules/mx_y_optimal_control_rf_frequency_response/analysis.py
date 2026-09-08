"""Mx Y 最优控制 RF 频率响应观察版离线绘图。"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import yaml
from ...experiment_runtime import runtime_run_dir
from ...plotting import new_figure, save_figure, set_plot_style, format_axis, style_legend

def analyze(run_dir: str | Path) -> dict[str, object]:
    root = Path(run_dir); raw = root / "raw"; results = root / "results"; results.mkdir(parents=True, exist_ok=True)
    path = raw / "phase_frequency_scan.npz"
    if not path.is_file(): raise FileNotFoundError(path)
    with np.load(path, allow_pickle=True) as data:
        frequency = np.asarray(data["frequency_hz"], dtype=float); phase = np.asarray(data["phase_deg"], dtype=float)
        arrays = {k: np.asarray(data[k], dtype=float) for k in ("r_mean_v", "r_std_v")}
        attempts = np.asarray(data["accepted_attempt_index"], dtype=int); files = np.asarray(data["accepted_file"], dtype=object)
    set_plot_style("paper")
    outputs = []
    fig, ax = new_figure(kind="wide")
    image = ax.pcolormesh(phase, frequency, arrays["r_mean_v"], shading="auto", cmap="viridis")
    format_axis(ax, xlabel="Y RF burst phase (deg)", ylabel="Y RF frequency (Hz)")
    fig.colorbar(image, ax=ax, label="Demod0 R (V)")
    out = results / "r_phase_frequency.png"; save_figure(fig, out); outputs.append(out.name)
    valid = np.isfinite(arrays["r_mean_v"])
    analysis = {"experiment_id": "mx-y-optimal-control-rf-frequency-response", "scan_mode": "frequency_phase_observation", "acquisition_signals": ["Demod0 R"], "frequency_hz": frequency.tolist(), "phase_deg": phase.tolist(), "shape": list(arrays["r_mean_v"].shape), "valid_points": int(np.count_nonzero(valid)), "total_points": int(valid.size), "quality": {"accepted_attempt_index": attempts.tolist(), "accepted_file": files.tolist(), "r_std_v": arrays["r_std_v"].tolist()}, "files": outputs}
    (results / "analysis.yaml").write_text(yaml.safe_dump(analysis, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (results / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, **analysis}

def main() -> int:
    run_dir = runtime_run_dir()
    if run_dir is None: raise FileNotFoundError("未指定运行目录")
    analyze(run_dir); return 0

if __name__ == "__main__": raise SystemExit(main())
