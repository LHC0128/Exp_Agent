import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MarkAreaComponent, MarkLineComponent } from "echarts/components";
import { ScopePlot } from "./ScopePlot";

const chart = vi.hoisted(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }));
const use = vi.hoisted(() => vi.fn());
vi.mock("echarts/core", () => ({ init: () => chart, use }));
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  chart.setOption.mockClear();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("示波器量程标记", () => {
  const traces = [{ id: "capture", color: "#147d74", x: [0, 1, 2], y: [-2, 0, 3],
    rangeMin: -1, rangeMax: 1, overrange: true }];
  it("注册标记组件并使用有限坐标高亮超量程区域", () => {
    render(<ScopePlot traces={traces} mode="time" reset={0} />);
    expect(use.mock.calls[0][0]).toEqual(expect.arrayContaining([MarkLineComponent, MarkAreaComponent]));
    const option = chart.setOption.mock.lastCall?.[0];
    expect(option.series[0].markLine.data).toEqual([{ name: "Range min", yAxis: -1 }, { name: "Range max", yAxis: 1 }]);
    expect(option.series[0].markArea.data).toEqual([
      [{ name: "OVERRANGE", yAxis: -2 }, { yAxis: -1 }],
      [{ name: "OVERRANGE", yAxis: 1 }, { yAxis: 3 }],
    ]);
    expect(option.yAxis.min({ min: 0 })).toBe(-1);
    expect(option.yAxis.max({ max: 0 })).toBe(1);
  });
  it("切换频域清除标记，旧数据没有量程线", () => {
    const { rerender } = render(<ScopePlot traces={traces} mode="time" reset={0} />);
    rerender(<ScopePlot traces={traces} mode="frequency" reset={0} />);
    let option = chart.setOption.mock.lastCall?.[0];
    expect(option.series[0].markLine).toBeUndefined();
    expect(option.series[0].markArea).toBeUndefined();
    expect(chart.setOption.mock.lastCall?.[1]).toEqual({ notMerge: true });
    rerender(<ScopePlot traces={[{ id: "old", color: "#147d74", x: [0, 1], y: [0, 1] }]} mode="time" reset={0} />);
    option = chart.setOption.mock.lastCall?.[0];
    expect(option.series[0].markLine).toBeUndefined();
    expect(option.yAxis.min).toBeUndefined();
  });
});
