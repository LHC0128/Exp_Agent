"""长飘的半开时间计划、可取消绝对等待与轮次清单。"""
from datetime import datetime, timezone
from pathlib import Path
import time
import yaml
from ...experiment_runtime import check_cancelled
from ..mx_y_rf_sensitivity.models import MxYRFParams
from .run_format import RAW_FORMAT_VERSION


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _planned_records(params):
    first = 0.0 if params.drift_start_immediately else params.drift_interval_s
    return [dict(cycle_index=index, scheduled_offset_s=first + index * params.drift_interval_s,
        status="pending", child_run_dir=None, actual_start_utc=None, start_lag_s=None,
        analysis=None, error=None) for index in range(params.planned_cycle_count)]


def _child_parameters(params):
    names = set(MxYRFParams.external_names())
    return {key: value for key, value in params.to_external().items() if key in names}


def _set_record_error(record, status, error):
    record.update(status=status, error=str(error))


def _sleep_until(deadline, *, clock=None, sleep=None):
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    while True:
        check_cancelled()
        remaining = deadline - clock()
        if remaining <= 0:
            return
        sleep(min(0.1, remaining))


def _write_manifest(path: Path, records):
    content = yaml.safe_dump(dict(schema_version=1, raw_format_version=RAW_FORMAT_VERSION,
        records=records), allow_unicode=True, sort_keys=False)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
