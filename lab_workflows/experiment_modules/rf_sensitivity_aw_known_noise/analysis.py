# %% 分阶段离线分析，不连接硬件
import hashlib
import json
from datetime import datetime
from ..rf_sensitivity_direct_aw import analysis as shared
from . import run_format, analysis_steps, stages


class AnalysisWorkflow(shared.AnalysisWorkflow):
    stages = stages.ANALYSIS
    identity = run_format
    analysis_version = "direct-aw-known-noise-v2-phase-comparison"
    steps_module = analysis_steps

    def persist(self):
        for version, child, _ in analysis_steps.control_inputs(self.run_dir, self.config):
            for step, names in {"B02": ("response_fit.npz",), "B03": ("psd_avg.npz",),
                "B04": ("sensitivity.npz", "analysis.json", "analysis.yaml", "full_analysis.png", "full_analysis.pdf")}.items():
                if self.states[step]["status"] == "completed":
                    files = self.states[step].setdefault("files", {})
                    for name in names:
                        files[f"../controls/{version}/results/{name}"] = self._file_signature(child / "results" / name)
        super().persist()

    def input_fingerprint(self):
        inputs = super().input_fingerprint()
        for version, child, config in analysis_steps.control_inputs(self.run_dir, self.config):
            clean = {key: value for key, value in config.items() if key not in ("analysis_status", "analysis_error")}
            inputs[version + "/config"] = hashlib.sha256(json.dumps(clean, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            for path in sorted((child / "raw").glob("*")):
                if path.is_file():
                    inputs[version + "/" + path.name] = self._file_signature(path)
        return inputs

    def _archive_from(self, item):
        super()._archive_from(item)
        for _, child, _ in analysis_steps.control_inputs(self.run_dir, self.config):
            names = []
            for stage in stages.invalidated_stages(self.stages, item):
                names.extend({"B02": ("response_fit.npz",), "B03": ("psd_avg.npz",), "B04": ("sensitivity.npz", "analysis.json", "analysis.yaml", "full_analysis.png", "full_analysis.pdf", "response_curve.png", "response_curve.pdf", "noise_psd.png", "noise_psd.pdf", "sensitivity.png", "sensitivity.pdf", "bandwidth_fit.png", "bandwidth_fit.pdf")}.get(stage.id, ()))
            existing = [child / "results" / name for name in names if (child / "results" / name).is_file()]
            if existing:
                archive = child / "results" / ("before_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
                archive.mkdir()
                for path in existing:
                    path.rename(archive / path.name)


def analyze(run_dir):
    return shared.analyze(run_dir, workflow_type=AnalysisWorkflow)


def main():
    shared.main(workflow_type=AnalysisWorkflow)


if __name__ == "__main__":
    main()
