import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";

vi.mock("../api", () => ({ api: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it.each([
  "mx-keithley-6221-main-field-calibration",
  "mx-keithley-6221-optimal-control-rf-sensitivity",
  "mx-keithley-6221-optimal-control-xyz-balance",
  "mx-keithley-6221-optimal-control-balanced-sensitivity",
  "mx-main-field-noise-spectrum", "mx-z-noise-spectrum", "noise-spectrum-xy-v2", "rf-sensitivity-aw-r",
  "aw-const-omega-awscale-check", "dg-am-comp-transfer-check", "rf-sensitivity-aw-frequency",
  "temperature-switch-pid-cycle", "aw-waveform-scope-check",
  "static-sensitivity-optimize", "static-sensitivity-optimize-local", "aw-const-omega-check",
  "xy-dc-voltage-calibration", "xy-aw-voltage-calibration",
])("旧书签 %s 保留归档说明和目标图入口，不加载参数、会话或历史", async id => {
  vi.mocked(api).mockImplementation(async path => {
    if (path === `/api/experiments/${id}`) return {
      id, title: "已归档实验", archived: true, archive_path: "old/experiments",
    } as never;
    if (path === "/api/jobs/old-job") return { id: "old-job", kind: `experiment:${id}`, status: "completed" } as never;
    if (path === "/api/wiring") return {
      schema_version: 1, revision: "wiring-r1", site: { nodes: [], edges: [] }, targets: {},
      diagnostics: { site: [], targets: {} },
    } as never;
    if (path === "/api/device-ports") return { schema_version: 1, revision: "ports-r1", devices: [], presets: [] } as never;
    throw new Error(`归档实验不应调用 ${path}`);
  });
  render(<MemoryRouter initialEntries={[`/experiments/${id}?job=old-job`]}><Routes>
    <Route path="/experiments/:experimentId" element={<ExperimentPage />} />
  </Routes></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "已归档实验" })).toBeVisible();
  expect(screen.getByText("old/experiments")).toBeVisible();
  expect(screen.getByRole("link", { name: "返回实验中心" })).toHaveAttribute("href", "/experiments");
  expect(screen.queryByRole("button", { name: /预检|运行|历史/ })).not.toBeInTheDocument();
  expect(screen.getAllByRole("tab").map(tab => tab.textContent)).toEqual(["目标接线图"]);
  await waitFor(() => expect(screen.getByRole("button", { name: "添加空白图内器件" })).toBeEnabled());
  // 页面与目标接线图各自读取一次实验定义；接线图只读接线要点，不加载参数或历史。
  expect(vi.mocked(api).mock.calls.map(([path]) => path).sort()).toEqual([
    `/api/experiments/${id}`, `/api/experiments/${id}`, "/api/jobs/old-job", "/api/wiring", "/api/device-ports",
  ].sort());
  expect(vi.mocked(api).mock.calls.every(([, options]) => !options?.method)).toBe(true);
});
