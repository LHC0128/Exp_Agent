"""多参数优化扫描 — 精细扫描 Pump, Probe, Duty, main_B，XY验证."""
import sys; sys.path.insert(0, '.')
import yaml, time, numpy as np
from pathlib import Path
from datetime import datetime
from scipy import signal as scipy_signal
from gs200 import GS200Instrument
from lab_workflows.devices import create_signal_generator
from lockin_amplifier import (
    HF2Instrument, DAQConfig,
    SignalInputConfig, OscillatorConfig, DemodulatorConfig,
    demod, daq,
)
from sensitivity_analysis import fit_dispersive, compute_sensitivity, write_run_record

with open("params/mapping.yaml", encoding="utf-8") as f:
    MAPPING = yaml.safe_load(f)["mapping"]
with open("params/safety_limits.yaml", encoding="utf-8") as f:
    LIMITS = yaml.safe_load(f)["safety_limits"]

def validate_safety_limit(name, value):
    lim = LIMITS.get(name)
    if lim is None: return value
    lo, hi = lim["min"], lim["max"]
    if lo is not None and hi is not None:
        if value < lo or value > hi:
            raise ValueError(f"[SAFETY] {name}={value} out of [{lo}, {hi}]")
    return value

EXPERIMENT_TYPE = "Static_Magnetic_Field_Sensitivity"
Z_V_TO_NT, Z_V_TO_FT = 3517, 3517 * 1e6
RAMP_LOW, RAMP_HIGH, RAMP_FREQ, RAMP_SYMMETRY, DAQ_DURATION = -0.5, 0.5, 1.0, 20, 1
NOISE_N_AVG, NOISE_DURATION = 5, 1.0
PUMP_MOD_FREQ, PUMP_MOD_AMPLITUDE = 90e3, 0.18
RF_GATE_AMPLITUDE, RF_GATE_OFFSET = 5.0, 2.5

QUALITY_THRESHOLDS = {
    "r_squared_min": 0.85,
    "relative_gamma_uncertainty_max": 0.5,
    "residual_sign_change_ratio_min": 0.0,
}

BASELINE = {
    "main_magnetic_field": 9.3,
    "Pump_laser_power": 0.2,
    "Probe_laser_power": 0.2,
    "PUMP_MOD_DUTY": 5,
    "X_magnetic_field": 0,
    "Y_magnetic_field": 0,
}

OPTIMIZE_SEQUENCE = [
    # 1) 精细扫描主磁场 (9.28-9.32 mA)
    {
        "parameter": "main_magnetic_field",
        "values": [9.28, 9.29, 9.30, 9.31, 9.32],
        "stabilize_time": 2.0,
        "recalibrate_phase": True,
    },
    # 2) 低功率段精细扫描 Pump
    {
        "parameter": "Pump_laser_power",
        "values": [0.10, 0.13, 0.16, 0.20, 0.23, 0.26],
        "stabilize_time": 2.0,
        "recalibrate_phase": True,
    },
    # 3) 精细扫描 Probe
    {
        "parameter": "Probe_laser_power",
        "values": [0.12, 0.15, 0.18, 0.20, 0.25, 0.30],
        "stabilize_time": 2.0,
        "recalibrate_phase": True,
    },
    # 4) 低占空比段精细扫描 Duty
    {
        "parameter": "PUMP_MOD_DUTY",
        "values": [3, 4, 5, 6, 8],
        "stabilize_time": 2.0,
        "recalibrate_phase": False,
    },
    # 5) X 补偿磁场验证 (±0.1V)
    {
        "parameter": "X_magnetic_field",
        "values": [-0.10, -0.05, 0, 0.05, 0.10],
        "stabilize_time": 1.5,
        "recalibrate_phase": False,
    },
    # 6) Y 补偿磁场验证 (±0.1V)
    {
        "parameter": "Y_magnetic_field",
        "values": [-0.10, -0.05, 0, 0.05, 0.10],
        "stabilize_time": 1.5,
        "recalibrate_phase": False,
    },
]

total_iters = sum(len(s["values"]) for s in OPTIMIZE_SEQUENCE)
print(f"优化扫描: {len(OPTIMIZE_SEQUENCE)} 个维度, 共 {total_iters} 次迭代")

