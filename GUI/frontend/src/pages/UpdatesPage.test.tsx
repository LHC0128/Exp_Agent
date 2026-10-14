import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";
import { UpdatesPage } from "./UpdatesPage";

vi.mock("../api", () => ({ api: vi.fn() }));

const mockApi = vi.mocked(api);

const GIT = {
  branch: "main", upstream: "origin/main", behind: 2, ahead: 0,
  clean: true, head: "abc123 演示提交", error: null,
};
const PIP = {
  items: [
    { name: "numpy", current: "1.24.0", latest: "2.1.0", source: "requirements.txt" },
  ],
};
const NPM_GLOBAL = {
  items: [
    { name: "@colbymchenry/codegraph", current: "1.6.2", wanted: "1.7.0", latest: "1.7.0" },
  ],
};
const SKILLS = {
  items: [{ name: "expcodegen", description: "生成实验脚本", dirs: [".agents/skills"] }],
  note: "Skills 为本地目录，无版本源，仅列出清单。",
};
const TOOLCHAIN = { items: [{ name: "python", version: "3.13.0", error: null }] };

const DONE_JOB = {
  id: "job-1", kind: "update:pip", status: "completed", stage: "complete",
  percent: 100, message: "任务完成", created_at: "2026-10-13T10:00:00",
  events: [], result: { updated: ["numpy"] },
};

beforeEach(() => {
  mockApi.mockReset();
  mockApi.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url === "/api/updates/git") return GIT as never;
    if (url === "/api/updates/pip" && options?.method !== "POST") return PIP as never;
    if (url === "/api/updates/npm-frontend") return { items: [] } as never;
    if (url === "/api/updates/npm-global") return NPM_GLOBAL as never;
    if (url === "/api/updates/npm-olcli") return { items: [] } as never;
    if (url === "/api/updates/skills") return SKILLS as never;
    if (url === "/api/updates/toolchain") return TOOLCHAIN as never;
    if (url === "/api/updates/git-pull" && options?.method === "POST") return { ...DONE_JOB, kind: "update:git" } as never;
    if (url === "/api/updates/pip" && options?.method === "POST") return DONE_JOB as never;
    throw new Error(`未预期的接口调用: ${url}`);
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("检查更新页", () => {
  it("渲染各分区与过期包行", async () => {
    render(<UpdatesPage />);
    expect(await screen.findByText("numpy")).toBeInTheDocument();
    expect(screen.getByText("@colbymchenry/codegraph")).toBeInTheDocument();
    expect(screen.getByText("expcodegen")).toBeInTheDocument();
    expect(screen.getByText(/落后远端/)).toBeInTheDocument();
    expect(screen.getAllByText("全部均为最新版本。")).toHaveLength(2);
    expect(screen.getByText("3.13.0")).toBeInTheDocument();
  });

  it("勾选包后调用对应更新接口", async () => {
    render(<UpdatesPage />);
    (await screen.findByRole("checkbox", { name: "选择 numpy" })).click();
    (await screen.findByRole("button", { name: /更新选中（1）/ })).click();
    await waitFor(() => expect(mockApi).toHaveBeenCalledWith(
      "/api/updates/pip",
      { method: "POST", body: JSON.stringify({ packages: ["numpy"] }) },
    ));
  });

  it("未勾选时更新按钮禁用，git 拉取按钮可用并调用接口", async () => {
    render(<UpdatesPage />);
    for (const button of await screen.findAllByRole("button", { name: /更新选中（0）/ })) {
      expect(button).toBeDisabled();
    }
    (await screen.findByRole("button", { name: /拉取更新（2）/ })).click();
    await waitFor(() => expect(mockApi).toHaveBeenCalledWith(
      "/api/updates/git-pull", { method: "POST" },
    ));
  });
});
