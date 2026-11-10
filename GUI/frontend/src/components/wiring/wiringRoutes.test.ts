import { expect, it } from "vitest";
import { routeWire, routeWires, type Point, type WireAnchor, type WireBox, type WireRequest, type WireRoute } from "./wiringRoutes";

const box: WireBox = { id: "a", left: 60, top: 60, right: 308, bottom: 240 };
function outsideBodies(source: WireAnchor, target: WireAnchor, boxes: WireBox[]) {
  const route = routeWire(source, target, boxes);
  expect(route.blocked).toBe(false); expect(route.path).toMatch(/^M .* L /);
  for (let index = 1; index < route.points.length; index++) {
    const a = route.points[index - 1]; const b = route.points[index];
    expect(a.x === b.x || a.y === b.y).toBe(true);
    for (let step = 0; step <= 100; step++) {
      const x = a.x + (b.x - a.x) * step / 100; const y = a.y + (b.y - a.y) * step / 100;
      for (const obstacle of boxes) {
        const endpointLead = index === 1 && obstacle.id === source.nodeId || index === route.points.length - 1 && obstacle.id === target.nodeId;
        if (!endpointLead) expect(x > obstacle.left && x < obstacle.right && y > obstacle.top && y < obstacle.bottom, `线段 ${index} 穿过 ${obstacle.id}`).toBe(false);
      }
    }
  }
  for (const obstacle of boxes) expect(route.label.x > obstacle.left && route.label.x < obstacle.right && route.label.y > obstacle.top && route.label.y < obstacle.bottom).toBe(false);
  expect(route.points[0]).toEqual(source); expect(route.points.at(-1)).toEqual(target);
  return route;
}

it.each(["left", "right"] as const)("同机 %s 起点回到另一侧，折线与标签都从盒外绕行", side => {
  const source = { x: side === "right" ? 308 : 60, y: 120, side, nodeId: "a" };
  const target = { x: side === "right" ? 60 : 308, y: 180, side: side === "right" ? "left" as const : "right" as const, nodeId: "a" };
  outsideBodies(source, target, [box]);
});
it("同侧两端口先外引，不直接贴着盒内走", () => {
  const route = outsideBodies({ x: 308, y: 110, side: "right", nodeId: "a" }, { x: 308, y: 200, side: "right", nodeId: "a" }, [box]);
  expect(Math.max(...route.points.map(point => point.x))).toBeGreaterThan(308);
});
it("跨机连接避开中间第三台仪器，位置变化后重新绕行", () => {
  const receiver = { ...box, id: "b", left: 700, right: 948 };
  const middle = { ...box, id: "c", left: 400, right: 600, top: 20, bottom: 320 };
  const source = { x: 308, y: 120, side: "right" as const, nodeId: "a" };
  const target = { x: 700, y: 120, side: "left" as const, nodeId: "b" };
  const first = outsideBodies(source, target, [box, middle, receiver]);
  const second = outsideBodies(source, target, [box, { ...middle, top: 500, bottom: 700 }, receiver]);
  expect(first.path).not.toBe(second.path); expect(second.points).toHaveLength(2);
});
it("两台仪器左右反向排列时仍绕过源与目标", () => {
  const receiver = { ...box, id: "b", left: 500, right: 748 };
  outsideBodies({ x: 60, y: 120, side: "left", nodeId: "a" }, { x: 748, y: 150, side: "right", nodeId: "b" }, [box, receiver]);
});
it("器件重叠挡住外引时明确 blocked，不返回一条穿盒的假路线", () => {
  const route = routeWire({ x: 308, y: 120, side: "right", nodeId: "a" }, { x: 60, y: 180, side: "left", nodeId: "a" }, [box, { ...box, id: "overlap", left: 300, right: 500 }]);
  expect(route.blocked).toBe(true); expect(route.path).toBe("");
});
it("同侧垂直回连的长标签按完整文字矩形避障，不仅判断中心点", () => {
  const tall = { ...box, bottom: 480 };
  const route = routeWire({ x: 300, y: 120, side: "right", nodeId: "a" }, { x: 300, y: 400, side: "right", nodeId: "a" }, [tall], { width: 260, height: 14 });
  const halfWidth = 130;
  const labelLeft = route.label.x - halfWidth; const labelRight = route.label.x + halfWidth;
  expect(labelLeft < tall.right && labelRight > tall.left && route.label.y - 14 < tall.bottom && route.label.y + 4 > tall.top).toBe(false);
});

