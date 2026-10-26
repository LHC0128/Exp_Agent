"""所有活动分析模块的无硬件导入边界；不替代各实验的科学回归。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import pytest
import yaml

from .registry import list_experiments


CASES = [(item.id, item.experiment_runner.__self__.analysis_module) for item in list_experiments()]


@pytest.mark.parametrize(("experiment_id", "module"), CASES, ids=[item[0] for item in CASES])
def test_analysis_import_needs_no_run_context_or_project_cwd(tmp_path, experiment_id, module):
    assert isinstance(module, str) and module
    assert not tmp_path.is_relative_to(Path(__file__).resolve().parents[2])
    env = {key: value for key, value in os.environ.items() if not key.startswith("LAB_") and key != "PYTHONPATH"}
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", MPLBACKEND="Agg")
    command = [sys.executable, "-X", "utf8", "-B", "-c",
        "import importlib,sys; importlib.import_module(sys.argv[1])", module]
    started = time.monotonic()
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True,
        text=True, encoding="utf-8", timeout=120)
    print(json.dumps(dict(experiment_id=experiment_id, command=command, cwd=str(tmp_path),
        returncode=result.returncode, elapsed_seconds=time.monotonic() - started), ensure_ascii=False))
    print(result.stdout, result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not list(tmp_path.iterdir()), "导入分析模块不能创建运行目录或产物"


def check_real_analysis_module(run, module, artifact, mode):
    """专属测试用自己的真实原始数据调用；共享导入检查不能替代它。"""
    run = run.resolve()
    config_path = run / "experiment_config.yaml"
    parameters = yaml.safe_load(config_path.read_text(encoding="utf-8"))["parameters"]
    raw = {p.relative_to(run): p.read_bytes() for p in (run / "raw").rglob("*") if p.is_file()}
    before = {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()}
    with tempfile.TemporaryDirectory(prefix="analysis-real-module-") as directory:
        cwd = Path(directory)
        decoy = cwd / "data" / run.parent.name / "9999_latest"
        shutil.copytree(run, decoy)
        decoy_before = {p.relative_to(decoy): p.read_bytes() for p in decoy.rglob("*") if p.is_file()}
        env = {key: value for key, value in os.environ.items() if not key.startswith("LAB_") and key != "PYTHONPATH"}
        env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", MPLBACKEND="Agg")
        if mode != "missing":
            env["LAB_RUN_DIR"] = str(run if mode == "explicit" else cwd / "absent")
        command = [sys.executable, "-X", "utf8", "-m", module]
        started = time.monotonic()
        if mode == "explicit":
            from . import get_experiment, typed

            identity = yaml.safe_load(config_path.read_text(encoding="utf-8"))["experiment_id"]
            adapter = get_experiment(identity).analysis_runner.__self__
            popen, launches = typed.subprocess.Popen, []

            def launch(argv, **kwargs):
                assert argv[-2:] == ["-m", module]
                assert kwargs["cwd"] == cwd
                assert kwargs["env"]["LAB_RUN_DIR"] == str(run)
                assert kwargs["env"].get("LAB_TYPED_PARAMETERS") != "invalid old context"
                process = popen([sys.executable, *argv[1:]], **kwargs)
                def expire():
                    if process.poll() is None:
                        print(json.dumps(dict(child_timeout=120, pid=process.pid)), flush=True)
                        process.kill()

                timer = threading.Timer(120, expire)
                timer.daemon = True
                launches.append(([sys.executable, *argv[1:]], process, timer))
                timer.start()
                return process

            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(adapter, "root", cwd)
                patch.setattr(typed.subprocess, "Popen", launch)
                patch.setenv("LAB_RUN_DIR", str(decoy))
                patch.setenv("LAB_TYPED_PARAMETERS", "invalid old context")
                try:
                    returned = adapter.analyze(run, None)
                finally:
                    for _, _, timer in launches:
                        timer.cancel()
            assert len(launches) == 1
            command, process, _ = launches[0]
            returncode, stdout, stderr = process.returncode, "", ""
            assert returned["run_dir"] == str(run) and returned["artifacts"]
            assert not list((cwd / "data/.gui_jobs").glob("*.cancel"))
        else:
            result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                text=True, encoding="utf-8", timeout=120)
            returncode, stdout, stderr = result.returncode, result.stdout, result.stderr
        print(json.dumps(dict(command=command, cwd=str(cwd), mode=mode,
            run_dir=env.get("LAB_RUN_DIR"), elapsed_seconds=time.monotonic()-started,
            returncode=returncode), ensure_ascii=False))
        print(stdout, stderr)
        assert {p.relative_to(decoy): p.read_bytes() for p in decoy.rglob("*") if p.is_file()} == decoy_before
        assert {p.relative_to(run): p.read_bytes() for p in (run / "raw").rglob("*") if p.is_file()} == raw
        if mode == "explicit":
            assert returncode == 0, stdout + stderr
            assert (run / "results" / artifact).stat().st_size > 0
            state = json.loads((run / "analysis_state.json").read_text(encoding="utf-8"))
            assert state["summary"]["status"] == "completed"
            assert all(item["status"] == "completed" for item in state["steps"].values())
            assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["parameters"] == parameters
        else:
            assert {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()} == before
            assert returncode != 0
            assert ("缺少 LAB_RUN_DIR" if mode == "missing" else "运行目录不存在") in stderr


def check_real_caller_config_failure(ctx, steps_module, monkeypatch, fault, original_error):
    """实际 execute→persist→writer；只在底层真实写入/替换点注入故障。"""
    config_path = ctx.config_path
    writer_error, analysis_error = OSError("状态磁盘故障"), ValueError("原始分析故障")
    dump, replace, load_inputs = yaml.safe_dump, os.replace, steps_module.load_inputs
    attempts = []

    def should_fail(config):
        return "analysis_status" in config and (not original_error or config["analysis_status"] == "failed")

    def failed_dump(config, stream=None, **kwargs):
        if stream is not None and fault == "write" and should_fail(config):
            attempts.append(config_path.read_bytes())
            stream.write("analysis_status: partial\n")
            raise writer_error
        return dump(config, stream, **kwargs)

    def failed_replace(source, destination):
        if fault == "replace" and Path(destination).resolve() == config_path.resolve():
            config = yaml.safe_load(Path(source).read_text(encoding="utf-8"))
            if should_fail(config):
                attempts.append(config_path.read_bytes())
                assert Path(source).read_bytes()
                raise writer_error
        return replace(source, destination)

    def failed_analysis(*args, **kwargs):
        load_inputs(*args, **kwargs)
        raise analysis_error

    monkeypatch.setattr(yaml, "safe_dump", failed_dump)
    monkeypatch.setattr(os, "replace", failed_replace)
    if original_error:
        monkeypatch.setattr(steps_module, "load_inputs", failed_analysis)
    with pytest.raises(ValueError if original_error else OSError) as caught:
        ctx.execute(["B01"])
    assert caught.value is (analysis_error if original_error else writer_error)
    assert len(attempts) == 1 and config_path.read_bytes() == attempts[0]
    assert not list(ctx.run_dir.glob(".experiment_config.yaml.*.tmp"))
