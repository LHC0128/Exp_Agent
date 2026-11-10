export type Point = { x: number; y: number };
export type WireAnchor = Point & { side: "left" | "right"; nodeId?: string };
export type WireBox = { id: string; left: number; top: number; right: number; bottom: number };
export type WireLabelSize = { width: number; height: number };
export type WireRoute = { path: string; points: Point[]; label: Point; blocked: boolean };
export type WireRequest = { id: string; source: WireAnchor; target: WireAnchor; labelSize?: WireLabelSize; netKey?: string };
export type WireSegment = { id: string; path: string; points: Point[]; edgeIds: string[]; netKey: string };
export type WireJunction = Point & { id: string; edgeIds: string[]; netKey: string };
export type WireLayout = { routes: Array<{ id: string; route: WireRoute }>; segments: WireSegment[]; junctions: WireJunction[] };
type OccupiedSegment = { a: Point; b: Point; net: string };
type RoutingGrid = { obstacles: WireBox[]; xs: number[]; ys: number[]; valid: Int8Array; horizontal: Int8Array; vertical: Int8Array };
type RoutingContext = { grid: RoutingGrid; occupied: OccupiedSegment[]; net: string };
const CLEARANCE = 16;
const LANE_GAP = 12;
const BEND_COST = 18;
const EPSILON = .01;
const DEFAULT_LABEL = { width: 0, height: 14 };
const same = (a: Point, b: Point) => Math.abs(a.x - b.x) < EPSILON && Math.abs(a.y - b.y) < EPSILON;
const inside = (point: Point, box: WireBox) => point.x > box.left + EPSILON && point.x < box.right - EPSILON && point.y > box.top + EPSILON && point.y < box.bottom - EPSILON;
const compare = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0;
const pathFor = (points: Point[]) => points.map((point, index) => `${index ? "L" : "M"} ${point.x} ${point.y}`).join(" ");
const inflate = (boxes: WireBox[]) => boxes.map(box => ({ ...box, left: box.left - CLEARANCE, right: box.right + CLEARANCE, top: box.top - CLEARANCE, bottom: box.bottom + CLEARANCE }));

function escape(anchor: WireAnchor, obstacles: WireBox[]): Point {
  const box = obstacles.find(item => item.id === anchor.nodeId);
  return { x: box ? anchor.side === "right" ? box.right : box.left : anchor.x + (anchor.side === "right" ? CLEARANCE : -CLEARANCE), y: anchor.y };
}
function crosses(a: Point, b: Point, box: WireBox) {
  return a.y === b.y
    ? a.y > box.top + EPSILON && a.y < box.bottom - EPSILON && Math.max(a.x, b.x) > box.left + EPSILON && Math.min(a.x, b.x) < box.right - EPSILON
    : a.x > box.left + EPSILON && a.x < box.right - EPSILON && Math.max(a.y, b.y) > box.top + EPSILON && Math.min(a.y, b.y) < box.bottom - EPSILON;
}
function compact(points: Point[]) {
  const result: Point[] = [];
  for (const point of points) {
    if (result.length && same(result[result.length - 1], point)) continue;
    while (result.length > 1) {
      const a = result[result.length - 2]; const b = result[result.length - 1];
      // 仅合并同向线段，保留端口向外引出再回转的方向。
      if ((a.x === b.x && b.x === point.x && (b.y - a.y) * (point.y - b.y) >= 0)
        || (a.y === b.y && b.y === point.y && (b.x - a.x) * (point.x - b.x) >= 0)) result.pop();
      else break;
    }
    result.push(point);
  }
  return result;
}
function result(points: Point[], boxes: WireBox[], size: WireLabelSize, blocked = false, reserved?: WireBox[]): WireRoute {
  const simplified = compact(points);
  const segments = simplified.slice(1).map((b, index) => ({ a: simplified[index], b, length: Math.abs(b.x - simplified[index].x) + Math.abs(b.y - simplified[index].y) })).sort((a, b) => b.length - a.length);
  // 按实际文字宽高（含描边余量）避障，不仅检查文字中心；负边界向画布内侧放置。
  const halfWidth = size.width / 2;
  const inBounds = (point: Point) => ({ x: Math.max(halfWidth + 4, point.x), y: Math.max(size.height + 4, point.y) });
  const obstacles = reserved ? [...boxes, ...reserved] : boxes;
  const clear = (point: Point) => !obstacles.some(box => point.x - halfWidth - 4 < box.right && point.x + halfWidth + 4 > box.left && point.y - size.height - 4 < box.bottom && point.y + 4 > box.top);
  function candidatesAt(ratio: number) {
    return segments.flatMap(({ a, b }) => {
      const middle = { x: a.x + (b.x - a.x) * ratio, y: a.y + (b.y - a.y) * ratio };
      return a.y === b.y
        ? [{ ...middle, y: middle.y - 8 }, { ...middle, y: middle.y + size.height + 8 }]
        : [{ x: middle.x + halfWidth + 8, y: middle.y + size.height / 2 }, { x: middle.x - halfWidth - 8, y: middle.y + size.height / 2 }];
    }).map(inBounds);
  }
  const candidates = candidatesAt(.5);
  if (reserved) candidates.push(...candidatesAt(.25), ...candidatesAt(.75));
  const label = candidates.find(clear) ?? inBounds({ x: Math.max(0, ...obstacles.map(box => box.right)) + halfWidth + 8, y: simplified[0].y });
  return { path: blocked ? "" : pathFor(simplified), points: simplified, label, blocked };
}
function makeGrid(obstacles: WireBox[], xs: number[], ys: number[]): RoutingGrid {
  const columns = [...new Set(xs)].sort((a, b) => a - b);
  const rows = [...new Set(ys)].sort((a, b) => a - b);
  const count = columns.length * rows.length;
  return { obstacles, xs: columns, ys: rows, valid: new Int8Array(count), horizontal: new Int8Array(count), vertical: new Int8Array(count) };
}

