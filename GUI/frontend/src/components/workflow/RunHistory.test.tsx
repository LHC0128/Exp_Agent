import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { useEffect, useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api } from "../../api";
import type { SchemaField } from "../../types/api";
import { RunHistoryView, runStatusLabel, useRunHistory } from "./RunHistory";

vi.mock("../../api", () => ({ api: vi.fn() }));

const ID = "run-history-test";
const BASE = `/api/experiments/${ID}`;
const mockApi = vi.mocked(api);
const fields: SchemaField[] = [
  { name: "GAIN", label: "增益", type: "number", default: 1, group: "basic", unit: "" },
];
type Run = { run_id: string; timestamp: string; completion_status: string };
type Summary = Run & { parameters: Record<string, unknown> };
const summaryOf = (run: Run): Summary => ({ ...run, parameters: { GAIN: 9 } });

let allRuns: Run[];
let pageTotal: number;
let requests: string[];

function Harness({ autoSelectLatest, listQuery, fillDisabled, onFillBack, onReanalyze,
  refreshDisabled, listTools, renderRunControls, renderRunStatus, renderRunNote, runRowClassName, onSelectRun,
  emptyListText, detailHeader, detailTools, detailHidden, detailExtra }: {
  autoSelectLatest?: boolean; listQuery?: string; fillDisabled?: boolean;
  onFillBack?: (summary: Summary) => void; onReanalyze?: (summary: Summary) => void;
  refreshDisabled?: boolean; listTools?: React.ReactNode; renderRunControls?: (run: Run) => React.ReactNode;
  renderRunStatus?: (run: Run) => React.ReactNode; renderRunNote?: (run: Run) => React.ReactNode;
  runRowClassName?: string; onSelectRun?: (run: Run) => void; emptyListText?: string;
  detailHeader?: React.ReactNode; detailTools?: (summary: Summary) => React.ReactNode;
  detailHidden?: boolean; detailExtra?: React.ReactNode;
}) {
  const [error, setError] = useState("");
  const history = useRunHistory<Run, Summary>({ experimentId: ID, reloadKeys: [], setError, autoSelectLatest, listQuery });
  return <>
    {error && <p role="alert">{error}</p>}
    <RunHistoryView history={history} fields={fields} renderResult={summary => <p>结果 {summary.run_id}</p>}
      fillBack={{ disabled: Boolean(fillDisabled), onClick: summary => onFillBack?.(summary) }}
      reanalyze={{ disabled: summary => summary.completion_status === "running", onClick: summary => onReanalyze?.(summary) }}
      refreshDisabled={refreshDisabled} listTools={listTools}
      renderRunControls={renderRunControls} renderRunStatus={renderRunStatus} renderRunNote={renderRunNote}
      runRowClassName={runRowClassName} onSelectRun={onSelectRun} emptyListText={emptyListText}
      detailHeader={detailHeader} detailTools={detailTools} detailHidden={detailHidden} detailExtra={detailExtra} />
  </>;
}

beforeEach(() => {
  allRuns = [
    { run_id: "r1", timestamp: "2026-11-01T10:00:00", completion_status: "completed" },
    { run_id: "r2", timestamp: "2026-11-01T11:00:00", completion_status: "failed" },
  ];
  pageTotal = 2;
  requests = [];
  mockApi.mockReset();
  mockApi.mockImplementation(async (url) => {
    requests.push(String(url));
    const query = new URLSearchParams(String(url).split("?")[1]);
    if (String(url).startsWith(`${BASE}/runs?`)) {
      const offset = Number(query.get("offset"));
      const limit = Number(query.get("limit"));
      return { runs: allRuns.slice(offset, offset + limit), total: pageTotal } as never;
    }
    const run = allRuns.find(item => item.run_id === decodeURIComponent(String(url).split("/").at(-2)!));
    if (run) return summaryOf(run) as never;
    throw new Error(`unexpected ${String(url)}`);
  });
});
afterEach(cleanup);

it("runStatusLabel 已知键给中文，未知值原样返回", () => {
  expect(runStatusLabel("completed")).toBe("完成");
  expect(runStatusLabel("acquired")).toBe("已采集，待安全收尾");
  expect(runStatusLabel("waiting_calibration")).toBe("待选择标定曲线");
  expect(runStatusLabel("weird_status")).toBe("weird_status");
});

it("自动选中最近一次并读取摘要，参数表用字段 label", async () => {
  render(<Harness />);
  expect(await screen.findByText("结果 r1")).toBeInTheDocument();
  expect(screen.getByText("增益")).toBeInTheDocument();
  expect(screen.getByText("9")).toBeInTheDocument();
  expect(requests).toContain(`${BASE}/runs/r1/summary`);
  // 最近一次独立读取 limit=1。
  expect(requests).toContain(`${BASE}/runs?limit=1&offset=0`);
});

