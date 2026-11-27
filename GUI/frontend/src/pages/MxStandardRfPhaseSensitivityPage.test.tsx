import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api } from "../api";
import type { XYSession } from "../components/workflow/WorkflowPanel";
import { ExperimentPage } from "./ExperimentPage";
import fixture from "./MxStandardRfPhaseSensitivityPage.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
vi.mock("../components/JobView", () => ({ JobView: () => null }));

const ID = "mx-standard-rf-phase-sensitivity";
const BASE = "/api/experiments/" + ID;
const { fields, flow } = fixture;
const parameters = Object.fromEntries(fields.map(field => [field.name, field.default]));
const history = { run_id: "1127_history", timestamp: "1127", run_tag: "history", completion_status: "completed" };
const summaryUrl = BASE + "/runs/" + history.run_id + "/summary";
const results = ["results/summary.json", "results/phase_diagram.png", "results/noise_paths.png"];
const artifacts = Object.fromEntries(results.map(name => [name, BASE + "/runs/" + history.run_id + "/files/" + name + "?v=1"]));
const sessionId = "mx-std-session";
const jobId = "mx-std-job";
const sessionUrl = BASE + "/sessions/" + sessionId;
const mockApi = vi.mocked(api);
const steps = (selected: string[] = []): XYSession["steps"] => Object.fromEntries(flow.acquisition.map(stage => [
  stage.id, { status: selected.includes(stage.id) ? "completed" : "pending", error: "" },
]));
const acquisitionLabel = (id: string) => id + " " + flow.acquisition.find(stage => stage.id === id)!.title;
const sentBodies = (url: string, method: string) => mockApi.mock.calls
  .filter(([path, options]) => path === url && options?.method === method)
  .map(([, options]) => JSON.parse(String(options?.body)));
const page = () => render(<MemoryRouter initialEntries={["/experiments/" + ID]}>
  <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes>
</MemoryRouter>);

let session: XYSession | null;
let preflightOk: boolean;

async function response(url: string, options?: RequestInit): Promise<unknown> {
  if (url === BASE) return { id: ID, title: "标准 Mx 射频相位图与噪声路径", description: "相图与噪声路径", wiring_notes: [] };
  if (url === BASE + "/schema") return { fields, parameter_layout_saved: false };
  if (url === BASE + "/workflow") return flow;
  if (url === BASE + "/sessions/active") return session;
  if (url === "/api/jobs") return [];
  if (url === "/api/jobs/" + jobId) return { id: jobId, kind: "session:" + ID, status: "running", events: [] };
  if (url === BASE + "/sessions" && options?.method === "POST") {
    return session = {
      session_id: sessionId, job_id: jobId, status: "waiting", revision: 0, parameters,
      pending_request: null, steps: steps(), output_source: "替身会话", demod_mode: "unknown",
    };
  }
  if (url === sessionUrl + "/steps" && options?.method === "POST" && session) {
    const { step_ids: ids } = JSON.parse(String(options.body)) as { step_ids: string[] };
    if (ids.length === 1 && ids[0] === "A03") throw new Error("缺少前置模块：A02");
    return session = { ...session, steps: steps(ids), revision: session.revision + 1 };
  }
  if (url === sessionUrl + "/finish" && options?.method === "POST" && session) {
    const done = flow.acquisition.filter(stage => stage.id !== flow.finish_step_id).map(stage => stage.id);
    return session = { ...session, status: "closed", steps: steps(done), revision: session.revision + 1 };
  }
  if (url.startsWith(BASE + "/runs?")) return { runs: [history], total: 1 };
  if (url === summaryUrl) return {
    ...history, analysis_status: "completed", analysis_error: "", parameters: { ...parameters, RUN_TAG: history.run_tag }, artifacts,
  };
  if (url === BASE + "/derive") return { ok: true, values: { main_field_current_ma: 0.4311 } };
  if (url === BASE + "/preflight" && options?.method === "POST") {
    return { ok: preflightOk, errors: preflightOk ? [] : ["标准 Mx 预检未通过"] };
  }
  throw new Error("未模拟接口：" + url);
}

beforeEach(() => {
  session = null;
  preflightOk = true;
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => await response(url, options) as never);
});
afterEach(cleanup);

it("从标准 Mx 路由勾选并执行分步会话，结束会话不触发额外测量", async () => {
  page();
  expect(await screen.findByRole("heading", { level: 1, name: "标准 Mx 射频相位图与噪声路径" })).toBeVisible();
  expect(screen.getByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
  const workflow = within(await screen.findByRole("region", { name: "交互实验流程图" }));
  expect(await workflow.findByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", BASE + "/workflow/diagram");
  fireEvent.click(workflow.getByRole("button", { name: "创建分步会话" }));
  await workflow.findByText(/分步等待期间保持输出状态/);
  const select = (id: string) => workflow.getByRole("checkbox", { name: acquisitionLabel(id) });
  const run = workflow.getByRole("button", { name: "运行所选" });
  await waitFor(() => expect(select("A03")).toBeEnabled());
  fireEvent.click(select("A03"));
  fireEvent.click(run);
  expect(await screen.findByRole("alert")).toHaveTextContent("缺少前置模块：A02");
  expect(select("A01")).not.toBeChecked();

  fireEvent.click(select("A03"));
  for (const id of ["A01", "A02", "A03"]) fireEvent.click(select(id));
  expect(select("A04")).not.toBeChecked();
  fireEvent.click(run);
  const measurement = within(select("A03").closest("li")!);
  expect(await measurement.findByText("执行完成")).toBeVisible();
  expect(sentBodies(sessionUrl + "/steps", "POST")).toEqual([
    { revision: 0, step_ids: ["A03"] },
    { revision: 0, step_ids: ["A01", "A02", "A03"] },
  ]);
  const finish = workflow.getByRole("button", { name: "结束会话" });
  await waitFor(() => expect(finish).toBeEnabled());
  fireEvent.click(finish);
  expect(await workflow.findByText(/下方状态为已结束会话/)).toBeVisible();
  expect(sentBodies(sessionUrl + "/finish", "POST")).toEqual([{ revision: 1 }]);
}, 20_000);

it("预检失败不启动；历史只填回快照草稿并显示两张结果图", async () => {
  page();
  const preflight = await screen.findByRole("button", { name: "仅预检" });
  await waitFor(() => expect(preflight).toBeEnabled());
  fireEvent.click(preflight);
  expect(await screen.findByText("参数预检通过")).toBeVisible();
  preflightOk = false;
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  expect(await screen.findByText("标准 Mx 预检未通过")).toBeVisible();
  expect(sentBodies(BASE + "/preflight", "POST")).toEqual([{ parameters }, { parameters }]);

  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  const detail = within(screen.getByRole("tabpanel", { name: "历史" }));
  expect(await detail.findByRole("button", { name: history.run_id })).toBeVisible();
  const fill = await detail.findByRole("button", { name: "填回参数" });
  expect(mockApi).toHaveBeenCalledWith(summaryUrl);
  for (const name of results.filter(item => item.endsWith(".png"))) {
    const image = detail.getByRole("img", { name });
    expect(image).toHaveAttribute("src", artifacts[name]);
  }
  const summary = "results/summary.json";
  const summaryLink = detail.getByRole("link", { name: summary });
  expect(summaryLink).toHaveAttribute("href", artifacts[summary]);
  expect(summaryLink).toHaveAttribute("download");
  fireEvent.click(fill);
  expect(screen.getByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
  expect(sentBodies(BASE + "/runs", "POST")).toEqual([]);
  expect(sentBodies(BASE + "/sessions", "POST")).toEqual([]);
}, 20_000);
