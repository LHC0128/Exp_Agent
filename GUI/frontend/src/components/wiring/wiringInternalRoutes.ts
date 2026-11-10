import type { Point } from "./wiringRoutes";

export type InternalWire = { id: string; source: Point; target: Point };
export type InternalCardSize = { width: number; height: number };
export type InternalRoute = { id: string; path: string; points: Point[] };

const MARGIN = 3;
const pathFor = (points: Point[]) => points.map((point, index) => `${index ? "L" : "M"} ${point.x} ${point.y}`).join(" ");
const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

/**
 * 器件内部正交路由：只在内部输入按钮右沿与内部输出按钮左沿之间的竖向通道布线，
 * 所有坐标收在卡片盒内并按线数分配车道；不退回穿盒直线，也不经过端口按钮文字。
 */
export function routeInternalWires(wires: InternalWire[], size: InternalCardSize): InternalRoute[] {
  const ordered = [...wires].sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  if (!ordered.length) return [];
  const maxX = Math.max(MARGIN, size.width - MARGIN);
  const maxY = Math.max(MARGIN, size.height - MARGIN);
  return ordered.map((wire, index) => {
    const source = { x: clamp(wire.source.x, MARGIN, maxX), y: clamp(wire.source.y, MARGIN, maxY) };
    const target = { x: clamp(wire.target.x, MARGIN, maxX), y: clamp(wire.target.y, MARGIN, maxY) };
    const low = Math.min(source.x, target.x);
    const high = Math.max(source.x, target.x);
    const inset = Math.min(MARGIN, (high - low) / 4);
    const lane = low + inset + Math.max(0, high - low - inset * 2) * ((index + .5) / ordered.length);
    const x = clamp(lane, low + inset, Math.max(low + inset, high - inset));
    const points: Point[] = [];
    for (const point of [source, { x, y: source.y }, { x, y: target.y }, target]) {
      if (points.length && Math.abs(points[points.length - 1].x - point.x) < .001 && Math.abs(points[points.length - 1].y - point.y) < .001) continue;
      points.push(point);
    }
    return { id: wire.id, path: pathFor(points), points };
  });
}
