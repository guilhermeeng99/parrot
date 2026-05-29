import { apiDelete, apiJson, apiPost } from "./client";
import type { TokenState } from "./types";

export const getTokenState = () => apiJson<TokenState>("/settings/hf-token");

export const setToken = (token: string) =>
  apiPost<TokenState>("/settings/hf-token", { token });

export const clearToken = () => apiDelete<TokenState>("/settings/hf-token");