it("无 DOM 障碍物时也能给出确定的正交路径且不修改输入", () => {
  const source = { x: 60, y: 100, side: "right" as const }; const target = { x: 500, y: 200, side: "left" as const };
  const before = structuredClone({ source, target });
  const first = routeWire(source, target, []); expect(first).toEqual(routeWire(source, target, []));
  expect(first.blocked).toBe(false); expect({ source, target }).toEqual(before);
});

function sharedLength(a: Point[], b: Point[]) {
  let length = 0;
  for (let index = 1; index < a.length; index++) for (let other = 1; other < b.length; other++) {
    const p = a[index - 1]; const q = a[index]; const r = b[other - 1]; const s = b[other];
    if (p.y === q.y && r.y === s.y && p.y === r.y) length += Math.max(0, Math.min(Math.max(p.x, q.x), Math.max(r.x, s.x)) - Math.max(Math.min(p.x, q.x), Math.min(r.x, s.x)));
    if (p.x === q.x && r.x === s.x && p.x === r.x) length += Math.max(0, Math.min(Math.max(p.y, q.y), Math.max(r.y, s.y)) - Math.max(Math.min(p.y, q.y), Math.min(r.y, s.y)));
  }
  return length;
}
function avoidsBoxes(route: WireRoute, boxes: WireBox[]) {
  expect(route.blocked).toBe(false);
  for (let index = 1; index < route.points.length; index++) {
    const a = route.points[index - 1]; const b = route.points[index];
    expect(a.x === b.x || a.y === b.y).toBe(true);
    for (const obstacle of boxes) {
      const crossing = a.y === b.y
        ? a.y > obstacle.top && a.y < obstacle.bottom && Math.max(a.x, b.x) > obstacle.left && Math.min(a.x, b.x) < obstacle.right
        : a.x > obstacle.left && a.x < obstacle.right && Math.max(a.y, b.y) > obstacle.top && Math.min(a.y, b.y) < obstacle.bottom;
      expect(crossing, `批量线段穿过 ${obstacle.id}`).toBe(false);
    }
  }
}
const branchWires = (): WireRequest[] => [
  { id: "a", source: { x: 100, y: 200, side: "right" }, target: { x: 600, y: 100, side: "left" }, netKey: "electrical:clock/out", labelSize: { width: 180, height: 16 } },
  { id: "b", source: { x: 100, y: 200, side: "right" }, target: { x: 600, y: 300, side: "left" }, netKey: "electrical:clock/out", labelSize: { width: 180, height: 16 } },
];

it("同一输出源多目标共用公共段，并仅在三向真实分支处生成连接点", () => {
  const wires = branchWires(); const layout = routeWires(wires, []);
  expect(layout.routes.map(item => item.id)).toEqual(["a", "b"]);
  expect(layout.routes.every(item => !item.route.blocked)).toBe(true);
  const common = layout.segments.filter(segment => segment.edgeIds.length > 1);
  expect(common.length).toBeGreaterThan(0);
  expect(common.every(segment => segment.edgeIds.join(",") === "a,b")).toBe(true);
  expect(layout.junctions.length).toBeGreaterThan(0);
  for (const junction of layout.junctions) {
    const directions = new Set<string>();
    for (const segment of layout.segments) {
      const [a, b] = segment.points;
      if (a.x === junction.x && a.y === junction.y) directions.add(a.x === b.x ? "down" : "right");
      if (b.x === junction.x && b.y === junction.y) directions.add(a.x === b.x ? "up" : "left");
    }
    expect(directions.size).toBeGreaterThanOrEqual(3); expect(junction.edgeIds).toEqual(["a", "b"]);
  }
  for (const item of layout.routes) {
    expect(item.route.points[0]).toEqual(wires.find(wire => wire.id === item.id)!.source);
    expect(item.route.points.at(-1)).toEqual(wires.find(wire => wire.id === item.id)!.target);
    const drawn = layout.segments.filter(segment => segment.edgeIds.includes(item.id)).reduce((total, segment) => total + Math.abs(segment.points[1].x - segment.points[0].x) + Math.abs(segment.points[1].y - segment.points[0].y), 0);
    const full = item.route.points.slice(1).reduce((total, point, index) => total + Math.abs(point.x - item.route.points[index].x) + Math.abs(point.y - item.route.points[index].y), 0);
    expect(drawn).toBe(full);
  }
});

