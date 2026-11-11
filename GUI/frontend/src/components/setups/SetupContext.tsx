import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

import { api, type ApiError } from "../../api";
import type { components } from "../../types/openapi";
import "./setups.css";

export type Setup = { id: string; label: string; archived: boolean };
// 登记响应在生成的 OpenAPI 中为 unknown，写请求沿用已生成的 schema。
export type SetupRegistry = { revision: string; active: string; stored: boolean; setups: Setup[] };
type SetupUpdate = Pick<components["schemas"]["SetupUpdateBody"], "label" | "archived">;
type SetupState = {
  registry: SetupRegistry | undefined;
  active: Pick<Setup, "id" | "label"> | undefined;
  error: string;
  refresh: () => Promise<void>;
  switchTo: (id: string) => Promise<void>;
  create: (label: string, copyFrom: string) => Promise<void>;
  update: (id: string, change: SetupUpdate) => Promise<void>;
  setupEpoch: number;
};
const providerRequired = async () => { throw new Error("装置操作需要 SetupProvider"); };
const SetupContext = createContext<SetupState>({
  registry: undefined, active: undefined, error: "", setupEpoch: 0,
  refresh: providerRequired, switchTo: providerRequired, create: providerRequired, update: providerRequired,
});

export function useSetups(): SetupState {
  return useContext(SetupContext);
}

export function SetupProvider({ children }: { children: ReactNode }) {
  const [registry, setRegistry] = useState<SetupRegistry>();
  const current = useRef<SetupRegistry | undefined>(undefined);
  const readGeneration = useRef(0);
  const [error, setError] = useState("");
  const [setupEpoch, setSetupEpoch] = useState(0);
  const accept = useCallback((value: SetupRegistry, switched = false) => {
    if (switched || current.current && current.current.active !== value.active) setSetupEpoch(epoch => epoch + 1);
    current.current = value;
    setRegistry(value);
  }, []);
  const refresh = useCallback(async () => {
    const generation = ++readGeneration.current;
    setError("");
    try {
      const value = await api<SetupRegistry>("/api/setups");
      if (generation === readGeneration.current) accept(value);
    } catch (reason) {
      if (generation === readGeneration.current) setError(reason instanceof Error ? reason.message : String(reason));
      throw reason;
    }
  }, [accept]);
  useEffect(() => {
    const controller = new AbortController();
    const generation = ++readGeneration.current;
    void api<SetupRegistry>("/api/setups", { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted && generation === readGeneration.current) accept(value);
    }).catch(reason => {
      if (!controller.signal.aborted && generation === readGeneration.current) setError(reason instanceof Error ? reason.message : String(reason));
    });
    return () => controller.abort();
  }, [accept]);
  const writeRegistry = async (path: string, method: string, body: object, switching = false) => {
    readGeneration.current += 1;
    setError("");
    try {
      const value = await api<SetupRegistry>(path, { method, body: JSON.stringify(body) });
      readGeneration.current += 1;
      accept(value, switching);
    }
    catch (reason) {
      const failure = reason as ApiError;
      if (failure.status === 409 && failure.code === "revision_conflict") {
        try { await refresh(); setError("装置登记已变化，已刷新，请核对后重试。"); }
        catch (refreshError) { setError(`装置登记已变化，刷新失败：${refreshError instanceof Error ? refreshError.message : String(refreshError)}`); }
      } else if (failure.status === 409 && failure.code === "hardware_busy") {
        setError(switching ? "硬件忙，未切换" : "硬件忙，操作未完成");
      } else setError(failure.message);
      throw reason;
    }
  };
  const switchTo = (id: string) => {
    const body: components["schemas"]["SetupActiveBody"] = { setup_id: id, base_revision: current.current!.revision };
    return writeRegistry("/api/setups/active", "PUT", body, true);
  };
  const create = (label: string, copyFrom: string) => {
    const body: components["schemas"]["SetupCreateBody"] = { label, copy_from: copyFrom, base_revision: current.current!.revision };
    return writeRegistry("/api/setups", "POST", body);
  };
  const update = (id: string, change: SetupUpdate) => {
    const body: components["schemas"]["SetupUpdateBody"] = { ...change, base_revision: current.current!.revision };
    return writeRegistry(`/api/setups/${encodeURIComponent(id)}`, "PATCH", body);
  };
  const selected = registry?.setups.find(setup => setup.id === registry.active);
  return <SetupContext.Provider value={{ registry, active: selected && { id: selected.id, label: selected.label }, error, refresh, switchTo, create, update, setupEpoch }}>{children}</SetupContext.Provider>;
}
