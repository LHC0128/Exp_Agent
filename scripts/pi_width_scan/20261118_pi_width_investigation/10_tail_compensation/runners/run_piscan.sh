#!/usr/bin/env bash
# π 宽度复测：用户 2026-11-18 授权 pi-width-scan 2 次（① 拖尾补偿、② 内置脉冲对照）。
# 22:09 用户授权第 3 次：拖尾补偿 + Y_BIAS_V −0.0069（环境变量 YB）。
# 用法：run_piscan.sh <label> <true|false> [--dry-run]
cd D:/Code/exp_agent
PY=agent_exp_env/Scripts/python.exe; LOG=.cache/tail_comp_scan/piscan_log.txt
LABEL=$1; TAIL=$2; DRY=${3:-}; YB=${YB:-0}
OUT=.cache/tail_comp_scan/out5_$LABEL.json
ALLOW=(--allow pi-width-scan
  --allow-param "Z_TAIL_COMP_ENABLED=true|false" --allow-param "Z_TAIL_S_F=0.544:0.544" --allow-param "Z_TAIL_TAU_E_US=46.9:46.9"
  --allow-param "SCAN_RF_PHASE_COUNT=4:4" --allow-param "N_REPEAT=5:5"
  --allow-param "Z_PULSE_WIDTH_START_US=4:4" --allow-param "Z_PULSE_WIDTH_STOP_US=12:12" --allow-param "Z_PULSE_WIDTH_STEP_US=0.25:0.25"
  --allow-param "X_BIAS_V=-0.0409:-0.0409" --allow-param "Y_BIAS_V=$YB:$YB")
PARAMS=(--param Z_TAIL_COMP_ENABLED=$TAIL --param Z_TAIL_S_F=0.544 --param Z_TAIL_TAU_E_US=46.9
  --param SCAN_RF_PHASE_COUNT=4 --param N_REPEAT=5
  --param Z_PULSE_WIDTH_START_US=4 --param Z_PULSE_WIDTH_STOP_US=12 --param Z_PULSE_WIDTH_STEP_US=0.25
  --param X_BIAS_V=-0.0409 --param Y_BIAS_V=$YB --timeout 3600)
attempt() {
  echo "[$(date +%H:%M:%S)] START $LABEL tail=$TAIL ybias=$YB $DRY" | tee -a $LOG
  PYTHONIOENCODING=utf-8 $PY tools/lab_client.py run pi-width-scan "${ALLOW[@]}" "${PARAMS[@]}" $DRY > $OUT 2>&1
  CODE=$?
  echo "[$(date +%H:%M:%S)] END $LABEL exit=$CODE $(grep -o '"run_dir": *"[^"]*"' $OUT | head -1)" | tee -a $LOG
}
attempt
if [ $CODE -ne 0 ] && grep -q "already in use\|占用" $OUT; then
  echo "[$(date +%H:%M:%S)] RETRY $LABEL after 60 s (MFLI in use at connect)" | tee -a $LOG
  sleep 60; attempt
fi
exit $CODE
