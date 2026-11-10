import { describe, expect, it } from "vitest";
import { findFreeSlot, nodePlacementSize } from "./wiringPlacement";

const box = (left: number, top: number, right: number, bottom: number) => ({ left, top, right, bottom });

describe("findFreeSlot", () => {
  it("空图从原点开始并保持 20 网格", () => {
    expect(findFreeSlot([], { width: 248, height: 120 })).toEqual({ x: 0, y: 0 });
    expect(findFreeSlot([], { width: 248, height: 120 }, { x: 133, y: 47 })).toEqual({ x: 120, y: 40 });
  });

  it("跳过已有外框并保留间隙，同一行依次向右排列", () => {
    const first = box(0, 0, 248, 180);
    expect(findFreeSlot([first], { width: 248, height: 120 })).toEqual({ x: 280, y: 0 });
    const second = box(280, 0, 528, 180);
    expect(findFreeSlot([first, second], { width: 248, height: 120 })).toEqual({ x: 560, y: 0 });
  });

  it("从视口左上角开始扫描，已有空位不被跳过", () => {
    expect(findFreeSlot([box(1000, 800, 1248, 980)], { width: 248, height: 120 }, { x: 420, y: 260 })).toEqual({ x: 420, y: 260 });
  });

  it("宽外框整行占用时直接跳到其右侧，不逐格试探", () => {
    expect(findFreeSlot([box(0, 0, 1000, 180)], { width: 248, height: 120 })).toEqual({ x: 1020, y: 0 });
  });

  it("横向放不下时换到下一行", () => {
    expect(findFreeSlot([box(0, 0, 20000, 180)], { width: 248, height: 120 }, { x: 0, y: 0 })).toEqual({ x: 0, y: 200 });
  });

  it("器件尺寸按端口数增长，空白器件保持最小外框", () => {
    const blank = nodePlacementSize({ ports: [] });
    expect(blank).toEqual({ width: 248, height: 96 });
    const two = nodePlacementSize({ ports: [{}, {}] as never });
    const four = nodePlacementSize({ ports: [{}, {}, {}, {}] as never });
    expect(two.width).toBe(248);
    expect(two.height).toBeGreaterThan(blank.height);
    expect(four.height).toBeGreaterThan(two.height);
  });
});
