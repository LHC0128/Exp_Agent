import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { ExperimentDefinition } from "../types/api";
import { PageHead } from "./PageHead";

afterEach(cleanup);
it("实验介绍与程序路径默认折叠，名称和安全提示始终可见", () => {
  const experiment: ExperimentDefinition = {
    id: "demo", title: "演示实验", description: "完整实验介绍", category: "measurement", category_label: "测量",
    family: "demo", variant: "demo", archived: false, execution_mode: "typed_workflow",
    acquisition_program: "experiments/demo.py", analysis_program: "experiments/demo_plot.py",
    required_devices: [], required_mapping_keys: [], wiring_notes: [], safety_notes: ["确认接线后再开始"],
    supports_cancel: true, can_analyze: true,
  };
  render(<PageHead eyebrow="实验" title={experiment.title} description={experiment.description} experiment={experiment} />);
  expect(screen.getByRole("heading", { name: "演示实验" })).toBeVisible();
  expect(screen.getByRole("note", { name: "实验安全提示" })).toBeVisible();
  expect(screen.getByText("确认接线后再开始")).toBeVisible();
  expect(screen.getByText("完整实验介绍")).not.toBeVisible();
  fireEvent.click(screen.getByText("实验说明"));
  expect(screen.getByText("完整实验介绍")).toBeVisible();
  expect(screen.getByText("experiments/demo.py")).toBeVisible();
  expect(screen.getByText("experiments/demo_plot.py")).toBeVisible();
});
it("非实验页面的简短说明不折叠", () => {
  render(<PageHead eyebrow="系统" title="仪器控制" description="管理设备" />);
  expect(screen.getByText("管理设备")).toBeVisible();
  expect(screen.queryByText("实验说明")).not.toBeInTheDocument();
});
