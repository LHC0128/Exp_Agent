import { createContext, useCallback, useContext, useEffect, useId, useState, type ReactNode } from "react";
import { useBlocker } from "react-router-dom";
import type { ParameterLayout, ParameterValues, SchemaField } from "../types/api";

const DraftContext = createContext<(id: string, dirty: boolean) => void>(() => undefined);

/** 只拦截会卸载页面的导航；同页切换任务、Tab 不丢草稿。 */
export function NavigationGuard({ children }: { children: ReactNode }) {
  const [drafts, setDrafts] = useState<Set<string>>(() => new Set());
  const register = useCallback((id: string, dirty: boolean) => setDrafts(current => {
    if (current.has(id) === dirty) return current;
    const next = new Set(current);
    if (dirty) next.add(id); else next.delete(id);
    return next;
  }), []);
  const dirty = drafts.size > 0;
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => {
    if (blocker.state !== "blocked") return;
    if (window.confirm("当前页面有未保存的参数或布局，离开会丢失这些修改。确定离开？\n离开页面不会停止正在运行的实验。")) blocker.proceed();
    else blocker.reset();
  }, [blocker]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  return <DraftContext.Provider value={register}>{children}</DraftContext.Provider>;
}

export function useUnsavedChanges(dirty: boolean) {
  const register = useContext(DraftContext);
  const id = useId();
  useEffect(() => { register(id, dirty); return () => register(id, false); }, [register, id, dirty]);
}

/** 活会话以已应用参数为基线，其余以磁盘默认值为基线；派生只读值不算编辑。 */
export function useParameterGuard(fields: SchemaField[], values: ParameterValues,
  layout: ParameterLayout, savedLayout: ParameterLayout, applied?: ParameterValues) {
  const parameter = (source: ParameterValues | undefined, field: SchemaField) => source && Object.hasOwn(source, field.name) ? source[field.name] : field.default;
  const dirty = fields.length > 0 && (fields.some(field => !field.read_only &&
    JSON.stringify(parameter(values, field)) !== JSON.stringify(parameter(applied, field))) ||
    JSON.stringify(layout) !== JSON.stringify(savedLayout));
  useUnsavedChanges(dirty);
}
