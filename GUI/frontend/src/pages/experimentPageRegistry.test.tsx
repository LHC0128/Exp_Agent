import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import { experimentPageRoutes } from "./experimentPageRegistry";

// 记录挂载次数与挂载时收到的 experimentId：切换变体后若没有按 key 重挂载，
// 页面内部状态会沿用上一个变体，下面的断言会把这种残留直接暴露出来。
const rfPageMounts = vi.hoisted(() => [] as string[]);

vi.mock("./RFSensitivityDirectAWPage", async () => {
  const { createElement, useEffect, useState } = await import("react");
  return {
    RFSensitivityDirectAWPage: ({ experimentId }: { experimentId?: string }) => {
      const [mountedWith] = useState(() => String(experimentId));
      useEffect(() => { rfPageMounts.push(mountedWith); }, [mountedWith]);
      return createElement("div", { "data-testid": "rf-sensitivity-direct-aw" },
        `mounted=${mountedWith} current=${String(experimentId)}`);
    },
  };
});

vi.mock("../api", () => ({ api: vi.fn() }));

// 稳定实验 ID 清单：原分发链 44 行 if 覆盖的 45 个 ID，顺序与 ExperimentPage 历史顺序一致；
// T14 归档 10 个实验后剩 35 个，T15 再归档 4 个后剩 31 个。
const STABLE_EXPERIMENT_IDS: readonly string[] = [
  "mx-z-optimal-control-rf-sensitivity",
  "mx-y-rf-sensitivity",
  "mx-y-rf-probe-detuning-optimization",
  "mx-y-rf-power-optimization",
  "mx-y-rf-frequency-response",
  "mx-main-field-calibration",
  "mx-main-field-scope-noise-spectrum",
  "mx-z-field-calibration",
  "scope-capture",
  "mx-y-optimal-control-rf-frequency-response",
  "static-sensitivity",
  "bell-bloom-z-field-calibration",
  "noise-spectrum-xy",
  "noise-spectrum-xy-demod3-r",
  "projection-noise",
  "photon-shot-noise",
  "rf-sensitivity-constxy-frequency",
  "t1-calibration",
  "t2-calibration",
  "rf-sensitivity-direct-aw-frequency",
  "xy-direct-aw-dc-calibration",
  "bb-power-scan-burst",
  "bb-duty-scan-burst",
  "coil-drive-characterization",
  "z-field-bandwidth",
  "noise-spectrum-xy-known-noise",
  "noise-spectrum-xy-uncontrolled-probe-am",
  "detection-chain-frequency-response",
  "rf-control-scale-sensitivity",
  "rf-sensitivity-aw",
  "rf-sensitivity-aw-known-noise",
];

function RouteSwitcher() {
  const navigate = useNavigate();
  return <>
    <button onClick={() => navigate("/experiments/rf-sensitivity-aw")}>切到 rf-sensitivity-aw</button>
    <button onClick={() => navigate("/experiments/rf-sensitivity-aw-known-noise")}>切到 rf-sensitivity-aw-known-noise</button>
  </>;
}

const show = (id: string) => render(
  <MemoryRouter initialEntries={[`/experiments/${id}`]}>
    <RouteSwitcher />
    <Routes>
      <Route path="/experiments/:experimentId" element={<ExperimentPage />} />
    </Routes>
  </MemoryRouter>,
);

beforeEach(() => { rfPageMounts.length = 0; });
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("配置表恰好覆盖 31 个稳定实验 ID，不漏不多", () => {
  expect(Object.keys(experimentPageRoutes)).toHaveLength(STABLE_EXPERIMENT_IDS.length);
  expect([...Object.keys(experimentPageRoutes)].sort()).toEqual([...STABLE_EXPERIMENT_IDS].sort());
  for (const route of Object.values(experimentPageRoutes)) expect(route).toBeTypeOf("function");
});

it("双 ID 页面按 ID 传入 experimentId，并在切换变体时以 key 重挂载、不残留状态", async () => {
  show("rf-sensitivity-aw");
  const page = await screen.findByTestId("rf-sensitivity-direct-aw");
  expect(page).toHaveTextContent("mounted=rf-sensitivity-aw current=rf-sensitivity-aw");

  fireEvent.click(screen.getByRole("button", { name: "切到 rf-sensitivity-aw-known-noise" }));
  await waitFor(() => expect(screen.getByTestId("rf-sensitivity-direct-aw"))
    .toHaveTextContent("current=rf-sensitivity-aw-known-noise"));
  expect(screen.getByTestId("rf-sensitivity-direct-aw"))
    .toHaveTextContent("mounted=rf-sensitivity-aw-known-noise");
  expect(rfPageMounts).toEqual(["rf-sensitivity-aw", "rf-sensitivity-aw-known-noise"]);

  fireEvent.click(screen.getByRole("button", { name: "切到 rf-sensitivity-aw" }));
  await waitFor(() => expect(screen.getByTestId("rf-sensitivity-direct-aw"))
    .toHaveTextContent("current=rf-sensitivity-aw"));
  expect(rfPageMounts).toEqual(["rf-sensitivity-aw", "rf-sensitivity-aw-known-noise", "rf-sensitivity-aw"]);
});

it("未登记的 ID 回落 GenericExperimentPage", async () => {
  const id = "legacy-archived-experiment";
  expect(experimentPageRoutes[id]).toBeUndefined();
  vi.mocked(api).mockImplementation(async path => {
    if (path === `/api/experiments/${id}`) return {
      id, title: "已归档实验", archived: true, archive_path: "old/experiments",
    } as never;
    if (path === "/api/wiring") return {
      schema_version: 1, revision: "wiring-r1", site: { nodes: [], edges: [] }, targets: {},
      diagnostics: { site: [], targets: {} },
    } as never;
    if (path === "/api/device-ports") return { schema_version: 1, revision: "ports-r1", devices: [], presets: [] } as never;
    throw new Error(`未登记实验不应调用 ${path}`);
  });
  show(id);
  expect(await screen.findByRole("heading", { name: "已归档实验" })).toBeVisible();
  expect(screen.getByRole("link", { name: "返回实验中心" })).toHaveAttribute("href", "/experiments");
  expect(screen.queryByTestId("rf-sensitivity-direct-aw")).not.toBeInTheDocument();
});
