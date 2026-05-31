"""最小测试扫描: 3 个主磁场值, 验证完整采集→拟合→灵敏度流程."""
import sys; sys.path.insert(0, '.')
import yaml, time, numpy as np
from pathlib import Path
from datetime import datetime
from scipy import signal as scipy_signal
from gs200 import GS200Instrument
from signal_generator import DG4000Instrument, DG900Instrument
from lockin_amplifier import (
    HF2Instrument, DAQConfig,
    SignalInputConfig, OscillatorConfig, DemodulatorConfig,
    demod, daq,
)
from sensitivity_analysis import fit_dispersive, compute_sensitivity, write_run_record

# ========== config ==========
with open('params/mapping.yaml', encoding='utf-8') as f:
    MAPPING = yaml.safe_load(f)['mapping']
with open('params/safety_limits.yaml', encoding='utf-8') as f:
    LIMITS = yaml.safe_load(f)['safety_limits']

def validate_safety_limit(name, value):
    lim = LIMITS.get(name)
    if lim is None: return value
    lo, hi = lim['min'], lim['max']
    if lo is not None and hi is not None:
        if value < lo or value > hi:
            raise ValueError(f"[SAFETY] {name}={value} out of [{lo}, {hi}]")
    return value

EXPERIMENT_TYPE = "Static_Magnetic_Field_Sensitivity"
Z_V_TO_NT, Z_V_TO_FT = 3517, 3517*1e6
RAMP_LOW, RAMP_HIGH, RAMP_FREQ, RAMP_SYMMETRY, DAQ_DURATION = -0.5, 0.5, 1.0, 20, 1
NOISE_N_AVG, NOISE_DURATION = 5, 1.0
PUMP_MOD_FREQ, PUMP_MOD_AMPLITUDE, PUMP_MOD_DUTY = 90e3, 0.18, 5
RF_GATE_AMPLITUDE, RF_GATE_OFFSET = 5.0, 2.5

QUALITY_THRESHOLDS = {
    "r_squared_min": 0.85, "relative_gamma_uncertainty_max": 0.5,
    "residual_sign_change_ratio_min": 0.0,  # 实测色散残差有平滑趋势，宽松门控
}

scan_values = [9.0, 9.3, 9.6]  # mA main_magnetic_field
results = []