it("参数表显示结构参数的键和值，同时保留标量的原有显示", async () => {
  const previous = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url.endsWith("/summary")) return { ...summaryOf(allRuns[0]), parameters: {
      GAIN: 9, SCALES: { ControlA: 0.9, ControlB: 1.1 }, CONTROLS: ["ControlA", "ControlB"],
      ENABLED: false, EMPTY: null, NOTE: "历史参数",
    } } as never;
    return previous(url, options);
  });
  render(<Harness />);
  await screen.findByText("结果 r1");
  fireEvent.click(screen.getByText("本次参数"));
  expect(screen.getByRole("cell", { name: '{"ControlA":0.9,"ControlB":1.1}' })).toBeVisible();
  expect(screen.getByRole("cell", { name: '["ControlA","ControlB"]' })).toBeVisible();
  for (const value of ["9", "false", "null", "历史参数"]) expect(screen.getByRole("cell", { name: value })).toBeVisible();
  expect(screen.queryByText("[object Object]")).not.toBeInTheDocument();
});

it("分页先返回旧快照时，默认选择独立读取的最近一次", async () => {
  const previous = mockApi.getMockImplementation()!;
  let resolveLatest!: (value: { runs: Run[]; total: number }) => void;
  const latestList = new Promise<{ runs: Run[]; total: number }>(resolve => { resolveLatest = resolve; });
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/runs?limit=1&offset=0`) return await latestList as never;
    if (url === `${BASE}/runs?limit=50&offset=0`) return { runs: [allRuns[0]], total: 1 } as never;
    return previous(url, options);
  });
  const { result } = renderHook(() => useRunHistory<Run, Summary>({ experimentId: ID, reloadKeys: [], setError: vi.fn() }));
  await waitFor(() => expect(result.current.list.runs[0]?.run_id).toBe("r1"));
  await act(async () => { resolveLatest({ runs: [allRuns[1]], total: 2 }); });
  await waitFor(() => expect(result.current.latest?.run_id).toBe("r2"));
  await waitFor(() => expect(result.current.selected?.run_id).toBe("r2"));
  expect(result.current.selectedId).toBe("r2");
});

it("最近一次延迟返回不覆盖用户手动选择，刷新后也保留", async () => {
  const previous = mockApi.getMockImplementation()!;
  let resolveLatest!: (value: { runs: Run[]; total: number }) => void;
  const latestList = new Promise<{ runs: Run[]; total: number }>(resolve => { resolveLatest = resolve; });
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/runs?limit=1&offset=0`) return await latestList as never;
    return previous(url, options);
  });
  const { result } = renderHook(() => useRunHistory<Run, Summary>({ experimentId: ID, reloadKeys: [], setError: vi.fn() }));
  await waitFor(() => expect(result.current.list.runs).toHaveLength(2));
  act(() => result.current.setSelectedId("r2"));
  await waitFor(() => expect(result.current.selected?.run_id).toBe("r2"));
  await act(async () => { resolveLatest({ runs: [allRuns[0]], total: 2 }); });
  await waitFor(() => expect(result.current.latest?.run_id).toBe("r1"));
  act(() => result.current.reload());
  await waitFor(() => expect(result.current.selected?.run_id).toBe("r2"));
  expect(result.current.selectedId).toBe("r2");
});

it("autoSelectLatest 为 false 时不自动选中，空列表显示列表空态文案", async () => {
  render(<Harness autoSelectLatest={false} />);
  await waitFor(() => expect(requests).toContain(`${BASE}/runs?limit=50&offset=0`));
  expect(screen.getByRole("button", { name: "r1" })).toBeInTheDocument();
  expect(screen.getByText("请选择一条运行记录。")).toBeInTheDocument();
  expect(screen.queryByText("正在读取运行详情…")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "r2" }));
  expect(await screen.findByText("结果 r2")).toBeInTheDocument();
  cleanup();
  allRuns = [];
  pageTotal = 0;
  render(<Harness autoSelectLatest={false} />);
  await waitFor(() => expect(screen.getByText("暂无运行记录。")).toBeInTheDocument());
});

it("选中后读取摘要，点其他卡片切换摘要", async () => {
  render(<Harness />);
  await screen.findByText("结果 r1");
  fireEvent.click(screen.getByRole("button", { name: "r2" }));
  expect(await screen.findByText("结果 r2")).toBeInTheDocument();
  expect(screen.queryByText("结果 r1")).toBeNull();
});

