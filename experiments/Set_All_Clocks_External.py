# %% [markdown] Cell 0
# # 设备时钟源批量切换（每台可选 EXT / INT）
#
# 适用于实验前批量同步系统时钟。需要在 Cell 3 的 `CLOCK_CONFIG` 中
# 为每台设备指定目标时钟源。
#
# - `EXT`：外部 10 MHz 参考（注入到设备 10MHz In 接口）
# - `INT`：设备内部 10 MHz 时钟
#
# **默认行为**：表中未列出的设备一律按 `EXT` 处理（保持向后兼容）。
#
# ## 涉及设备
# - **DG4000** 系列（每台 1 个 10 MHz 参考，按 VISA resource 去重）:
#   - DG4E242401288 (Z_magnetic_field, Time_sequence_2)
#   - DG4E231500376 (X_magnetic_field_AM, Y_magnetic_field_AM)
#   - DG4E234902522 (X_magnetic_field, Y_magnetic_field, rf_coil)
#   - DG4E222800868 (Pump_modulation, Time_sequence)
# - **DG900 Pro** 系列（每台 1 个 10 MHz 参考）:
#   - DG9Q280100002 (Pump_laser_power, Probe_laser_power)
#   - DG9Q271200104 (Heat_Control, Temp_Switch)
# - **HF2 锁相放大器** (`/system/extclk`):
#   - dev18246
#
# 不涉及外部时钟的设备：GS200 直流源、TEC103 温控器、SDS 示波器。

# %% Cell 1
from pathlib import Path
import sys
# 自动定位项目根目录（以 params/ 目录为标记）
project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import yaml
import time

# 设备库
from signal_generator import DG4000Instrument, DG900Instrument
from lockin_amplifier import HF2Instrument

print("库导入完成")

# %% Cell 2
# ========== 加载物理量→仪器映射 ==========
with open(project_root / "params" / "mapping.yaml", encoding="utf-8") as f:
    MAPPING = yaml.safe_load(f)["mapping"]

# ========== 收集所有信号发生器（按 VISA resource 去重）==========
# 字典: device_key -> {"type", "resource", "label", "channels", "mapped_keys"}
# device_key 用 "label__short_res" 保证唯一（同一台仪器在 mapping 中可能挂多个 mapping_key）
devices = {}
order = []  # 保持插入顺序

for key, cfg in MAPPING.items():
    if cfg.get("instrument") != "signal_generator":
        continue
    resource = cfg.get("resource")
    channel = cfg.get("channel")
    if resource is None or channel is None:
        continue
    if "::0x0641::" in resource:
        dtype = "DG4000"
    elif "::0x0646::" in resource:
        dtype = "DG900"
    else:
        continue
    # 同一台仪器的多个 mapping_key 合并到一条记录
    if resource not in devices:
        short = resource.split("::")[3] if "::" in resource else resource
        dev_key = f"{cfg.get('label', key)}__{short}"
        devices[resource] = {
            "device_key": dev_key,
            "type": dtype,
            "resource": resource,
            "short_resource": short,
            "label": cfg.get("label", key),
            "channels": set(),
            "mapped_keys": [],
        }
        order.append(resource)
    devices[resource]["channels"].add(int(channel))
    devices[resource]["mapped_keys"].append(key)

# HF2 单条
hf2_cfg = MAPPING.get("lockin_r", {}) or MAPPING.get("lockin_xy", {})
hf2_resource = (f"zi://{hf2_cfg.get('host', '127.0.0.1')}:"
                f"{hf2_cfg.get('port', 8005)}/"
                f"{hf2_cfg.get('device_id', 'dev18246')}")
devices[hf2_resource] = {
    "device_key": "HF2__" + hf2_cfg.get("device_id", "dev18246"),
    "type": "HF2",
    "resource": hf2_resource,
    "short_resource": hf2_cfg.get("device_id", "dev18246"),
    "label": "HF2 锁相放大器",
    "channels": set(),
    "mapped_keys": ["lockin_r"],
    "hf2_cfg": hf2_cfg,
}
order.append(hf2_resource)

