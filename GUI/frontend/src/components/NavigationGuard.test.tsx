import { useState } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, Link, Route, RouterProvider, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { NavigationGuard, useParameterGuard, useUnsavedChanges } from "./NavigationGuard";
import type { ParameterLayout, SchemaField } from "../types/api";

const layout: ParameterLayout = { basic: ["value"], advanced: [] };
function Editor() {
  const [value, setValue] = useState(1);
  const [saved, setSaved] = useState(1);
  const [applied, setApplied] = useState<number>();
  const [derived, setDerived] = useState(1);
  const [otherDirty, setOtherDirty] = useState(false);
  const fields: SchemaField[] = [
    { name: "value", label: "参数", type: "number", unit: "", group: "basic", default: saved },
    { name: "derived", label: "派生值", type: "number", unit: "", group: "basic", default: 1, read_only: true },
  ];
  useParameterGuard(fields, { value, derived }, layout, layout, applied === undefined ? undefined : { value: applied });
  useUnsavedChanges(otherDirty);
  const navigate = useNavigate();
  return <>
    <input aria-label="参数" type="number" value={value} onChange={event => setValue(Number(event.target.value))} />
    <button onClick={() => setSaved(value)}>保存成功</button>
    <button onClick={() => setApplied(value)}>应用到会话</button>
    <button onClick={() => setDerived(2)}>更新只读预览</button>
    <button onClick={() => setOtherDirty(true)}>修改另一个草稿</button>
    <button onClick={() => navigate(-1)}>后退</button>
    <Link to="/other">离开</Link><Link to="/edit?job=active">同页任务</Link>
  </>;
}
function start() {
  const router = createMemoryRouter([{ path: "*", element: <NavigationGuard><Routes>
    <Route path="/edit" element={<Editor />} /><Route path="/other" element={<h1>其他页面</h1>} />
  </Routes></NavigationGuard> }], { initialEntries: ["/other", "/edit"] });
  render(<RouterProvider router={router} />);
  return router;
}
beforeEach(() => { vi.spyOn(window, "confirm").mockReturnValue(false); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("未保存编辑拦截链接与浏览器后退；取消保留原值，确认才离开", async () => {
  const router = start();
  fireEvent.change(screen.getByLabelText("参数"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("link", { name: "离开" }));
  expect(window.confirm).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText("参数")).toHaveValue(2);
  await act(async () => router.navigate(-1));
  expect(window.confirm).toHaveBeenCalledTimes(2);
  vi.mocked(window.confirm).mockReturnValue(true);
  fireEvent.click(screen.getByRole("link", { name: "离开" }));
  expect(await screen.findByRole("heading", { name: "其他页面" })).toBeInTheDocument();
});
it("还原、成功保存、应用会话后不误拦截，只读预览也不算草稿", async () => {
  const router = start();
  const input = screen.getByLabelText("参数");
  fireEvent.change(input, { target: { value: "2" } });
  let event = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(true);
  fireEvent.change(input, { target: { value: "1" } });
  event = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(false);
  fireEvent.change(input, { target: { value: "3" } });
  fireEvent.click(screen.getByRole("button", { name: "保存成功" }));
  fireEvent.click(screen.getByRole("button", { name: "更新只读预览" }));
  fireEvent.click(screen.getByRole("link", { name: "离开" }));
  expect(await screen.findByRole("heading")).toHaveTextContent("其他页面");
  expect(window.confirm).not.toHaveBeenCalled();
  await act(async () => router.navigate("/edit"));
  fireEvent.change(screen.getByLabelText("参数"), { target: { value: "4" } });
  fireEvent.click(screen.getByRole("button", { name: "应用到会话" }));
  fireEvent.click(screen.getByRole("link", { name: "离开" }));
  expect(window.confirm).not.toHaveBeenCalled();
});
it("同页任务跳转不卸载编辑；另一个未保存草稿仍独立受保护", () => {
  start();
  fireEvent.change(screen.getByLabelText("参数"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("link", { name: "同页任务" }));
  expect(window.confirm).not.toHaveBeenCalled();
  expect(screen.getByLabelText("参数")).toHaveValue(2);
  fireEvent.click(screen.getByRole("button", { name: "修改另一个草稿" }));
  fireEvent.click(screen.getByRole("button", { name: "保存成功" }));
  fireEvent.click(screen.getByRole("link", { name: "离开" }));
  expect(window.confirm).toHaveBeenCalledTimes(1);
});