it("列表分页按 pageSize 翻页，刷新按钮重新读取", async () => {
  allRuns = Array.from({ length: 120 }, (_, index) => ({ run_id: `run${index}`, timestamp: "t", completion_status: "completed" }));
  pageTotal = 120;
  render(<Harness />);
  await screen.findByRole("button", { name: "run0" });
  expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "下一页" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByRole("button", { name: "run50" });
  expect(screen.queryByRole("button", { name: "run0" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByRole("button", { name: "run100" });
  fireEvent.click(screen.getByRole("button", { name: "上一页" }));
  await screen.findByRole("button", { name: "run50" });
  const before = requests.length;
  fireEvent.click(screen.getByRole("button", { name: "刷新记录" }));
  await waitFor(() => expect(requests.length).toBeGreaterThan(before));
});

it("当前页越界时回退到最后一页并重新读取", async () => {
  allRuns = Array.from({ length: 60 }, (_, index) => ({ run_id: `run${index}`, timestamp: "t", completion_status: "completed" }));
  pageTotal = 60;
  render(<Harness />);
  await screen.findByRole("button", { name: "run0" });
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByRole("button", { name: "run50" });
  // 删除后只剩 2 条，offset=50 越界，应回退到 offset=0。
  allRuns = [
    { run_id: "r1", timestamp: "t", completion_status: "completed" },
    { run_id: "r2", timestamp: "t", completion_status: "completed" },
  ];
  pageTotal = 2;
  fireEvent.click(screen.getByRole("button", { name: "刷新记录" }));
  expect(await screen.findByRole("button", { name: "r2" })).toBeInTheDocument();
  expect(requests.filter(url => url === `${BASE}/runs?limit=50&offset=0`).length).toBeGreaterThanOrEqual(2);
  expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
});

it("填回与重新分析按钮走回调并遵循禁用条件", async () => {
  const onFillBack = vi.fn();
  const onReanalyze = vi.fn();
  render(<Harness onFillBack={onFillBack} onReanalyze={onReanalyze} />);
  await screen.findByText("结果 r1");
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(onFillBack).toHaveBeenCalledWith(expect.objectContaining({ run_id: "r1" }));
  fireEvent.click(screen.getByRole("button", { name: "重新分析" }));
  expect(onReanalyze).toHaveBeenCalledWith(expect.objectContaining({ run_id: "r1" }));
  cleanup();
  // 选中运行中的运行：重新分析禁用；填回跟随外部禁用条件。
  allRuns = [{ run_id: "r1", timestamp: "t", completion_status: "running" }];
  pageTotal = 1;
  render(<Harness fillDisabled onReanalyze={onReanalyze} />);
  await screen.findByText("结果 r1");
  expect(screen.getByRole("button", { name: "填回参数" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "重新分析" })).toBeDisabled();
});

it("reload 引用在重渲染间保持稳定，消费方把它列入 effect 依赖不会循环刷新", async () => {
  const seen = new Set<() => void>();
  let effectRuns = 0;
  function Probe() {
    const [, setError] = useState("");
    const [finished, setFinished] = useState(false);
    const history = useRunHistory<Run, Summary>({ experimentId: ID, reloadKeys: [], setError });
    seen.add(history.reload);
    // 与 WorkflowPanel 相同的用法：onRefresh 在依赖数组内，任务结束时调用一次。
    useEffect(() => { effectRuns += 1; if (finished) history.reload(); }, [finished, history.reload]);
    return <button onClick={() => setFinished(true)}>任务结束</button>;
  }
  const listUrl = `${BASE}/runs?limit=50&offset=0`;
  render(<Probe />);
  await waitFor(() => expect(requests).toContain(listUrl));
  fireEvent.click(screen.getByRole("button", { name: "任务结束" }));
  await waitFor(() => expect(requests.filter(url => url === listUrl)).toHaveLength(2));
  // 等摘要等后续渲染全部落定后，列表仍只多读一次。
  await waitFor(() => expect(requests).toContain(`${BASE}/runs/r1/summary`));
  expect(requests.filter(url => url === listUrl)).toHaveLength(2);
  expect(seen.size).toBe(1);
  expect(effectRuns).toBe(2);
});

it("listQuery 追加到列表请求串", async () => {
  render(<Harness listQuery="&starred_only=true" />);
  await waitFor(() => expect(requests).toContain(`${BASE}/runs?limit=50&offset=0&starred_only=true`));
});

it("refreshDisabled 禁用刷新按钮；refreshKey 只在 reload 后递增", async () => {
  function Probe({ refreshDisabled, onKey }: { refreshDisabled?: boolean; onKey?: (key: number) => void }) {
    const [, setError] = useState("");
    const history = useRunHistory<Run, Summary>({ experimentId: ID, reloadKeys: [], setError });
    onKey?.(history.refreshKey);
    return <RunHistoryView history={history} fields={fields} renderResult={summary => <p>结果 {summary.run_id}</p>}
      fillBack={{ disabled: false, onClick: () => {} }} refreshDisabled={refreshDisabled} />;
  }
  const keys: number[] = [];
  const { rerender } = render(<Probe onKey={key => keys.push(key)} />);
  await screen.findByText("结果 r1");
  // 点击卡片切换选中不递增 refreshKey。
  const afterSelect = keys[keys.length - 1];
  fireEvent.click(screen.getByRole("button", { name: "r2" }));
  await screen.findByText("结果 r2");
  expect(keys[keys.length - 1]).toBe(afterSelect);
  // 禁用状态下刷新按钮不可点。
  rerender(<Probe refreshDisabled onKey={key => keys.push(key)} />);
  expect(screen.getByRole("button", { name: "刷新记录" })).toBeDisabled();
  rerender(<Probe onKey={key => keys.push(key)} />);
  const before = keys[keys.length - 1];
  fireEvent.click(screen.getByRole("button", { name: "刷新记录" }));
  await waitFor(() => expect(keys[keys.length - 1]).toBe(before + 1));
});

it("卡片插槽：左侧控件、状态行、下方说明与选中回调", async () => {
  const onSelectRun = vi.fn();
  render(<Harness
    onSelectRun={onSelectRun}
    renderRunControls={run => <input type="checkbox" aria-label={`对比 ${run.run_id}`} />}
    renderRunStatus={run => <>采集：{runStatusLabel(run.completion_status)}</>}
    renderRunNote={run => <p>不可对比：{run.run_id}</p>}
    runRowClassName="custom-row with-star" />);
  expect(await screen.findByRole("checkbox", { name: "对比 r1" })).toBeInTheDocument();
  expect(screen.getByText("采集：完成")).toBeInTheDocument();
  expect(screen.getByText("不可对比：r1")).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "对比 r1" }).parentElement?.className).toBe("custom-row with-star");
  fireEvent.click(screen.getByRole("button", { name: "r2" }));
  await screen.findByText("结果 r2", { selector: "p" });
  expect(onSelectRun).toHaveBeenCalledWith(expect.objectContaining({ run_id: "r2" }));
});

