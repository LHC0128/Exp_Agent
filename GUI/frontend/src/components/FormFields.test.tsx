import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Field } from "./FormFields";

function getInput(container: HTMLElement): HTMLInputElement {
  const input = container.querySelector("input");
  if (!input) throw new Error("未找到输入框");
  return input as HTMLInputElement;
}

describe("Field 数字输入", () => {
  it("完整输入负数时回传解析后的数值", () => {
    const onChange = vi.fn();
    const { container } = render(<Field label="电流 (mA)" value={5} onChange={onChange} />);
    const input = getInput(container);
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "-1.5" } });
    expect(onChange).toHaveBeenLastCalledWith(-1.5);
  });

  it("小数中间态不提交，继续输入后提交完整数值", () => {
    const onChange = vi.fn();
    const { container } = render(<Field label="频率 (Hz)" value={5} onChange={onChange} />);
    const input = getInput(container);
    fireEvent.focus(input);
    // 浏览器在 "1." 中间态会报告空值（badInput）；非受控设计不做任何提交与重渲染，
    // 因此可见的 "1." 不会被清掉。jsdom 只能模拟空值上报，无法模拟可见编辑态。
    fireEvent.change(input, { target: { value: "" } });
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: "1.5" } });
    expect(onChange).toHaveBeenLastCalledWith(1.5);
  });

  it("非法输入失焦后回退到上次有效值", () => {
    const onChange = vi.fn();
    const { container } = render(<Field label="温度 (°C)" value={5} onChange={onChange} />);
    const input = getInput(container);
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "abc" } });
    fireEvent.blur(input);
    expect(input.value).toBe("5");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("外部数值更新且未聚焦时同步显示", () => {
    const onChange = vi.fn();
    const { container, rerender } = render(<Field label="电流" value={1} onChange={onChange} />);
    const input = getInput(container);
    rerender(<Field label="电流" value={2} onChange={onChange} />);
    expect(input.value).toBe("2");
  });

  it("聚焦编辑期间外部快照刷新不打断输入", () => {
    const onChange = vi.fn();
    const { container, rerender } = render(<Field label="电流" value={1} onChange={onChange} />);
    const input = getInput(container);
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "1.5" } });
    rerender(<Field label="电流" value={1.5} onChange={onChange} />);
    expect(input.value).toBe("1.5");
  });

  it("文本模式直接回传字符串并支持失焦回调", () => {
    const onChange = vi.fn();
    const onBlur = vi.fn();
    const { container } = render(
      <Field label="负载" value="INF" type="text" onChange={onChange} onBlur={onBlur} />,
    );
    const input = getInput(container);
    fireEvent.change(input, { target: { value: "50" } });
    expect(onChange).toHaveBeenCalledWith("50");
    fireEvent.blur(input);
    expect(onBlur).toHaveBeenCalled();
  });
});
