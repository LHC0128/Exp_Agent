"""打印 analysis 的逐曲线中心，便于贴表。"""
from lab_workflows.common import data_root
import json
import pathlib

base = (data_root() / "Coil_Constant_Calibration/1116_161937_coil_const_coarse3/results")
data = json.loads((base / "curve_fits.json").read_text(encoding="utf-8"))
curves = data if isinstance(data, list) else data.get("curves", [])
for c in curves:
    fit = c.get("fit") or {}
    params = fit.get("parameters") or []
    if len(params) < 4 or any(value is None for value in params[:4]):
        print("I={:8.2f} mA  fit 不可用（{}）".format(float(c.get("current_ma", float("nan"))),
                                                     fit.get("method", "?")))
        continue
    print(
        "I={:8.2f} mA  center={:9.1f} Hz  err={:6.1f}  amp={:.3f}  gamma={:.0f}".format(
            float(c.get("current_ma", float("nan"))),
            float(params[2]),
            float(fit.get("center_uncertainty_hz", float("nan"))),
            float(params[1]),
            float(params[3]),
        )
    )