// 占用线只投影到固定坐标网的邻接边，避免在 A* 每一步扫描全部旧线，也不递归增加通道坐标。
function occupiedCosts({ grid, occupied, net }: RoutingContext) {
  const { xs, ys } = grid; const columns = xs.length; const count = columns * ys.length;
  const horizontal = new Float64Array(count); const vertical = new Float64Array(count);
  const sharedHorizontal = new Float64Array(count); const sharedVertical = new Float64Array(count);
  for (const segment of occupied) {
    const isHorizontal = segment.a.y === segment.b.y;
    const along = isHorizontal ? xs : ys; const across = isHorizontal ? ys : xs;
    const fixed = isHorizontal ? segment.a.y : segment.a.x;
    const low = Math.min(isHorizontal ? segment.a.x : segment.a.y, isHorizontal ? segment.b.x : segment.b.y);
    const high = Math.max(isHorizontal ? segment.a.x : segment.a.y, isHorizontal ? segment.b.x : segment.b.y);
    const costs = isHorizontal ? horizontal : vertical;
    const shared = isHorizontal ? sharedHorizontal : sharedVertical;
    const offset = (axis: number, lane: number) => isHorizontal ? lane * columns + axis : axis * columns + lane;
    for (let lane = 0; lane < across.length; lane++) {
      const gap = Math.abs(across[lane] - fixed);
      if (gap >= LANE_GAP) continue;
      for (let axis = 0; axis + 1 < along.length; axis++) {
        const overlap = Math.min(high, along[axis + 1]) - Math.max(low, along[axis]);
        if (overlap <= 0) continue;
        const index = offset(axis, lane);
        if (segment.net !== net) costs[index] += overlap * (gap < EPSILON ? 5 : 3 * (1 - gap / LANE_GAP));
        else if (gap < EPSILON) shared[index] = Math.max(shared[index], overlap * .35);
      }
    }
    if (segment.net === net) continue;
    // 正交交叉只计小代价，不为追求绝无交叉绕到远端；交叉点本身不生成连接点。
    const crossingCosts = isHorizontal ? vertical : horizontal;
    for (let lane = 0; lane < along.length; lane++) {
      if (along[lane] <= low + EPSILON || along[lane] >= high - EPSILON) continue;
      for (let axis = 0; axis + 1 < across.length; axis++) {
        if (fixed < across[axis] - EPSILON || fixed > across[axis + 1] + EPSILON) continue;
        const index = isHorizontal ? axis * columns + lane : lane * columns + axis;
        crossingCosts[index] += 2;
      }
    }
  }
  for (let index = 0; index < count; index++) {
    horizontal[index] -= sharedHorizontal[index]; vertical[index] -= sharedVertical[index];
  }
  return { horizontal, vertical };
}

