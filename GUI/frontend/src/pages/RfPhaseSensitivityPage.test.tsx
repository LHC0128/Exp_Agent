import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api } from "../api";
import type { XYSession } from "../components/workflow/WorkflowPanel";
import { ExperimentPage } from "./ExperimentPage";
import fixture from "./RfPhaseSensitivityPage.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
vi.mock("../components/JobView", () => ({ JobView: () => null }));

const ID = "rf-phase-sensitivity";
const BASE = `/api/experiments/${ID}`;
const { fields, flow } = fixture;
const parameters = Object.fromEntries(fields.map(field => [field.name, field.default]));
const history = { run_id: "1123_history", timestamp: "1123", run_tag: "history", completion_status: "completed" };
const summaryUrl = `${BASE}/runs/${history.run_id}/summary`;
const results = ["results/phase_transfer.png", "results/phase_noise.png", "results/phase_sensitivity_summary.json"];
const artifacts = Object.fromEntries(results.map(name => [name, `${BASE}/runs/${history.run_id}/files/${name}?v=1`]));
const sessionId = "phase-session";
const jobId = "phase-job";
const sessionUrl = `${BASE}/sessions/${sessionId}`;
const mockApi = vi.mocked(api);
const steps = (selected: string[] = []): XYSession["steps"] => Object.fromEntries(flow.acquisition.map(stage => [
  stage.id, { status: selected.includes(stage.id) ? "completed" : "pending", error: "" },
]));
const acquisitionLabel = (id: string) => `${id} ${flow.acquisition.find(stage => stage.id === id)!.title}`;
const sentBodies = (url: string, method: string) => mockApi.mock.calls
  .filter(([path, options]) => path === url && options?.method === method)
  .map(([, options]) => JSON.parse(String(options?.body)));
const page = () => render(<MemoryRouter initialEntries={[`/experiments/${ID}`]}>
  <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes>
</MemoryRouter>);

let session: XYSession | null;
let preflightOk: boolean;

async function response(url: string, options?: RequestInit): Promise<unknown> {
  if (url === BASE) return { id: ID, title: "射频相位灵敏度", description: "同次标定、扫频与噪声", wiring_notes: [] };
  if (url === `${BASE}/schema`) return { fields, parameter_layout_saved: false };
  if (url === `${BASE}/workflow`) return flow;
  if (url === `${BASE}/sessions/active`) return session;
  if (url === "/api/jobs") return [];
  if (url === `/api/jobs/${jobId}`) return { id: jobId, kind: `session:${ID}`, status: "running", events: [] };
  if (url === `${BASE}/sessions` && options?.method === "POST") {
    return session = {
      session_id: sessionId, job_id: jobId, status: "waiting", revision: 0, parameters,
      pending_request: null, steps: steps(), output_source: "替身会话", demod_mode: "unknown",
    };
  }
  if (url === `${sessionUrl}/steps` && options?.method === "POST" && session) {
    const { step_ids: ids } = JSON.parse(String(options.body)) as { step_ids: string[] };
    if (ids.length === 1 && ids[0] === "A03") throw new Error("缺少前置模块：A02");
    return session = { ...session, steps: steps(ids), revision: session.revision + 1 };
  }
  if (url === `${sessionUrl}/finish` && options?.method === "POST" && session) {
    return session = { ...session, status: "closed", steps: steps(["A01", "A02", "A03", "A04"]), revision: session.revision + 1 };
  }
  if (url.startsWith(`${BASE}/runs?`)) return { runs: [history], total: 1 };
  if (url === summaryUrl) return {
    ...history, analysis_status: "completed", analysis_error: "", parameters: { ...parameters, RUN_TAG: history.run_tag }, artifacts,
  };
  if (url === `${BASE}/derive`) return { ok: true, values: {} };
  if (url === `${BASE}/preflight` && options?.method === "POST") {
    return { ok: preflightOk, errors: preflightOk ? [] : ["相位灵敏度预检未通过"] };
  }
  throw new Error(`未模拟接口：${url}`);
}

beforeEach(() => {
  session = null;
  preflightOk = true;
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => await response(url, options) as never);
});
afterEach(cleanup);

