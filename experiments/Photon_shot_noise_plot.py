# %% [markdown] Cell 0
# # 光散粒噪声数据分析 & 可视化 (ASD 底噪法)
#
# 从  加载原始 DAQ 波形，
# Welch PSD -> ASD 平坦区中位数 -> ASD^2 线性拟合。
# 无需连接任何仪器。

# %% Cell 1
from pathlib import Path
import sys
project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import numpy as np
import yaml
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from lab_workflows.plotting import new_figure, save_figure, set_plot_style
set_plot_style("paper")
from scipy.optimize import curve_fit
from scipy import signal as scipy_signal
from tqdm import tqdm

print("done (offline mode)")

# %% Cell 2
USE_LATEST = True  # True = 自动用最新数据; False = 用下面指定的路径
if USE_LATEST:
    base = project_root / "data" / "Photon_shot_noise"
    dirs = sorted([d for d in base.iterdir() if d.is_dir()], reverse=True)
    if dirs:
        DATA_DIR = dirs[0]
    else:
        raise FileNotFoundError(f"未找到数据目录: {base}")
else:
    DATA_DIR = project_root / "data" / "Photon_shot_noise" / "0527_1750_shot_noise_cal"
raw_dir = DATA_DIR / "raw"
results_dir = DATA_DIR / "results"
results_dir.mkdir(parents=True, exist_ok=True)

PSD_NPERSEG = 4096
FIT_START_IDX = 2

meta = np.load(raw_dir / "scan_meta.npz")
probe_powers = meta["probe_powers"]
actual_rate = float(meta["actual_rate"])
FLAT_FREQ_LIMIT = actual_rate * 0.05
n_powers = len(probe_powers)
print(f"{n_powers} powers, {probe_powers[0]:.3f}-{probe_powers[-1]:.3f}V, flat<{FLAT_FREQ_LIMIT:.0f}Hz")

# %% Cell 3
all_asd_x, all_asd_y = [], []
all_psd_x, all_psd_y = [], []
freqs_saved = None