it("列表与详情插槽：listTools、emptyListText、detailHeader、detailTools、刷新禁用", async () => {
  render(<Harness listTools={<label>仅显示星标</label>} emptyListText="暂无星标运行记录。"
    detailHeader={<div role="group" aria-label="查看方式">切换</div>}
    detailTools={summary => <button>重命名 {summary.run_id}</button>} />);
  expect(await screen.findByText("仅显示星标")).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "查看方式" })).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: "重命名 r1" })).toBeInTheDocument();
  cleanup();
  allRuns = [];
  pageTotal = 0;
  render(<Harness emptyListText="暂无星标运行记录。" />);
  await waitFor(() => expect(screen.getByText("暂无星标运行记录。")).toBeInTheDocument());
});

it("detailHidden 隐藏默认详情但保持挂载，detailExtra 始终渲染", async () => {
  render(<Harness detailHidden detailExtra={<div className="comparison">对比面板</div>} />);
  expect(await screen.findByText("对比面板")).toBeInTheDocument();
  // 隐藏的详情仍在 DOM 中（hidden 属性）：文本查询可见，角色查询不可见。
  await waitFor(() => expect(screen.getByText("结果 r1")).toBeInTheDocument());
  const hiddenDetail = document.querySelector(".zaw-run-details > div[hidden]");
  expect(hiddenDetail).not.toBeNull();
  expect(hiddenDetail?.textContent).toContain("结果 r1");
  expect(screen.queryByRole("button", { name: "填回参数" })).toBeNull();
});

it("detailHidden 为 false 时详情容器可见且无 hidden 属性", async () => {
  render(<Harness detailHidden={false} />);
  await screen.findByRole("button", { name: "填回参数" });
  const wrapper = document.querySelector(".zaw-run-details > div");
  expect(wrapper?.hasAttribute("hidden")).toBe(false);
});

it("setList 本地更新列表不重新请求", async () => {
  function Probe() {
    const [, setError] = useState("");
    const history = useRunHistory<Run, Summary>({ experimentId: ID, reloadKeys: [], setError });
    return <>
      <RunHistoryView history={history} fields={fields} renderResult={summary => <p>结果 {summary.run_id}</p>}
        fillBack={{ disabled: false, onClick: () => {} }} />
      <button onClick={() => history.setList(current => ({ ...current, runs: current.runs.slice(0, 1) }))}>本地更新</button>
    </>;
  }
  render(<Probe />);
  await screen.findByRole("button", { name: "r2" });
  const before = requests.length;
  fireEvent.click(screen.getByRole("button", { name: "本地更新" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "r2" })).toBeNull());
  expect(screen.getByRole("button", { name: "r1" })).toBeInTheDocument();
  expect(requests.length).toBe(before);
});
