import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { __resetApiBase } from "./client";
import { openTtsStream } from "./ttsStream";

// A controllable WebSocket stand-in. openTtsStream installs onopen/onerror/
// onmessage on the instance; tests drive those handlers by hand. The constructor
// records the last instance so a test can reach in and fire events.
class FakeWebSocket {
  static last: FakeWebSocket | null = null;
  url: string;
  binaryType = "blob";
  onopen: (() => void) | null = null;
  onerror: ((ev?: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  sent: string[] = [];
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.last = this;
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.closed = true;
  }
  // Test helpers.
  fireOpen() {
    this.onopen?.();
  }
  fireError() {
    this.onerror?.();
  }
  fireMessage(data: unknown) {
    this.onmessage?.({ data });
  }
}

beforeEach(() => {
  __resetApiBase();
  FakeWebSocket.last = null;
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
  __resetApiBase();
});

/** Spin the microtask queue until openTtsStream has built the socket + handler. */
async function waitForSocket(predicate: () => boolean): Promise<void> {
  for (let i = 0; i < 50 && !predicate(); i++) await Promise.resolve();
}

/** Open a stream and fire onopen once the open handler is installed. */
async function open(handlers = {}) {
  const p = openTtsStream(handlers);
  // apiBase() resolves through an async chain before the socket is created and
  // onopen is wired, so wait for that handler rather than a fixed tick count.
  await waitForSocket(() => Boolean(FakeWebSocket.last?.onopen));
  FakeWebSocket.last?.fireOpen();
  return p;
}

describe("openTtsStream connection lifecycle", () => {
  it("derives a ws:// URL from the http base and resolves on open", async () => {
    const stream = await open();
    expect(FakeWebSocket.last?.url).toBe("ws://127.0.0.1:3900/ws/tts");
    expect(FakeWebSocket.last?.binaryType).toBe("arraybuffer");
    expect(typeof stream.send).toBe("function");
  });

  it("rejects when an error fires before the socket opens", async () => {
    const p = openTtsStream({});
    await waitForSocket(() => Boolean(FakeWebSocket.last?.onerror));
    FakeWebSocket.last?.fireError();
    await expect(p).rejects.toThrow(/failed to open/i);
  });

  it("send JSON-encodes the request; close closes the socket", async () => {
    const stream = await open();
    stream.send({ text: "hi", seed: 7 });
    expect(FakeWebSocket.last?.sent[0]).toBe(JSON.stringify({ text: "hi", seed: 7 }));
    stream.close();
    expect(FakeWebSocket.last?.closed).toBe(true);
  });
});

describe("incoming frames", () => {
  it("routes an ArrayBuffer message to onChunk", async () => {
    const onChunk = vi.fn();
    await open({ onChunk });
    const buf = new ArrayBuffer(8);
    FakeWebSocket.last?.fireMessage(buf);
    expect(onChunk).toHaveBeenCalledWith(buf);
  });

  it("routes JSON start/done/error control frames", async () => {
    const onStart = vi.fn();
    const onDone = vi.fn();
    const onError = vi.fn();
    await open({ onStart, onDone, onError });
    const ws = FakeWebSocket.last;

    ws?.fireMessage(JSON.stringify({ type: "start", sample_rate: 24000, channels: 1, format: "pcm16" }));
    ws?.fireMessage(JSON.stringify({ type: "done", duration_s: 1, gen_time_s: 2, samples: 100 }));
    ws?.fireMessage(JSON.stringify({ type: "error", detail: "boom" }));

    expect(onStart).toHaveBeenCalledWith(expect.objectContaining({ sample_rate: 24000 }));
    expect(onDone).toHaveBeenCalledWith(expect.objectContaining({ samples: 100 }));
    expect(onError).toHaveBeenCalledWith("boom");
  });

  it("ignores a non-JSON text frame", async () => {
    const onStart = vi.fn();
    const onDone = vi.fn();
    const onError = vi.fn();
    await open({ onStart, onDone, onError });
    FakeWebSocket.last?.fireMessage("not json {");
    expect(onStart).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });
});
