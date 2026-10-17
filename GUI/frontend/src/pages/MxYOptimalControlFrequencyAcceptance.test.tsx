import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import fixture from "./MxYOptimalControlFrequencyPage.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it("真实路由逐个渲染37个可见字段，初始化独占、共享参数同步且操作位于流程容器", async () => {
  const base = "/api/experiments/mx-y-optimal-control-rf-frequency-response";
  vi.mocked(api).mockImplementation(async (url: string) => {
    if (url === base) return { id: base.split("/").at(-1), title: "Mx Y 最优控制 RF 频率响应", description: "有限相位中位数", required_devices: [], wiring_notes: [] } as never;
    if (url === `${base}/schema`) return fixture.schema as never;
    if (url === `${base}/workflow`) return fixture.flow as never;
    if (url === `${base}/sessions/active`) return null as never;
    if (url.startsWith(`${base}/runs?`)) return { runs: [], total: 0 } as never;
    if (url === `${base}/derive`) return { values: {} } as never;
    if (url === "/api/jobs") return [] as never;
    throw new Error(`独立验收未提供的接口：${url}`);
  });
  render(<MemoryRouter initialEntries={["/experiments/mx-y-optimal-control-rf-frequency-response"]}>
    <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes>
  </MemoryRouter>);
  await waitFor(() => expect(screen.getByRole("button", { name: "创建分步会话" })).toBeEnabled());
  const flow = screen.getByRole("region", { name: "交互实验流程图" });
  expect(within(flow).getByRole("button", { name: "保存参数与布局" })).toBeInTheDocument();
  expect(within(flow).getByRole("button", { name: "预检并运行实验" })).toBeInTheDocument();
  const regions = new Map<string, HTMLElement>();
  for (const stage of fixture.flow.acquisition) {
    if (!stage.display_parameters.length) continue;
    const region = screen.getByRole("region", { name: `${stage.id} ${stage.title} 参数`, hidden: true });
    regions.set(stage.id, region);
    const details = region.closest("details")!;
    if (!details.open) fireEvent.click(details.querySelector("summary")!);
    const advanced = within(region).queryByText(/^高级参数（/);
    if (advanced && !advanced.closest("details")!.open) fireEvent.click(advanced);
  }
  fireEvent.change(within(regions.get("A01")!).getByLabelText("启用常数 Z 控制对照"), { target: { value: "true" } });
  const rendered = new Set<string>();
  for (const stage of fixture.flow.acquisition) {
    for (const key of stage.display_parameters) {
      const field = fixture.schema.fields.find(item => item.name === key)!;
      const label = `${field.label}${field.unit ? ` (${field.unit})` : ""}`;
      expect(within(regions.get(stage.id)!).getByLabelText(label)).toBeInTheDocument();
      rendered.add(key);
    }
  }
  expect(rendered.size).toBe(37);
  expect(within(regions.get("A03")!).getByLabelText("气室温度 (°C)")).toBeInTheDocument();
  expect(within(regions.get("A04")!).queryByLabelText("气室温度 (°C)")).not.toBeInTheDocument();
  fireEvent.change(within(regions.get("A04")!).getByLabelText("系统稳定时间 (s)"), { target: { value: ".25" } });
  expect(within(regions.get("A05")!).getByLabelText("系统稳定时间 (s)")).toHaveValue(.25);
  expect(screen.queryByLabelText(/历史兼容幅度/)).not.toBeInTheDocument();
});