/** 按实际黑盒矩形绕行，端口先向外引出；无法绕行时明确返回 blocked，不退回穿盒直线。 */
export function routeWire(source: WireAnchor, target: WireAnchor, boxes: WireBox[], labelSize: WireLabelSize = DEFAULT_LABEL): WireRoute {
  return findRoute(source, target, boxes, labelSize);
}
function findRoute(source: WireAnchor, target: WireAnchor, boxes: WireBox[], labelSize: WireLabelSize, context?: RoutingContext): WireRoute {
  const obstacles = context?.grid.obstacles ?? inflate(boxes);
  const start = escape(source, obstacles); const end = escape(target, obstacles);
  // 自身盒子只允许端口到外引点的短段穿过；其他盒子重叠时不伪造可见路线。
  if (obstacles.some(box => box.id !== source.nodeId && crosses(source, start, box) || box.id !== target.nodeId && crosses(target, end, box))
    || obstacles.some(box => inside(start, box) || inside(end, box))) return result([source, target], boxes, labelSize, true);
  const grid = context?.grid ?? makeGrid(obstacles, [start.x, end.x, ...obstacles.flatMap(box => [box.left, box.right])], [start.y, end.y, ...obstacles.flatMap(box => [box.top, box.bottom])]);
  const { xs, ys, valid } = grid;
  const costs = context ? occupiedCosts(context) : undefined;
  const columns = xs.length; const count = columns * ys.length;
  const pointAt = (index: number): Point => ({ x: xs[index % columns], y: ys[Math.floor(index / columns)] });
  const startIndex = ys.indexOf(start.y) * columns + xs.indexOf(start.x);
  const endIndex = ys.indexOf(end.y) * columns + xs.indexOf(end.x);
  const distances = new Float64Array(count * 3).fill(Infinity);
  const previous = new Int32Array(count * 3).fill(-1);
  const queue: Array<{ state: number; cost: number; priority: number }> = [];
  function push(state: number, cost: number) {
    const point = pointAt(Math.floor(state / 3));
    // 同网共段最低成本为原长度的 .65，启发式同步缩放，保持最优性。
    const item = { state, cost, priority: cost + (context ? .65 : 1) * (Math.abs(point.x - end.x) + Math.abs(point.y - end.y)) };
    let index = queue.length; queue.push(item);
    while (index) { const parent = (index - 1) >> 1; if (queue[parent].priority <= item.priority) break; queue[index] = queue[parent]; index = parent; }
    queue[index] = item;
  }
  function pop() {
    const first = queue[0]; const last = queue.pop()!;
    if (queue.length) {
      let index = 0;
      while (index * 2 + 1 < queue.length) {
        let child = index * 2 + 1;
        if (child + 1 < queue.length && queue[child + 1].priority < queue[child].priority) child++;
        if (queue[child].priority >= last.priority) break;
        queue[index] = queue[child]; index = child;
      }
      queue[index] = last;
    }
    return first;
  }
  function available(index: number) {
    if (!valid[index]) valid[index] = obstacles.some(box => inside(pointAt(index), box)) ? -1 : 1;
    return valid[index] === 1;
  }
  const initial = startIndex * 3 + 1; // 初始与末段均为水平外引。
  distances[initial] = 0; push(initial, 0);
  let final = -1;
  while (queue.length) {
    const current = pop();
    if (current.cost !== distances[current.state]) continue;
    const index = Math.floor(current.state / 3); const direction = current.state % 3;
    if (index === endIndex) { final = current.state; break; }
    const a = pointAt(index); const column = index % columns;
    const neighbors = [column > 0 ? index - 1 : -1, column + 1 < columns ? index + 1 : -1, index >= columns ? index - columns : -1, index + columns < count ? index + columns : -1];
    for (const neighbor of neighbors) {
      if (neighbor < 0 || !available(neighbor)) continue;
      const b = pointAt(neighbor);
      const nextDirection = a.y === b.y ? 1 : 2;
      const edgeIndex = Math.min(index, neighbor);
      const clearEdges = nextDirection === 1 ? grid.horizontal : grid.vertical;
      if (!clearEdges[edgeIndex]) clearEdges[edgeIndex] = obstacles.some(box => crosses(a, b, box)) ? -1 : 1;
      if (clearEdges[edgeIndex] < 0) continue;
      const next = neighbor * 3 + nextDirection;
      const occupation = costs ? (nextDirection === 1 ? costs.horizontal[edgeIndex] : costs.vertical[edgeIndex]) : 0;
      const cost = current.cost + Math.abs(a.x - b.x) + Math.abs(a.y - b.y) + occupation + (direction !== nextDirection ? BEND_COST : 0) + (neighbor === endIndex && nextDirection !== 1 ? BEND_COST : 0);
      if (cost < distances[next]) { distances[next] = cost; previous[next] = current.state; push(next, cost); }
    }
  }
  if (final === -1) return result([source, target], boxes, labelSize, true);
  const points: Point[] = [];
  for (let state = final; state !== -1; state = previous[state]) points.push(pointAt(Math.floor(state / 3)));
  return result([source, ...points.reverse(), target], boxes, labelSize);
}

