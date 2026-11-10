import type { components } from "../../types/openapi";

type DevicePortProfile = components["schemas"]["DevicePortProfile"];

export type PlacementRect = { left: number; top: number; right: number; bottom: number };
export type PlacementPoint = { x: number; y: number };
export type PlacementSize = { width: number; height: number };

// 与 .wiring-node 的 248px 宽度、20px 吸附网格一致。
export const PLACEMENT_STEP = 20;
export const PLACEMENT_GAP = 20;
export const NODE_WIDTH = 248;
const PLACEMENT_LIMIT = 20000;
const MIN_NODE_HEIGHT = 96;
const DEFAULT_NODE_HEIGHT = 96;

/** 新器件的粗略外框：端口按左右两列估算，内部区与图内设置各自追加行高。 */
export function nodePlacementSize(device: Pick<DevicePortProfile, "ports">, internalPorts = 0, settings = 0): PlacementSize {
  if (!device.ports.length && !internalPorts && !settings) return { width: NODE_WIDTH, height: DEFAULT_NODE_HEIGHT };
  const portRows = Math.ceil(device.ports.length / 2);
  const internalRows = internalPorts > 0 ? Math.ceil(internalPorts / 2) + 1 : 0;
  const height = 80 + portRows * 40 + internalRows * 30 + (settings > 0 ? 40 + settings * 34 : 0);
  return { width: NODE_WIDTH, height: Math.max(MIN_NODE_HEIGHT, height) };
}

/** 从 origin 起按行扫描网格空位；命中已有外框时直接跳到该框右侧，避免逐格试探。 */
export function findFreeSlot(boxes: PlacementRect[], size: PlacementSize, origin: PlacementPoint = { x: 0, y: 0 }, options: { gap?: number; step?: number; limit?: number } = {}): PlacementPoint {
  const gap = options.gap ?? PLACEMENT_GAP;
  const step = Math.max(1, options.step ?? PLACEMENT_STEP);
  const limit = Math.max(step, options.limit ?? PLACEMENT_LIMIT);
  const width = Math.min(limit, Math.max(step, size.width));
  const height = Math.min(limit, Math.max(step, size.height));
  const snapUp = (value: number) => Math.ceil(value / step) * step;
  const start = { x: Math.max(0, Math.floor(origin.x / step) * step), y: Math.max(0, Math.floor(origin.y / step) * step) };
  for (let y = start.y; y + height <= limit; y = snapUp(y + step)) {
    for (let x = start.x; x + width <= limit;) {
      const hit = boxes.find(box => x < box.right + gap && x + width + gap > box.left && y < box.bottom + gap && y + height + gap > box.top);
      if (!hit) return { x, y };
      x = snapUp(Math.max(x + step, hit.right + gap));
    }
  }
  // 极端情况下没有可用网格时，落到已占用区域的下一行，仍保持网格吸附。
  const bottom = Math.max(start.y, ...boxes.map(box => box.bottom + gap));
  return { x: start.x, y: Math.max(0, Math.min(snapUp(bottom), limit - height)) };
}
