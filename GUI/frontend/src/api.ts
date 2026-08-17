export type ApiError = Error & { code?: string };

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const hasBody = options.body !== undefined && options.body !== null;
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string> | undefined),
  };
  if (hasBody) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    ...options,
    cache: "no-store",
    headers,
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") {
        message = body.detail;
      } else if (Array.isArray(body.detail)) {
        const parts = (body.detail as unknown[])
          .map((item: unknown) => (
            item && typeof item === "object" && "msg" in item
              ? String((item as { msg?: unknown }).msg)
              : String(item)
          ))
          .filter(Boolean);
        message = parts.join("；") || `${response.status} ${response.statusText}`;
      } else if (body.detail !== undefined && body.detail !== null) {
        message = JSON.stringify(body.detail);
      }
    } catch {
      // 保留 HTTP 错误文本。
    }
    const error: ApiError = new Error(message);
    error.code = response.headers.get("X-Error-Code") ?? undefined;
    throw error;
  }
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    throw new Error(`API 返回了非 JSON 内容，请重启 GUI 后端后刷新页面：${path}`);
  }
  return response.json();
}

export type { Device, Job } from "./types/api";