type NetRoute = { id: string; route: WireRoute; net: string; netKey: string };
type Line = { horizontal: boolean; fixed: number; cuts: Set<number>; spans: Array<{ low: number; high: number; id: string }> };
function renderSegments(routes: NetRoute[]): Pick<WireLayout, "segments" | "junctions"> {
  const nets = new Map<string, NetRoute[]>();
  for (const item of routes) {
    if (item.route.blocked) continue;
    const members = nets.get(item.net) ?? []; members.push(item); nets.set(item.net, members);
  }
  const segments: WireSegment[] = []; const junctions: WireJunction[] = [];
  for (const [net, members] of [...nets].sort(([a], [b]) => compare(a, b))) {
    const lines = new Map<string, Line>(); const netKey = members[0].netKey;
    for (const member of members) {
      for (let index = 1; index < member.route.points.length; index++) {
        const a = member.route.points[index - 1]; const b = member.route.points[index];
        if (same(a, b)) continue;
        const horizontal = a.y === b.y; const fixed = horizontal ? a.y : a.x;
        const low = Math.min(horizontal ? a.x : a.y, horizontal ? b.x : b.y);
        const high = Math.max(horizontal ? a.x : a.y, horizontal ? b.x : b.y);
        const key = JSON.stringify([horizontal, fixed]);
        const line = lines.get(key) ?? { horizontal, fixed, cuts: new Set<number>(), spans: [] };
        line.cuts.add(low); line.cuts.add(high); line.spans.push({ low, high, id: member.id }); lines.set(key, line);
      }
    }
    const ordered = [...lines.values()].sort((a, b) => Number(b.horizontal) - Number(a.horizontal) || a.fixed - b.fixed);
    // 只在同一网内切分交会，跨网交叉永远不会因此产生 junction。
    for (const horizontal of ordered.filter(line => line.horizontal)) {
      for (const vertical of ordered.filter(line => !line.horizontal)) {
        if (horizontal.spans.some(span => vertical.fixed >= span.low && vertical.fixed <= span.high)
          && vertical.spans.some(span => horizontal.fixed >= span.low && horizontal.fixed <= span.high)) {
          horizontal.cuts.add(vertical.fixed); vertical.cuts.add(horizontal.fixed);
        }
      }
    }
    const vertices = new Map<string, { point: Point; directions: Set<string>; edgeIds: Set<string> }>();
    function vertex(point: Point, direction: string, edgeIds: string[]) {
      const key = JSON.stringify([point.x, point.y]);
      const item = vertices.get(key) ?? { point, directions: new Set<string>(), edgeIds: new Set<string>() };
      item.directions.add(direction); edgeIds.forEach(id => item.edgeIds.add(id)); vertices.set(key, item);
    }
    for (const line of ordered) {
      const cuts = [...line.cuts].sort((a, b) => a - b);
      for (let index = 1; index < cuts.length; index++) {
        const low = cuts[index - 1]; const high = cuts[index];
        const edgeIds = [...new Set(line.spans.filter(span => span.low < high && span.high > low).map(span => span.id))].sort(compare);
        if (!edgeIds.length || high - low < EPSILON) continue;
        const a = line.horizontal ? { x: low, y: line.fixed } : { x: line.fixed, y: low };
        const b = line.horizontal ? { x: high, y: line.fixed } : { x: line.fixed, y: high };
        const points = [a, b];
        segments.push({ id: `segment:${JSON.stringify([net, a.x, a.y, b.x, b.y])}`, path: pathFor(points), points, edgeIds, netKey });
        vertex(a, line.horizontal ? "right" : "down", edgeIds); vertex(b, line.horizontal ? "left" : "up", edgeIds);
      }
    }
    for (const { point, directions, edgeIds } of vertices.values()) {
      if (directions.size >= 3 && edgeIds.size >= 2) junctions.push({ ...point, id: `junction:${JSON.stringify([net, point.x, point.y])}`, edgeIds: [...edgeIds].sort(compare), netKey });
    }
  }
  return { segments, junctions };
}

