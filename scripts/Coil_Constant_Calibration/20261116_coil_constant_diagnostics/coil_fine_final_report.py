"""打印细扫最终轮逐曲线结果与整体标定。"""
from lab_workflows.common import data_root
import json
import pathlib

base = (data_root() / "Coil_Constant_Calibration/1116_173127_coil_const_fine/results")
report = json.loads((base / "analysis.json").read_text(encoding="utf-8"))
print("K = {:.1f} ± {:.1f} Hz/mA  ({:.2f} nT/mA)".format(
    report["K_Hz_per_mA"], report["K_uncertainty_Hz_per_mA"], report["K_B_nT_per_mA"]))
print("I0 = {:.2f} mA, B_res = {:.0f} nT, R² = {:.5f}".format(
    report["I0_ma"], report["B_res_nT"], report["linearity_current"]["r_squared"]))
print("S_f = {:.0f} Hz/V, closure = {:.4f}".format(
    report.get("S_f_Hz_per_V", float("nan")), (report.get("closure") or {}).get("ratio", float("nan"))))

curves = json.loads((base / "curve_fits.json").read_text(encoding="utf-8"))
curves = curves if isinstance(curves, list) else curves.get("curves", [])
print("\n I(mA)      center(kHz)   gamma(Hz)   amp(V)    reasons")
for c in curves:
    fit = c.get("fit") or {}
    params = fit.get("parameters") or [None] * 4
    if params[2] is None:
        print("{:8.2f}   -- fit 不可用 -- {}".format(float(c.get("current_ma", float("nan"))),
                                                     fit.get("rejection_reasons")))
        continue
    print("{:8.2f}   {:10.3f}   {:8.0f}   {:8.4f}  {}".format(
        float(c.get("current_ma", float("nan"))), params[2] / 1e3, params[3], params[1],
        fit.get("rejection_reasons")))
