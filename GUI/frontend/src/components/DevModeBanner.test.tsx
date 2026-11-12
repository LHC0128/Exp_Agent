import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DevModeBanner } from "./DevModeBanner";

function stubHealth(runtimeMode: string) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ status: "ok", instance_id: "i", hardware_busy: false, queued_hardware_jobs: 0, runtime_mode: runtimeMode }),
    { status: 200, headers: { "content-type": "application/json" } },
  )));
}

beforeEach(() => { document.title = "原子磁力仪实验控制台"; });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("DevModeBanner", () => {
  it("开发目录后端显示横幅并给标题加前缀", async () => {
    stubHealth("dev");
    render(<DevModeBanner />);
    expect(await screen.findByRole("status")).toHaveTextContent("开发目录：仪器已禁用");
    await waitFor(() => expect(document.title).toBe("[开发] 原子磁力仪实验控制台"));
  });

  it("运行目录后端不显示横幅", async () => {
    stubHealth("lab");
    render(<DevModeBanner />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.queryByRole("status")).toBeNull();
    expect(document.title).toBe("原子磁力仪实验控制台");
  });
});
