import { useCallback, useState } from "react";

// Client feedback: "after choosing a store (e.g. hdcam) and reloading the
// page it goes to Test hdcam by default" and "any general setting which can
// switch the store in each menu?". One remembered store choice shared by
// every settings menu: picking a store in ANY menu becomes the default for
// all the others, and survives a reload (this browser only).
// Stored value: a store id, or "global" for the All stores / Global view.
const KEY = "pipelinepro.selectedStore";

export type SavedStore = number | "global" | undefined;

export function readSavedStore(): SavedStore {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "global") return "global";
    const n = v ? Number(v) : NaN;
    return Number.isFinite(n) ? n : undefined;
  } catch {
    return undefined;
  }
}

export function saveStore(v: number | "global" | null): void {
  try {
    if (v === null) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, String(v));
  } catch { /* storage unavailable -- choice just isn't remembered */ }
}

// The saved store if it's in the list, else the first store.
export function pickSavedStore(list: { id: number }[]): number | null {
  const saved = readSavedStore();
  if (typeof saved === "number" && list.some(s => s.id === saved)) return saved;
  return list[0]?.id ?? null;
}

// Drop-in for useState<number | null>: the setter also remembers the
// choice (null = "global" for menus that have an All stores option).
export function useRememberedStore(initial: number | null = null) {
  const [storeId, setStoreIdRaw] = useState<number | null>(initial);
  const setStoreId = useCallback((id: number | null) => {
    setStoreIdRaw(id);
    saveStore(id === null ? "global" : id);
  }, []);
  return [storeId, setStoreId, setStoreIdRaw] as const;
}
