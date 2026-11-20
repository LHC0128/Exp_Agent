"""用 5 kHz/190 mA 网格拟合出的慢响应模型，预言其他频率/电流下 c-1 的峰与回零点。

用法：python predict.py <fit.json> <输出前缀> <freq_hz> <current_ma> <w_start> <w_stop> <w_step>
上机前先跑，结果作为可证伪的预言存档。
"""
import json
import sys

import numpy as np

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import fitmodel as fm

fit = json.load(open(sys.argv[1], encoding="utf-8-sig"))
prefix = sys.argv[2]
freq, cur = float(sys.argv[3]), float(sys.argv[4])
widths = np.arange(float(sys.argv[5]), float(sys.argv[6]) + 1e-9, float(sys.argv[7]))
fm.PERIOD = 1e6 / freq
fm.OMEGA = 2 * np.pi / fm.PERIOD
lg, w0, bx, by, s_f, ltau, phi_off, lr = fit["x"]
g, tau = np.exp(lg), np.exp(ltau)
rows = []
for w in widths:
    z0 = fm.fundamental(w, g, w0, bx, by, s_f, tau, 0.0, 0.0, current_ma=cur)
    za = fm.fundamental(w, g, w0, bx, by, s_f, tau, fm.RF_SIM, phi_off, current_ma=cur) - z0
    zb = fm.fundamental(w, g, w0, bx, by, s_f, tau, fm.RF_SIM, phi_off + np.pi / 2, current_ma=cur) - z0
    cm = (za + 1j * zb) / 2 / fm.RF_SIM
    rows.append(cm)
cm = np.array(rows)
k = int(np.argmax(abs(cm)))
j = k + int(np.argmin(abs(cm[k:])))
res = dict(freq_hz=freq, current_ma=cur, model=fit["x"], widths=widths.tolist(), abs_cm=abs(cm).tolist(),
           peak_us=float(widths[k]), null_us=float(widths[j]) if j > k else None)
# 对照：纯比例模型（无慢响应、常数取 5 kHz 拟合等效值）只按 1/电流缩放、与频率无关
print(f"prediction f={freq:g} Hz, I={cur:g} mA: |c-1| peak ~ {res['peak_us']:.2f} us, "
      f"closest to 0 ~ {res['null_us']} us")
json.dump(res, open(prefix + ".json", "w", encoding="utf-8"), indent=1)

