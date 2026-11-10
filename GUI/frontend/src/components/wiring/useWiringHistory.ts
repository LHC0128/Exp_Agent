import { useReducer, useRef } from "react";

export const WIRING_HISTORY_LIMIT = 50;
export const WIRING_HISTORY_MERGE_MS = 500;

type Entry<T> = { key: string; snapshot: T; mergeKey: string | null; at: number };

function lastEntry<T>(entries: Entry<T>[], key: string): number {
  for (let index = entries.length - 1; index >= 0; index -= 1) if (entries[index].key === key) return index;
  return -1;
}

/** 按图维护有界快照历史；mergeKey 相同的连续记录只保留第一步之前的状态。 */
export class WiringHistory<T> {
  private undoEntries: Entry<T>[] = [];
  private redoEntries: Entry<T>[] = [];
  private lastMerge: { token: string; at: number } | null = null;

  constructor(private readonly limit = WIRING_HISTORY_LIMIT, private readonly mergeWindowMs = WIRING_HISTORY_MERGE_MS) {}

  record(key: string, snapshot: T, mergeKey: string | null = null, at = Date.now()): boolean {
    const token = mergeKey === null ? null : `${key}\u0000${mergeKey}`;
    const merged = token !== null && this.lastMerge?.token === token && at - this.lastMerge.at <= this.mergeWindowMs;
    this.lastMerge = token === null ? null : { token, at };
    this.redoEntries = [];
    if (merged) return false;
    this.undoEntries.push({ key, snapshot, mergeKey: token, at });
    if (this.undoEntries.length > this.limit) this.undoEntries.splice(0, this.undoEntries.length - this.limit);
    return true;
  }

  undo(key: string, current: T, at = Date.now()): T | undefined {
    this.lastMerge = null;
    const index = lastEntry(this.undoEntries, key);
    if (index < 0) return undefined;
    const [entry] = this.undoEntries.splice(index, 1);
    this.redoEntries.push({ key, snapshot: current, mergeKey: null, at });
    return entry.snapshot;
  }

  redo(key: string, current: T, at = Date.now()): T | undefined {
    this.lastMerge = null;
    const index = lastEntry(this.redoEntries, key);
    if (index < 0) return undefined;
    const [entry] = this.redoEntries.splice(index, 1);
    this.undoEntries.push({ key, snapshot: current, mergeKey: null, at });
    if (this.undoEntries.length > this.limit) this.undoEntries.splice(0, this.undoEntries.length - this.limit);
    return entry.snapshot;
  }

  canUndo(key: string) { return lastEntry(this.undoEntries, key) >= 0; }
  canRedo(key: string) { return lastEntry(this.redoEntries, key) >= 0; }
  undoDepth() { return this.undoEntries.length; }
  redoDepth() { return this.redoEntries.length; }

  clear() {
    this.undoEntries = [];
    this.redoEntries = [];
    this.lastMerge = null;
  }
}

export function useWiringHistory<T>(key: string) {
  const store = useRef<WiringHistory<T> | null>(null);
  const history = store.current ??= new WiringHistory<T>();
  const [, bump] = useReducer((value: number) => value + 1, 0);
  return {
    record(snapshot: T, mergeKey: string | null = null) {
      if (history.record(key, snapshot, mergeKey)) bump();
    },
    undo(current: T) {
      const value = history.undo(key, current);
      if (value !== undefined) bump();
      return value;
    },
    redo(current: T) {
      const value = history.redo(key, current);
      if (value !== undefined) bump();
      return value;
    },
    clear() {
      history.clear();
      bump();
    },
    canUndo: history.canUndo(key),
    canRedo: history.canRedo(key),
  };
}
