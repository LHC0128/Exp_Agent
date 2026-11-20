"""对各次相位粗扫做复数傅里叶分解：Z(φ) = Σ c_m e^{imφ}。"""
from lab_workflows.common import data_root
import csv
from collections import defaultdict
from pathlib import Path

import numpy as np
import yaml

ROOT = (data_root() / "pi_width_scan")
RUNS = ["1117_171916", "1117_175239", "1117_175856", "1117_190508", "1117_192355", "1117_192712",
        "1117_193027", "1117_215430", "1117_220403", "1117_224531"]


def params(run):
    text = (ROOT / f"{run}_pi_width_scan" / "experiment_config.yaml").read_text(encoding="utf-8")
    keys = ("Y_RF_VPP", "Z_PEAK_CURRENT_MA", "PHASE_CHECK_WIDTH_US", "X_BIAS_V")
    out = {}
    for line in text.splitlines():
        s = line.strip()
        for k in keys:
            if s.startswith(k + ":") and k not in out:
                out[k] = s.split(":", 1)[1].strip()
    return out


print("run          Vpp   mA   w(us) Xb    |  c0(mV,deg)      c+1            c-1          |c+2| |c-2| |c+3| |c-3| resid")
for run in RUNS:
    groups = defaultdict(list)
    with open(ROOT / f"{run}_pi_width_scan" / "raw" / "phase_scan.csv", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            groups[float(row["phase_deg"])].append(float(row["R_v"]) * np.exp(1j * np.radians(float(row["theta_deg"]))))
    phi = np.array(sorted(k for k in groups if k < 360 - 1e-6 and not (k >= 359)))
    z = np.array([np.mean(groups[k]) for k in phi]) * 1e3
    ph = np.radians(phi)
    ms = [0, 1, -1, 2, -2, 3, -3] if len(phi) >= 8 else [0, 1, -1, 2, -2]
    A = np.stack([np.exp(1j * m * ph) for m in ms], axis=1)
    c, *_ = np.linalg.lstsq(A, z, rcond=None)
    res = np.sqrt(np.mean(abs(A @ c - z) ** 2))
    cd = dict(zip(ms, c))
    p = params(run)
    f = lambda v: f"{abs(v):5.1f}@{np.degrees(np.angle(v)):6.1f}"
    print(f"{run} {p.get('Y_RF_VPP','?'):>4} {p.get('Z_PEAK_CURRENT_MA','?'):>5} {p.get('PHASE_CHECK_WIDTH_US','?'):>4} "
          f"{p.get('X_BIAS_V','0'):>5} | {f(cd[0])} {f(cd[1])} {f(cd[-1])} | "
          + " ".join(f"{abs(cd.get(m, np.nan)):5.2f}" for m in (2, -2, 3, -3)) + f" {res:5.2f}  n={len(phi)}")
