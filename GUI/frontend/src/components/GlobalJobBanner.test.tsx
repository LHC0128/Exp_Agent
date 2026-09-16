import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GlobalJobBanner } from "./GlobalJobBanner";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("./JobActivity", () => ({
  useJobActivity: () => ({
    hardwareBusy: false,
    activeJobs: [
      {
        id: "job-1",
        kind: "experiment:mx-y-optimal-control-rf-frequency-response",
        status: "running",
        stage: "optimal_control_scan",
        percent: 45,
        estimated_remaining_seconds: 3725,
        message: "最优控制扫描",
        created_at: "2026-09-13T10:00:00",
      },
    ],
  }),
}));

afterEach(cleanup);

describe("GlobalJobBanner", () => {
  it("横幅显示百分比与格式化 ETA", () => {
    const { getByText } = render(<GlobalJobBanner />);
    expect(getByText("45%")).toBeInTheDocument();
    expect(getByText("预计剩余 1:02:05")).toBeInTheDocument();
  });
});
