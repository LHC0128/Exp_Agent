import { useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type Ref } from "react";
import type { components } from "../../types/openapi";
import { endpointKey, portUsageSets, type PortUsage } from "./portUsage";
import { findFreeSlot, type PlacementPoint, type PlacementSize } from "./wiringPlacement";
import { wiringSyncBadgeLabel, type WiringSyncBadge } from "./wiringSync";
import { routeInternalWires, type InternalWire } from "./wiringInternalRoutes";
import { routeWire, routeWires, type Point, type WireAnchor as Anchor, type WireBox, type WireLabelSize } from "./wiringRoutes";
import { type WiringGraph } from "./wiringTypes";

type WiringEndpoint = components["schemas"]["WiringEndpoint"];
type PhysicalPort = components["schemas"]["PhysicalPort"];
type WiringInternalPort = components["schemas"]["WiringInternalPort"];
type WiringNode = WiringGraph["nodes"][number];
type ModeChoice = { deviceId: string; portId: string };
type SearchResult = { kind: "device"; deviceId: string; label: string } | { kind: "port"; deviceId: string; portId: string; label: string } | { kind: "edge"; edgeId: string; label: string };
export type WiringCanvasHandle = { findFreeSlot: (size: PlacementSize) => PlacementPoint };
// 节点坐标存的是黑盒左上角，盒子测量值包含显示层外绕边距；放置扫描需换回存储坐标。
const PLANE_INSET = 40;
// 内部端口锚点朝卡片内侧：输入取右沿，输出取左沿；section 为内部区相对节点盒的偏移。
type InternalGeometry = { width: number; height: number; section: Point; inputs: Record<string, Point>; outputs: Record<string, Point> };
type Geometry = { anchors: Record<string, Anchor>; boxes: WireBox[]; labels: Record<string, WireLabelSize>; internal: Record<string, InternalGeometry> };
// 与上次提交值比较并保留原对象，避免屏幕小数误差在路由与测量之间反复反馈。
function sameGeometry(previous: Geometry, next: Geometry, tolerance: number) {
  const near = (a: number, b: number) => Math.abs(a - b) <= tolerance;
  const samePoints = (old: Record<string, Point>, value: Record<string, Point>) => Object.keys(old).length === Object.keys(value).length && Object.entries(value).every(([key, point]) => {
    const before = old[key];
    return before && near(before.x, point.x) && near(before.y, point.y);
  });
  return Object.keys(previous.anchors).length === Object.keys(next.anchors).length && Object.entries(next.anchors).every(([key, anchor]) => {
    const old = previous.anchors[key];
    return old && old.nodeId === anchor.nodeId && old.side === anchor.side && near(old.x, anchor.x) && near(old.y, anchor.y);
  }) && previous.boxes.length === next.boxes.length && next.boxes.every((box, index) => {
    const old = previous.boxes[index];
    return old.id === box.id && near(old.left, box.left) && near(old.right, box.right) && near(old.top, box.top) && near(old.bottom, box.bottom);
  }) && Object.keys(previous.labels).length === Object.keys(next.labels).length && Object.entries(next.labels).every(([key, size]) => {
    const old = previous.labels[key];
    return old && near(old.width, size.width) && near(old.height, size.height);
  }) && Object.keys(previous.internal).length === Object.keys(next.internal).length && Object.entries(next.internal).every(([key, item]) => {
    const old = previous.internal[key];
    return old && near(old.width, item.width) && near(old.height, item.height) && near(old.section.x, item.section.x) && near(old.section.y, item.section.y)
      && samePoints(old.inputs, item.inputs) && samePoints(old.outputs, item.outputs);
  });
}
type Gesture = { pointerId: number; start: Point; moved: boolean } & (
  { kind: "node"; deviceId: string; origin: Point }
  | { kind: "port"; endpoint: WiringEndpoint }
  | { kind: "edge"; edgeId: string; end: "source" | "target"; fixed: WiringEndpoint; internal: boolean }
  | { kind: "pan"; origin: Point }
);

const keyOf = (endpoint: WiringEndpoint) => endpointKey(endpoint.device_id, endpoint.port_id);
const snap = (value: number) => Math.min(20000, Math.max(0, Math.round(value / 20) * 20));
const directionOf = (node: WiringNode, port: PhysicalPort) => port.direction === "switchable"
  ? port.modes.find(mode => mode.id === port.selected_mode)?.direction ?? null
  : port.direction === "inout" ? node.port_directions?.[port.id] ?? "inout" : port.direction;
const signalName = (type: PhysicalPort["signal_type"] | WiringGraph["edges"][number]["signal_type"]) => type === "internal" ? "内部信号（非线缆）" : type === "optical" ? "光学" : type === "electrical" ? "电学" : "光/电待确认";
const portName = (port: PhysicalPort) => [port.group, port.name || "未命名端口"].filter(Boolean).join(" / ");
const directionName = (value: string | null) => value === "in" ? "输入" : value === "out" ? "输出" : value === "inout" ? "历史方向待确认" : "方向待确认";
const cableText = (edge: WiringGraph["edges"][number]) => [edge.connector, edge.cable_id].filter(Boolean).join(" / ");
const wireText = (edge: WiringGraph["edges"][number]) => `${signalName(edge.signal_type)}${edge.label ? ` · ${edge.label}` : ""}${cableText(edge) ? ` · 线缆 ${cableText(edge)}` : ""}`;
const estimatedLabelSize = (edge: WiringGraph["edges"][number]): WireLabelSize => ({ width: Array.from(wireText(edge)).length * 12, height: 14 });

