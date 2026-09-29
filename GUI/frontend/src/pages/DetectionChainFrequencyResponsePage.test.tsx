import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false }) }));
vi.mock("../components/JobView", () => ({ JobView: () => null }));
const mockApi = vi.mocked(api);
const base = "/api/experiments/detection-chain-frequency-response";

afterEach(() => { cleanup(); mockApi.mockReset(); });

it("按真实路由显示三 Tab，保留编辑并从历史填回，预检失败不启动", async () => {
  const summary = {
    run_id: "0924_1200_freq_resp", timestamp: "2026-09-24T12:00:00", run_tag: "saved-tag",
    completion_status: "completed", analysis_status: "completed", failure_reason: "", analysis_error: "",
    parameters: { RUN_TAG: "saved-tag" }, artifacts: { "frequency_response.png": "/plot.png" },
    temperature_control: { control_source: "external_software", actual_temperature_c: null },
  };
  mockApi.mockImplementation(async (url) => {
    if (url === base) return { title: "探测链路频率响应标定", description: "探测链路标定", wiring_notes: [] } as never;
    if (url === `${base}/schema`) return { fields: [{ name: "RUN_TAG", label: "运行标签", type: "string", default: "freq_resp", group: "basic" }], parameter_layout: { basic: ["RUN_TAG"], advanced: [] } } as never;
    if (url === "/api/jobs") return [] as never;
    if (url.startsWith(`${base}/runs?`)) return { total: 1, runs: [summary] } as never;
    if (url === `${base}/runs/${summary.run_id}/summary`) return summary as never;
    if (url === `${base}/preflight`) return { ok: false, errors: ["频率范围无效"] } as never;
    throw new Error(`未模拟 ${url}`);
  });
  render(<MemoryRouter initialEntries={["/experiments/detection-chain-frequency-response"]}><Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes></MemoryRouter>);
  const tabs = screen.getByRole("tablist", { name: "探测链路频率响应标定视图" });
  expect(tabs).toHaveClass("zaw-tab-strip");
  expect(within(tabs).getAllByRole("tab")).toHaveLength(3);
  fireEvent.change(await screen.findByLabelText("运行标签"), { target: { value: "edited" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(await within(screen.getByRole("tabpanel", { name: "历史" })).findByAltText("频率响应曲线")).toHaveAttribute("src", "/plot.png");
  expect(within(screen.getByRole("tabpanel", { name: "历史" })).getByText(/外部温控：程序未设置或检测温度/)).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("edited");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("saved-tag");
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  expect(await screen.findByText("频率范围无效")).toBeVisible();
  await waitFor(() => expect(mockApi.mock.calls.some(([url, init]) => url === `${base}/runs` && init?.method === "POST")).toBe(false));
});

it("架构 Tab 内嵌总览与子图切换，可在新页面打开", async () => {
  mockApi.mockImplementation(async (url) => {
    if (url === base) return { title: "探测链路频率响应标定", description: "探测链路标定", wiring_notes: [] } as never;
    if (url === `${base}/schema`) return { fields: [{ name: "RUN_TAG", label: "运行标签", type: "string", default: "freq_resp", group: "basic" }], parameter_layout: { basic: ["RUN_TAG"], advanced: [] } } as never;
    if (url === "/api/jobs") return [] as never;
    if (url.startsWith(`${base}/runs?`)) return { total: 0, runs: [] } as never;
    throw new Error(`未模拟 ${url}`);
  });
  render(<MemoryRouter initialEntries={["/experiments/detection-chain-frequency-response"]}><Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes></MemoryRouter>);
  const tablist = await screen.findByRole("tablist", { name: "探测链路频率响应标定视图" });
  fireEvent.click(within(tablist).getByRole("tab", { name: "架构" }));
  const panel = screen.getByRole("tabpanel", { name: "架构" });
  const frame = within(panel).getByTitle("探测链路频率响应标定实验总架构图");
  expect(frame).toHaveAttribute("src", "/architecture/detection-chain-frequency-response.html");
  expect(within(panel).getByRole("link", { name: "在新页面打开" })).toHaveAttribute("href", "/architecture/detection-chain-frequency-response.html");
  fireEvent.click(within(panel).getByRole("tab", { name: "采集流程" }));
  expect(within(panel).getByTitle("探测链路频率响应标定实验采集执行流程图")).toHaveAttribute("src", "/architecture/detection-chain-frequency-response.acquisition.html");
  fireEvent.click(within(panel).getByRole("tab", { name: "单点时序" }));
  expect(within(panel).getByTitle("探测链路频率响应标定实验单频点采集时序图")).toHaveAttribute("src", "/architecture/detection-chain-frequency-response.timing.html");
  fireEvent.click(within(panel).getByRole("tab", { name: "数据分析" }));
  expect(within(panel).getByTitle("探测链路频率响应标定实验数据与分析图")).toHaveAttribute("src", "/architecture/detection-chain-frequency-response.dataflow.html");
  // 键盘导航在三个 Tab 间循环：外层切回"运行"后左移回绕到架构，再右移回运行。
  const runTab = within(tablist).getByRole("tab", { name: "运行" });
  fireEvent.click(runTab);
  runTab.focus();
  fireEvent.keyDown(tablist, { key: "ArrowLeft" });
  expect(within(tablist).getByRole("tab", { name: "架构" })).toHaveFocus();
  fireEvent.keyDown(tablist, { key: "ArrowRight" });
  expect(runTab).toHaveFocus();
});
