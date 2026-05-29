import { afterEach, describe, expect, it, vi } from "vitest";
import { __resetApiBase, ApiError, apiBase, apiJson } from "./client";

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
