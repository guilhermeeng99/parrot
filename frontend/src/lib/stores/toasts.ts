import { writable } from "svelte/store";

// Transient corner notices. Non-error toasts auto-dismiss; errors stay until
// dismissed (design-system Toast store). One concern: messages are already
// user-safe (the sidecar redacts secrets before they reach a `detail`).
export type ToastLevel = "info" | "success" | "danger";

export interface Toast {
  id: number;
  message: string;
  level: ToastLevel;
}

let seq = 0;

function createToasts() {
  const { subscribe, update } = writable<Toast[]>([]);

  function dismiss(id: number) {
    update((list) => list.filter((t) => t.id !== id));
  }

  function push(message: string, level: ToastLevel = "info", ttlMs = 4000) {
    const id = ++seq;
    update((list) => [...list, { id, message, level }]);
    if (level !== "danger" && ttlMs > 0) setTimeout(() => dismiss(id), ttlMs);
    return id;
  }

  return {
    subscribe,
    dismiss,
    push,
    info: (m: string) => push(m, "info"),
    success: (m: string) => push(m, "success"),
    error: (m: string) => push(m, "danger", 0),
  };
}

export const toasts = createToasts();
