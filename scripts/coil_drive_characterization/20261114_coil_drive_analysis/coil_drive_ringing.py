"""离线试算：用现有脉冲帧的拖尾振铃找谐振，与扫频的谷交叉验证。只读运行目录数据。"""
from lab_workflows.common import data_root
import json
import math
import numpy as np
from pathlib import Path

RAW = (data_root() / "coil_drive_characterization/1114_114241_coil_drive/raw")
R_LOOP = 72.0      # 采样 10 + 阻尼 10 + 线圈 2 + 功放输出 50
R_SENSE = 10.0


def load(name):
    with np.load(RAW / name, allow_pickle=False) as a:
        t = a["time"]
        v = a["sense_v"]
        meta = json.loads(str(a["meta"]))
    return t, v / R_SENSE, meta


print("脉冲拖尾振铃（取脉宽结束后 1 µs 起，去均值后 FFT）")
for name in ("pulse_09.npz", "pulse_08.npz", "pulse_07.npz"):
    t, i, meta = load(name)
    width = meta["width_s"]
    dt = float(np.median(np.diff(t)))
    mask = t > width + 1e-6
    tail = i[mask] - np.mean(i[mask])
    if tail.size < 64:
        print(f"  {name}: 拖尾样本不足")
        continue
    spectrum = np.abs(np.fft.rfft(tail * np.hanning(tail.size)))
    freqs = np.fft.rfftfreq(tail.size, dt)
    band = (freqs > 5e3) & (freqs < 3e6)
    peak = freqs[band][np.argmax(spectrum[band])]
    # 由相邻峰衰减估计 Q：用包络对数斜率
    env = np.abs(tail)
    idx = np.arange(env.size)
    valid = env > 0.02 * env.max()
    slope = np.polyfit(idx[valid] * dt, np.log(env[valid]), 1)[0]
    q = math.pi * peak / max(-slope, 1e-12)
    print(f"  {name} w={width*1e6:g}us  f_ring={peak/1e3:8.1f} kHz  tau_decay={(-1/slope)*1e6:5.2f} us  Q={q:5.1f}")
