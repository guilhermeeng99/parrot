// Optional low-latency streaming synthesis over ws://127.0.0.1:<port>/ws/tts
// (chunked PCM16 mono @ 24 kHz). The primary Speak path is POST /generate; this
// is for live preview. See docs/specs/ipc-contract.md §10.

import { apiBase } from "./client";

export interface WsTtsRequest {
  text: string;
  voice?: string; // profile_id, resolved server-side
  language?: string;
  speed?: number;
  instruct?: string;
  seed?: number;
}

export interface TtsStreamHandlers {
  onStart?: (info: { sample_rate: number; channels: number; format: string }) => void;
  onChunk?: (pcm: ArrayBuffer) => void;
  onDone?: (info: { duration_s: number; gen_time_s: number; samples: number }) => void;
  onError?: (detail: string) => void;
}

export interface TtsStream {
  send: (req: WsTtsRequest) => void;
  close: () => void;
}

/** Open the streaming-synthesis socket. Resolves once the socket is open. */
export async function openTtsStream(handlers: TtsStreamHandlers): Promise<TtsStream> {
  const base = await apiBase();
  const ws = new WebSocket(base.replace(/^http/, "ws") + "/ws/tts");
  ws.binaryType = "arraybuffer";

  ws.onmessage = (ev) => {
    if (ev.data instanceof ArrayBuffer) {
      handlers.onChunk?.(ev.data);
      return;
    }
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === "start") handlers.onStart?.(msg);
      else if (msg.type === "done") handlers.onDone?.(msg);
      else if (msg.type === "error") handlers.onError?.(msg.detail);
    } catch {
      // ignore non-JSON control frames
    }
  };
  ws.onerror = () => handlers.onError?.("The streaming connection failed.");

  await new Promise<void>((resolve, reject) => {
    ws.onopen = () => resolve();
    const prevErr = ws.onerror;
    ws.onerror = (e) => {
      reject(new Error("WebSocket failed to open"));
      if (typeof prevErr === "function") prevErr.call(ws, e);
    };
  });

  return {
    send: (req) => ws.send(JSON.stringify(req)),
    close: () => ws.close(),
  };
}