for i in tqdm(range(n_powers), desc="PSD/ASD"):
    wf = np.load(raw_dir / f"waveforms_P{i:03d}.npz")
    x_wf, y_wf = wf["x_waveforms"], wf["y_waveforms"]
    asd_xl, asd_yl, psd_xa, psd_ya = [], [], [], []
    for rep in range(x_wf.shape[0]):
        x, y = x_wf[rep], y_wf[rep]
        if np.all(np.isnan(x)):
            asd_xl.append(np.nan); asd_yl.append(np.nan); continue
        nperseg = min(PSD_NPERSEG, len(x)//2)
        freqs, psd_x = scipy_signal.welch(x, fs=actual_rate, nperseg=nperseg, scaling="density", detrend="constant")
        _, psd_y = scipy_signal.welch(y, fs=actual_rate, nperseg=nperseg, scaling="density", detrend="constant")
        if freqs_saved is None: freqs_saved = freqs
        fm = (freqs > 0) & (freqs <= FLAT_FREQ_LIMIT)
        asd_xl.append(np.median(np.sqrt(np.maximum(psd_x[fm], 0))))
        asd_yl.append(np.median(np.sqrt(np.maximum(psd_y[fm], 0))))
        psd_xa.append(psd_x); psd_ya.append(psd_y)
    all_asd_x.append(asd_xl); all_asd_y.append(asd_yl)
    all_psd_x.append(np.mean(psd_xa, axis=0)); all_psd_y.append(np.mean(psd_ya, axis=0))

all_asd_x = np.array(all_asd_x, dtype=float)
all_asd_y = np.array(all_asd_y, dtype=float)
mean_asd_x = np.nanmean(all_asd_x, axis=1); std_asd_x = np.nanstd(all_asd_x, axis=1)
mean_asd_y = np.nanmean(all_asd_y, axis=1); std_asd_y = np.nanstd(all_asd_y, axis=1)
psd_xm = np.array(all_psd_x); psd_ym = np.array(all_psd_y)
print("ASD done")

# %% Cell 4
def linear_model(p, a, b): return a*p + b

p_fit = probe_powers[FIT_START_IDX:]
asd2_x = mean_asd_x[FIT_START_IDX:]**2
s_x = 2*mean_asd_x[FIT_START_IDX:]*std_asd_x[FIT_START_IDX:]
popt_x, pcov_x = curve_fit(linear_model, p_fit, asd2_x, sigma=s_x, absolute_sigma=True)
alpha_x, beta_x = popt_x; perr_x = np.sqrt(np.diag(pcov_x))

asd2_y = mean_asd_y[FIT_START_IDX:]**2
s_y = 2*mean_asd_y[FIT_START_IDX:]*std_asd_y[FIT_START_IDX:]
popt_y, pcov_y = curve_fit(linear_model, p_fit, asd2_y, sigma=s_y, absolute_sigma=True)
alpha_y, beta_y = popt_y; perr_y = np.sqrt(np.diag(pcov_y))

bx_asd = np.sqrt(max(beta_x,0)); by_asd = np.sqrt(max(beta_y,0))
snr = 10*np.log10(alpha_x*probe_powers[-1]/beta_x) if beta_x>0 else None
print(f"a_x={alpha_x:.3e}/V, b_x={beta_x:.3e} V2/Hz, elec={bx_asd:.3e} V/√Hz")
print(f"a_y={alpha_y:.3e}/V, b_y={beta_y:.3e} V2/Hz, SNR={snr:.1f}dB" if snr else "")

# %% Cell 5
pf = np.linspace(probe_powers[0], probe_powers[-1], 100)

# ---- 图1: ASD X 标定 ----
fig1, ax1 = new_figure(kind="standard", height_mm=65)
ax1.errorbar(probe_powers, mean_asd_x, yerr=std_asd_x, fmt="o", capsize=4,
             color="C0", label="Mean ± SD (X)")
ax1.plot(pf, np.sqrt(np.maximum(alpha_x * pf + beta_x, 0)), "--", color="C0",
         alpha=0.7, label="Shot-noise fit")
ax1.axhline(bx_asd, color="gray", ls=":", alpha=0.5,
            label=f"Elec. noise = {bx_asd:.2e} V/√Hz")
ax1.legend()
ax1.set_xlabel("Probe control voltage (V)")
ax1.set_ylabel("ASD Noise Floor (V/√Hz)")
ax1.set_title("Shot Noise Calibration — X Channel")
ax1.grid(False)
fig1.set_layout_engine("constrained")
save_figure(fig1, results_dir / "shot_noise_x.png", close=False)
print(f"图表已保存: {results_dir / 'shot_noise_x.png'}")

# ---- 图2: ASD Y 标定 ----
fig2, ax2 = new_figure(kind="standard", height_mm=65)
ax2.errorbar(probe_powers, mean_asd_y, yerr=std_asd_y, fmt="s", capsize=4,
             color="C1", label="Mean ± SD (Y)")
ax2.plot(pf, np.sqrt(np.maximum(alpha_y * pf + beta_y, 0)), "--", color="C1",
         alpha=0.7, label="Shot-noise fit")
ax2.axhline(by_asd, color="gray", ls=":", alpha=0.5,
            label=f"Elec. noise = {by_asd:.2e} V/√Hz")
ax2.legend()
ax2.set_xlabel("Probe control voltage (V)")
ax2.set_ylabel("ASD Noise Floor (V/√Hz)")
ax2.set_title("Shot Noise Calibration — Y Channel")
ax2.grid(False)
fig2.set_layout_engine("constrained")
save_figure(fig2, results_dir / "shot_noise_y.png", close=False)
print(f"图表已保存: {results_dir / 'shot_noise_y.png'}")

# ---- 图3: ASD vs RMS 诊断 ----
all_rms = []
for i in range(n_powers):
    wf = np.load(raw_dir / f"waveforms_P{i:03d}.npz")["x_waveforms"]
    all_rms.append([np.std(wf[r]) for r in range(wf.shape[0])])
all_rms = np.array(all_rms, dtype=float)
mr = np.nanmean(all_rms, axis=1)
sr = np.nanstd(all_rms, axis=1)

fig3, ax3 = new_figure(kind="standard", height_mm=65)
ax3.errorbar(probe_powers, mean_asd_x, yerr=std_asd_x, fmt="o-", capsize=3,
             color="C0", label="ASD X (V/√Hz)", markersize=5)
ax3.set_xlabel("Probe control voltage (V)")
ax3.set_ylabel("ASD Noise Floor (V/√Hz)", color="C0")
ax3.tick_params(axis="y", labelcolor="C0")
ax3r = ax3.twinx()
ax3r.errorbar(probe_powers, mr, yerr=sr, fmt="s--", capsize=3,
              color="C2", label="RMS X (V)", markersize=5)
ax3r.set_ylabel("AC RMS (V)", color="C2")
ax3r.tick_params(axis="y", labelcolor="C2")
h1, l1 = ax3.get_legend_handles_labels()
h2, l2 = ax3r.get_legend_handles_labels()
ax3.legend(h1 + h2, l1 + l2, loc="upper left")
ax3.set_title("ASD and AC RMS")
ax3.grid(False)
fig3.set_layout_engine("constrained")
save_figure(fig3, results_dir / "asd_vs_rms.png", close=False)
print(f"图表已保存: {results_dir / 'asd_vs_rms.png'}")

# ---- 图4: PSD 白噪声检验 ----
sel_idx = [0, n_powers // 2, -1]
sel_colors = ["#2166ac", "#f4a582", "#b2182b"]
fig4, ax4 = new_figure(kind="standard", height_mm=65)
for idx, c in zip(sel_idx, sel_colors):
    fm = (freqs_saved > 0) & (freqs_saved <= FLAT_FREQ_LIMIT)
    ax4.plot(freqs_saved[fm], psd_xm[idx][fm], color=c, alpha=0.8, lw=0.8,
             label=f"P = {probe_powers[idx]:.3f} V")
    ax4.axhline(np.median(psd_xm[idx][fm]), color=c, ls="--", alpha=0.35)
ax4.set_yscale("log")
ax4.axvline(FLAT_FREQ_LIMIT, color="green", alpha=0.5, ls="--",
            label=f"Flat limit ({FLAT_FREQ_LIMIT:.0f} Hz)")
ax4.legend()
ax4.set_xlabel("Frequency (Hz)")
ax4.set_ylabel("PSD (V²/Hz)")
ax4.set_title("X-Channel PSD — White Noise Check")
ax4.grid(False)
fig4.set_layout_engine("constrained")
save_figure(fig4, results_dir / "psd_white_noise_check.png", close=False)
print(f"图表已保存: {results_dir / 'psd_white_noise_check.png'}")

plt.show()

# %% Cell 6
analysis = {"method":"Welch PSD->ASD flat median->ASD^2 fit","fit":{"alpha_x":float(alpha_x),"alpha_x_err":float(perr_x[0]),"beta_x":float(beta_x),"beta_x_err":float(perr_x[1]),"beta_x_ASD":float(bx_asd),"alpha_y":float(alpha_y),"alpha_y_err":float(perr_y[0]),"beta_y":float(beta_y),"beta_y_err":float(perr_y[1]),"snr_db":float(snr) if snr else None},"params":{"nperseg":PSD_NPERSEG,"flat_freq":float(FLAT_FREQ_LIMIT),"fit_start":FIT_START_IDX}}
with open(results_dir/"analysis.yaml","w") as f: yaml.dump(analysis,f,default_flow_style=False,allow_unicode=True)
print(f"saved: {results_dir/'analysis.yaml'}")