export function WiringCanvas({ ref, graph, locked, resetToken, active = true, autoFit = false, selectedDevice, selectedEdge, profileBadges = {}, onSelectDevice, onSelectEdge, onMove, onPair, onReconnect, onCancel, onPortDirection, onPortMode, onPortUsage, onSettingChange, onAutoFitSettled, onBlockedRoutesChange, endpointLabel }: {
  ref?: Ref<WiringCanvasHandle>;
  graph: WiringGraph;
  locked: boolean;
  resetToken: number;
  active?: boolean;
  autoFit?: boolean;
  selectedDevice: string | null;
  selectedEdge: string | null;
  profileBadges?: Partial<Record<string, WiringSyncBadge[]>>;
  onSelectDevice: (id: string) => void;
  onSelectEdge: (id: string) => void;
  onMove: (id: string, x: number, y: number) => void;
  onPair: (source: WiringEndpoint, target: WiringEndpoint) => void;
  onReconnect: (edgeId: string, end: "source" | "target", endpoint: WiringEndpoint) => void;
  onCancel: () => void;
  onPortDirection: (deviceId: string, portId: string, direction: "in" | "out") => void;
  onPortMode: (deviceId: string, portId: string, modeId: string | null) => void;
  onPortUsage: (deviceId: string, portId: string, usage: PortUsage) => void;
  onSettingChange: (deviceId: string, settingId: string, value: string | null) => void;
  onAutoFitSettled?: () => void;
  onBlockedRoutesChange?: (edgeIds: string[]) => void;
  endpointLabel: (endpoint: WiringEndpoint) => string;
}) {
  const section = useRef<HTMLElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const plane = useRef<HTMLDivElement>(null);
  const expandButton = useRef<HTMLButtonElement>(null);
  const zoomRef = useRef(1);
  const spacePressed = useRef(false);
  const pendingScroll = useRef<Point | null>(null);
  const wiringCamera = useRef({ zoom: 1, x: 0, y: 0 });
  const pendingOverviewFit = useRef(false);
  const pendingAutoFit = useRef(false);
  const [canvasView, setCanvasView] = useState<"wiring" | "overview">("wiring");
  const [query, setQuery] = useState("");
  const [zoom, setZoom] = useState(1);
  const [expanded, setExpanded] = useState(false);
  const [panning, setPanning] = useState(false);
  const [hoveredEdges, setHoveredEdges] = useState<string[]>([]);
  const [branchChoices, setBranchChoices] = useState<string[]>([]);
  const gesture = useRef<Gesture | null>(null);
  const captureTarget = useRef<Element | null>(null);
  const suppressClick = useRef(false);
  const [moving, setMoving] = useState<{ id: string; position: Point } | null>(null);
  const [reconnecting, setReconnecting] = useState<{ edgeId: string; end: "source" | "target"; fixed: WiringEndpoint; internal: boolean } | null>(null);
  const [firstPort, setFirstPort] = useState<WiringEndpoint | null>(null);
  const [cursor, setCursor] = useState<Point | null>(null);
  const [geometry, setGeometry] = useState<Geometry>({ anchors: {}, boxes: [], labels: {}, internal: {} });
  const { anchors, boxes, labels, internal } = geometry;
  const [modeChoice, setModeChoice] = useState<ModeChoice | null>(null);
  const [expandedUnused, setExpandedUnused] = useState<string[]>([]);
  const activeRef = useRef(active);
  const expandedRef = useRef(expanded);
  const cancelRef = useRef<() => void>(() => {});
  const { connected: connectedEndpoints, used: usedEndpoints } = useMemo(() => portUsageSets(graph), [graph]);
  // 只读过滤：器件、端口、连线标签共用一次匹配，避免高亮与结果顺序不一致。
  const search = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    if (!term) return null;
    const devices = new Set<string>(); const ports = new Set<string>(); const edges = new Set<string>();
    const results: SearchResult[] = [];
    for (const node of graph.nodes) {
      const device = node.device;
      if (device.name.toLocaleLowerCase().includes(term)) {
        devices.add(device.id);
        results.push({ kind: "device", deviceId: device.id, label: device.name });
      }
      const matchPort = (portId: string, name: string) => {
        if (!name.toLocaleLowerCase().includes(term)) return;
        const key = endpointKey(device.id, portId);
        devices.add(device.id); ports.add(key);
        results.push({ kind: "port", deviceId: device.id, portId, label: `${device.name} / ${name}` });
      };
      for (const port of device.ports) matchPort(port.id, portName(port));
      for (const port of node.internal_ports ?? []) matchPort(port.id, port.name || "未命名内部端口");
    }
    for (const edge of graph.edges) {
      if (!edge.label.toLocaleLowerCase().includes(term)) continue;
      // 连线标签命中时同时保留两端器件，避免节点整体变淡遮住线内信号。
      edges.add(edge.id); devices.add(edge.source.device_id); devices.add(edge.target.device_id);
      results.push({ kind: "edge", edgeId: edge.id, label: edge.label });
    }
    return { devices, ports, edges, results };
  }, [graph, query]);
  useImperativeHandle(ref, () => ({
    findFreeSlot(size) {
      const view = viewport.current;
      const origin = view ? { x: view.scrollLeft / zoomRef.current - PLANE_INSET, y: view.scrollTop / zoomRef.current - PLANE_INSET } : { x: 0, y: 0 };
      return findFreeSlot(boxes.map(box => ({ left: box.left - PLANE_INSET, top: box.top - PLANE_INSET, right: box.right - PLANE_INSET, bottom: box.bottom - PLANE_INSET })), size, origin);
    },
  }), [boxes]);
  function hoverEdges(ids: string[]) {
    setHoveredEdges(previous => previous.length === ids.length && previous.every((id, index) => id === ids[index]) ? previous : ids);
  }

  function localPoint(clientX: number, clientY: number): Point {
    const rect = plane.current?.getBoundingClientRect();
    return { x: (clientX - (rect?.left ?? 0)) / zoomRef.current, y: (clientY - (rect?.top ?? 0)) / zoomRef.current };
  }
  function changeZoom(value: number, focus?: Point) {
    const view = viewport.current;
    if (!view) return;
    // 手动缩放后不再自动取景，避免覆盖用户当前视角。
    if (pendingAutoFit.current) { pendingAutoFit.current = false; onAutoFitSettled?.(); }
    const next = Math.min(2, Math.max(.01, value));
    const at = focus ?? { x: view.clientWidth / 2, y: view.clientHeight / 2 };
    pendingScroll.current = { x: (view.scrollLeft + at.x) / zoomRef.current * next - at.x, y: (view.scrollTop + at.y) / zoomRef.current * next - at.y };
    if (next === zoomRef.current) {
      view.scrollLeft = Math.max(0, pendingScroll.current.x); view.scrollTop = Math.max(0, pendingScroll.current.y); pendingScroll.current = null;
    }
    zoomRef.current = next; setZoom(next);
  }
  useLayoutEffect(() => {
    const view = viewport.current; const scroll = pendingScroll.current;
    if (view && scroll) { view.scrollLeft = Math.max(0, scroll.x); view.scrollTop = Math.max(0, scroll.y); pendingScroll.current = null; }
  });
  useEffect(() => {
    const view = viewport.current;
    if (!view || !active) return;
    const wheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      const rect = view.getBoundingClientRect();
      changeZoom(zoomRef.current * Math.exp(-Math.max(-100, Math.min(100, event.deltaY)) * .005), { x: event.clientX - rect.left, y: event.clientY - rect.top });
    };
    view.addEventListener("wheel", wheel, { passive: false });
    return () => view.removeEventListener("wheel", wheel);
  }, [active]);
  function release() {
    const current = gesture.current;
    gesture.current = null;
    const target = captureTarget.current;
    captureTarget.current = null;
    if (current && target?.hasPointerCapture?.(current.pointerId)) target.releasePointerCapture(current.pointerId);
  }
  function cancel() {
    if (gesture.current) suppressClick.current = true;
    release(); setPanning(false); setMoving(null); setReconnecting(null); setFirstPort(null); setCursor(null); setModeChoice(null); setBranchChoices([]); hoverEdges([]); onCancel();
  }
  useLayoutEffect(() => { activeRef.current = active; expandedRef.current = expanded; cancelRef.current = cancel; });
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (!activeRef.current) return;
      if (event.key === "Escape") {
        event.preventDefault(); cancelRef.current();
        if (expandedRef.current) { setExpanded(false); expandButton.current?.focus(); }
      }
      const target = event.target instanceof Element ? event.target : null;
      if (event.code === "Space" && section.current?.contains(target) && !target?.closest("button,input,textarea,select,[role='button'],[contenteditable='true']")) {
        event.preventDefault(); spacePressed.current = true;
      }
      if (expandedRef.current && event.key === "Tab") {
        const items = Array.from(section.current?.querySelectorAll<HTMLElement>("button:not(:disabled),select:not(:disabled),[tabindex='0']") ?? []).filter(item => item.getClientRects().length > 0);
        const first = items[0]; const last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    const keyup = (event: KeyboardEvent) => { if (event.code === "Space") spacePressed.current = false; };
    const blur = () => { spacePressed.current = false; if (gesture.current) cancelRef.current(); };
    window.addEventListener("keydown", keydown); window.addEventListener("keyup", keyup); window.addEventListener("blur", blur);
    return () => { window.removeEventListener("keydown", keydown); window.removeEventListener("keyup", keyup); window.removeEventListener("blur", blur); };
  }, []);
  useEffect(() => {
    if (!expanded || !active) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden"; expandButton.current?.focus();
    return () => { document.body.style.overflow = previous; };
  }, [expanded, active]);
  useEffect(() => {
    release(); spacePressed.current = false; setPanning(false); setMoving(null); setReconnecting(null); setFirstPort(null); setCursor(null); setModeChoice(null); setBranchChoices([]); hoverEdges([]);
    if (!active) setExpanded(false);
  }, [locked, resetToken, active]);

  // 锚点来自真实端口按钮，长名称、模式分组和不同端口数都不依赖固定行高。
  useLayoutEffect(() => {
    const update = () => {
      const root = plane.current;
      if (!root || !active) return;
      const origin = root.getBoundingClientRect();
      let screenMagnitude = Math.max(1, Math.abs(origin.left), Math.abs(origin.top), Math.abs(origin.right), Math.abs(origin.bottom));
      const measure = (element: Element) => {
        const rect = element.getBoundingClientRect();
        screenMagnitude = Math.max(screenMagnitude, Math.abs(rect.left), Math.abs(rect.top), Math.abs(rect.right), Math.abs(rect.bottom));
        return rect;
      };
      const logical = (value: number) => Math.round(value / zoomRef.current * 1000) / 1000;
      const next: Record<string, Anchor> = {};
      root.querySelectorAll<HTMLElement>("[data-port-id]").forEach(element => {
        const rect = measure(element);
        const endpoint = { device_id: element.dataset.deviceId!, port_id: element.dataset.portId! };
        const side = element.dataset.side === "right" ? "right" : "left";
        next[keyOf(endpoint)] = { x: logical((side === "right" ? rect.right : rect.left) - origin.left), y: logical(rect.top + rect.height / 2 - origin.top), side, nodeId: endpoint.device_id };
      });
      const nextInternal: Record<string, InternalGeometry> = {};
      const nextBoxes = Array.from(root.querySelectorAll<HTMLElement>(".wiring-node[data-node-id]"), element => {
        const rect = measure(element);
        const section = element.querySelector<HTMLElement>(".wiring-internal-section");
        if (section) {
          const sectionRect = measure(section);
          const inputs: Record<string, Point> = {}; const outputs: Record<string, Point> = {};
          element.querySelectorAll<HTMLElement>('[data-port-kind="internal"][data-port-id]').forEach(port => {
            const portRect = measure(port);
            // 输入取右沿、输出取左沿，均朝卡片内侧；内部路线因此不会越过卡片边界。
            const inner = port.dataset.side === "right" ? portRect.left : portRect.right;
            (port.dataset.side === "right" ? outputs : inputs)[port.dataset.portId!] = {
              x: logical(inner - rect.left), y: logical(portRect.top + portRect.height / 2 - rect.top),
            };
          });
          nextInternal[element.dataset.nodeId!] = {
            width: logical(rect.width), height: logical(rect.height),
            section: { x: logical(sectionRect.left - rect.left), y: logical(sectionRect.top - rect.top) }, inputs, outputs,
          };
        }
        return { id: element.dataset.nodeId!, left: logical(rect.left - origin.left), right: logical(rect.right - origin.left), top: logical(rect.top - origin.top), bottom: logical(rect.bottom - origin.top) };
      }).filter(box => box.right > box.left && box.bottom > box.top);
      const nextLabels = Object.fromEntries(Array.from(root.querySelectorAll<SVGTextElement>("text[data-edge-label]"), element => {
        const edge = graph.edges.find(item => item.id === element.dataset.edgeLabel)!;
        // SVG 本地字形宽高不随屏幕缩放和平移变化；描边余量仍由路由和取景保留。
        try {
          const bbox = element.getBBox?.();
          if (bbox && Number.isFinite(bbox.width) && Number.isFinite(bbox.height) && bbox.width > 0 && bbox.height > 0) {
            return [element.dataset.edgeLabel!, { width: bbox.width, height: Math.max(14, bbox.height) }];
          }
        } catch { /* 隐藏或不支持本地 SVG 测量时，保留屏幕矩形回退。 */ }
        const rect = measure(element);
        return [element.dataset.edgeLabel!, Number.isFinite(rect.width) && Number.isFinite(rect.height) && rect.width > 0 && rect.height > 0 ? { width: logical(rect.width), height: Math.max(14, logical(rect.height)) } : estimatedLabelSize(edge)];
      }));
      const nextGeometry = { anchors: next, boxes: nextBoxes, labels: nextLabels, internal: nextInternal };
      // 两次屏幕端点差最多累积约四个 float32 舍入误差，再折回逻辑坐标；不是粗化网格。
      const tolerance = Math.max(.002, screenMagnitude * 2 ** -21 / zoomRef.current);
      setGeometry(previous => sameGeometry(previous, nextGeometry, tolerance) ? previous : nextGeometry);
    };
    update();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    if (plane.current) {
      observer?.observe(plane.current);
      plane.current.querySelectorAll(".wiring-node, text[data-edge-label]").forEach(element => observer?.observe(element));
    }
    window.addEventListener("resize", update);
    return () => { observer?.disconnect(); window.removeEventListener("resize", update); };
  }, [graph, moving, active, modeChoice, expandedUnused, geometry, zoom, expanded]);

  function begin(event: ReactPointerEvent, value: Gesture) {
    if (locked || event.button !== 0) return;
    suppressClick.current = false;
    gesture.current = value;
    // 保持原按钮为捕获目标，未拖动的 pointer 序列仍会产生按钮 click。
    captureTarget.current = event.currentTarget;
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }
  function move(event: ReactPointerEvent) {
    const current = gesture.current;
    if (!current || event.pointerId !== current.pointerId) return;
    if (current.kind === "pan") {
      const dx = event.clientX - current.start.x; const dy = event.clientY - current.start.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) current.moved = true;
      if (viewport.current) { viewport.current.scrollLeft = Math.max(0, current.origin.x - dx); viewport.current.scrollTop = Math.max(0, current.origin.y - dy); }
      return;
    }
    if (locked) return;
    const position = localPoint(event.clientX, event.clientY);
    if ((Math.abs(position.x - current.start.x) + Math.abs(position.y - current.start.y)) * zoomRef.current > 4) current.moved = true;
    if (!current.moved) return;
    if (current.kind === "node") setMoving({ id: current.deviceId, position: {
      x: snap(current.origin.x + position.x - current.start.x), y: snap(current.origin.y + position.y - current.start.y),
    } });
    else if (current.kind === "edge") { setReconnecting({ edgeId: current.edgeId, end: current.end, fixed: current.fixed, internal: current.internal }); setCursor(position); }
    else { setFirstPort(current.endpoint); setCursor(position); }
  }
  function finish(event: ReactPointerEvent) {
    const current = gesture.current;
    if (!current || event.pointerId !== current.pointerId) return;
    release(); setPanning(false); setMoving(null); setReconnecting(null); setCursor(null);
    if (current.kind === "pan") { if (current.moved) suppressClick.current = true; return; }
    if (locked || !current.moved) return;
    // 捕获后 event.target 仍是起始按钮，必须按屏幕坐标查找真正落下的端口。
    const hit = document.elementFromPoint?.(event.clientX, event.clientY) ?? event.target;
    suppressClick.current = true;
    if (current.kind === "node") {
      const position = localPoint(event.clientX, event.clientY);
      onMove(current.deviceId, snap(current.origin.x + position.x - current.start.x), snap(current.origin.y + position.y - current.start.y));
    } else if (current.kind === "edge") {
      const element = hit instanceof Element ? hit.closest<HTMLElement>("[data-port-id]") : null;
      if (!element || !plane.current?.contains(element)) return;
      onReconnect(current.edgeId, current.end, { device_id: element.dataset.deviceId!, port_id: element.dataset.portId! });
    } else {
      const element = hit instanceof Element ? hit.closest<HTMLElement>("[data-port-id]") : null;
      setFirstPort(null);
      if (!element || !plane.current?.contains(element)) return;
      const target = { device_id: element.dataset.deviceId!, port_id: element.dataset.portId! };
      if (keyOf(target) === keyOf(current.endpoint)) { onCancel(); return; }
      onPair(current.endpoint, target);
    }
  }
  function chooseDirection(node: WiringNode, port: PhysicalPort, direction: "in" | "out") {
    if (locked || !usedEndpoints.has(endpointKey(node.device.id, port.id))) return;
    setFirstPort(null); setCursor(null); setModeChoice(null); onCancel();
    // 仅旧双向记录需要补录工作方向；复用口直接选功能，由功能决定方向。
    if (port.direction === "inout") onPortDirection(node.device.id, port.id, direction);
  }
  function usageToggle(node: WiringNode, port: PhysicalPort) {
    const name = `${node.device.name} / ${portName(port)}`;
    const key = endpointKey(node.device.id, port.id);
    const hasEdge = connectedEndpoints.has(key);
    const used = usedEndpoints.has(key);
    return <button className="wiring-usage-toggle" disabled={locked || hasEdge} aria-label={`使用状态：${name}`} aria-pressed={used}
      title={hasEdge ? "此端口已有连线，不能标为未用；请先明确删除相关连线。" : `点击改为${used ? "未用" : "使用"}；仅记录当前图，未用不代表关闭硬件。`}
      onClick={() => { if (!locked && !hasEdge) onPortUsage(node.device.id, port.id, used ? "unused" : "used"); }}>{used ? "使用" : "未用"}</button>;
  }
  function renderPort(node: WiringNode, port: PhysicalPort, side: Anchor["side"]) {
    const device = node.device;
    const endpoint = { device_id: device.id, port_id: port.id };
    const key = keyOf(endpoint);
    const direction = directionOf(node, port);
    const name = `${device.name} / ${portName(port)}`;
    const selectedMode = port.modes.find(mode => mode.id === port.selected_mode);
    const usage = usedEndpoints.has(key) ? "used" : "unused";
    const pending = firstPort ? keyOf(firstPort) === key : false;
    const choosing = modeChoice?.deviceId === device.id && modeChoice.portId === port.id;
    const choices = port.modes.filter(mode => mode.direction === "in" || mode.direction === "out");
    return <div key={port.id} className={`wiring-port-item signal-${port.signal_type ?? "unknown"}`}>
      <button disabled={locked || usage === "unused"} className={`wiring-port signal-${port.signal_type ?? "unknown"}${pending ? " pending" : ""}${highlightedEndpoints.has(key) ? " wiring-port-highlight" : ""}${search?.ports.has(key) ? " wiring-port-search-match" : ""}`}
        data-device-id={device.id} data-port-id={port.id} data-side={side}
        aria-label={`连接端口：${name}`} aria-pressed={pending}
        title={`${signalName(port.signal_type)} · ${directionName(direction)}${selectedMode ? ` · ${selectedMode.name}` : ""}${port.connector ? ` · 接头 ${port.connector}` : ""}`}
        onPointerDown={event => { if (usage !== "unused") begin(event, { kind: "port", pointerId: event.pointerId, start: localPoint(event.clientX, event.clientY), moved: false, endpoint }); }}
        onClick={() => { if (locked || usage === "unused") return; if (!firstPort) setFirstPort(endpoint); else if (keyOf(firstPort) === key) { setFirstPort(null); setCursor(null); onCancel(); } else { onPair(firstPort, endpoint); setFirstPort(null); setCursor(null); } }}>
        <strong>{portName(port)}</strong>
        {(direction === null || direction === "inout") && <small className="wiring-port-state">{direction === "inout" ? "历史方向待确认" : "? 方向待确认"}</small>}
      </button>
      {port.direction === "inout" && <div className="wiring-port-shortcuts" role="group" aria-label={`历史端口工作方向：${name}`}>
        {(["in", "out"] as const).map(value => <button key={value} disabled={locked || usage === "unused"}
          aria-label={`记录${value === "in" ? "输入" : "输出"}：${name}`} aria-pressed={direction === value}
          title="补录旧端口在本图的单向工作方向，不修改原物理档案或硬件" onClick={() => chooseDirection(node, port, value)}>{value === "in" ? "输入" : "输出"}</button>)}
      </div>}
      {port.direction === "switchable" && <button className="wiring-mode-toggle" disabled={locked || usage === "unused"} aria-label={`选择图内模式：${name}`}
        title={selectedMode?.name ?? "尚未选择图内功能"} aria-expanded={choosing}
        onClick={() => { setFirstPort(null); setCursor(null); onCancel(); setModeChoice(choosing ? null : { deviceId: device.id, portId: port.id }); }}>{selectedMode?.name ?? "选择功能"}</button>}
      {choosing && <div className="wiring-mode-choice">
        <label>选择功能<select autoFocus disabled={locked || usage === "unused"} value="" aria-label={`确认图内模式：${name}`} onChange={event => {
          if (!event.target.value) return;
          onPortMode(device.id, port.id, event.target.value); setModeChoice(null);
        }}><option value="" disabled>请明确选择</option>{choices.map(mode => <option key={mode.id} value={mode.id}>{mode.name}（{directionName(mode.direction)}）</option>)}</select></label>
        <button className="wiring-mode-close" disabled={locked} onClick={() => setModeChoice(null)} aria-label={`取消模式选择：${name}`}>取消</button>
      </div>}
      {usageToggle(node, port)}
    </div>;
  }
  function renderInternalPort(node: WiringNode, port: WiringInternalPort, side: Anchor["side"]) {
    const device = node.device;
    const endpoint = { device_id: device.id, port_id: port.id };
    const key = keyOf(endpoint);
    const name = port.name || "未命名内部端口";
    const pending = firstPort ? keyOf(firstPort) === key : false;
    return <button key={port.id} disabled={locked} className={`wiring-port signal-internal${pending ? " pending" : ""}${highlightedEndpoints.has(key) ? " wiring-port-highlight" : ""}${search?.ports.has(key) ? " wiring-port-search-match" : ""}`}
      data-device-id={device.id} data-port-id={port.id} data-side={side} data-port-kind="internal"
      aria-label={`连接内部端口：${device.name} / ${name}`} aria-pressed={pending}
      title={`内部信号（非线缆） · ${directionName(port.direction)}；仅连接同一器件的内部输出与内部输入`}
      onPointerDown={event => begin(event, { kind: "port", pointerId: event.pointerId, start: localPoint(event.clientX, event.clientY), moved: false, endpoint })}
      onClick={() => { if (locked) return; if (!firstPort) setFirstPort(endpoint); else if (keyOf(firstPort) === key) { setFirstPort(null); setCursor(null); onCancel(); } else { onPair(firstPort, endpoint); setFirstPort(null); setCursor(null); } }}>
      <strong>{name}</strong>
    </button>;
  }
  // 只平移显示层，为坐标 0 的黑盒外绕线留边；图中坐标、吸附和保存数据保持原值。
  const width = Math.max(2600, ...graph.nodes.map(node => (moving?.id === node.device.id ? moving.position.x : node.x) + 440)) + PLANE_INSET * 2;
  const height = Math.max(1600, ...graph.nodes.map(node => (moving?.id === node.device.id ? moving.position.y : node.y) + 360 + (node.device.ports.length + (node.internal_ports?.length ?? 0) + (node.settings?.length ?? 0)) * 120)) + PLANE_INSET * 2;
  const previewStart = firstPort ? anchors[keyOf(firstPort)] : undefined;
  // 内部信号不进入黑盒绕行算法，只走器件内部区自己的正交通道。
  const externalEdges = useMemo(() => graph.edges.filter(edge => edge.signal_type !== "internal"), [graph]);
  const layout = useMemo(() => {
    function endpointDirection(endpoint: WiringEndpoint) {
      const node = graph.nodes.find(item => item.device.id === endpoint.device_id);
      const port = node?.device.ports.find(item => item.id === endpoint.port_id);
      return node && port ? directionOf(node, port) : node?.internal_ports?.find(item => item.id === endpoint.port_id)?.direction;
    }
    return routeWires(externalEdges.flatMap(edge => {
      let source = edge.source; let target = edge.target;
      // 物理连线无向保存；仅明确的输出→输入可以按实际输出口共享，不用频率或标签猜同源。
      if (endpointDirection(source) === "in" && endpointDirection(target) === "out") [source, target] = [target, source];
      const a = anchors[keyOf(source)]; const b = anchors[keyOf(target)];
      if (!a || !b) return [];
      const netKey = endpointDirection(source) === "out" && endpointDirection(target) === "in" ? JSON.stringify([edge.signal_type, source.device_id, source.port_id]) : undefined;
      return [{ id: edge.id, source: a, target: b, labelSize: labels[edge.id] ?? estimatedLabelSize(edge), netKey }];
    }), boxes);
  }, [graph, externalEdges, anchors, boxes, labels]);
  const routes = externalEdges.map(edge => ({ edge, route: layout.routes.find(item => item.id === edge.id)?.route ?? null }));
  const internalRoutes = useMemo(() => {
    const groups = new Map<string, InternalWire[]>();
    for (const edge of graph.edges) {
      if (edge.signal_type !== "internal" || edge.source.device_id !== edge.target.device_id) continue;
      const card = internal[edge.source.device_id];
      const source = card?.outputs[edge.source.port_id]; const target = card?.inputs[edge.target.port_id];
      if (!card || !source || !target) continue;
      const items = groups.get(edge.source.device_id) ?? [];
      items.push({ id: edge.id, source, target }); groups.set(edge.source.device_id, items);
    }
    return new Map([...groups].map(([nodeId, items]) => [nodeId, routeInternalWires(items, internal[nodeId])]));
  }, [graph, internal]);
  const blockedRoutes = routes.filter(item => item.route?.blocked);
  // 只按阻塞连线的稳定 ID 集合上报；回调放 ref，父组件内联函数变化不会反复触发 setState。
  const blockedEdgeKey = JSON.stringify(blockedRoutes.map(({ edge }) => edge.id).sort());
  const blockedRoutesChange = useRef(onBlockedRoutesChange);
  blockedRoutesChange.current = onBlockedRoutesChange;
  useEffect(() => {
    blockedRoutesChange.current?.(JSON.parse(blockedEdgeKey) as string[]);
  }, [blockedEdgeKey]);
  const sharedNets = new Set(layout.segments.filter(segment => segment.edgeIds.length > 1).map(segment => segment.netKey));
  const sharedEdgeIds = new Set(layout.segments.filter(segment => sharedNets.has(segment.netKey)).flatMap(segment => segment.edgeIds));
  const highlighted = new Set(hoveredEdges.length ? hoveredEdges : branchChoices.length ? branchChoices : selectedEdge ? [selectedEdge] : []);
  const highlightedEndpoints = new Set(graph.edges.filter(edge => highlighted.has(edge.id)).flatMap(edge => [keyOf(edge.source), keyOf(edge.target)]));
  const focusedEdges = graph.edges.filter(edge => highlighted.has(edge.id));
  const preview = previewStart && cursor ? routeWire(previewStart, { ...cursor, side: "left" }, boxes) : null;
  const reconnectEdge = selectedEdge ? graph.edges.find(edge => edge.id === selectedEdge) : undefined;
  const reconnectAnchor = reconnecting ? anchors[keyOf(reconnecting.fixed)] : undefined;
  // 内部信号只在卡片内走线，预览退化为两端直连；外部线复用现有绕行路由。
  const reconnectPreview = reconnecting && cursor && reconnectAnchor
    ? reconnecting.internal ? `M ${reconnectAnchor.x} ${reconnectAnchor.y} L ${cursor.x} ${cursor.y}`
      : (() => { const route = routeWire(reconnectAnchor, { ...cursor, side: "left" }, boxes); return route.blocked ? null : route.path; })()
    : null;
  function edgeLabel(edge: WiringGraph["edges"][number]) {
    const cable = cableText(edge);
    return `${endpointLabel(edge.source)} ${edge.signal_type === "internal" ? "→" : "↔"} ${endpointLabel(edge.target)}（${signalName(edge.signal_type)}${edge.label ? ` · ${edge.label}` : ""}${cable ? `，线缆 ${cable}` : ""}${edge.note ? `，备注 ${edge.note}` : ""}）`;
  }
  function chooseEdges(ids: string[]) {
    if (locked) return;
    if (ids.length === 1) { setBranchChoices([]); onSelectEdge(ids[0]); }
    else setBranchChoices(ids);
  }
  function selectCanvasView(next: "wiring" | "overview") {
    const view = viewport.current;
    if (!view || next === canvasView) return;
    // 共用原图和交互，只保存接线视角；不取消编辑器中尚待确认的连线。
    release(); spacePressed.current = false; setPanning(false); setMoving(null);
    if (next === "overview") {
      wiringCamera.current = { zoom: zoomRef.current, x: view.scrollLeft, y: view.scrollTop };
      pendingOverviewFit.current = true;
    } else {
      pendingOverviewFit.current = false;
      const camera = wiringCamera.current;
      pendingScroll.current = { x: camera.x, y: camera.y };
      zoomRef.current = camera.zoom; setZoom(camera.zoom);
    }
    setCanvasView(next);
  }
  function fitAll() {
    const view = viewport.current;
    if (!view || !boxes.length) return;
    const points = boxes.flatMap(box => [{ x: box.left, y: box.top }, { x: box.right, y: box.bottom }]);
    routes.forEach(({ edge, route }) => {
      if (!route || route.blocked) return;
      const size = labels[edge.id] ?? estimatedLabelSize(edge);
      points.push(...route.points, { x: route.label.x - size.width / 2 - 4, y: route.label.y - size.height - 4 }, { x: route.label.x + size.width / 2 + 4, y: route.label.y + 4 });
    });
    const left = Math.max(0, Math.min(...points.map(point => point.x)) - 24); const top = Math.max(0, Math.min(...points.map(point => point.y)) - 24);
    const right = Math.max(...points.map(point => point.x)) + 24; const bottom = Math.max(...points.map(point => point.y)) + 24;
    const next = Math.max(.01, Math.min(1, view.clientWidth / Math.max(1, right - left), view.clientHeight / Math.max(1, bottom - top)));
    pendingScroll.current = { x: Math.max(0, left * next - (view.clientWidth - (right - left) * next) / 2), y: Math.max(0, top * next - (view.clientHeight - (bottom - top) * next) / 2) };
    if (next === zoomRef.current) {
      view.scrollLeft = pendingScroll.current.x; view.scrollTop = pendingScroll.current.y; pendingScroll.current = null;
    }
    zoomRef.current = next; setZoom(next);
  }
  function centerView(point: Point) {
    const view = viewport.current;
    if (!view) return;
    const scale = zoomRef.current;
    view.scrollLeft = Math.max(0, point.x * scale - view.clientWidth / 2);
    view.scrollTop = Math.max(0, point.y * scale - view.clientHeight / 2);
  }
  // 搜索只改变选中与滚动位置：找到首个结果即停，不修改任何连线或坐标。
  function focusFirstSearchResult() {
    const first = search?.results[0];
    if (!first) return;
    if (pendingAutoFit.current) { pendingAutoFit.current = false; onAutoFitSettled?.(); }
    if (first.kind === "edge") {
      onSelectEdge(first.edgeId);
      const route = layout.routes.find(item => item.id === first.edgeId)?.route;
      const edge = graph.edges.find(item => item.id === first.edgeId);
      const a = edge ? anchors[keyOf(edge.source)] : undefined;
      const b = edge ? anchors[keyOf(edge.target)] : undefined;
      const point = route ? route.points[Math.floor(route.points.length / 2)] ?? route.label : a && b ? { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } : null;
      if (point) centerView(point);
      return;
    }
    onSelectDevice(first.deviceId);
    if (first.kind === "port") {
      const anchor = anchors[endpointKey(first.deviceId, first.portId)];
      if (anchor) centerView(anchor);
      return;
    }
    const box = boxes.find(item => item.id === first.deviceId);
    if (box) centerView({ x: (box.left + box.right) / 2, y: (box.top + box.bottom) / 2 });
  }

  useLayoutEffect(() => {
    if (autoFit) pendingAutoFit.current = true;
  }, [autoFit]);
  useLayoutEffect(() => {
    if (!pendingAutoFit.current) return;
    if (!graph.nodes.length) { pendingAutoFit.current = false; onAutoFitSettled?.(); return; }
    if (!active) return;
    // 等真实盒子、路线及已绘制标签完成测量后取景一次，之后不再重复。
    if (boxes.length !== graph.nodes.length || routes.some(({ edge, route }) => !route || !route.blocked && !labels[edge.id])) return;
    pendingAutoFit.current = false;
    const view = viewport.current;
    // 未布局或隐藏容器测不到视口，保持当前缩放，避免被压到最小比例。
    if (view && view.clientWidth > 0 && view.clientHeight > 0) fitAll();
    onAutoFitSettled?.();
  });

  useLayoutEffect(() => {
    if (!active || canvasView !== "overview" || !pendingOverviewFit.current) return;
    if (!graph.nodes.length) { pendingOverviewFit.current = false; return; }
    // 等真实盒子、路线及已绘制标签完成测量后，仅在进入总览时取景一次。
    if (boxes.length !== graph.nodes.length || routes.some(({ edge, route }) => !route || !route.blocked && !labels[edge.id])) return;
    pendingOverviewFit.current = false;
    fitAll();
  });

  return <section ref={section} className={`wiring-canvas-section${expanded ? " wiring-canvas-expanded" : ""}`} data-view={canvasView} aria-label="接线画布" role={expanded ? "dialog" : undefined} aria-modal={expanded || undefined}>
    <div className="wiring-view-toolbar" role="toolbar" aria-label="画布视图工具">
      <div className="wiring-view-switch" role="group" aria-label="接线图视图">
        <button className="secondary" aria-pressed={canvasView === "overview"} onClick={() => selectCanvasView("overview")}>总览视图</button>
        <button className="secondary" aria-pressed={canvasView === "wiring"} onClick={() => selectCanvasView("wiring")}>接线视图</button>
      </div>
      <button ref={expandButton} className="secondary" aria-pressed={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? "收起画布" : "展开画布"}</button>
      <button className="secondary" disabled={!boxes.length} onClick={() => { pendingAutoFit.current = false; onAutoFitSettled?.(); fitAll(); }}>适合全图</button>
      <button className="secondary" aria-label="缩小画布" disabled={zoom <= .01} onClick={() => changeZoom(zoom / 1.25)}>−</button>
      <output aria-label="画布缩放比例">{Math.round(zoom * 100)}%</output>
      <button className="secondary" aria-label="放大画布" disabled={zoom >= 2} onClick={() => changeZoom(zoom * 1.25)}>＋</button>
      <button className="secondary" onClick={() => changeZoom(1)}>100%</button>
      <div className="wiring-search">
        <input type="search" value={query} aria-label="搜索器件、端口或连线标签" placeholder="搜索器件 / 端口 / 连线标签"
          onChange={event => setQuery(event.target.value)}
          onKeyDown={event => {
            if (event.key === "Enter") { event.preventDefault(); focusFirstSearchResult(); }
            else if (event.key === "Escape" && query) { event.preventDefault(); event.stopPropagation(); setQuery(""); }
          }} />
        {search && <output aria-label="搜索结果">{search.results.length ? `${search.results.length} 个匹配` : "无匹配"}</output>}
      </div>
    </div>
    <div className="wiring-canvas-help"><span>{canvasView === "overview" ? "总览视图：进入时适合全图，全部实际连线均保留。" : "接线视图：放大核对与调整，切回恢复原缩放和位置。"}两种视图共用同一份图，不折叠连线。</span><span>拖动器件标题 / 方向键移动 · 20 网格 · 坐标 0–20000</span><span>Ctrl＋滚轮缩放 · 空格＋拖动平移 · 普通滚轮滚动 · 视图不改接线数据；Esc 撤销或收起画布。</span><span>搜索按器件 / 端口 / 连线标签高亮，Enter 定位首个匹配，Esc 清空。</span></div>
    <div ref={viewport} className={`wiring-viewport${panning ? " panning" : ""}${highlighted.size ? " wiring-has-highlight" : ""}${search ? " wiring-search-active" : ""}`} aria-label="可滚动接线平面" tabIndex={0}
      onPointerDownCapture={event => {
        if (!spacePressed.current || event.button !== 0) return;
        event.preventDefault(); event.stopPropagation(); suppressClick.current = false;
        gesture.current = { kind: "pan", pointerId: event.pointerId, start: { x: event.clientX, y: event.clientY }, origin: { x: event.currentTarget.scrollLeft, y: event.currentTarget.scrollTop }, moved: false };
        captureTarget.current = event.currentTarget; event.currentTarget.setPointerCapture?.(event.pointerId); setPanning(true);
      }}
      onPointerMove={move} onPointerUp={finish} onPointerCancel={cancel}
      onLostPointerCapture={() => { if (gesture.current) cancel(); }}
      onClickCapture={event => { if (suppressClick.current && event.detail !== 0) { suppressClick.current = false; event.stopPropagation(); event.preventDefault(); } }}>
      <div className="wiring-scaled-area" style={{ width: width * zoom, height: height * zoom }}>
      <div ref={plane} className="wiring-plane" style={{ width, height, transform: `scale(${zoom})` }} data-testid="wiring-plane" data-zoom={zoom}>
        <svg className="wiring-lines" width={width} height={height} aria-label="物理端口连线">
          {layout.segments.filter(segment => sharedNets.has(segment.netKey)).map(segment => {
            const edge = graph.edges.find(item => item.id === segment.edgeIds[0])!;
            const selected = segment.edgeIds.some(id => highlighted.has(id));
            return <g key={segment.id} className={`wiring-line wiring-shared-segment signal-${edge.signal_type}${selected ? " selected" : ""}${search && segment.edgeIds.some(id => search.edges.has(id)) ? " wiring-search-match" : ""}`} data-edge-ids={JSON.stringify(segment.edgeIds)}>
              <path className="wiring-line-stroke" d={segment.path} />
              <path className="wiring-line-hit" d={segment.path} role="button" tabIndex={locked ? -1 : 0} aria-disabled={locked} aria-label={segment.edgeIds.length > 1 ? `选择公共段：${segment.edgeIds.length} 条支路` : `选择支路：${edgeLabel(edge)}`}
                onPointerEnter={() => hoverEdges(segment.edgeIds)} onPointerLeave={() => hoverEdges([])}
                onClick={() => chooseEdges(segment.edgeIds)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); chooseEdges(segment.edgeIds); } }} />
            </g>;
          })}
          {layout.junctions.map(junction => <circle key={junction.id} className={`wiring-junction signal-${graph.edges.find(edge => edge.id === junction.edgeIds[0])?.signal_type}${junction.edgeIds.some(id => highlighted.has(id)) ? " selected" : ""}${search && junction.edgeIds.some(id => search.edges.has(id)) ? " wiring-search-match" : ""}`} data-edge-ids={JSON.stringify(junction.edgeIds)} cx={junction.x} cy={junction.y} r={4} />)}
          {routes.map(({ edge, route }) => {
            if (!route || route.blocked) return null;
            const shared = sharedEdgeIds.has(edge.id);
            return <g key={edge.id} className={`wiring-line signal-${edge.signal_type}${highlighted.has(edge.id) ? " selected" : ""}${search?.edges.has(edge.id) ? " wiring-search-match" : ""}`} data-edge-id={edge.id}>
              {!shared && <path className="wiring-line-stroke" d={route.path} />}
              <path className={`wiring-line-hit${shared ? " wiring-line-keyboard" : ""}`} d={route.path} role="button" tabIndex={locked ? -1 : 0} aria-disabled={locked} aria-label={`选择连线：${edgeLabel(edge)}`}
                onPointerEnter={() => hoverEdges([edge.id])} onPointerLeave={() => hoverEdges([])}
                onClick={() => chooseEdges([edge.id])} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); chooseEdges([edge.id]); } }} />
              <text data-edge-label={edge.id} x={route.label.x} y={route.label.y} textAnchor="middle">{wireText(edge)}</text>
            </g>;
          })}
          {preview && !preview.blocked && <path className="wiring-line-preview" d={preview.path} />}
          {reconnectPreview && <path className="wiring-line-preview wiring-reconnect-preview" d={reconnectPreview} />}
        </svg>
        {graph.nodes.map(node => {
          const device = node.device; const position = moving?.id === device.id ? moving.position : node;
          // 即使收到旧的矛盾数据，已有连线的端口仍留在两侧，不会被未用折叠吞掉。
          const unused = device.ports.filter(port => !usedEndpoints.has(endpointKey(device.id, port.id)));
          const visiblePorts = device.ports.filter(port => !unused.includes(port));
          const left = visiblePorts.filter(port => directionOf(node, port) !== "out");
          const right = visiblePorts.filter(port => directionOf(node, port) === "out");
          const expanded = expandedUnused.includes(device.id);
          const internalPorts = node.internal_ports ?? [];
          const internalInputs = internalPorts.filter(port => port.direction === "in");
          const internalOutputs = internalPorts.filter(port => port.direction === "out");
          const settings = node.settings ?? [];
          const internalBox = internal[device.id];
          const internalLines = internalRoutes.get(device.id) ?? [];
          return <article key={device.id} data-node-id={device.id} className={`wiring-node${selectedDevice === device.id ? " selected" : ""}${search?.devices.has(device.id) ? " wiring-search-match" : ""}`} style={{ left: position.x, top: position.y, transform: `translate(${PLANE_INSET}px, ${PLANE_INSET}px)` }} role="group" aria-label={`器件快照：${device.name}`}>
            <button className="wiring-node-title" disabled={locked} aria-label={`移动器件：${device.name}`} onClick={() => onSelectDevice(device.id)}
              onPointerDown={event => { if (locked || event.button !== 0) return; onSelectDevice(device.id); begin(event, { kind: "node", pointerId: event.pointerId, start: localPoint(event.clientX, event.clientY), moved: false, deviceId: device.id, origin: { x: node.x, y: node.y } }); }}
              onKeyDown={event => {
                const step = { ArrowLeft: [-20, 0], ArrowRight: [20, 0], ArrowUp: [0, -20], ArrowDown: [0, 20] }[event.key];
                if (!locked && step) { event.preventDefault(); onSelectDevice(device.id); onMove(device.id, snap(node.x + step[0]), snap(node.y + step[1])); }
              }}><strong>{device.name || "未命名器件"}</strong><span>{device.model || "型号待确认"}</span></button>
            {profileBadges[device.id]?.length ? <div className="wiring-node-profile-badges" aria-label={`${device.name}与设备端口档案差异`}>
              {profileBadges[device.id]!.map(badge => <span key={badge} className={`wiring-profile-badge ${badge}`}>{wiringSyncBadgeLabel[badge]}</span>)}
            </div> : null}
            <div className="wiring-node-ports wiring-inputs" aria-label={`${device.name}左侧端口`}>{left.map(port => renderPort(node, port, "left"))}</div>
            <div className="wiring-node-ports wiring-outputs" aria-label={`${device.name}右侧端口`}>{right.map(port => renderPort(node, port, "right"))}</div>
            {unused.length > 0 && <div className="wiring-unused-ports">
              <button className="wiring-unused-toggle" disabled={locked} aria-label={`未用端口：${device.name}（${unused.length}）`} aria-expanded={expanded}
                onClick={() => setExpandedUnused(previous => expanded ? previous.filter(id => id !== device.id) : [...previous, device.id])}>未用 {unused.length} · {expanded ? "收起" : "展开"}</button>
              {expanded && <div className="wiring-unused-list" aria-label={`${device.name}未用端口`}>{unused.map(port => <div key={port.id} className="wiring-unused-item"><span>{portName(port)}</span>{usageToggle(node, port)}</div>)}</div>}
            </div>}
            {internalPorts.length > 0 && <div className="wiring-internal-section" role="group" aria-label={`${device.name}内部信号（非线缆）`}>
              {internalBox && internalLines.length > 0 && <svg className="wiring-internal-lines" width={internalBox.width} height={internalBox.height}
                style={{ left: -internalBox.section.x, top: -internalBox.section.y }} aria-label={`${device.name}内部信号连线`}>
                {internalLines.map(line => {
                  const edge = graph.edges.find(item => item.id === line.id);
                  if (!edge) return null;
                  return <g key={line.id} className={`wiring-line signal-internal${highlighted.has(edge.id) ? " selected" : ""}${search?.edges.has(edge.id) ? " wiring-search-match" : ""}`} data-edge-id={edge.id} data-internal-route="true">
                    <title>{edgeLabel(edge)}</title>
                    <path className="wiring-line-stroke" d={line.path} />
                    <path className="wiring-line-hit" d={line.path} role="button" tabIndex={locked ? -1 : 0} aria-disabled={locked} aria-label={`选择连线：${edgeLabel(edge)}`}
                      onPointerEnter={() => hoverEdges([edge.id])} onPointerLeave={() => hoverEdges([])}
                      onClick={() => chooseEdges([edge.id])} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); chooseEdges([edge.id]); } }} />
                  </g>;
                })}
              </svg>}
              <div className="wiring-internal-heading">内部信号（非线缆）<small>仅限同一器件内部连接</small></div>
              <div className="wiring-node-ports wiring-internal-inputs" aria-label={`${device.name}内部输入`}>
                <span className="wiring-internal-direction">内部输入</span>{internalInputs.map(port => renderInternalPort(node, port, "left"))}
              </div>
              <div className="wiring-node-ports wiring-internal-outputs" aria-label={`${device.name}内部输出`}>
                <span className="wiring-internal-direction">内部输出</span>{internalOutputs.map(port => renderInternalPort(node, port, "right"))}
              </div>
            </div>}
            {settings.length > 0 && <div className="wiring-node-settings" role="group" aria-label={`${device.name}图内设置（仅记录）`}>
              <div className="wiring-settings-heading">图内设置<small>仅记录，不下发硬件</small></div>
              {settings.map(setting => <label key={setting.id}><span>{setting.name}</span><select disabled={locked} value={setting.value ?? ""}
                aria-label={`图内设置：${device.name} / ${setting.name}`} onChange={event => { if (!locked) onSettingChange(device.id, setting.id, event.target.value || null); }}>
                <option value="">未记录</option>{setting.options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select></label>)}
            </div>}
          </article>;
        })}
        {reconnectEdge && (["source", "target"] as const).map(end => {
          const anchor = anchors[keyOf(reconnectEdge[end])];
          if (!anchor) return null;
          return <button key={end} type="button" className="wiring-reconnect-handle" disabled={locked}
            style={{ left: anchor.x, top: anchor.y }}
            data-reconnect-edge={reconnectEdge.id} data-reconnect-end={end}
            aria-label={`拖动改接${end === "source" ? "起点" : "终点"}：${edgeLabel(reconnectEdge)}`}
            title="拖到另一个端口改接；标签与光电类型保持不变，Esc 取消"
            onClick={event => event.stopPropagation()}
            onPointerDown={event => {
              if (locked || event.button !== 0) return;
              setFirstPort(null); setCursor(null);
              begin(event, { kind: "edge", pointerId: event.pointerId, start: localPoint(event.clientX, event.clientY), moved: false,
                edgeId: reconnectEdge.id, end, fixed: reconnectEdge[end === "source" ? "target" : "source"], internal: reconnectEdge.signal_type === "internal" });
            }} />;
        })}
        {!graph.nodes.length && <p className="wiring-canvas-empty">此图尚无器件。手动加入档案快照或空白器件；不会自动绘制真实装置连接。</p>}
      </div>
      </div>
    </div>
    {branchChoices.length > 0 && <div className="wiring-branch-choices" role="group" aria-label="公共段支路选择">
      <strong>此公共段包含 {branchChoices.length} 条支路，请明确选择：</strong>
      {graph.edges.filter(edge => branchChoices.includes(edge.id)).map(edge => <button key={edge.id} className="secondary" disabled={locked} onClick={() => chooseEdges([edge.id])}>{edgeLabel(edge)}</button>)}
      <button className="secondary" onClick={() => setBranchChoices([])}>关闭支路选择</button>
    </div>}
    {focusedEdges.length > 0 && <div className="wiring-path-details" role="status" aria-label="高亮连线路径">{focusedEdges.map(edge => <span key={edge.id}>{edgeLabel(edge)}</span>)}</div>}
    {blockedRoutes.length > 0 && <div className="wiring-warning" role="alert">有 {blockedRoutes.length} 条连线因器件重叠或端口外侧被挡，暂时无法绕行；记录与端口未删除，请移开器件。可在下方连线列表选择：{blockedRoutes.map(({ edge }) => <button className="secondary" disabled={locked} key={edge.id} onClick={() => onSelectEdge(edge.id)}>{endpointLabel(edge.source)} ↔ {endpointLabel(edge.target)}</button>)}</div>}
    {firstPort && <div className="wiring-connect-status" role="status">起点：{endpointLabel(firstPort)}。请选择另一个端口。<button className="secondary" disabled={locked} onClick={cancel}>取消连线</button></div>}
    <p className="wiring-legend"><span className="signal-optical">━━ 光学连线</span><span className="signal-electrical">┄┄ 电学连线</span><span className="signal-internal">━·━ 内部信号（非线缆）</span><span>● 同一输出的分支点；普通交叉无点、并不相连。悬停或选择高亮完整路径与两端端口；公共段可选具体支路。记录不是安全判定。</span></p>
  </section>;
}