it.each(["different", "missing"])("不同时钟输出即使值相同，%s netKey 仍不得合并", mode => {
  const wires = branchWires().map((wire, index) => ({ ...wire, netKey: mode === "missing" ? undefined : `electrical:clock-${index}/10MHz` }));
  const layout = routeWires(wires, []);
  expect(layout.segments.every(segment => segment.edgeIds.length === 1)).toBe(true);
  expect(layout.junctions).toEqual([]);
});

it("不同信号原本重合的长段使用相邻通道，允许小代价交叉而不画连接点", () => {
  const wires: WireRequest[] = [
    { id: "a", source: { x: 100, y: 200, side: "right" }, target: { x: 800, y: 200, side: "left" }, netKey: "electrical:a/out" },
    { id: "b", source: { x: 100, y: 200, side: "right" }, target: { x: 800, y: 200, side: "left" }, netKey: "optical:b/out" },
  ];
  const layout = routeWires(wires, []); const [a, b] = layout.routes.map(item => item.route);
  expect(a.blocked || b.blocked).toBe(false);
  expect(sharedLength(a.points, b.points)).toBeLessThanOrEqual(32);
  const length = b.points.slice(1).reduce((total, point, index) => total + Math.abs(point.x - b.points[index].x) + Math.abs(point.y - b.points[index].y), 0);
  expect(length).toBeLessThan(800); expect(layout.junctions).toEqual([]);
  const cross = routeWires([
    { id: "x", source: { x: 60, y: 200, side: "right" }, target: { x: 700, y: 200, side: "left" }, netKey: "electrical:x" },
    { id: "y", source: { x: 300, y: 50, side: "right" }, target: { x: 330, y: 400, side: "left" }, netKey: "electrical:y" },
  ], []);
  expect(cross.routes.every(item => !item.route.blocked)).toBe(true); expect(cross.junctions).toEqual([]);
});

it("六条同走廊的独立网都有有限平行通道，而非第三条之后再次长段重合", () => {
  const wires: WireRequest[] = Array.from({ length: 6 }, (_, index) => ({
    id: `lane-${index}`, source: { x: 100, y: 0, side: "right" }, target: { x: 800, y: 0, side: "left" }, netKey: `electrical:independent-${index}`,
  }));
  const layout = routeWires(wires, []);
  expect(layout.routes.every(item => !item.route.blocked)).toBe(true);
  expect(layout.segments.every(segment => segment.edgeIds.length === 1)).toBe(true); expect(layout.junctions).toEqual([]);
  const longLanes: number[] = [];
  for (let index = 0; index < layout.routes.length; index++) {
    const points = layout.routes[index].route.points;
    expect(Math.min(...points.map(point => point.y))).toBeGreaterThanOrEqual(-40);
    for (let part = 1; part < points.length; part++) if (points[part].y === points[part - 1].y && Math.abs(points[part].x - points[part - 1].x) > 600) longLanes.push(points[part].y);
    // 同位置端点允许共用很短的外引及转弯，不能把 700px 走廊重新叠画。
    for (const previous of layout.routes.slice(0, index)) expect(sharedLength(points, previous.route.points)).toBeLessThan(100);
  }
  expect(longLanes).toHaveLength(6); expect(new Set(longLanes).size).toBe(6);
});

it("批量路线避开第三盒，并保留外引被挡线路的 blocked 记录而不伪造 segments", () => {
  const receiver = { ...box, id: "b", left: 700, right: 948 };
  const middle = { ...box, id: "c", left: 400, right: 600, top: 20, bottom: 320 };
  const wires: WireRequest[] = [{ id: "valid", source: { x: 308, y: 120, side: "right", nodeId: "a" }, target: { x: 700, y: 120, side: "left", nodeId: "b" }, netKey: "electrical:a/out" }];
  const layout = routeWires(wires, [box, middle, receiver]);
  avoidsBoxes(layout.routes[0].route, [box, middle, receiver]);
  const blocked = routeWires(wires, [box, receiver, { ...box, id: "overlap", left: 300, right: 500 }]);
  expect(blocked.routes).toHaveLength(1); expect(blocked.routes[0].route.blocked).toBe(true);
  expect(blocked.routes[0].route.path).toBe(""); expect(blocked.segments).toEqual([]); expect(blocked.junctions).toEqual([]);
});

