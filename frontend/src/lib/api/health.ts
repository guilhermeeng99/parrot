import { apiJson } from "./client";
import type { Health } from "./types";

export type { Health };

/** Liveness probe — mirrors the Rust supervisor's check. */
export const getHealth = () => apiJson<Health>("/healthz");