# ===== Connect devices =====
print("\n连接设备...")
gs = GS200Instrument(MAPPING["main_magnetic_field"]["resource"]); gs.connect()
gs.set_source_function("CURRent"); gs.set_current_limit(10 / 1000)
print(f"  GS200: {gs.idn()}")

dg_sweep = create_signal_generator(MAPPING["Z_magnetic_field"]["resource"], channel=1)
dg_sweep.connect(); dg_sweep.set_ref_clock_source("EXTernal")

dg_laser = create_signal_generator(MAPPING["Pump_laser_power"]["resource"], channel=1)
dg_laser.connect()

dg_comp = create_signal_generator(MAPPING["X_magnetic_field"]["resource"], channel=1)
dg_comp.connect(); dg_comp.set_ref_clock_source("EXTernal")

dg_mod = create_signal_generator(MAPPING["Pump_modulation"]["resource"], channel=1)
dg_mod.connect()

dg_temp = create_signal_generator(MAPPING["Temp_Switch"]["resource"], channel=2)
dg_temp.connect()

hfi = HF2Instrument(host="127.0.0.1", port=8005, api_level=1, device_id="dev18246")
hfi.connect(); hfi.set_extclk(True)
print(f"  HF2: {hfi.idn}")

# ===== Setup baseline =====
print("\n设置基准参数...")
dg_laser.setup_dc(BASELINE["Pump_laser_power"], channel=1)
dg_laser.setup_dc(BASELINE["Probe_laser_power"], channel=2)
dg_comp.setup_dc(BASELINE["X_magnetic_field"], channel=1)
dg_comp.set_output(abs(BASELINE["X_magnetic_field"]) > 0, channel=1)
dg_comp.setup_dc(BASELINE["Y_magnetic_field"], channel=2)
dg_comp.set_output(abs(BASELINE["Y_magnetic_field"]) > 0, channel=2)
gs.set_current(BASELINE["main_magnetic_field"] / 1000); gs.set_output(True)
dg_temp.setup_dc(5.0, channel=2)
dg_sweep.setup_square(freq=10, amplitude=10, offset=0, dcycle=50, channel=2)
dg_sweep.set_output(True, channel=2)

# Pump modulation
current_duty = BASELINE["PUMP_MOD_DUTY"]
dg_mod.setup_sine(freq=100e6, amplitude=PUMP_MOD_AMPLITUDE, offset=0, phase=0, channel=1)
pw = (current_duty / 100) / PUMP_MOD_FREQ
dg_mod.setup_pulse(freq=PUMP_MOD_FREQ, amplitude=RF_GATE_AMPLITUDE,
                   offset=RF_GATE_OFFSET, width=pw, channel=2)

# ===== Phase calibration =====
print("初始相位校准...")
dg_temp.set_output(False, channel=2)
dg_sweep.setup_dc(0, channel=1); dg_sweep.set_output(False, channel=1)
sig_cfg = SignalInputConfig(input_index=0, range=2.0, ac_coupling=True, diff=False, impedance=50)
demod.configure_signal_input(hfi, sig_cfg)
osc_cfg = OscillatorConfig(osc_index=0, frequency=PUMP_MOD_FREQ, source="manual")
demod.configure_oscillator(hfi, osc_cfg)
demod_cfg = DemodulatorConfig(demod_index=0, enable=True, rate=1000, input_channel=0,
                               osc_select=0, harmonic=1, time_constant=0.001, order=4, phase=0)
actual_rate = demod.configure_demodulator(hfi, demod_cfg)
phase = demod.auto_calibrate_phase(hfi, demod_idx=0, tolerance_deg=1, max_attempts=5, settle_time=0.2)
dg_temp.set_output(True, channel=2)
print(f"  Phase={phase:.2f}deg, rate={actual_rate:.0f} Sa/s")

# ===== Helper functions =====
def recalibrate():
    """重校准 HF2 相位."""
    global phase, actual_rate
    dg_temp.set_output(False, channel=2)
    dg_sweep.setup_dc(0, channel=1); dg_sweep.set_output(False, channel=1)
    phase = demod.auto_calibrate_phase(hfi, demod_idx=0, tolerance_deg=1, max_attempts=5, settle_time=0.2)
    dg_temp.set_output(True, channel=2); time.sleep(0.5)
    return phase

