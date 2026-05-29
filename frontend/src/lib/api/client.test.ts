import { afterEach, describe, expect, it, vi } from "vitest";
import { __resetApiBase, ApiError, apiBase, apiFetch, apiJson, errMsg } from "./client";

// Intercept the dynamic `import("@tauri-apps/api/core")` inside resolvePort().
const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));

afterEach(() => {
  __resetApiBase(); // base URL is cached once resolved — reset between cases
  invokeMock.mockReset();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("apiBase port resolution", () => {
  it("falls back to loopback :3900 outside Tauri with no override", async () => {
    expect(await apiBase()).toBe("http://127.0.0.1:3900");
  });

  it("honors the VITE_PARROT_PORT build-time override outside Tauri", async () => {
    vi.stubEnv("VITE_PARROT_PORT", "4555");
    expect(await apiBase()).toBe("http://127.0.0.1:4555");
  });

  it("learns the runtime port from backend_port inside Tauri", async () => {
    vi.stubGlobal("window", { __TAURI_INTERNALS__: {} });
    invokeMock.mockResolvedValue(7788);
    expect(await apiBase()).toBe("http://127.0.0.1:7788");
    expect(invokeMock).toHaveBeenCalledWith("backend_port");
  });

  it("falls back when the backend_port command throws", async () => {
    vi.stubGlobal("window", { __TAURI_INTERNALS__: {} });
    vi.stubEnv("VITE_PARROT_PORT", "4555");
    invokeMock.mockRejectedValue(new Error("no such command"));
    expect(await apiBase()).toBe("http://127.0.0.1:4555");
  });
});

describe("ApiError", () => {
  it("carries the path + status and a readable message", () => {
    const err = new ApiError("/engine/status", 503);
    expect(err).toBeInstanceOf(Error);
    expect(err.path).toBe("/engine/status");
    expect(err.status).toBe(503);
    expect(err.message).toContain("503");
  });
});

describe("apiJson", () => {
  it("returns parsed JSON on a 2xx response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ status: "ok" }), { status: 200 })),
    );
    expect(await apiJson<{ status: string }>("/healthz")).toEqual({ status: "ok" });
  });

  it("throws ApiError carrying the status on a non-2xx response", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 503 })));
    await expect(apiJson("/engine/status")).rejects.toMatchObject({
      name: "ApiError",
      status: 503,
    });
  });
});

describe("apiFetch error envelope", () => {
  it("parses {detail} on a 400 into ApiError.detail and the message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "bad input" }), { status: 400 })),
    );
    const err = await apiFetch("/generate").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(400);
    expect(err.detail).toBe("bad input");
    expect(err.message).toContain("bad input");
  });

  it("falls back to {error} when there is no {detail}", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ error: "engine offline" }), { status: 503 })),
    );
    const err = await apiFetch("/engine/status").catch((e) => e);
    expect(err.detail).toBe("engine offline");
  });

  it("falls back to raw text when the body is not JSON", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("plain failure", { status: 500 })));
    const err = await apiFetch("/healthz").catch((e) => e);
    expect(err.detail).toBe("plain failure");
  });
});

describe("errMsg", () => {
  it("returns the ApiError detail string when present", () => {
    expect(errMsg(new ApiError("/generate", 400, "too long"))).toBe("too long");
  });

  it("falls back to the Error message for a plain Error", () => {
    expect(errMsg(new Error("kaboom"))).toBe("kaboom");
  });

  it("stringifies a non-Error value", () => {
    expect(errMsg("just a string")).toBe("just a string");
  });
});
