#!/usr/bin/env bash
# 拖尾补偿 (A, w) 扫描：用户 2026-11-18 授权 bperp-null 共 21 次运行（基线 + 20 组）。
# 用法：run_scan.sh [--dry-run]；逐次顺序运行，任一次非零退出即停止。
cd D:/Code/exp_agent
PY=agent_exp_env/Scripts/python.exe
LOG=.cache/tail_comp_scan/scan2_log.txt
DRY=${1:-}
FILES=$(ls D:/Code/exp_agent_run/data/z_waveforms/*.csv | xargs -n1 basename | tr '\n' '|' | sed 's/|$//')
ALLOW=(--allow bperp-null
  --allow-param "X_BIAS_MV=89.1:89.1" --allow-param "Y_BIAS_MV=-15.1:-15.1"
  --allow-param "X_SCAN_START_MV=-20:-20" --allow-param "X_SCAN_STOP_MV=20:20" --allow-param "X_SCAN_STEP_MV=20:20"
  --allow-param "Y_SCAN_START_MV=-20:-20" --allow-param "Y_SCAN_STOP_MV=20:20" --allow-param "Y_SCAN_STEP_MV=20:20"
  --allow-param "Z_PULSE_WIDTH_US=6.6|8.2|7.55|7.8|8.05|7.35|7.6|7.85"
  --allow-param "Z_WAVEFORM_FROM_FILE=true|false" --allow-param "Z_WAVEFORM_FILE=$FILES")
COMMON=(--param X_BIAS_MV=89.1 --param Y_BIAS_MV=-15.1
  --param X_SCAN_START_MV=-20 --param X_SCAN_STOP_MV=20 --param X_SCAN_STEP_MV=20
  --param Y_SCAN_START_MV=-20 --param Y_SCAN_STOP_MV=20 --param Y_SCAN_STEP_MV=20 --timeout 900)
START_FROM=${START_FROM:-}
run() {  # label, extra params...
  local label=$1; shift
  if [ -n "$START_FROM" ]; then
    if [ "$label" = "$START_FROM" ]; then START_FROM=""; else echo "skip $label"; return 0; fi
  fi
  echo "[$(date +%H:%M:%S)] START $label" | tee -a $LOG
  PYTHONIOENCODING=utf-8 $PY tools/lab_client.py run bperp-null "${ALLOW[@]}" "${COMMON[@]}" "$@" $DRY > .cache/tail_comp_scan/out2_$label.json 2>&1
  local code=$?
  echo "[$(date +%H:%M:%S)] END $label exit=$code $(grep -o '"run_dir": *"[^"]*"' .cache/tail_comp_scan/out2_$label.json | head -1)" | tee -a $LOG
  if [ $code -ne 0 ] && grep -q "already in use" .cache/tail_comp_scan/out2_$label.json && [ -z "$RETRIED" ]; then
    echo "[$(date +%H:%M:%S)] RETRY $label after 60 s (MFLI in use at connect; no outputs written)" | tee -a $LOG
    sleep 60; RETRIED=1 run "$label" "$@"; return $?
  fi
  [ $code -eq 0 ] || { echo "STOP: $label exit $code" | tee -a $LOG; exit $code; }
  [ -n "$DRY" ] || sleep 30
}
# 第二轮：τc 扫描，用户 2026-11-18 授权 20 次。
run t2_base --param Z_PULSE_WIDTH_US=6.6 --param Z_WAVEFORM_FROM_FILE=false
run t2_ref --param Z_PULSE_WIDTH_US=8.2 --param Z_WAVEFORM_FROM_FILE=true --param "Z_WAVEFORM_FILE=z_tail_w8.20_A0.114_tc25.5.csv"
for spec in "35.0:7.55 7.80 8.05:0.060 0.085 0.110" "45.0:7.35 7.60 7.85:0.050 0.070 0.090"; do
  tcv=${spec%%:*}; rest=${spec#*:}; ws=${rest%%:*}; as=${rest#*:}
  for w in $ws; do for a in $as; do
    wn=$(echo $w | sed 's/0$//')
    run "t2_tc${tcv}_w${w}_A${a}" --param Z_PULSE_WIDTH_US=$wn --param Z_WAVEFORM_FROM_FILE=true --param "Z_WAVEFORM_FILE=z_tail_w${w}_A${a}_tc${tcv}.csv"
  done; done
done
echo "ALL DONE" | tee -a $LOG
