import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api } from "../../api";
import type { ExperimentSchema, Job, ParameterLayout, ParameterValues } from "../../types/api";
import { LatestResultPanel, RunControls, useRunControls } from "./RunControls";

vi.mock("../../api", () => ({ api: vi.fn() }));
vi.mock("../JobView", () => ({ JobView: ({ job }: { job?: Job }) => job ? <p>任务 {job.id}</p> : null }));

const ID = "run-controls-test";
const BASE = `/api/experiments/${ID}`;
const mockApi = vi.mocked(api);
const layout: ParameterLayout = { basic: ["GAIN"], advanced: [] };
const fields = [{ name: "GAIN", label: "增益", type: "number", default: 1, group: "basic", unit: "" }] as ExperimentSchema["fields"];
let preflightOk = true;
let savedLayout: unknown = layout;
let saved: ParameterLayout | undefined;

function Harness({ canEdit = true, canStart = true, normalize, onStarted }: {
  canEdit?: boolean; canStart?: boolean; normalize?: (values: ParameterValues) => ParameterValues; onStarted?: (job: Job) => void;
}) {
  const [error, setError] = useState("");
  const [job, setJob] = useState<Job>();
  const controls = useRunControls({ experimentId: ID, values: { GAIN: 2 }, layout, normalize, onStarted, setError, setJob,
    onSaved: (_, confirmed) => { saved = confirmed; } });
  return <>
    {error && <p role="alert">{error}</p>}
    <RunControls controls={controls} canEdit={canEdit} canStart={canStart} layoutDirty={false}
      hardwareBusyNote={false} job={job} onJobUpdate={setJob} preview={<p>实验预览</p>} />
  </>;
}

beforeEach(() => {
  preflightOk = true; savedLayout = layout; saved = undefined;
  mockApi.mockReset();
  mockApi.mockImplementation(async (url) => {
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["增益超限"] } as never;
    if (url === `${BASE}/runs`) return { id: "job-1", status: "queued", events: [] } as never;
    if (url === `${BASE}/defaults`) return { message: "默认参数已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: savedLayout } } as never;
    if (String(url).startsWith("/api/jobs/")) return { id: "job-1", status: "queued", events: [] } as never;
    throw new Error(`unexpected ${String(url)}`);
  });
});
afterEach(cleanup);

const posts = (path: string) => mockApi.mock.calls.filter(([url]) => url === `${BASE}${path}`);

it("仅预检只调用预检接口并在按钮下显示结果，不启动运行", async () => {
  render(<Harness />);
  expect(screen.getByText("实验预览")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "仅预检" }));
  expect(await screen.findByText("参数预检通过")).toBeInTheDocument();
  expect(posts("/preflight")).toHaveLength(1);
  expect(posts("/runs")).toHaveLength(0);
});

it("预检失败显示错误且不启动运行", async () => {
  preflightOk = false;
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  expect(await screen.findByText("增益超限")).toBeInTheDocument();
  expect(posts("/runs")).toHaveLength(0);
});

it("预检通过后启动运行，请求体使用 normalize 后的参数", async () => {
  const onStarted = vi.fn();
  render(<Harness normalize={values => ({ ...values, LINKED: true })} onStarted={onStarted} />);
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  expect(await screen.findByText("任务 job-1")).toBeInTheDocument();
  expect(onStarted).toHaveBeenCalledWith(expect.objectContaining({ id: "job-1" }));
  for (const [, options] of [...posts("/preflight"), ...posts("/runs")]) {
    expect(JSON.parse(String(options?.body)).parameters).toEqual({ GAIN: 2, LINKED: true });
  }
});

it("保存需后端回读一致布局；一致时回传并提示，不一致时报错", async () => {
  const { unmount } = render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  expect(await screen.findByText("默认参数已保存")).toBeInTheDocument();
  expect(saved).toEqual(layout);
  unmount(); saved = undefined;
  savedLayout = { basic: [], advanced: ["GAIN"] };
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("后端未确认参数布局已保存");
  expect(saved).toBeUndefined();
});

it("按页面条件禁用按钮", async () => {
  render(<Harness canEdit={false} canStart={false} />);
  for (const name of ["保存参数与布局", "仅预检", "预检并运行实验"]) expect(screen.getByRole("button", { name })).toBeDisabled();
  cleanup();
  render(<Harness canEdit canStart={false} />);
  expect(screen.getByRole("button", { name: "仅预检" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "预检并运行实验" })).toBeDisabled();
  await waitFor(() => expect(mockApi).not.toHaveBeenCalled());
});

it("最近一次结果：无记录提示；有记录时渲染并可打开详情", () => {
  const onOpen = vi.fn();
  const { rerender } = render(<LatestResultPanel latest={undefined} render={() => null} onOpen={onOpen} />);
  expect(screen.getByText("暂无运行记录。")).toBeInTheDocument();
  rerender(<LatestResultPanel latest={{ run_id: "r1" }} render={value => <p>结果 {value.run_id}</p>} onOpen={onOpen} />);
  expect(screen.getByText("结果 r1")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "查看本次详情" }));
  expect(onOpen).toHaveBeenCalledWith("r1");
});
