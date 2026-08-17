import { describe, expect, it } from "vitest";

import type { SchemaField } from "../types/api";
import {
  defaultParameterLayout,
  loadLocalParameterLayout,
  parseArrayValue,
  validatedParameterLayout,
} from "./experimentHelpers";

function field(name: string, group: string): SchemaField {
  return {
    name,
    label: name,
    type: "number",
    unit: "",
    group,
    default: 0,
  };
}

describe("defaultParameterLayout", () => {
  it("按 field.group 拆分为基础与高级参数", () => {
    const layout = defaultParameterLayout([
      field("a", "basic"),
      field("b", "advanced"),
      field("c", "basic"),
    ]);
    expect(layout).toEqual({ basic: ["a", "c"], advanced: ["b"] });
  });
});

describe("validatedParameterLayout", () => {
  const fields = [field("a", "basic"), field("b", "advanced"), field("c", "basic")];

  it("接受完整且无重复的布局", () => {
    expect(validatedParameterLayout(fields, { basic: ["c", "a"], advanced: ["b"] }))
      .toEqual({ basic: ["c", "a"], advanced: ["b"] });
  });

  it("拒绝缺字段的布局", () => {
    expect(validatedParameterLayout(fields, { basic: ["a"], advanced: ["b"] })).toBeUndefined();
  });

  it("拒绝包含未知字段的布局", () => {
    expect(validatedParameterLayout(fields, { basic: ["a", "c", "zzz"], advanced: [] })).toBeUndefined();
  });

  it("拒绝重复字段的布局", () => {
    expect(validatedParameterLayout(fields, { basic: ["a", "a", "c"], advanced: [] })).toBeUndefined();
  });

  it("拒绝非对象输入", () => {
    expect(validatedParameterLayout(fields, "bad")).toBeUndefined();
  });
});

describe("loadLocalParameterLayout", () => {
  const fields = [field("a", "basic"), field("b", "advanced"), field("c", "basic")];

  it("无本地存储时回退默认布局", () => {
    window.localStorage.clear();
    expect(loadLocalParameterLayout(fields, "test:key")).toEqual(
      { basic: ["a", "c"], advanced: ["b"] },
    );
  });

  it("本地布局缺失字段时自动补齐", () => {
    window.localStorage.setItem("test:key", JSON.stringify({ basic: ["a"], advanced: [] }));
    expect(loadLocalParameterLayout(fields, "test:key")).toEqual(
      { basic: ["a", "c"], advanced: ["b"] },
    );
  });

  it("损坏的本地数据回退默认布局", () => {
    window.localStorage.setItem("test:key", "{bad json");
    expect(loadLocalParameterLayout(fields, "test:key")).toEqual(
      { basic: ["a", "c"], advanced: ["b"] },
    );
  });
});

describe("parseArrayValue", () => {
  it("解析逗号分隔的数字数组", () => {
    expect(parseArrayValue("1, 2.5, 3", [0])).toEqual({ value: [1, 2.5, 3] });
  });

  it("字符串默认值时返回字符串数组", () => {
    expect(parseArrayValue("a, b", ["x"])).toEqual({ value: ["a", "b"] });
  });

  it("空输入返回 null", () => {
    expect(parseArrayValue("", [0])).toBeNull();
    expect(parseArrayValue(", ,", [0])).toBeNull();
  });

  it("数字数组中混入非法值返回 null", () => {
    expect(parseArrayValue("1, abc", [0])).toBeNull();
  });
});
