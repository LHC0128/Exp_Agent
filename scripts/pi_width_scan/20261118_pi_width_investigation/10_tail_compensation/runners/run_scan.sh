#!/usr/bin/env bash
# 拖尾补偿 (A, w) 扫描：用户 2026-11-18 授权 bperp-null 共 21 次运行（基线 + 20 组）。
# 用法：run_scan.sh [--dry-run]；逐次顺序运行，任一次非零退出即停止。
cd D:/Code/exp_agent
PY=agent_exp_env/Scripts/python.exe
LOG=.cache/tail_comp_scan/scan_log.txt
DRY=${1:-}
FILES=$(ls D:/Code/exp_agent_run/data/z_waveforms/*.csv | xargs -n1 basename | tr '\n' '|' | sed 's/|$//')
ALLOW=(--allow bperp-null
  --allow-param "X_BIAS_MV=89.1:89.1" --allow-param "Y_BIAS_MV=-15.1:-15.1"
  --allow-param "X_SCAN_START_MV=-20:-20" --allow-param "X_SCAN_STOP_MV=20:20" --allow-param "X_SCAN_STEP_MV=20:20"
  --allow-param "Y_SCAN_START_MV=-20:-20" --allow-param "Y_SCAN_STOP_MV=20:20" --allow-param "Y_SCAN_STEP_MV=20:20"
  --allow-param "Z_PULSE_WIDTH_US=6.6|7.4|7.7|7.93|8.2|8.5"
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
  PYTHONIOENCODING=utf-8 $PY tools/lab_client.py run bperp-null "${ALLOW[@]}" "${COMMON[@]}" "$@" $DRY > .cache/tail_comp_scan/out_$label.json 2>&1
  local code=$?
  echo "[$(date +%H:%M:%S)] END $label exit=$code $(grep -o '"run_dir": *"[^"]*"' .cache/tail_comp_scan/out_$label.json | head -1)" | tee -a $LOG
  [ $code -eq 0 ] || { echo "STOP: $label exit $code" | tee -a $LOG; exit $code; }
}
ONLY=${ONLY:-}
if [ -z "$ONLY" ] || [ "$ONLY" = base ]; then
  run base --param Z_PULSE_WIDTH_US=6.6 --param Z_WAVEFORM_FROM_FILE=false
fi
[ "$ONLY" = base ] && exit 0
for w in 7.40 7.70 7.93 8.20 8.50; do for a in 0.000 0.060 0.114 0.160; do
  wn=$(echo $w | sed 's/0$//; s/\.$//'); [ "$w" = 7.93 ] && wn=7.93
  run "w${w}_A${a}" --param Z_PULSE_WIDTH_US=$wn --param Z_WAVEFORM_FROM_FILE=true --param "Z_WAVEFORM_FILE=z_tail_w${w}_A${a}_tc25.5.csv"
done; done
echo "ALL DONE" | tee -a $LOG
