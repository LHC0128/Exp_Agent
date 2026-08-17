import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("api", () => {
  it("成功时返回 JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, { ok: true })));
    await expect(api<{ ok: boolean }>("/api/health")).resolves.toEqual({ ok: true });
  });

  it("把 FastAPI 422 detail 数组拼成可读消息", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(422, {
      detail: [{ msg: "缺少字段" }, { msg: "数值非法" }],
    })));
    const error = await api("/api/x").catch((reason: Error) => reason);
    expect(error).toBeInstanceOf(Error);
    expect((error as Error).message).toContain("缺少字段");
    expect((error as Error).message).toContain("数值非法");
  });

  it("从 X-Error-Code 响应头提取结构化错误码", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(
      409,
      { detail: "修订已变化" },
      { "X-Error-Code": "revision_conflict" },
    )));
    const error = await api("/api/device-library").catch((reason: Error) => reason);
    expect((error as Error & { code?: string }).code).toBe("revision_conflict");
  });

  it("有 body 时携带 JSON Content-Type，GET 请求不带", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(200, {})));
    vi.stubGlobal("fetch", fetchMock);
    await api("/api/jobs");
    const getInit = fetchMock.mock.calls[0][1] as RequestInit;
    expect((getInit.headers as Record<string, string>)["Content-Type"]).toBeUndefined();

    await api("/api/tools/keithley-waveform-convert", {
      method: "POST",
      body: JSON.stringify({ arbitrary_text: "1\n2\n" }),
    });
    const postInit = fetchMock.mock.calls[1][1] as RequestInit;
    expect((postInit.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("非 JSON 响应抛出可读错误", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>", {
      status: 200,
      headers: { "Content-Type": "text/html" },
    })));
    await expect(api("/api/x")).rejects.toThrow("非 JSON");
  });
});
