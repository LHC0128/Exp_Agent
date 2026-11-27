"""三档载波幅度下标准 Mx 相图与噪声路径的对比。

读正式实验 mx-standard-rf-phase-sensitivity 各轮 results/summary.json，按 Y_RF_VPP
归并成一张表与一张图：相图半径与切线斜率是否随幅度线性、3-50 Hz 噪声底如何随幅度变化，
以及 theta 与 y 两条路径在这些轮次里是否仍然等价。只读运行目录，不连接仪器。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from lab_workflows.common import data_root  # noqa: E402

DATA_TYPE = "mx_standard_rf_phase_sensitivity"
BAND = "band_3_50_hz"


def summarize(run_dir: Path) -> dict:
    """取一轮的关键量；未完成或缺少分析产物的轮次返回 None。"""
    summary_path = run_dir / "results" / "summary.json"
    if not summary_path.is_file():
        return None
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    diagram, fit, band = summary["diagram"], summary["circle_fit"], summary[BAND]
    working = summary["working_point"]
    return {
        "run": run_dir.name,
        "vpp": float(summary["rf_vpp"]),
        "radius_mV": fit["radius_v"] * 1e3,
        "rms_residual_uV": fit["rms_residual_v"] * 1e6,
        "segments": int(summary["segments"]),
        "accepted": (diagram["accepted_count"], diagram["accepted_total"]),
        "tangent_mV_per_rad": diagram["tangent_median_v_per_rad"] * 1e3,
        "tangent_over_radius": diagram["tangent_over_radius"],
        "tangent_spread_pct": diagram["tangent_relative_spread"] * 100.0,
        "theta_urad": band["theta_rad_per_sqrt_hz"] * 1e6,
        "y_route_urad": band["y_over_slope_rad_per_sqrt_hz"] * 1e6,
        "ratio": band["theta_over_y_route_ratio"],
        "mains_urad": band["mains_theta_rad_per_sqrt_hz"] * 1e6,
        "command_deg": working["command_deg"],
        "noise_angle_deg": working["noise_phasor_angle_deg"],
        "gs200_ma": summary["gs200_target_ma"],
        "verdict": summary["verdict"],
    }


def collect(root: Path) -> list[dict]:
    """按幅度归并：同一幅度多轮时取最新一轮。"""
    newest: dict[float, tuple[str, dict]] = {}
    for run_dir in sorted((root / DATA_TYPE).glob("*")):
        item = summarize(run_dir)
        if item is None:
            continue
        if item["vpp"] not in newest or run_dir.name > newest[item["vpp"]][0]:
            newest[item["vpp"]] = (run_dir.name, item)
    return [row for _, (_name, row) in sorted(newest.items())]


def report(rows: list[dict]) -> None:
    header = f"{'Vpp':>5} {'run':<26} {'R/mV':>7} {'txt/R':>6} {'txtspread':>9} {'theta':>8} {'y-route':>8} {'ratio':>6} {'mains':>7} {'seg':>3}"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(f"{r['vpp']:>5.2f} {r['run']:<26} {r['radius_mV']:>7.3f} {r['tangent_over_radius']:>6.3f} "
              f"{r['tangent_spread_pct']:>8.1f}% {r['theta_urad']:>8.1f} {r['y_route_urad']:>8.1f} "
              f"{r['ratio']:>6.3f} {r['mains_urad']:>7.1f} {r['segments']:>3}")
    for r in rows:
        print(f"{r['run']}: 工作点 {r['command_deg']:.0f}°、相位 {r['noise_angle_deg']:.1f}°、"
              f"GS200 {r['gs200_ma']:.4f} mA、段 {r['accepted'][0]}/{r['accepted'][1]}、{r['verdict']}")


def plot(rows: list[dict], out: Path) -> Path:
    """左图：半径与切线斜率随幅度；右图：3-50 Hz 噪声底随幅度。"""
    vpp = [r["vpp"] for r in rows]
    fig, (ax_amp, ax_noise) = plt.subplots(1, 2, figsize=(10.5, 4.2))
    ax_amp.plot(vpp, [r["radius_mV"] for r in rows], "o-", label="radius R")
    ax_amp.plot(vpp, [r["tangent_mV_per_rad"] for r in rows], "s--", label="tangent slope")
    ax_amp.set_xscale("log")
    ax_amp.set_xlabel("Y RF amplitude (Vpp)")
    ax_amp.set_ylabel("amplitude (mV, mV/rad)")
    ax_amp.set_title("Phase diagram: radius vs tangent slope")
    ax_amp.grid(alpha=0.3, which="both")
    ax_amp.legend(fontsize=9)
    ax_noise.plot(vpp, [r["theta_urad"] for r in rows], "o-", label=r"$\theta$ route")
    ax_noise.plot(vpp, [r["y_route_urad"] for r in rows], "s--", label="y route (K-corrected)")
    ax_noise.plot(vpp, [r["mains_urad"] for r in rows], "^:", label="100 Hz mains")
    ax_noise.set_xscale("log")
    ax_noise.set_yscale("log")
    ax_noise.set_xlabel("Y RF amplitude (Vpp)")
    ax_noise.set_ylabel(r"ASD (µrad/√Hz)")
    ax_noise.set_title("3-50 Hz noise floor vs amplitude")
    ax_noise.grid(alpha=0.3, which="both")
    ax_noise.legend(fontsize=9)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def load_asd(run_dir: Path) -> dict | None:
    """读一轮的灵敏度谱（theta 与 y 折算两条），供三档叠图。"""
    summary_path = run_dir / "results" / "summary.json"
    if not summary_path.is_file():
        return None
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    asd = summary["asd"]
    return {"vpp": float(summary["rf_vpp"]),
            "freq_hz": asd["frequency_hz"],
            "theta": asd["theta_rad_per_sqrt_hz"],
            "y_route": asd["y_over_slope_rad_per_sqrt_hz"]}


def plot_spectra(root: Path, out: Path) -> Path:
    """三档幅度下的灵敏度谱叠图；阴影是取中位数的 3-50 Hz 频段。"""
    series = [item for run_dir in sorted((root / DATA_TYPE).glob("*"))
              if (item := load_asd(run_dir)) is not None]
    newest: dict[float, dict] = {}
    for item in series:
        newest[item["vpp"]] = item
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.axvspan(3.0, 50.0, color="0.85", alpha=0.5, zorder=0, label="3-50 Hz band")
    colors = plt.cm.viridis([0.15, 0.5, 0.85])
    for color, (vpp, item) in zip(colors, sorted(newest.items())):
        freq = list(item["freq_hz"])
        ax.loglog(freq[1:], [v * 1e6 for v in item["theta"][1:]], color=color, lw=1.0,
                  label=f"{vpp:g} Vpp  theta")
        ax.loglog(freq[1:], [v * 1e6 for v in item["y_route"][1:]], color=color, lw=0.8, ls="--", alpha=0.7)
    ax.set_xlim(1.0, 500.0)
    ax.set_ylim(5.0, 5000.0)
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Phase noise ASD (µrad/√Hz)")
    ax.set_title("Sensitivity spectra: theta (solid) vs y route (dashed)")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8, loc="upper right")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="三档载波幅度下标准 Mx 相图与噪声路径对比")
    parser.add_argument("--data-root", type=Path, default=None, help="运行目录 data/ 的根；默认取仓库配置")
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("figures") / "amplitude_compare.png")
    args = parser.parse_args()
    rows = collect(args.data_root or data_root())
    if not rows:
        raise SystemExit("没有找到带 results/summary.json 的运行轮次")
    report(rows)
    figure = plot(rows, args.out)
    spectra = plot_spectra(args.data_root or data_root(), args.out.with_name("sensitivity_spectra.png"))
    table = args.out.with_suffix(".json")
    table.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"figure: {figure}\nspectra: {spectra}\ntable: {table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