def restore_baseline():
    """恢复所有参数到基准值."""
    global current_duty
    current_duty = BASELINE["PUMP_MOD_DUTY"]
    dg_laser.setup_dc(BASELINE["Pump_laser_power"], channel=1)
    dg_laser.setup_dc(BASELINE["Probe_laser_power"], channel=2)
    gs.set_current(BASELINE["main_magnetic_field"] / 1000)
    pw_new = (current_duty / 100) / PUMP_MOD_FREQ
    dg_mod.setup_pulse(freq=PUMP_MOD_FREQ, amplitude=RF_GATE_AMPLITUDE,
                       offset=RF_GATE_OFFSET, width=pw_new, channel=2)
    dg_comp.setup_dc(BASELINE["X_magnetic_field"], channel=1)
    dg_comp.set_output(abs(BASELINE["X_magnetic_field"]) > 0, channel=1)
    dg_comp.setup_dc(BASELINE["Y_magnetic_field"], channel=2)
    dg_comp.set_output(abs(BASELINE["Y_magnetic_field"]) > 0, channel=2)
    time.sleep(1.0)

def apply_param(name, val):
    """应用单个参数."""
    global current_duty
    validate_safety_limit(name, val)
    if name == "Pump_laser_power":
        dg_laser.setup_dc(val, channel=1)
    elif name == "Probe_laser_power":
        dg_laser.setup_dc(val, channel=2)
    elif name == "main_magnetic_field":
        gs.set_current(val / 1000)
    elif name == "PUMP_MOD_DUTY":
        current_duty = int(val)
        pw_new = (current_duty / 100) / PUMP_MOD_FREQ
        dg_mod.setup_pulse(freq=PUMP_MOD_FREQ, amplitude=RF_GATE_AMPLITUDE,
                           offset=RF_GATE_OFFSET, width=pw_new, channel=2)
    elif name == "X_magnetic_field":
        dg_comp.setup_dc(val, channel=1)
        dg_comp.set_output(abs(val) > 0, channel=1)
    elif name == "Y_magnetic_field":
        dg_comp.setup_dc(val, channel=2)
        dg_comp.set_output(abs(val) > 0, channel=2)

def get_all_params(override=None):
    p = dict(BASELINE)
    p["PUMP_MOD_AMPLITUDE"] = PUMP_MOD_AMPLITUDE
    p["PUMP_MOD_DUTY"] = current_duty
    if override: p.update(override)
    return p

# ===== Scan loop =====
summary_path = Path("data") / EXPERIMENT_TYPE / "run_summary.csv"
all_results = []
t_start = datetime.now()

