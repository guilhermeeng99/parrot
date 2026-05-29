import { writable } from "svelte/store";
import { type HistoryRow, clearHistory, deleteHistory, errMsg, listHistory } from "$lib/api";
import { toasts } from "./toasts";

// The synthesis log. Re-fetched after any synthesis or delete (no push channel).
export const history = writable<HistoryRow[]>([]);

export async function loadHistory(): Promise<void> {
  try {
    history.set(await listHistory());
  } catch (e) {
    toasts.error(`Couldn't load history: ${errMsg(e)}`);
  }
}

export async function deleteRow(id: string): Promise<void> {
  try {
    await deleteHistory(id);
    await loadHistory();
  } catch (e) {
    toasts.error(errMsg(e));
  }
}

export async function clearAll(): Promise<void> {
  try {
    await clearHistory();
    await loadHistory();
  } catch (e) {
    toasts.error(errMsg(e));
  }
}