# ========== 打印自动发现的设备清单 ==========
print("=" * 60)
print("自动发现的设备清单（按 VISA resource / HF2 设备 ID 去重）")
print("=" * 60)
for res in order:
    d = devices[res]
    ch_str = (f"CH={sorted(d['channels'])}" if d["channels"] else "")
    print(f"  [{d['type']:6s}] {d['label']:18s} "
          f"({d['short_resource']})  {ch_str}")
    if d["mapped_keys"]:
        print(f"           mapping_keys: {d['mapped_keys']}")
print(f"\n共 {len(order)} 台可设置时钟源的设备")

# %% Cell 3
# ============================================================
# ⬇️ 用户配置区：在下方修改每台设备的目标时钟源
# ============================================================
# 取值: "EXT" = 外部 10 MHz | "INT" = 设备内部时钟
# 设备简称必须与 Cell 2 打印的 "(short_resource)" 严格一致
# 表中未列出的设备默认按 "EXT" 处理
# ============================================================
CLOCK_CONFIG = {
    # ----- DG4000 -----
    "DG4E242401288": "EXT",   # Z方向磁场
    "DG4E231500376": "EXT",   # X方向磁场 AM
    "DG4E234902522": "EXT",   # X方向磁场 (与 Y 场、RF 线圈同一台)
    "DG4E222800868": "INT",   # Pump调制
    # ----- DG900 Pro -----
    "DG9Q280100002": "EXT",   # Pump/Probe 光功率
    "DG9Q271200104": "EXT",   # 温度开关信号
    # ----- HF2 -----
    "dev18246":      "EXT",   # HF2 锁相放大器
}

# 校验配置项
_VALID_MODES = {"EXT", "INT", "EXTERNAL", "INTERNAL"}
for k, v in CLOCK_CONFIG.items():
    if v.upper() not in _VALID_MODES:
        raise ValueError(
            f"[配置错误] CLOCK_CONFIG['{k}'] = {v!r} 非法，"
            f"仅接受 'EXT' / 'INT' / 'EXTERNAL' / 'INTERNAL'"
        )

# 汇总实际生效的设置
print("=" * 60)
print("本次将应用的时钟源设置")
print("=" * 60)
print(f"{'设备':18s} {'型号':8s} {'短ID':18s} {'目标':6s}")
print("-" * 60)
for res in order:
    d = devices[res]
    short = d["short_resource"]
    target = CLOCK_CONFIG.get(short, "EXT")
    print(f"{d['label']:18s} {d['type']:8s} {short:18s} {target}")
print("-" * 60)
n_ext = sum(1 for v in CLOCK_CONFIG.values() if v.upper().startswith("EXT"))
n_int = sum(1 for v in CLOCK_CONFIG.values() if v.upper().startswith("INT"))
n_default = len(order) - len(CLOCK_CONFIG)
print(f"统计: 显式 EXT = {n_ext}, 显式 INT = {n_int}, "
      f"默认 EXT = {n_default}")

# %% Cell 4
# ========== 连接所有设备 ==========
all_devices = []  # [(resource, dev, dtype), ...]

try:
    for res in order:
        d = devices[res]
        dtype = d["type"]
        if dtype in ("DG4000", "DG900"):
            ch_list = sorted(d["channels"])
            bind_ch = ch_list[0]
            Cls = DG4000Instrument if dtype == "DG4000" else DG900Instrument
            dev = Cls(res, channel=bind_ch)
            dev.connect()
            try:
                idn = dev.idn()
            except Exception:
                idn = "(?)"
            print(f"  [{dtype}] 已连接: {d['label']} ({d['short_resource']}) -> {idn.strip()}")
        elif dtype == "HF2":
            cfg = d["hf2_cfg"]
            dev = HF2Instrument(
                host=cfg.get("host", "127.0.0.1"),
                port=cfg.get("port", 8005),
                api_level=1,
                device_id=cfg["device_id"],
            )
            dev.connect()
            print(f"  [HF2]   已连接: {d['label']} ({d['short_resource']}) -> {dev.idn}")

        all_devices.append((res, dev, dtype))