try:
    iter_counter = 0
    for seq_idx, seq_cfg in enumerate(OPTIMIZE_SEQUENCE):
        pname = seq_cfg["parameter"]
        values = seq_cfg["values"]
        stab = seq_cfg.get("stabilize_time", 2.0)
        do_recal = seq_cfg.get("recalibrate_phase", True)

        print(f"\n{'='*60}")
        print(f"[维度 {seq_idx+1}/{len(OPTIMIZE_SEQUENCE)}] {pname}: {values}")
        print(f"{'='*60}")

        print("  恢复基准参数...")
        restore_baseline()
        time.sleep(stab)
        p_base = recalibrate()
        print(f"  基准相位: {p_base:.2f}deg")

        for i, val in enumerate(values):
            iter_counter += 1
            ts = datetime.now().strftime("%m%d_%H%M")
            tag = f"{pname}_{val}"
            run_dir = Path("data") / EXPERIMENT_TYPE / f"{ts}_{tag}"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "raw").mkdir(exist_ok=True)
            (run_dir / "results").mkdir(exist_ok=True)

            print(f"\n--- [{iter_counter}/{total_iters}] {pname} = {val} ---")

            apply_param(pname, val)
            time.sleep(stab)

            if do_recal:
                p = recalibrate()
                print(f"  Phase: {p:.2f}deg")
            else:
                print(f"  Phase kept: {phase:.2f}deg")

            # Acquire dispersion
            dg_sweep.setup_ramp(freq=RAMP_FREQ, amplitude=(RAMP_HIGH - RAMP_LOW),
                                offset=0, symmetry=RAMP_SYMMETRY, channel=1)
            dg_sweep.set_sync_state(True, channel=1); time.sleep(0.5)
            dg_temp.set_output(False, channel=2); time.sleep(1.0)

            aux_path = f"/{MAPPING['lockin_r']['device_id']}/auxins/0/sample.AuxIn0"
            daq_cfg = DAQConfig(
                device=MAPPING["lockin_r"]["device_id"],
                trigger_type=1, trigger_channel=0, trigger_level=1, trigger_slope=0,
                trigger_delay=0, duration=DAQ_DURATION,
                grid_cols=int(actual_rate * DAQ_DURATION), grid_rows=1, grid_mode=2,
                signal_paths=["sample.r", "sample.x", "sample.y"], extra_paths=[aux_path],
            )
            daq_results = daq.acquire_data(hfi, config=daq_cfg, demod_idx=0,
                                           actual_rate=actual_rate, timeout=DAQ_DURATION + 10)

            dg_sweep.set_output(False, channel=1); dg_sweep.set_sync_state(False, channel=1)
            dg_temp.set_output(True, channel=2); time.sleep(0.5)

            # Parse
            T_ramp = 1.0 / RAMP_FREQ; s_val = RAMP_SYMMETRY / 100
            rd = {}
            for res in daq_results:
                rd[res.signal_name] = res.values
                rd[f"{res.signal_name}_time"] = res.time
            t_arr = daq_results[0].time; tn_arr = t_arr / T_ramp
            V_z = np.where(tn_arr % 1 < s_val,
                RAMP_LOW + (RAMP_HIGH - RAMP_LOW) * (tn_arr % 1) / s_val,
                RAMP_HIGH - (RAMP_HIGH - RAMP_LOW) * ((tn_arr % 1) - s_val) / (1 - s_val))
            rd["Z_voltage"] = V_z; rd["t_norm"] = tn_arr % 1
            np.savez(run_dir / "raw" / "scan_data.npz", **rd)

            # Fit
            fall_start = s_val + (1 - s_val) * 0.1
            fall_end = s_val + (1 - s_val) * 0.9
            fall_mask = (rd["t_norm"] >= fall_start) & (rd["t_norm"] < fall_end)
            V_fit = rd["Z_voltage"][fall_mask]
            Y_raw = rd["sample.y"][fall_mask]

            fit_result = fit_dispersive(V_fit, Y_raw, Z_V_TO_NT, Z_V_TO_FT,
                                        quality_thresholds=QUALITY_THRESHOLDS)

            if fit_result.is_valid:
                print(f"  Fit OK: R^2={fit_result.r_squared:.3f}, "
                      f"slope={fit_result.slope_V_per_fT:.2e} V/fT, "
                      f"HWHM={fit_result.gamma_nT:.1f}nT")
            else:
                print(f"  Fit REJ: {fit_result.rejection_reasons}")

            # Noise
            noise_dcfg = DemodulatorConfig(demod_index=0, enable=True, rate=50000,
                                           input_channel=0, osc_select=0, harmonic=1,
                                           time_constant=1e-6, order=4, phase=phase)
            actual_noise_rate = demod.configure_demodulator(hfi, noise_dcfg)
            time.sleep(0.3)
            dg_sweep.setup_dc(0, channel=1); dg_sweep.set_output(False, channel=1)
            time.sleep(0.5)
            fs_noise = float(actual_noise_rate)
            noise_dir = run_dir / "raw" / "noise_raw"
            noise_dir.mkdir(parents=True, exist_ok=True)

            for ni in range(NOISE_N_AVG):
                dg_temp.set_output(False, channel=2)
                ncfg = DAQConfig(device=MAPPING["lockin_r"]["device_id"],
                                 trigger_type=0, duration=NOISE_DURATION,
                                 grid_cols=int(fs_noise * NOISE_DURATION),
                                 grid_rows=1, grid_mode=2, signal_paths=["sample.y"])
                nres = daq.acquire_data(hfi, config=ncfg, demod_idx=0,
                                        actual_rate=fs_noise, timeout=NOISE_DURATION + 5)
                dg_temp.set_output(True, channel=2); time.sleep(5)
                np.save(noise_dir / f"noise_{ni:04d}.npy", nres[0].values)
                del nres

            # PSD
            psd_sum = None; freq_arr = None
            nperseg = int(fs_noise * NOISE_DURATION)
            for ni in range(NOISE_N_AVG):
                y = np.load(noise_dir / f"noise_{ni:04d}.npy")
                f_arr, p = scipy_signal.welch(y - np.mean(y), fs=fs_noise,
                                              nperseg=nperseg, scaling="density")
                if psd_sum is None: freq_arr = f_arr; psd_sum = p.copy()
                else: psd_sum += p
            psd_avg = psd_sum / NOISE_N_AVG
            freq_res = float(freq_arr[1] - freq_arr[0])
            np.savez(run_dir / "raw" / "noise_data.npz",
                     psd_avg=psd_avg, freq=freq_arr, fs=fs_noise, n_avg=NOISE_N_AVG)

            # Restore demodulator to scan rate
            restore_cfg = DemodulatorConfig(demod_index=0, enable=True, rate=1000,
                                            input_channel=0, osc_select=0, harmonic=1,
                                            time_constant=0.001, order=4, phase=phase)
            actual_rate = demod.configure_demodulator(hfi, restore_cfg); time.sleep(0.2)

            # Sensitivity (new algorithm: flat region median)
            sens_result = compute_sensitivity(psd_avg, freq_arr,
                                              slope_V_per_fT=fit_result.slope_V_per_fT,
                                              f_larmor_Hz=fit_result.f_larmor_Hz)

            # Record
            all_params = get_all_params({pname: val})
            write_run_record(run_dir, all_params, fit_result, sens_result,
                             summary_path=summary_path, timestamp=ts, run_tag=tag,
                             n_avg=NOISE_N_AVG, freq_resolution_Hz=freq_res)

            status = "OK" if fit_result.is_valid else "REJ"
            print(f"  [{status}] Sens={sens_result.sens_flat:.0f} fT/sqrtHz, "
                  f"HWHM={fit_result.gamma_nT:.1f}nT, "
                  f"f_larmor={fit_result.f_larmor_Hz:.0f}Hz, "
                  f"R^2={fit_result.r_squared:.3f}")
            all_results.append({
                "param": pname, "value": val,
                "fit": fit_result, "sens": sens_result, "status": status,
            })

            valid_res = [r for r in all_results if r["status"] == "OK"]
            if valid_res:
                best = min(valid_res, key=lambda r: r["sens"].sens_flat)
                print(f"  >>> Best so far: {best['param']}={best['value']}, "
                      f"Sens={best['sens'].sens_flat:.0f} fT/sqrtHz")