/**
 * 纯批量布局：稳定 id 排序；仅同一非空 netKey 且同源的线共段。未提供 netKey 的线各自独立。
 * routes 保留完整支路和 blocked；segments.points 必有，公共区间只绘制一次；junctions 仅同网三向以上分支。
 * 坐标保持调用方坐标系，不修改图数据或输入；画布的 40px inset 由调用方统一施加。
 */
export function routeWires(wires: WireRequest[], boxes: WireBox[]): WireLayout {
  const ordered = [...wires].sort((a, b) => compare(a.id, b.id));
  if (!ordered.length) return { routes: [], segments: [], junctions: [] };
  const obstacles = inflate(boxes);
  const requests = ordered.map(wire => ({
    wire,
    net: wire.netKey ? JSON.stringify([wire.netKey, wire.source.nodeId ?? null, wire.source.side, wire.source.x, wire.source.y]) : JSON.stringify([null, wire.id]),
    netKey: wire.netKey || `wire:${wire.id}`,
  }));
  const endpoints = requests.flatMap(({ wire, net }) => [wire.source, wire.target].map(anchor => ({ point: escape(anchor, obstacles), net })));
  const seedX = [...obstacles.flatMap(box => [box.left, box.right]), ...endpoints.map(({ point }) => point.x)];
  const seedY = [...obstacles.flatMap(box => [box.top, box.bottom]), ...endpoints.map(({ point }) => point.y)];
  // 坐标只从初始盒边/端点派生；同轴独立网较多时补够有限通道，不随已布线路径递归膨胀。
  function lanes(values: number[], axis: "x" | "y") {
    const loads = new Map<number, Set<string>>();
    for (const { point, net } of endpoints) {
      const members = loads.get(point[axis]) ?? new Set<string>(); members.add(net); loads.set(point[axis], members);
    }
    const lowerBound = Math.min(...values) - LANE_GAP;
    return [...new Set(values)].flatMap(value => {
      const count = Math.max(3, loads.get(value)?.size ?? 1);
      const coordinates = [value - LANE_GAP, value, value + LANE_GAP];
      // 不向初始最小边界之外不断扩张，近零坐标优先把额外通道放到正向，保留默认显示 inset。
      for (let step = 2; coordinates.length < count; step++) {
        if (value - step * LANE_GAP >= lowerBound) coordinates.push(value - step * LANE_GAP);
        if (coordinates.length < count) coordinates.push(value + step * LANE_GAP);
      }
      return coordinates;
    });
  }
  const grid = makeGrid(obstacles, lanes(seedX, "x"), lanes(seedY, "y"));
  const occupied: OccupiedSegment[] = []; const reserved: WireBox[] = []; const routed: NetRoute[] = [];
  for (const { wire, net, netKey } of requests) {
    const size = wire.labelSize ?? DEFAULT_LABEL;
    const route = findRoute(wire.source, wire.target, boxes, size, { grid, occupied, net });
    const labeled = result(route.points, boxes, size, route.blocked, reserved);
    routed.push({ id: wire.id, route: labeled, net, netKey });
    reserved.push({ id: `label:${wire.id}`, left: labeled.label.x - size.width / 2, right: labeled.label.x + size.width / 2, top: labeled.label.y - size.height, bottom: labeled.label.y + 4 });
    if (!route.blocked) for (let index = 1; index < route.points.length; index++) occupied.push({ a: route.points[index - 1], b: route.points[index], net });
  }
  return { routes: routed.map(({ id, route }) => ({ id, route })), ...renderSegments(routed) };
}
