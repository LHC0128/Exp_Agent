export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...options,
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body);
    } catch {
      // 保留 HTTP 错误文本。
    }
    throw new Error(message);
  }
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    throw new Error(`API 返回了非 JSON 内容，请重启 GUI 后端后刷新页面：${path}`);
  }
  return response.json();
}

export type Job = {
  id: string;
  kind: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  stage: string;
  percent: number | null;
  message: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  result?: unknown;
  error?: string;
  events: Array<{ timestamp: string; message: string; level: string }>;
};

export type Device = {
  id: string;
  type: "DG4000" | "DG900" | "SDS";
  label: string;
  short_resource: string;
  channels: Array<{ number: number; label: string; mapping_key: string; read_only: boolean }>;
};
