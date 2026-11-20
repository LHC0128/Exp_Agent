"""加密扫频（200-600 kHz，20 点）的谐振形状：谷深、f_r、Q 与相位。"""
from lab_workflows.common import data_root
import json
import math
import pathlib

path = (data_root() / "coil_drive_characterization/1114_171514_coil_drive/results/transfer.json")
rows = json.loads(path.read_text(encoding="utf-8"))["transfer"]
rows.sort(key=lambda r: r["frequency_hz"])
print("  f/kHz   |I/V| mA/V   |Z|/ohm   相位/deg   extra/deg")
for r in rows:
    f = r["frequency_hz"]
    adm = r["admittance_a_per_v"]
    print(f"{f/1e3:8.1f} {adm*1e3:11.2f} {10*1.44/(adm*1e3)*1e3:9.1f} {r['phase_deg']:10.2f} {r['phase_deg']+90:10.2f}")

low = rows[0]["admittance_a_per_v"] * 1e3
dip = min(rows, key=lambda r: r["admittance_a_per_v"])
dip_mag = dip["admittance_a_per_v"] * 1e3
print()
print(f"起点 {rows[0]['frequency_hz']:.0f} Hz: {low:.1f} mA/V")
print(f"谷底 {dip['frequency_hz']:.0f} Hz: {dip_mag:.1f} mA/V"
      f"（相对起点 {20*math.log10(dip_mag/low):.1f} dB；|Z|={10*1.44*(1e-3)/dip['admittance_a_per_v']:.0f} ohm）")
# 半功率点（|I/V| 回升到 dip*sqrt(2)）之间的宽度
half = dip_mag * math.sqrt(2)
edges = [r["frequency_hz"] for r in rows if r["admittance_a_per_v"] * 1e3 < half]
if edges:
    print(f"|I/V| < √2×谷底 的点：{min(edges)/1e3:.1f}–{max(edges)/1e3:.1f} kHz（共 {len(edges)} 点）")
# 峰值点（谷后最高）
peak = max(rows, key=lambda r: r["admittance_a_per_v"] if r["frequency_hz"] > dip["frequency_hz"] else 0)
print(f"谷后峰 {peak['frequency_hz']:.0f} Hz: {peak['admittance_a_per_v']*1e3:.1f} mA/V")
