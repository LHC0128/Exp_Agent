"""把一个或多个拟合（fits/*.npz）与数据画在一起：|c0|、|c+1|、|c-1| 与相位。"""
import json
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

out = Path(sys.argv[1])
prefixes = sys.argv[2:]
names = ["c0 (S)", "c+1", "c-1"]
fig, ax = plt.subplots(2, 3, figsize=(15, 7.5))
first = True
for pre in prefixes:
    d = np.load(pre + ".npz")
    s = json.loads(Path(pre + ".json").read_text(encoding="utf-8"))
    lab = (f"{Path(pre).name}: rms {s['rms_mV']:.2f} mV, fast {s['fast_fraction']:.2f}, "
           f"tau_e {s['tau_e_us']:.3g} us")
    for j in range(3):
        if first:
            ax[0, j].plot(d["w"], abs(d["data"][:, j]), "ko", ms=3, label="data")
            ax[1, j].plot(d["w"], np.degrees(np.unwrap(np.angle(d["data"][:, j]))), "ko", ms=3)
        ax[0, j].plot(d["w"], abs(d["model"][:, j]), "-", label=lab)
        ax[1, j].plot(d["w"], np.degrees(np.unwrap(np.angle(d["model"][:, j]))), "-")
    first = False
for j in range(3):
    ax[0, j].set_title(names[j]); ax[0, j].set_ylabel("mV"); ax[1, j].set_ylabel("phase (deg)")
    ax[1, j].set_xlabel("Pulse width (us)")
ax[0, 2].legend(fontsize=7)
fig.tight_layout()
fig.savefig(out, dpi=120)
print(out)
