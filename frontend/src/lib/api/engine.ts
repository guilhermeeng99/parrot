import { apiJson } from "./client";

export interface EngineStatus {
  /** Always "omnivoice" — Parrot ships one fixed engine. */
  active: string;
  /** Auto-detected compute device: cuda | mps | cpu (ROCm hardware reports as cuda). */
  device: string;
}

export const getEngineStatus = () => apiJson<EngineStatus>("/engine/status");
