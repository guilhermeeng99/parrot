import { apiJson } from "./client";
import type { EngineStatus } from "./types";

export type { EngineStatus };

/** Read the single fixed engine + auto-detected device. */
export const getEngineStatus = () => apiJson<EngineStatus>("/engine/status");
