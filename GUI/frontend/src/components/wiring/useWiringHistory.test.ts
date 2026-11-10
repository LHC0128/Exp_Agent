import { describe, expect, it } from "vitest";
import { WiringHistory, WIRING_HISTORY_LIMIT } from "./useWiringHistory";

describe("WiringHistory", () => {
  it("同一控件在防抖窗口内连续修改合并为一步，窗口外重新开始", () => {
    const history = new WiringHistory<string>();
    history.record("site", "初始", "device:name", 1000);
    expect(history.record("site", "初始a", "device:name", 1200)).toBe(false);
    expect(history.record("site", "初始ab", "device:name", 1499)).toBe(false);
    expect(history.record("site", "初始abc", "device:name", 2100)).toBe(true);
    expect(history.undoDepth()).toBe(2);

    expect(history.undo("site", "初始abcd")).toBe("初始abc");
    expect(history.undo("site", "初始abc")).toBe("初始");
    expect(history.canUndo("site")).toBe(false);
  });

  it("不同控件或未声明合并键的修改各自成步，且按图隔离", () => {
    const history = new WiringHistory<string>();
    history.record("site", "s0", "device:name", 1000);
    history.record("site", "s1", "device:model", 1010);
    history.record("site", "s2", null, 1020);
    history.record("site", "s3", null, 1030);
    history.record("target:a", "t0", "device:name", 1040);
    expect(history.undoDepth()).toBe(5);

    expect(history.undo("site", "current")).toBe("s3");
    expect(history.undo("site", "s3")).toBe("s2");
    expect(history.undo("site", "s2")).toBe("s1");
    expect(history.canUndo("target:a")).toBe(true);
    expect(history.undo("target:a", "t1")).toBe("t0");
    expect(history.canUndo("target:a")).toBe(false);
  });

  it(`最多保留 ${WIRING_HISTORY_LIMIT} 步，超出后丢弃最早快照`, () => {
    const history = new WiringHistory<string>();
    for (let index = 0; index < WIRING_HISTORY_LIMIT + 10; index += 1) history.record("site", `s${index}`, null, index);
    expect(history.undoDepth()).toBe(WIRING_HISTORY_LIMIT);

    for (let index = WIRING_HISTORY_LIMIT + 9; index >= 10; index -= 1) expect(history.undo("site", "current")).toBe(`s${index}`);
    expect(history.canUndo("site")).toBe(false);
  });

  it("撤销后可重做最新状态，新修改清空重做", () => {
    const history = new WiringHistory<string>();
    history.record("site", "a", null, 1000);
    history.record("site", "b", null, 1010);
    expect(history.undo("site", "c")).toBe("b");
    expect(history.redo("site", "b")).toBe("c");
    expect(history.undo("site", "c")).toBe("b");

    history.record("site", "b", null, 1020);
    expect(history.canRedo("site")).toBe(false);
    expect(history.undo("site", "d")).toBe("b");
  });

  it("清空后不再提供撤销或重做", () => {
    const history = new WiringHistory<string>();
    history.record("site", "a", null, 1000);
    history.undo("site", "b");
    history.clear();
    expect(history.canUndo("site")).toBe(false);
    expect(history.canRedo("site")).toBe(false);
    expect(history.undoDepth()).toBe(0);
  });
});
