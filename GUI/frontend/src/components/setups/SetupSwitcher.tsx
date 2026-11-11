import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { useSetups } from "./SetupContext";
import { useSetupSwitch } from "./useSetupSwitch";

export function SetupSwitcher() {
  const { registry, error, refresh } = useSetups();
  if (!registry) return <div className="setup-switcher"><button className="secondary setup-switcher-trigger" disabled={!error} onClick={() => void refresh().catch(() => {})}>当前装置：{error ? "读取失败，点击重试" : "正在读取…"} <span aria-hidden="true">▾</span></button>{error && <p className="setup-switcher-notice setup-error" role="alert">{error}</p>}</div>;
  return <ReadySetupSwitcher />;
}

function ReadySetupSwitcher() {
  const { registry, active, error } = useSetups();
  const { hardwareBusy, switching, notice, switchSetup } = useSetupSwitch();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menuId = useId();
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);
  return <div className="setup-switcher" ref={root} onKeyDown={event => {
    if (event.key === "Escape") { setOpen(false); trigger.current?.focus(); }
  }}>
    <button className="secondary setup-switcher-trigger" ref={trigger} aria-expanded={open} aria-controls={menuId}
      onClick={() => setOpen(value => !value)}>当前装置：{active?.label ?? "正在读取…"} <span aria-hidden="true">▾</span></button>
    {open && <div className="setup-switcher-menu" id={menuId} aria-label="装置列表">
      {hardwareBusy && <p className="setup-help" role="status">硬件任务运行中，结束后再切换</p>}
      {registry?.setups.filter(setup => !setup.archived).map(setup => <button className="secondary setup-switcher-item" key={setup.id}
        disabled={hardwareBusy || switching || setup.id === active?.id} aria-current={setup.id === active?.id ? "true" : undefined}
        onClick={() => void switchSetup(setup)}><span>{setup.label}</span>{setup.id === active?.id && <span aria-label="当前装置">✓</span>}</button>)}
      <Link to="/instruments?view=setups" onClick={() => setOpen(false)}>管理装置…</Link>
    </div>}
    {(error || notice) && <p className={`setup-switcher-notice ${error ? "setup-error" : ""}`} role={error ? "alert" : "status"}>{error || notice}</p>}
  </div>;
}
