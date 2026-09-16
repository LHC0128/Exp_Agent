import { describe, expect, it } from "vitest";
import { formatEta } from "./formatEta";

describe("formatEta", () => {
  it("分钟以内显示 MM:SS", () => {
    expect(formatEta(59.4)).toBe("00:59");
    expect(formatEta(125)).toBe("02:05");
  });
  it("超过一小时显示 H:MM:SS", () => {
    expect(formatEta(3725)).toBe("1:02:05");
  });
  it("无 ETA 或非法值返回空字符串", () => {
    expect(formatEta(null)).toBe("");
    expect(formatEta(undefined)).toBe("");
    expect(formatEta(Number.NaN)).toBe("");
  });
});
