"""原 main.py 的 R 专属接口快照；仅作恢复参考，不注册到当前 GUI。"""

@app.get("/api/experiments/rf-sensitivity-aw-r/runs", response_model=RFDirectAWRunsResponse)
def rf_direct_aw_r_runs(limit: int = 50, offset: int = 0):
    return _rf_aw_runs(rf_aw_r_format.EXPERIMENT_ID, limit, offset)


@app.get("/api/experiments/rf-sensitivity-aw-r/runs/{run_id}/summary", response_model=RFDirectAWRunSummary)
def rf_direct_aw_r_summary(run_id: str):
    return _rf_aw_summary(rf_aw_r_format.EXPERIMENT_ID, run_id)


@app.post("/api/experiments/rf-sensitivity-aw-r/compare")
def rf_direct_aw_r_compare(body: RFDirectAWCompareBody):
    return _rf_aw_compare(rf_aw_r_format.EXPERIMENT_ID, body)


@app.get("/api/experiments/rf-sensitivity-aw-r/comparisons/{comparison_id}/artifacts/{name}")
def rf_direct_aw_r_comparison_artifact(comparison_id: str, name: str):
    return _rf_aw_comparison_artifact(rf_aw_r_format.EXPERIMENT_ID, comparison_id, name)
