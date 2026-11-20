#!/usr/bin/env bash
# 提高 Bz 恢复 2f：用户 2026-11-18 授权 bperp-null 3 次（主磁场 关 / +0.40 mA / −0.40 mA），工作波形 8.20 µs。
cd D:/Code/exp_agent
PY=agent_exp_env/Scripts/python.exe; LOG=.cache/tail_comp_scan/bz_log.txt; DRY=${1:-}
ALLOW=(--allow bperp-null
  --allow-param "X_BIAS_MV=89.1:89.1" --allow-param "Y_BIAS_MV=-15.1:-15.1"
  --allow-param "X_SCAN_START_MV=-60:-60" --allow-param "X_SCAN_STOP_MV=60:60" --allow-param "X_SCAN_STEP_MV=30:30"
  --allow-param "Y_SCAN_START_MV=-60:-60" --allow-param "Y_SCAN_STOP_MV=60:60" --allow-param "Y_SCAN_STEP_MV=30:30"
  --allow-param "Z_PULSE_WIDTH_US=8.2|8.2" --allow-param "Z_WAVEFORM_FROM_FILE=true|true"
  --allow-param "Z_WAVEFORM_FILE=z_tail_w8.20_A0.114_tc25.5.csv|z_tail_w8.20_A0.114_tc25.5.csv"
  --allow-param "MAIN_FIELD_ENABLED=true|false" --allow-param "MAIN_FIELD_CURRENT_MA=-0.4:0.4")
COMMON=(--param X_BIAS_MV=89.1 --param Y_BIAS_MV=-15.1
  --param X_SCAN_START_MV=-60 --param X_SCAN_STOP_MV=60 --param X_SCAN_STEP_MV=30
  --param Y_SCAN_START_MV=-60 --param Y_SCAN_STOP_MV=60 --param Y_SCAN_STEP_MV=30
  --param Z_PULSE_WIDTH_US=8.2 --param Z_WAVEFORM_FROM_FILE=true --param Z_WAVEFORM_FILE=z_tail_w8.20_A0.114_tc25.5.csv --timeout 900)
run() {
  local label=$1; shift
  echo "[$(date +%H:%M:%S)] START $label" | tee -a $LOG
  PYTHONIOENCODING=utf-8 $PY tools/lab_client.py run bperp-null "${ALLOW[@]}" "${COMMON[@]}" "$@" $DRY > .cache/tail_comp_scan/out3_$label.json 2>&1
  local code=$?
  echo "[$(date +%H:%M:%S)] END $label exit=$code $(grep -o '"run_dir": *"[^"]*"' .cache/tail_comp_scan/out3_$label.json | head -1)" | tee -a $LOG
  if [ $code -ne 0 ] && grep -q "already in use" .cache/tail_comp_scan/out3_$label.json && [ -z "$RETRIED" ]; then
    echo "[$(date +%H:%M:%S)] RETRY $label after 60 s (MFLI in use at connect; no outputs written)" | tee -a $LOG
    sleep 60; RETRIED=1 run "$label" "$@"; return $?
  fi
  [ $code -eq 0 ] || { echo "STOP: $label exit $code" | tee -a $LOG; exit $code; }
  [ -n "$DRY" ] || sleep 30
}
run bz_off --param MAIN_FIELD_ENABLED=false
run bz_p040 --param MAIN_FIELD_ENABLED=true --param MAIN_FIELD_CURRENT_MA=0.4
run bz_m040 --param MAIN_FIELD_ENABLED=true --param MAIN_FIELD_CURRENT_MA=-0.4
echo "ALL DONE" | tee -a $LOG