it("从独立的相位灵敏度路由勾选并执行同次测量，结束会话不执行额外测量", async () => {
  page();
  expect(await screen.findByRole("heading", { level: 1, name: "射频相位灵敏度" })).toBeVisible();
  expect(screen.getByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: "历史" })).toBeVisible();
  const workflow = within(await screen.findByRole("region", { name: "交互实验流程图" }));
  expect(await workflow.findByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
  const create = workflow.getByRole("button", { name: "创建分步会话" });
  expect(create).toBeEnabled();
  fireEvent.click(create);
  await workflow.findByText(/分步等待期间保持输出状态/);
  const select = (id: string) => workflow.getByRole("checkbox", { name: acquisitionLabel(id) });
  const run = workflow.getByRole("button", { name: "运行所选" });
  await waitFor(() => expect(select("A03")).toBeEnabled());
  fireEvent.click(select("A03"));
  fireEvent.click(run);
  expect(await screen.findByRole("alert")).toHaveTextContent("缺少前置模块：A02");
  expect(select("A01")).not.toBeChecked();
  expect(select("A02")).not.toBeChecked();

  fireEvent.click(select("A03"));
  for (const id of ["A01", "A02", "A03"]) fireEvent.click(select(id));
  expect(select("A04")).not.toBeChecked();
  fireEvent.click(run);
  const measurement = within(select("A03").closest("li")!);
  expect(await measurement.findByText("执行完成")).toBeVisible();
  expect(sentBodies(`${sessionUrl}/steps`, "POST")).toEqual([
    { revision: 0, step_ids: ["A03"] },
    { revision: 0, step_ids: ["A01", "A02", "A03"] },
  ]);
  const finish = workflow.getByRole("button", { name: "结束会话" });
  await waitFor(() => expect(finish).toBeEnabled());
  fireEvent.click(finish);
  expect(await workflow.findByText(/下方状态为已结束会话/)).toBeVisible();
  expect(sentBodies(`${sessionUrl}/finish`, "POST")).toEqual([{ revision: 1 }]);
  expect(mockApi.mock.calls.some(([url]) => url.startsWith("/api/experiments/rf-phase-response"))).toBe(false);
}, 20_000);

it("预检失败不启动；历史显示两张结果图并只填回快照草稿", async () => {
  page();
  const preflight = await screen.findByRole("button", { name: "仅预检" });
  await waitFor(() => expect(preflight).toBeEnabled());
  fireEvent.click(preflight);
  expect(await screen.findByText("参数预检通过")).toBeVisible();
  preflightOk = false;
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  expect(await screen.findByText("相位灵敏度预检未通过")).toBeVisible();
  expect(sentBodies(`${BASE}/preflight`, "POST")).toEqual([{ parameters }, { parameters }]);

  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  const detail = within(screen.getByRole("tabpanel", { name: "历史" }));
  expect(await detail.findByRole("button", { name: history.run_id })).toBeVisible();
  const fill = await detail.findByRole("button", { name: "填回参数" });
  expect(mockApi).toHaveBeenCalledWith(summaryUrl);
  for (const name of results.filter(name => name.endsWith(".png"))) {
    expect(detail.getByRole("img", { name })).toHaveAttribute("src", artifacts[name]);
  }
  const summary = "results/phase_sensitivity_summary.json";
  expect(detail.getByRole("link", { name: summary })).toHaveAttribute("href", artifacts[summary]);
  fireEvent.click(fill);
  expect(screen.getByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
  const tag = fields.find(field => field.name === "RUN_TAG")!;
  const owner = flow.acquisition.find(stage => stage.display_parameters.includes(tag.name))!;
  const node = screen.getByRole("checkbox", { name: acquisitionLabel(owner.id) }).closest("li")!;
  fireEvent.click(node.querySelector(".xy-node-parameters > summary")!);
  expect(within(node as HTMLElement).getByLabelText(tag.label)).toHaveValue(history.run_tag);
  expect(sentBodies(`${BASE}/runs`, "POST")).toEqual([]);
  expect(sentBodies(`${BASE}/sessions`, "POST")).toEqual([]);
  expect(sentBodies(`${BASE}/defaults`, "PUT")).toEqual([]);
}, 20_000);