except Exception as e:
    print(f"设备连接失败: {e}")
    for _, dev, _ in all_devices:
        try:
            if hasattr(dev, "disconnect"):
                dev.disconnect()
        except Exception:
            pass
    raise

print(f"\n共连接 {len(all_devices)} 台设备")

# %% Cell 5
# ========== 按配置切换时钟源 ==========
print("=" * 60)
print("正在按 CLOCK_CONFIG 应用时钟源设置")
print("=" * 60)

results = []  # (label, dtype, short, before, after, target, ok, error)

for res, dev, dtype in all_devices:
    d = devices[res]
    short = d["short_resource"]
    target_raw = CLOCK_CONFIG.get(short, "EXT")
    target = "EXT" if target_raw.upper().startswith("EXT") else "INT"
    scpi_target = "EXTernal" if target == "EXT" else "INTernal"
    label = d["label"]

    try:
        if dtype in ("DG4000", "DG900"):
            try:
                before_raw = dev.get_ref_clock_source().strip()
            except Exception:
                before_raw = "?"
            before = "EXT" if before_raw.upper().startswith("EXT") else "INT"
            dev.set_ref_clock_source(scpi_target)
            time.sleep(0.2)
            after_raw = dev.get_ref_clock_source().strip()
            after = "EXT" if after_raw.upper().startswith("EXT") else "INT"
            ok = (after == target)
            err = "" if ok else f"期望 {target}, 实际 {after}"

        elif dtype == "HF2":
            before_bool = dev.get_extclk()
            before = "EXT" if before_bool else "INT"
            dev.set_extclk(target == "EXT")
            time.sleep(0.2)
            after_bool = dev.get_extclk()
            after = "EXT" if after_bool else "INT"
            ok = (after == target)
            err = "" if ok else f"期望 {target}, 实际 {after}"

        results.append((label, dtype, short, before, after, target, ok, err))
        tag = "✅" if ok else "⚠"
        print(f"  {tag} {label:18s} ({short}): {before} -> {after}  [目标: {target}]")

    except Exception as e:
        results.append((label, dtype, short, "?", "?", target, False, str(e)))
        print(f"  ✗ {label:18s} ({short}): {e}")

# ---- 汇总 ----
n_ok = sum(1 for r in results if r[6])
n_total = len(results)
print(f"\n应用结果: {n_ok}/{n_total} 成功")
if n_ok < n_total:
    print("以下设备未达到目标状态:")
    for r in results:
        if not r[6]:
            print(f"  - {r[0]} ({r[1]}/{r[2]}): {r[7]}")

# %% Cell 6
# ========== 验证最终状态 + 断开所有设备 ==========
print("=" * 60)
print("最终时钟源状态 + 断开")
print("=" * 60)

for res, dev, dtype in all_devices:
    d = devices[res]
    try:
        if dtype in ("DG4000", "DG900"):
            current = dev.get_ref_clock_source().strip()
        elif dtype == "HF2":
            current = "EXT" if dev.get_extclk() else "INT"
        else:
            current = "?"
        print(f"  {d['label']:18s} ({d['short_resource']}): {current}")
    except Exception as e:
        print(f"  {d['label']:18s} ({d['short_resource']}): 读取失败 ({e})")

# ---- 断开 ----
for res, dev, dtype in all_devices:
    d = devices[res]
    try:
        if hasattr(dev, "disconnect"):
            dev.disconnect()
            print(f"  已断开: {d['label']} ({d['short_resource']})")
    except Exception as e:
        print(f"  断开失败 {d['label']}: {e}")

print("\n✅ 时钟源批量设置完成")
