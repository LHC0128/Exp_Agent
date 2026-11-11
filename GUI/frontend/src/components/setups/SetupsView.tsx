import { useState, type FormEvent } from "react";

import { useSetups } from "./SetupContext";
import { useSetupSwitch } from "./useSetupSwitch";

export function SetupsView() {
  const { registry, active, error, refresh, create, update } = useSetups();
  const { hardwareBusy, switching, notice: switchNotice, switchSetup } = useSetupSwitch();
  const [label, setLabel] = useState("");
  const [source, setSource] = useState<string>();
  const [editing, setEditing] = useState<string>();
  const [rename, setRename] = useState("");
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState("");
  const copyFrom = source ?? active?.id ?? "";
  const disabled = pending || switching || hardwareBusy;
  const createSetup = async (event: FormEvent) => {
    event.preventDefault(); setPending(true); setNotice("");
    try { await create(label.trim(), copyFrom); setLabel(""); setNotice("装置已创建，当前装置未改变。"); }
    catch { /* 错误由共享装置状态展示，保留表单输入。 */ }
    finally { setPending(false); }
  };
  const renameSetup = async (event: FormEvent, id: string) => {
    event.preventDefault(); setPending(true); setNotice("");
    try { await update(id, { label: rename.trim() }); setEditing(undefined); setNotice("装置已改名。"); }
    catch { /* 保存失败保留行内草稿。 */ }
    finally { setPending(false); }
  };
  const archiveSetup = async (id: string, archived: boolean) => {
    setPending(true); setNotice("");
    try { await update(id, { archived }); setNotice(archived ? "装置已归档。" : "装置已取消归档。"); }
    catch { /* 错误由共享装置状态展示。 */ }
    finally { setPending(false); }
  };
  return <section className="setups-view" aria-label="装置管理">
    <div className="config-toolbar"><div><h2>装置</h2><p>管理物理量映射与现场接线图的装置归属。</p></div><button className="secondary" disabled={pending || switching} onClick={() => void refresh().catch(() => {})}>刷新装置</button></div>
    {error && <p className="alert error" role="alert">{error}</p>}
    {(notice || switchNotice) && !error && <p className="alert success" role="status">{notice || switchNotice}</p>}
    {hardwareBusy && <p className="alert" role="status">硬件任务运行中，结束后再切换；新建、改名与归档也暂不可用。</p>}
    {registry?.stored === false && <p className="alert">目前只有默认装置；新建第二个装置后才会生成 params/setups.yaml</p>}
    <form className="setup-create" onSubmit={event => void createSetup(event)} aria-label="新建装置">
      <h3>新建装置</h3>
      <label><span>装置名称</span><input required value={label} onChange={event => setLabel(event.target.value)} /></label>
      <label><span>复制来源</span><select value={copyFrom} onChange={event => setSource(event.target.value)}>{registry?.setups.map(setup => <option key={setup.id} value={setup.id}>{setup.label}{setup.archived ? "（已归档）" : ""}</option>)}</select></label>
      <button disabled={!registry || disabled || !label.trim() || !copyFrom}>创建装置</button>
      <p className="setup-help">新建会复制来源装置的物理量映射与现场接线图，不改变当前装置</p>
    </form>
    {!registry ? <p role="status">正在读取装置…</p> : <div className="setup-table-scroll"><table className="setup-table">
      <thead><tr><th scope="col">装置名称</th><th scope="col">ID</th><th scope="col">状态</th><th scope="col">操作</th></tr></thead>
      <tbody>{registry.setups.map(setup => {
        const current = setup.id === active?.id;
        return <tr key={setup.id}>
          <td>{editing === setup.id ? <form className="setup-rename" onSubmit={event => void renameSetup(event, setup.id)}>
            <input aria-label={`${setup.label} 新名称`} required value={rename} onChange={event => setRename(event.target.value)} />
            <button disabled={disabled || !rename.trim()}>保存名称</button><button type="button" className="secondary" disabled={pending} onClick={() => setEditing(undefined)}>取消</button>
          </form> : setup.label}</td>
          <td><code>{setup.id}</code></td><td>{current ? "当前" : setup.archived ? "已归档" : "可用"}</td>
          <td><div className="setup-row-actions">
            <button className="secondary" disabled={disabled || current || setup.archived} onClick={() => { setNotice(""); void switchSetup(setup); }}>设为当前</button>
            <button className="secondary" disabled={disabled} onClick={() => { setEditing(setup.id); setRename(setup.label); }}>改名</button>
            <button className="secondary" disabled={disabled || current} title={current ? "当前装置不可归档，请先切换到其他装置" : undefined} onClick={() => void archiveSetup(setup.id, !setup.archived)}>{setup.archived ? "取消归档" : "归档"}</button>
          </div>{current && <small className="setup-help">当前装置不可归档，请先切换到其他装置</small>}</td>
        </tr>;
      })}</tbody>
    </table></div>}
  </section>;
}