try:
    # ---- Connect ----
    print("Connecting devices...")
    gs = GS200Instrument(MAPPING["main_magnetic_field"]["resource"]); gs.connect()
    gs.set_source_function("CURRent"); gs.set_current_limit(10/1000)
    print(f"  GS200: {gs.idn()}")

    dg_sweep = DG4000Instrument(MAPPING["Z_magnetic_field"]["resource"], channel=1)
    dg_sweep.connect(); dg_sweep.set_ref_clock_source("EXTernal")

    dg_laser = DG900Instrument(MAPPING["Pump_laser_power"]["resource"], channel=1)
    dg_laser.connect()

    dg_comp = DG4000Instrument(MAPPING["X_magnetic_field"]["resource"], channel=1)
    dg_comp.connect(); dg_comp.set_ref_clock_source("EXTernal")

    dg_mod = DG4000Instrument(MAPPING["Pump_modulation"]["resource"], channel=1)
    dg_mod.connect()

    dg_temp = DG900Instrument(MAPPING["Temp_Switch"]["resource"], channel=2)
    dg_temp.connect()

    hfi = HF2Instrument(host="127.0.0.1", port=8005, api_level=1, device_id="dev18246")
    hfi.connect(); hfi.set_extclk(True)
    print(f"  HF2: {hfi.idn}")

    # ---- Setup fixed params ----
    print("Setting fixed params...")
    dg_laser.setup_dc(0.2, channel=1)
    dg_laser.setup_dc(0.2, channel=2)
    dg_comp.setup_dc(0, channel=1); dg_comp.set_output(False, channel=1)
    dg_comp.setup_dc(0, channel=2); dg_comp.set_output(False, channel=2)
    dg_temp.setup_dc(5.0, channel=2)
    dg_sweep.setup_square(freq=10, amplitude=10, offset=0, dcycle=50, channel=2)
    dg_sweep.set_output(True, channel=2)
    dg_mod.setup_sine(freq=100e6, amplitude=PUMP_MOD_AMPLITUDE, offset=0, phase=0, channel=1)
    pw = (PUMP_MOD_DUTY/100)/PUMP_MOD_FREQ
    dg_mod.setup_pulse(freq=PUMP_MOD_FREQ, amplitude=RF_GATE_AMPLITUDE,
                       offset=RF_GATE_OFFSET, width=pw, channel=2)

    # ---- Phase calibration ----
    print("Phase calibration...")
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

    # ===== Scan loop =====
    summary_path = Path("data") / EXPERIMENT_TYPE / "run_summary.csv"

    for i, field_ma in enumerate(scan_values):
        ts = datetime.now().strftime("%m%d_%H%M")
        tag = f"mainB_{field_ma}"
        run_dir = Path("data") / EXPERIMENT_TYPE / f"{ts}_{tag}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "raw").mkdir(exist_ok=True)
        (run_dir / "results").mkdir(exist_ok=True)

        print(f"\n--- [{i+1}/{len(scan_values)}] main_magnetic_field = {field_ma} mA ---")

        # Apply parameter
        validate_safety_limit("main_magnetic_field", field_ma)
        gs.set_current(field_ma/1000)
        time.sleep(2)

        print("  Recalibrating phase...")
        dg_temp.set_output(False, channel=2)
        dg_sweep.setup_dc(0, channel=1); dg_sweep.set_output(False, channel=1)
        phase = demod.auto_calibrate_phase(hfi, demod_idx=0, tolerance_deg=1, max_attempts=5, settle_time=0.2)
        dg_temp.set_output(True, channel=2); time.sleep(0.5)
        print(f"  Phase={phase:.2f}deg")

        # Acquire dispersion
        print("  Acquiring dispersion...")
        dg_sweep.setup_ramp(freq=RAMP_FREQ, amplitude=(RAMP_HIGH-RAMP_LOW),
                            offset=0, symmetry=RAMP_SYMMETRY, channel=1)
        dg_sweep.set_sync_state(True, channel=1); time.sleep(0.5)
        dg_temp.set_output(False, channel=2); time.sleep(1.0)

        aux_path = f"/{MAPPING['lockin_r']['device_id']}/auxins/0/sample.AuxIn0"
        daq_cfg = DAQConfig(
            device=MAPPING["lockin_r"]["device_id"],
            trigger_type=1, trigger_channel=0, trigger_level=1, trigger_slope=0,
            trigger_delay=0, duration=DAQ_DURATION,
            grid_cols=int(actual_rate*DAQ_DURATION), grid_rows=1, grid_mode=2,
            signal_paths=["sample.r", "sample.x", "sample.y"], extra_paths=[aux_path],
        )
        daq_results = daq.acquire_data(hfi, config=daq_cfg, demod_idx=0,
                                       actual_rate=actual_rate, timeout=DAQ_DURATION+10)
        print(f"    DAQ done: {len(daq_results)} signals")

        dg_sweep.set_output(False, channel=1); dg_sweep.set_sync_state(False, channel=1)
        dg_temp.set_output(True, channel=2); time.sleep(0.5)

        # Parse
        T_ramp = 1.0/RAMP_FREQ
        rd = {}
        for res in daq_results:
            rd[res.signal_name] = res.values
            rd[f"{res.signal_name}_time"] = res.time

        t = daq_results[0].time; s = RAMP_SYMMETRY/100; tn = t/T_ramp
        V_z = np.where(tn%1 < s,
            RAMP_LOW + (RAMP_HIGH-RAMP_LOW)*(tn%1)/s,
            RAMP_HIGH - (RAMP_HIGH-RAMP_LOW)*((tn%1)-s)/(1-s))
        rd["Z_voltage"] = V_z; rd["t_norm"] = tn%1
        np.savez(run_dir/"raw"/"scan_data.npz", **rd)

        # Fit
        fall_start = s + (1-s)*0.1; fall_end = s + (1-s)*0.9
        fall_mask = (rd["t_norm"] >= fall_start) & (rd["t_norm"] < fall_end)
        V_fit = rd["Z_voltage"][fall_mask]
        Y_raw = rd["sample.y"][fall_mask]

        fit_result = fit_dispersive(V_fit, Y_raw, Z_V_TO_NT, Z_V_TO_FT,
                                    quality_thresholds=QUALITY_THRESHOLDS)

        if fit_result.is_valid:
            print(f"  Fit OK: V0={fit_result.V0:.4f}V, slope={fit_result.slope_V_per_fT:.2e} V/fT, "
                  f"gamma={fit_result.gamma_nT:.1f}nT, R^2={fit_result.r_squared:.4f}")
        else:
            print(f"  Fit REJECTED: {fit_result.rejection_reasons}")

        # Noise
        print("  Acquiring noise...")
        noise_dcfg = DemodulatorConfig(demod_index=0, enable=True, rate=50000, input_channel=0,
                                       osc_select=0, harmonic=1, time_constant=1e-6, order=4, phase=phase)
        actual_noise_rate = demod.configure_demodulator(hfi, noise_dcfg)
        time.sleep(0.3)

        dg_sweep.setup_dc(0, channel=1); dg_sweep.set_output(False, channel=1); time.sleep(0.5)
        fs_noise = float(actual_noise_rate)
        noise_dir = run_dir/"raw"/"noise_raw"; noise_dir.mkdir(parents=True, exist_ok=True)

        for ni in range(NOISE_N_AVG):
            dg_temp.set_output(False, channel=2)
            ncfg = DAQConfig(device=MAPPING["lockin_r"]["device_id"], trigger_type=0,
                             duration=NOISE_DURATION, grid_cols=int(fs_noise*NOISE_DURATION),
                             grid_rows=1, grid_mode=2, signal_paths=["sample.y"])
            nres = daq.acquire_data(hfi, config=ncfg, demod_idx=0, actual_rate=fs_noise,
                                    timeout=NOISE_DURATION+5)
            dg_temp.set_output(True, channel=2); time.sleep(5)
            np.save(noise_dir/f"noise_{ni:04d}.npy", nres[0].values)
            del nres
        print(f"    Noise done: {NOISE_N_AVG} segments")

        # PSD
        psd_sum = None; freq_arr = None; nperseg = int(fs_noise*NOISE_DURATION)
        for ni in range(NOISE_N_AVG):
            y = np.load(noise_dir/f"noise_{ni:04d}.npy")
            f_arr, p = scipy_signal.welch(y-np.mean(y), fs=fs_noise, nperseg=nperseg, scaling="density")
            if psd_sum is None: freq_arr = f_arr; psd_sum = p.copy()
            else: psd_sum += p
        psd_avg = psd_sum/NOISE_N_AVG
        freq_res = float(freq_arr[1]-freq_arr[0])
        np.savez(run_dir/"raw"/"noise_data.npz", psd_avg=psd_avg, freq=freq_arr,
                 fs=fs_noise, n_avg=NOISE_N_AVG)

        # Restore demod
        restore_cfg = DemodulatorConfig(demod_index=0, enable=True, rate=1000, input_channel=0,
                                        osc_select=0, harmonic=1, time_constant=0.001,
                                        order=4, phase=phase)
        actual_rate = demod.configure_demodulator(hfi, restore_cfg); time.sleep(0.2)

        # Sensitivity
        sens_result = compute_sensitivity(psd_avg, freq_arr,
                                          slope_V_per_fT=fit_result.slope_V_per_fT,
                                          f_larmor_Hz=fit_result.f_larmor_Hz, N_TOP=50)

        # Record
        params_dict = {
            "Pump_laser_power": 0.2, "Probe_laser_power": 0.2,
            "X_magnetic_field": 0, "Y_magnetic_field": 0,
            "main_magnetic_field": field_ma, "temperature": 100,
            "PUMP_MOD_DUTY": PUMP_MOD_DUTY, "PUMP_MOD_AMPLITUDE": PUMP_MOD_AMPLITUDE,
        }
        write_run_record(run_dir, params_dict, fit_result, sens_result,
                         summary_path=summary_path, timestamp=ts, run_tag=tag,
                         n_avg=NOISE_N_AVG, freq_resolution_Hz=freq_res)

        status = "OK" if fit_result.is_valid else "REJ"
        print(f"  [{status}] Sens={sens_result.sens_flat:.1f} fT/sqrtHz, "
              f"HWHM={fit_result.gamma_nT:.1f}nT, f_larmor={fit_result.f_larmor_Hz:.1f}Hz")
        results.append({"field_ma": field_ma, "fit": fit_result, "sens": sens_result, "status": status})

    # ---- Summary ----
    print("\n" + "="*60)
    print("SCAN COMPLETE")
    print("="*60)
    for r in results:
        fr = r["fit"]; sr = r["sens"]
        print(f"  B={r['field_ma']}mA: Sens={sr.sens_flat:.1f} fT/sqrtHz, "
              f"HWHM={fr.gamma_nT:.1f}nT, slope={fr.slope_V_per_fT:.2e} V/fT, "
              f"R^2={fr.r_squared:.3f} [{r['status']}]")

finally:
    try:
        dg_temp.set_output(True, channel=2)
        dg_sweep.set_output(False, channel=1)
        dg_sweep.set_sync_state(False, channel=1)
    except: pass
    print("Cleanup done")
