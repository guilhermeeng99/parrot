import { apiJson } from "./client";

export interface Health {
  status: string;
}

/** Liveness probe — mirrors the Rust supervisor's check. */
export const getHealth = () => apiJson<Health>("/healthz");
