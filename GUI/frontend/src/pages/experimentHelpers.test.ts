import { describe, expect, it } from "vitest";

import type { SchemaField } from "../types/api";
import {
  defaultParameterLayout,
  historyParameterValues,
  loadLocalParameterLayout,
  parseArrayValue,
  preflightMessage,
  sameParameterLayout,
  validatedParameterLayout,
} from "./experimentHelpers";

it("预检显示模块及请求/预计速率，并兼容旧接口", () => {
  expect(preflightMessage({ ok: true, errors: [], acquisition: [{ stage: "噪声", acquisition_module: "scope", requested_rate_Sa_s: 200000, estimated_rate_Sa_s: 234375 }] }, "预检通过"))
    .toContain("噪声：SCOPE，请求 200 kSa/s，预计 234.375 kSa/s");
  expect(preflightMessage({ ok: true, errors: [] }, "预检通过")).toBe("预检通过");
  expect(preflightMessage({ ok: false, errors: ["缺少 MF-DIG"] }, "预检通过")).toBe("缺少 MF-DIG");
});

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

it("历史填回先取旧等待最大值，新键优先且不带回判稳字段", () => {
  const fields = [{ ...field("SYSTEM_SETTLE_S", "basic"), legacy_defaults: { CONFIG_WAIT: 0.2, OFF_WAIT: 0.5 } }];
  expect(historyParameterValues(fields, { CONFIG_WAIT: 0.7, OFF_WAIT: 0.3, TEMPERATURE_TIMEOUT_S: 100 }))
    .toEqual({ SYSTEM_SETTLE_S: 0.7 });
  expect(historyParameterValues(fields, { CONFIG_WAIT: 0.7 }))
    .toEqual({ SYSTEM_SETTLE_S: 0.7 });
  expect(historyParameterValues(fields, { SYSTEM_SETTLE_S: 0, OFF_WAIT: 10 }))
    .toEqual({ SYSTEM_SETTLE_S: 0 });
  expect(historyParameterValues(fields, { OFF_WAIT: -1 })).toEqual({ SYSTEM_SETTLE_S: null });
  expect(historyParameterValues(fields, { OFF_WAIT: null })).toEqual({ SYSTEM_SETTLE_S: null });
});

it("历史缺少补偿映射时使用模型声明的兼容值，不继承当前默认补偿", () => {
  const factors: SchemaField = { ...field("CONTROL_SCALE_FACTORS", "basic"), type: "object",
    default: { v1: 1.4 }, legacy_default: {} };
  expect(historyParameterValues([factors], {})).toEqual({ CONTROL_SCALE_FACTORS: {} });
  expect(historyParameterValues([factors], { CONTROL_SCALE_FACTORS: { v1: .8 } }))
    .toEqual({ CONTROL_SCALE_FACTORS: { v1: .8 } });
});

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

describe("sameParameterLayout", () => {
  it("分组和组内顺序都一致时为 true", () => {
    expect(sameParameterLayout(
      { basic: ["a", "c"], advanced: ["b"] },
      { basic: ["a", "c"], advanced: ["b"] },
    )).toBe(true);
  });

  it("参数换组或换序后为 false", () => {
    expect(sameParameterLayout(
      { basic: ["a", "c"], advanced: ["b"] },
      { basic: ["a"], advanced: ["c", "b"] },
    )).toBe(false);
    expect(sameParameterLayout(
      { basic: ["a", "c"], advanced: ["b"] },
      { basic: ["c", "a"], advanced: ["b"] },
    )).toBe(false);
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
