#!/usr/bin/env bash
# 续跑 Z 细扫：-0.088..-0.050 mA，步长 0.002（-0.090 已完成）。
set -u
cd /d/Code/exp_agent
export PYTHONIOENCODING=utf-8
PY=agent_exp_env/Scripts/python.exe
LOG=.cache/z2_fine_resume.log
: > "$LOG"
VALS="-0.088 -0.086 -0.084 -0.082 -0.080 -0.078 -0.076 -0.074 -0.072 -0.070 -0.068 -0.066 -0.064 -0.062 -0.060 -0.058 -0.056 -0.054 -0.052 -0.050"

fail=0
for v in $VALS; do
  [ $fail -ne 0 ] && break
  ival=$(python -c "print(int(round(abs($v)*1000)))")
  tag="z2_m$(printf '%03d' "$ival")"
  echo "=== $tag $(date '+%H:%M:%S')" | tee -a "$LOG"
  if ! "$PY" tools/lab_client.py run rf-phase-response --allow rf-phase-response \
       --allow-param MAIN_FIELD_CURRENT_MA="$v" --param MAIN_FIELD_CURRENT_MA="$v" \
       --allow-param Z_TAIL_S_F=0.65 --param Z_TAIL_S_F=0.65 \
       --allow-param Z_TAIL_TAU_E_US=45 --param Z_TAIL_TAU_E_US=45 \
       --param RUN_TAG="$tag" --allow-param RUN_TAG="$tag" --timeout 600 >>"$LOG" 2>&1; then
    echo "FAILED $tag (session)" | tee -a "$LOG"; fail=1; continue
  fi
  d=$(ls -dt /d/Code/exp_agent_run/data/rf_phase_response/*_"$tag" 2>/dev/null | head -1)
  rows=$(wc -l < "$d/raw/phase_response.csv" 2>/dev/null || echo 0)
  echo "rows=$rows" | tee -a "$LOG"
  if [ "$rows" -lt 37 ]; then echo "FAILED $tag (rows=$rows)" | tee -a "$LOG"; fail=1; continue; fi
  echo "OK $tag" | tee -a "$LOG"
done
[ $fail -eq 0 ] && echo "DONE resume" | tee -a "$LOG" || echo "STOPPED" | tee -a "$LOG"
exit $fail
