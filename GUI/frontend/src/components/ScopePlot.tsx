import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import { GridComponent, TooltipComponent, DataZoomComponent, LegendComponent, MarkLineComponent, MarkAreaComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([LineChart, GridComponent, TooltipComponent, DataZoomComponent, LegendComponent, MarkLineComponent, MarkAreaComponent, CanvasRenderer]);
export type ScopeTrace = { id: string; color: string; x: number[]; y: number[]; rangeMin?: number; rangeMax?: number; overrange?: boolean };

export type AxisScale = "linear" | "log";

export function ScopePlot({
  traces,
  mode,
  reset,
  xScale = "linear",
  yScale = "linear",
}: {
  traces: ScopeTrace[];
  mode: string;
  reset: number;
  xScale?: AxisScale;
  yScale?: AxisScale;
}) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.EChartsType | undefined>(undefined);
  useEffect(() => {
    if (!host.current) return;
    const instance = echarts.init(host.current);
    chart.current = instance;
    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(host.current);
    return () => { observer.disconnect(); instance.dispose(); chart.current = undefined; };
  }, []);
  useEffect(() => {
    const limits = mode === "time" ? traces.flatMap(t => [t.rangeMin, t.rangeMax]).filter((n): n is number => n != null && Number.isFinite(n)) : [];
    chart.current?.setOption({
      animation: false,
      grid: { left: 66, right: 22, top: 78, bottom: 92 },
      legend: { type: "scroll", top: 6, left: 12, right: 12, selectedMode: false,
        textStyle: { fontSize: 11, width: 180, overflow: "truncate" }, data: traces.map(t => t.id) },
      tooltip: { trigger: "axis", confine: true },
      xAxis: { type: xScale === "log" ? "log" : "value", logBase: 10, name: mode === "frequency" ? "Frequency (Hz)" : "Time (s)",
        nameLocation: "middle", nameGap: 30, axisLabel: { hideOverlap: true }, scale: true,
        min: "dataMin", max: "dataMax" },
      yAxis: { type: yScale === "log" ? "log" : "value", logBase: 10, name: mode === "frequency" ? "PSD (V²/Hz)" : "Voltage (V)",
        min: limits.length ? (extent: { min: number }) => Math.min(extent.min, ...limits) : undefined,
        max: limits.length ? (extent: { max: number }) => Math.max(extent.max, ...limits) : undefined,
        nameLocation: "end", scale: true, splitLine: { lineStyle: { color: "#e5ebed" } },
        axisLabel: { formatter: (n: number) => Math.abs(n) >= 10000 || (n !== 0 && Math.abs(n) < 0.001) ? n.toExponential(1) : String(Number(n.toPrecision(4))) } },
      dataZoom: [{ type: "inside", filterMode: "none" }, { type: "slider", bottom: 15, height: 22 }],
      series: traces.map(t => ({ name: t.id, type: "line", showSymbol: false, lineStyle: { width: 1.2, color: t.color },
        markLine: mode === "time" && t.rangeMin != null && t.rangeMax != null ? { silent: true, symbol: "none", label: { position: "insideEndTop", formatter: "{b}: {c} V" }, lineStyle: { color: t.color, type: "dashed" }, data: [{ name: "Range min", yAxis: t.rangeMin }, { name: "Range max", yAxis: t.rangeMax }] } : undefined,
        markArea: mode === "time" && t.overrange && t.rangeMin != null && t.rangeMax != null ? { silent: true, itemStyle: { color: "rgba(200,40,40,0.12)" }, data: [
          ...(Math.min(...t.y) < t.rangeMin ? [[{ name: "OVERRANGE", yAxis: Math.min(...t.y) }, { yAxis: t.rangeMin }]] : []),
          ...(Math.max(...t.y) > t.rangeMax ? [[{ name: "OVERRANGE", yAxis: t.rangeMax }, { yAxis: Math.max(...t.y) }]] : []),
        ] } : undefined,
        itemStyle: { color: t.color }, data: t.x.map((x, i) => [x, t.y[i]]).filter(([x, y]) =>
          (xScale !== "log" || x > 0) && (yScale !== "log" || y > 0)) })),
    }, { notMerge: true });
  }, [traces, mode, reset, xScale, yScale]);
  return <div ref={host} className="scope-chart" role="img" aria-label={mode === "frequency" ? "功率谱密度图" : "时域波形图"} />;
}
