#!/usr/bin/env bash
# X/Y 横向拖尾补偿标定：用户 2026-11-18 授权 bperp-null 3 次。
# 22:5x 用户授权：8.03 µs 精确 π 波形下重标 X/Y 补偿，最多 3 次（环境变量 W、WF）。
# 23:0x 用户授权验证 1 次：GS200 关闭（ME=false）、8.03 µs、A*，以 GS200 关闭零场为中心。
# 用法：run_xycal.sh <label> <X_BIAS_MV> <Y_BIAS_MV> <XY_TAIL_X_MV> <XY_TAIL_Y_MV> [--dry-run]
cd D:/Code/exp_agent
PY=agent_exp_env/Scripts/python.exe; LOG=.cache/tail_comp_scan/xycal_log.txt
MF=${MF:--0.4}; W=${W:-8.2}; WF=${WF:-z_tail_w8.20_A0.114_tc25.5.csv}
LABEL=$1; XB=$2; YB=$3; AX=$4; AY=$5; DRY=${6:-}
OUT=.cache/tail_comp_scan/out4_$LABEL.json
ME=${ME:-true}
if [ "$ME" = true ]; then MFA=(--allow-param "MAIN_FIELD_CURRENT_MA=$MF:$MF"); MFP=(--param MAIN_FIELD_CURRENT_MA=$MF); else MFA=(); MFP=(); MF=off; fi
ALLOW=(--allow bperp-null
  --allow-param "X_BIAS_MV=$XB:$XB" --allow-param "Y_BIAS_MV=$YB:$YB"
  --allow-param "X_SCAN_START_MV=-20:-20" --allow-param "X_SCAN_STOP_MV=20:20" --allow-param "X_SCAN_STEP_MV=10:10"
  --allow-param "Y_SCAN_START_MV=-20:-20" --allow-param "Y_SCAN_STOP_MV=20:20" --allow-param "Y_SCAN_STEP_MV=10:10"
  --allow-param "Z_PULSE_WIDTH_US=$W:$W" --allow-param "Z_WAVEFORM_FROM_FILE=true|true"
  --allow-param "Z_WAVEFORM_FILE=$WF|$WF"
  --allow-param "MAIN_FIELD_ENABLED=$ME|$ME" ${MFA[@]}
  --allow-param "XY_TAIL_COMP_ENABLED=true|true" --allow-param "XY_TAIL_TAU_E_US=46.9:46.9"
  --allow-param "XY_TAIL_X_MV=$AX:$AX" --allow-param "XY_TAIL_Y_MV=$AY:$AY")
PARAMS=(--param X_BIAS_MV=$XB --param Y_BIAS_MV=$YB
  --param X_SCAN_START_MV=-20 --param X_SCAN_STOP_MV=20 --param X_SCAN_STEP_MV=10
  --param Y_SCAN_START_MV=-20 --param Y_SCAN_STOP_MV=20 --param Y_SCAN_STEP_MV=10
  --param Z_PULSE_WIDTH_US=$W --param Z_WAVEFORM_FROM_FILE=true --param Z_WAVEFORM_FILE=$WF
  --param MAIN_FIELD_ENABLED=$ME ${MFP[@]}
  --param XY_TAIL_COMP_ENABLED=true --param XY_TAIL_TAU_E_US=46.9 --param XY_TAIL_X_MV=$AX --param XY_TAIL_Y_MV=$AY
  --timeout 900)
attempt() {
  echo "[$(date +%H:%M:%S)] START $LABEL W=$W MF=$MF bias=($XB,$YB) A=($AX,$AY) $DRY" | tee -a $LOG
  PYTHONIOENCODING=utf-8 $PY tools/lab_client.py run bperp-null "${ALLOW[@]}" "${PARAMS[@]}" $DRY > $OUT 2>&1
  CODE=$?
  echo "[$(date +%H:%M:%S)] END $LABEL exit=$CODE $(grep -o '"run_dir": *"[^"]*"' $OUT | head -1)" | tee -a $LOG
}
attempt
if [ $CODE -ne 0 ] && grep -q "already in use\|占用" $OUT; then
  echo "[$(date +%H:%M:%S)] RETRY $LABEL after 60 s (MFLI in use at connect)" | tee -a $LOG
  sleep 60; attempt
fi
exit $CODE