it("批量完整长标签避开所有黑盒和彼此，不依靠把文字盖在黑盒上", () => {
  const tall = { ...box, bottom: 480 };
  const wires: WireRequest[] = [0, 1, 2].map(index => ({
    id: `label-${index}`, source: { x: 308, y: 100 + index * 30, side: "right", nodeId: "a" },
    target: { x: 308, y: 400 + index * 20, side: "right", nodeId: "a" },
    labelSize: { width: 260, height: 20 }, netKey: `electrical:output-${index}`,
  }));
  const extra: WireBox = { id: "outside", left: 380, right: 650, top: 100, bottom: 440 };
  const layout = routeWires(wires, [tall, extra]);
  const labels = layout.routes.map(({ route }) => ({ left: route.label.x - 130, right: route.label.x + 130, top: route.label.y - 20, bottom: route.label.y + 4 }));
  for (let index = 0; index < labels.length; index++) {
    const a = labels[index];
    for (const b of [tall, extra, ...labels.slice(0, index)]) expect(a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top).toBe(false);
  }
});

it("反转线路及盒子输入顺序仍得到相同布局，输入对象不变，零坐标不丢失", () => {
  const wires = branchWires(); const boxes = [{ ...box, left: 300, right: 430, top: 150, bottom: 250 }, { ...box, id: "far", left: 1000, right: 1200 }];
  wires.push({ id: "zero", source: { x: 0, y: 0, side: "left" }, target: { x: 0, y: 120, side: "left" } });
  const before = structuredClone({ wires, boxes });
  const layout = routeWires(wires, boxes);
  expect(layout).toEqual(routeWires([...wires].reverse(), [...boxes].reverse()));
  expect({ wires, boxes }).toEqual(before);
  expect(layout.routes.find(item => item.id === "zero")!.route.points[0]).toEqual(wires[2].source);
});

it("内部信号使用独立网，即便物理线与内部线具有同一输出位置也不共段", () => {
  const wires = branchWires(); wires[1].netKey = "internal:clock/out";
  const layout = routeWires(wires, []);
  expect(layout.segments.every(segment => segment.edgeIds.length === 1)).toBe(true);
  expect(new Set(layout.segments.map(segment => segment.netKey))).toEqual(new Set(["electrical:clock/out", "internal:clock/out"]));
  expect(layout.junctions).toEqual([]);
});

it.each([false, true])("20台24线布局在有限通道网内完成（盒坐标不对齐=%s）", staggered => {
  const boxes: WireBox[] = Array.from({ length: 20 }, (_, index) => ({ id: `node-${index}`, left: 60 + index % 5 * 400 + (staggered ? Math.floor(index / 5) * 7 : 0), right: 308 + index % 5 * 400 + (staggered ? Math.floor(index / 5) * 7 + index % 4 * 3 : 0), top: 60 + Math.floor(index / 5) * 340 + (staggered ? index % 5 * 9 : 0), bottom: 240 + Math.floor(index / 5) * 340 + (staggered ? index % 5 * 9 + index % 3 * 5 : 0) }));
  const wires: WireRequest[] = Array.from({ length: 24 }, (_, index) => {
    const source = boxes[index % 20]; const target = boxes[(index * 7 + 3) % 20];
    return { id: `edge-${index.toString().padStart(2, "0")}`, source: { x: source.right, y: source.top + 60, side: "right", nodeId: source.id }, target: { x: target.left, y: target.top + 100, side: "left", nodeId: target.id }, netKey: `electrical:${source.id}/out`, labelSize: { width: 160, height: 14 } };
  });
  const start = performance.now(); const layout = routeWires(wires, boxes); const elapsed = performance.now() - start;
  expect(layout.routes).toHaveLength(24); expect(layout.routes.every(item => !item.route.blocked)).toBe(true);
  expect(elapsed).toBeLessThan(3000);
});
