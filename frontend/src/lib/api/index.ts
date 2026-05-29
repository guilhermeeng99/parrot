// Barrel for the typed IPC layer. Components import from "$lib/api".
export * from "./types";
export * from "./client";
export * from "./health";
export * from "./engine";
export * from "./generate";
export * from "./profiles";
export * from "./history";
export * from "./setup";
export * from "./settings";
export * from "./ttsStream";
export * from "./native";
export { whenSidecarReady, SidecarFailedError } from "./sidecar";