finally:
    t_end = datetime.now()
    print("\n" + "=" * 60)
    print("OPTIMIZATION COMPLETE")
    print(f"Duration: {(t_end - t_start).total_seconds() / 60:.1f} min")
    print("=" * 60)

    valid_res = [r for r in all_results if r["status"] == "OK"]
    rejected = [r for r in all_results if r["status"] != "OK"]
    print(f"Valid: {len(valid_res)}/{len(all_results)}, Rejected: {len(rejected)}")

    valid_res.sort(key=lambda r: r["sens"].sens_flat)

    print("\n=== All Results (sorted) ===")
    print(f"{'Rank':<5} {'Param':<24} {'Value':<10} {'Sens(fT/sqrtHz)':<16} {'HWHM(nT)':<10} {'Slope(V/fT)':<14} {'R^2':<7}")
    print("-" * 90)
    for rank, r in enumerate(valid_res):
        fr = r["fit"]; sr = r["sens"]
        print(f"{rank+1:<5} {r['param']:<24} {str(r['value']):<10} "
              f"{sr.sens_flat:<16.0f} {fr.gamma_nT:<10.1f} "
              f"{fr.slope_V_per_fT:<14.2e} {fr.r_squared:<7.3f}")

    try:
        dg_temp.set_output(True, channel=2)
        dg_sweep.set_output(False, channel=1)
        dg_sweep.set_sync_state(False, channel=1)
    except: pass
    print("\nCleanup done")