"""采集与离线分析共用原子存储；不连接或操作仪器。"""
from pathlib import Path
from uuid import uuid4
import json
import numpy as np


def save_npz(path, **values):
    path = Path(path)
    temporary = path.with_name(path.name+"."+uuid4().hex+".tmp")
    try:
        with temporary.open("wb") as stream:
            np.savez(stream, **values)
        temporary.replace(path)
    except BaseException:
        if temporary.exists():
            try:
                temporary.replace(path.with_name(path.stem+"_rescue_"+uuid4().hex+".npz"))
            except BaseException:
                pass  # 抢救亦失败时仍保留原写盘异常与临时文件。
        raise


def save_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name+"."+uuid4().hex+".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def persist_point(raw, name, record, records):
    save_npz(raw / name, **{key: value for key, value in record.items() if key != "acquisition_metadata"})
    if "acquisition_metadata" in record:
        save_json(raw / (name+".metadata.json"), record["acquisition_metadata"])
    # JSON 先提交；新格式分析以不含 pickle 的 JSON 为权威，NPZ 保留历史兼容。
    save_json(raw / "frequency_index.json", records)
    save_npz(raw / "frequency_index.npz", records=np.asarray(records, dtype=object))
