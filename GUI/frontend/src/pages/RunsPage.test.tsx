import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";
import type { RunSummary, RunWiringResponse, RunsResponse } from "../types/api";
import { RunsPage } from "./RunsPage";

vi.mock("../api", () => ({ api: vi.fn() }));

const mockApi = vi.mocked(api);

const RUN: RunSummary = {
  id: "1001_120000_first",
  experiment_id: "demo-experiment",
  experiment_title: "示例实验",
  path: "data/Demo/1001_120000_first",
  artifacts: [],
  can_analyze: false,
  modified_at: 1,
};

const RUNS: RunsResponse = { total: 1, offset: 0, limit: 50, runs: [RUN] };

function graph(label: string) {
  return {
    nodes: [
      { device: { id: "source", name: "信号源", model: "", device_library_id: null, ports: [] }, x: 0, y: 0 },
      { device: { id: "receiver", name: "接收器", model: "", device_library_id: null, ports: [] }, x: 20, y: 20 },
    ],
    edges: [{
      id: "edge-1", label, signal_type: "electrical",
      source: { device_id: "source", port_id: "out" }, target: { device_id: "receiver", port_id: "in" },
    }],
  };
}

function wiring(overrides: Partial<RunWiringResponse> = {}): RunWiringResponse {
  return {
    available: true,
    recorded_revision: "abcdef1234567890abcdef",
    recorded_at: "1001_120000",
    site: graph("测试线"),
    targets: { [RUN.experiment_id]: graph("目标线") },
    current_revision: "abcdef1234567890abcdef",
    matches_current: true,
    current_error: null,
    reason: null,
    note: "运行记录中的接线快照，不代表当时硬件实际状态或安全校验",
    ...overrides,
  } as RunWiringResponse;
}

function respond(body: RunWiringResponse) {
  mockApi.mockImplementation(async (url: string) => {
    if (url.startsWith("/api/runs?")) return RUNS as never;
    if (url === "/api/experiments") return [] as never;
    if (url === `/api/runs/${RUN.experiment_id}/${RUN.id}/wiring`) return body as never;
    throw new Error(`未预期的接口调用: ${url}`);
  });
}

async function expand() {
  render(<RunsPage />);
  fireEvent.click(await screen.findByRole("button", { name: "查看记录接线" }));
}

beforeEach(() => {
  mockApi.mockReset();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("运行记录接线快照", () => {
  it("展开后显示修订、时间、计数与固定说明", async () => {
    respond(wiring());
    await expand();
    expect(await screen.findByText("abcdef123456")).toBeInTheDocument();
    expect(screen.getByText(/记录时间：1001_120000/)).toBeInTheDocument();
    expect(screen.getByText("与当前接线一致")).toBeInTheDocument();
    expect(screen.getByText("现场图：2 台器件 / 1 条连线")).toBeInTheDocument();
    expect(screen.getByText("目标图（demo-experiment）：2 台器件 / 1 条连线")).toBeInTheDocument();
    expect(screen.getByText("目标图器件（2）")).toBeInTheDocument();
    expect(screen.getByText("信号源（source）")).toBeInTheDocument();
    expect(screen.getByText(/目标线：source\/out → receiver\/in/)).toBeInTheDocument();
    expect(screen.getAllByText(/记录快照不代表当时硬件实际状态或安全校验/).length).toBeGreaterThan(0);
  });

  it("当前接线变化时显示已变更", async () => {
    respond(wiring({ matches_current: false, current_revision: "ffffffffffffffff" }));
    await expand();
    expect(await screen.findByText("当前接线已变更")).toBeInTheDocument();
  });

  it("旧运行没有快照时显示原因且不请求写接口", async () => {
    respond(wiring({
      available: false, site: null, targets: null, matches_current: null, recorded_revision: null,
      reason: "该运行早于接线快照记录，未保存当时的接线图",
    }));
    await expand();
    expect(await screen.findByText("该运行早于接线快照记录，未保存当时的接线图")).toBeInTheDocument();
    for (const [, options] of mockApi.mock.calls) {
      const method = (options as RequestInit | undefined)?.method;
      expect(method === undefined || method === "GET").toBe(true);
    }
  });

  it("当前接线不可读时保留记录快照并提示", async () => {
    respond(wiring({
      current_revision: null, matches_current: null, current_error: "接线图损坏或结构非法：bad",
    }));
    await expand();
    expect(await screen.findByText("当前接线不可读")).toBeInTheDocument();
    expect(screen.getByText(/无法读取当前接线，以下仅为运行记录快照/)).toBeInTheDocument();
    expect(screen.getByText("目标图（demo-experiment）：2 台器件 / 1 条连线")).toBeInTheDocument();
  });

  it("收起或卸载时中止未完成请求", async () => {
    let signal: AbortSignal | undefined;
    mockApi.mockImplementation((url: string, options?: RequestInit) => {
      if (url.startsWith("/api/runs?")) return Promise.resolve(RUNS as never);
      if (url === "/api/experiments") return Promise.resolve([] as never);
      if (url.endsWith("/wiring")) {
        signal = options?.signal ?? undefined;
        return new Promise(() => {});
      }
      return Promise.reject(new Error(`未预期的接口调用: ${url}`));
    });
    const view = render(<RunsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "查看记录接线" }));
    await waitFor(() => expect(signal).toBeDefined());
    view.unmount();
    expect(signal?.aborted).toBe(true);
  });
});
