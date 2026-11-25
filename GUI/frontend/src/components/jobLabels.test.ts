import { describe, expect, it } from "vitest";
import { isScriptJob, kindLabel, scriptJobPath } from "./jobLabels";

describe("任务标签", () => {
  it("脚本任务标签取文件名，完整路径单独给出", () => {
    expect(kindLabel("script:scripts/a/b.py")).toBe("调试脚本 · b.py");
    expect(scriptJobPath("script:scripts/a/b.py")).toBe("scripts/a/b.py");
    expect(kindLabel("script:quick_test_scan.py")).toBe("调试脚本 · quick_test_scan.py");
    expect(isScriptJob("script:scripts/a/b.py")).toBe(true);
  });
  it("其他任务的标签不变，也没有脚本路径", () => {
    expect(kindLabel("experiment:demo")).toBe("实验运行");
    expect(kindLabel("session:demo")).toBe("分步会话");
    expect(kindLabel("analysis:demo:1010_测试")).toBe("离线分析");
    expect(kindLabel("comparison:demo")).toBe("对比分析");
    expect(kindLabel("clock-sync")).toBe("时钟同步");
    expect(kindLabel("load-init")).toBe("输出负载初始化");
    expect(kindLabel("update:gui")).toBe("软件更新");
    expect(kindLabel("unknown-kind")).toBe("unknown-kind");
    expect(scriptJobPath("experiment:demo")).toBeUndefined();
    expect(isScriptJob("experiment:demo")).toBe(false);
  });
});