import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import type { Job } from "../types/api";
import { ExperimentNavigation, experimentDestination, experimentJobUrl, recoverableJob, useExperimentTask } from "./experimentNavigation";
import { ExperimentPage } from "../pages/ExperimentPage";
import { JobView } from "./JobView";

vi.mock("../api", () => ({ api: vi.fn() }));
const task = (kind: string, id = "target", status: Job["status"] = "running"): Job => ({
  id, kind, status, stage: "scan", percent: 45, message: `任务 ${id}`, created_at: "2026-10-01T10:00:00", events: [],
});
let jobs: Record<string, Job>;
function Page() {
  const [tab, setTab] = useState<"run" | "history">("history");
  const [job, setJob] = useState<Job>();
  const [analysisJob, setAnalysisJob] = useState<Job>();
  const [comparisonJob, setComparisonJob] = useState<Job>();
  const [selectedId, setSelectedId] = useState("");
  const [historyView, setHistoryView] = useState<"single" | "comparison">("single");
  const [draft, setDraft] = useState("未保存草稿");
  useExperimentTask({ ready: true, setTab, setJob, setAnalysisJob, setComparisonJob, setSelectedId, setHistoryView });
  return <>
    <p>主视图：{tab}；历史方式：{historyView}；目录：{selectedId}</p>
    <input aria-label="参数草稿" value={draft} onChange={event => setDraft(event.target.value)} />
    <button onClick={() => setTab("history")}>切换历史</button>
    <Link to="/experiments/demo?job=target">前往采集</Link>
    <Link to="/experiments/demo?job=analysis">前往分析</Link>
    <Link to="/experiments/demo?job=comparison">前往对比</Link>
    <div hidden={tab !== "run"}><JobView job={job} onUpdate={setJob} /><JobView job={analysisJob} onUpdate={setAnalysisJob} /></div>
    <div hidden={tab !== "history"}><JobView job={comparisonJob} onUpdate={setComparisonJob} /></div>
  </>;
}
function start(path = "/experiments/demo") {
  return render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/experiments/:experimentId" element={<ExperimentNavigation><Page /></ExperimentNavigation>} />
  </Routes></MemoryRouter>);
}
beforeEach(() => {
  jobs = { target: task("session:demo"), analysis: task("analysis:demo:1001_中文目录", "analysis"), comparison: task("comparison:demo", "comparison") };
  vi.mocked(api).mockReset().mockImplementation(async path => {
    const value = jobs[decodeURIComponent(path.split("/").at(-1)!)];
    if (!value) throw new Error("任务已被清理");
    return value as never;
  });
  vi.stubGlobal("EventSource", class { close() {} });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("只识别正式任务 kind 并编码任务 ID，不把任意字符串拼成实验链接", () => {
  for (const kind of ["experiment:demo", "session:demo", "analysis:demo:1001_a", "comparison:demo"]) {
    expect(experimentJobUrl({ kind, id: "a/b?c" })).toBe("/experiments/demo?job=a%2Fb%3Fc");
  }
  for (const kind of ["clock-sync", "update:pip", "session:", "experiment:../tools"]) expect(experimentDestination({ kind })).toBeUndefined();
});
it.each(["experiment", "session"])("%s 跳转恢复指定任务、切回运行并定位，不启动实验或丢失同页草稿", async type => {
  jobs.target = task(`${type}:demo`);
  start();
  fireEvent.change(screen.getByLabelText("参数草稿"), { target: { value: "仍然保留" } });
  fireEvent.click(screen.getByRole("link", { name: "前往采集" }));
  const status = await screen.findByRole("region", { name: "任务状态" });
  expect(screen.getByText(/主视图：run/)).toBeVisible();
  expect(status).toHaveClass("is-targeted");
  await waitFor(() => expect(status).toHaveFocus());
  expect(screen.getByRole("button", { name: "安全停止" })).toBeVisible();
  expect(screen.getByLabelText("参数草稿")).toHaveValue("仍然保留");
  expect(vi.mocked(api).mock.calls.every(([, options]) => !options?.method)).toBe(true);
});
it("窄屏任务条换行增高时，定位不把任务状态藏到固定任务条后面", async () => {
  const toolbar = document.createElement("div");
  toolbar.className = "app-chrome";
  toolbar.getBoundingClientRect = () => ({ bottom: 220 } as DOMRect);
  document.body.append(toolbar);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ top: 160 } as DOMRect);
  const scroll = vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
  try {
    start("/experiments/demo?job=target");
    await waitFor(() => expect(scroll).toHaveBeenCalledWith({ top: -72 }));
  } finally { toolbar.remove(); }
});
it("分析跳转定位具体目录，对比跳转到历史；同页再次点击仍能回到目标", async () => {
  start();
  fireEvent.click(screen.getByRole("link", { name: "前往分析" }));
  expect(await screen.findByText("任务 analysis")).toBeVisible();
  expect(screen.getByText(/目录：1001_中文目录/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "切换历史" }));
  fireEvent.click(screen.getByRole("link", { name: "前往分析" }));
  await waitFor(() => expect(screen.getByText(/主视图：run/)).toBeVisible());
  fireEvent.click(screen.getByRole("link", { name: "前往对比" }));
  expect(await screen.findByText("任务 comparison")).toBeVisible();
  expect(screen.getByText(/主视图：history；历史方式：comparison/)).toBeVisible();
  expect(vi.mocked(api).mock.calls.every(([, options]) => !options?.method)).toBe(true);
});
it("默认优先恢复运行任务，显式跳转可选排队或已结束任务且不串入别的实验", () => {
  const running = task("experiment:demo", "running");
  const queued = task("experiment:demo", "queued", "queued");
  const finished = task("experiment:demo", "finished", "completed");
  const other = task("session:other", "other");
  const items = [other, queued, running, finished];
  expect(recoverableJob(items, "demo")?.id).toBe("running");
  expect(recoverableJob(items, "demo", "queued")?.id).toBe("queued");
  expect(recoverableJob(items, "demo", "finished")?.id).toBe("finished");
  expect(recoverableJob(items, "demo", "other")?.id).toBe("running");
});
it("真实通用实验路由初始恢复不会用另一个运行任务覆盖指定排队任务", async () => {
  const queued = task("experiment:demo", "target", "queued");
  const old = task("experiment:demo", "old");
  vi.mocked(api).mockImplementation(async path => {
    if (path === "/api/jobs") return [old, queued] as never;
    if (path === "/api/jobs/target") return queued as never;
    if (path.endsWith("/schema")) return { fields: [{ name: "value", label: "参数", type: "number", group: "basic", unit: "", default: 1 }] } as never;
    if (path.endsWith("/derive")) return { ok: true, values: {} } as never;
    if (path === "/api/experiments/demo") return { title: "演示实验", description: "介绍", required_devices: [], wiring_notes: [] } as never;
    throw new Error(`不应读取 ${path}`);
  });
  render(<MemoryRouter initialEntries={["/experiments/demo?job=target"]}><Routes>
    <Route path="/experiments/:experimentId" element={<ExperimentPage />} />
  </Routes></MemoryRouter>);
  expect(await screen.findByText("任务 target")).toBeVisible();
  expect(screen.getByText("排队中")).toBeVisible();
  expect(vi.mocked(api).mock.calls.some(([path]) => path === "/api/jobs/old")).toBe(false);
  expect(vi.mocked(api).mock.calls.filter(([, options]) => options?.method === "POST").every(([path]) => path.endsWith("/derive"))).toBe(true);
});
it("过期任务和实验身份不符均报错，不恢复错误实验的状态", async () => {
  jobs.target = task("experiment:other");
  const first = start("/experiments/demo?job=target");
  expect(await screen.findByRole("alert")).toHaveTextContent("不属于当前实验");
  expect(screen.queryByRole("region", { name: "任务状态" })).not.toBeInTheDocument();
  first.unmount();
  start("/experiments/demo?job=expired");
  expect(await screen.findByRole("alert")).toHaveTextContent("任务已被清理");
});
