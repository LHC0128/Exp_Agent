// 从 openapi 派生的 ZAWClosedLoopRunSummary 别名。
// 后端 Pydantic model 总是为 static_calibration/parameters/artifacts 设置 default_factory，
// 所以运行时这三个字段不会出现 undefined，这里把 openapi 中的可选标记收窄为必选。
import type { ZAWClosedLoopRunSummary as OpenApiZAWClosedLoopRunSummary } from "../../types/api";

export type ZAWClosedLoopRunSummary = Omit<
  OpenApiZAWClosedLoopRunSummary,
  "static_calibration" | "parameters" | "artifacts"
> & {
  static_calibration: Record<string, unknown>;
  parameters: Record<string, unknown>;
  artifacts: Record<string, string>;
};
