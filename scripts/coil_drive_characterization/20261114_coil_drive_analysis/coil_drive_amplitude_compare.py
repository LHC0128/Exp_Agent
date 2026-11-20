"""对比 100 mA 与 200 mA 两次加密扫频：谐振区是否随幅度线性缩放。"""
from lab_workflows.common import data_root
import json
import pathlib

base = (data_root() / "coil_drive_characterization")
runs = {"100mA": base / "1114_165713_coil_drive" / "results" / "transfer.json",
        "200mA": base / "1114_170336_coil_drive" / "results" / "transfer.json"}

data = {}
for key, path in runs.items():
    rows = json.loads(path.read_text(encoding="utf-8"))["transfer"]
    data[key] = {round(row["frequency_hz"], 1): row for row in rows}

freq = sorted(set(data["100mA"]) & set(data["200mA"]))
print("   f/Hz   |I/V|100mA  |I/V|200mA   ratio   ph100    ph200")
for f in freq:
    if f < 7e4:
        continue
    a, b = data["100mA"][f], data["200mA"][f]
    ratio = b["admittance_a_per_v"] / a["admittance_a_per_v"]
    print(f"{f:9.4g} {a['admittance_a_per_v'] * 1e3:10.2f} {b['admittance_a_per_v'] * 1e3:10.2f} "
          f"{ratio:7.3f} {a['phase_deg']:8.2f} {b['phase_deg']:8.2f}")

low = [f for f in freq if f < 100]
print()
print("低频 |I/V| 均值：100mA = %.2f mA/V，200mA = %.2f mA/V"
      % (sum(data["100mA"][f]["admittance_a_per_v"] for f in low) / len(low) * 1e3,
         sum(data["200mA"][f]["admittance_a_per_v"] for f in low) / len(low) * 1e3))
