import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";

import { api } from "../api";
import { PageHead } from "../components/PageHead";
import { useJobActivity } from "../components/JobActivity";
import { useUnsavedChanges } from "../components/NavigationGuard";
import { experimentJobUrl } from "../components/experimentNavigation";
import type {
  ExperimentCatalogConfig,
  ExperimentDefinition,
  ExperimentTag,
} from "../types/api";

const VIEW_KEY = "exp-agent:catalog-view";
function readView() {
  try {
    const value = JSON.parse(window.sessionStorage.getItem(VIEW_KEY) || "{}");
    return { category: typeof value?.category === "string" ? value.category : "all",
      query: typeof value?.query === "string" ? value.query : "", archived: value?.archived === true,
      scroll: typeof value?.scroll === "number" && Number.isFinite(value.scroll) ? Math.max(0, value.scroll) : 0 };
  } catch { return { category: "all", query: "", archived: false, scroll: 0 }; }
}

export function ExperimentCatalogPage() {
  const [view] = useState(readView);
  const restored = useRef(false);
  const currentView = useRef(view);
  const { activeJobs = [], connection = "connected" } = useJobActivity();
  const [experiments, setExperiments] = useState<ExperimentDefinition[]>([]);
  const [tags, setTags] = useState<ExperimentTag[]>([]);
  const [category, setCategory] = useState(view.category);
  const [showArchived, setShowArchived] = useState(view.archived);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const [query, setQuery] = useState(view.query);
  const [error, setError] = useState("");
  const [manageTags, setManageTags] = useState(false);
  const [newTag, setNewTag] = useState("");
  const [tagNames, setTagNames] = useState<Record<string, string>>({});
  const [experimentTitles, setExperimentTitles] = useState<Record<string, string>>({});
  const [experimentDescriptions, setExperimentDescriptions] = useState<Record<string, string>>({});
  const [status, setStatus] = useState("");
  const [statusError, setStatusError] = useState(false);
  const [saving, setSaving] = useState("");
  useUnsavedChanges(Boolean(newTag.trim()) || tags.some(tag => tagNames[tag.id] !== tag.label) || experiments.some(item =>
    experimentTitles[item.id] !== item.title || experimentDescriptions[item.id] !== item.description));
  useEffect(() => {
    currentView.current = { category, query, archived: showArchived, scroll: restored.current ? window.scrollY : view.scroll };
    try { window.sessionStorage.setItem(VIEW_KEY, JSON.stringify(currentView.current)); } catch { /* 存储不可用不影响筛选。 */ }
  }, [category, query, showArchived, view.scroll]);
  useEffect(() => {
    const remember = () => {
      currentView.current.scroll = window.scrollY;
      try { window.sessionStorage.setItem(VIEW_KEY, JSON.stringify(currentView.current)); } catch { /* 只保留本次状态。 */ }
    };
    window.addEventListener("scroll", remember, { passive: true });
    return () => { remember(); window.removeEventListener("scroll", remember); };
  }, []);
  useEffect(() => {
    if (loading || error || restored.current) return;
    restored.current = true;
    window.scrollTo({ top: view.scroll });
  }, [loading, error, view.scroll]);

  const loadCatalog = (signal?: AbortSignal) => {
    setLoading(true); setError("");
    return Promise.all([
      api<ExperimentDefinition[]>("/api/experiments", { signal }),
      api<ExperimentCatalogConfig>("/api/experiment-tags", { signal }),
    ]).then(([items, config]) => {
      if (signal?.aborted) return;
      setExperiments(items);
      setTags(config.tags);
      setTagNames(Object.fromEntries(config.tags.map((tag) => [tag.id, tag.label])));
      setExperimentTitles(Object.fromEntries(items.map((item) => [item.id, item.title])));
      setExperimentDescriptions(Object.fromEntries(items.map((item) => [item.id, item.description])));
    }).finally(() => { if (!signal?.aborted) setLoading(false); });
  };

  useEffect(() => {
    const controller = new AbortController();
    loadCatalog(controller.signal).catch((reason) => {
      if (!controller.signal.aborted) setError(String(reason));
    });
    return () => controller.abort();
  }, [reload]);
  const busy = loading || Boolean(saving);
  const active = experiments.filter((item) => !item.archived);
  const archived = experiments.filter((item) => item.archived);
  const current = showArchived ? archived : active;
  const keyword = query.trim().toLowerCase();
  const visible = (category === "all" ? current : current.filter((item) => item.category === category))
    .filter((item) => !keyword || `${item.id} ${item.title} ${item.description}`.toLowerCase().includes(keyword));

  const add = async () => {
    if (!newTag.trim()) return;
    setSaving("new"); setStatus(""); setStatusError(false);
    try {
      await api("/api/experiment-tags", { method: "POST", body: JSON.stringify({ label: newTag }) });
      setNewTag(""); await loadCatalog(); setStatus("标签已添加");
    } catch (reason) { setStatus(String(reason)); setStatusError(true); } finally { setSaving(""); }
  };
  const rename = async (tag: ExperimentTag) => {
    setSaving(tag.id); setStatus(""); setStatusError(false);
    try {
      await api(`/api/experiment-tags/${tag.id}`, { method: "PUT", body: JSON.stringify({ label: tagNames[tag.id] }) });
      await loadCatalog(); setStatus("标签名称已保存");
    } catch (reason) { setStatus(String(reason)); setStatusError(true); } finally { setSaving(""); }
  };
  const move = async (experimentId: string, tagId: string) => {
    setSaving(experimentId); setStatus(""); setStatusError(false);
    try {
      const result = await api<{ experiment: ExperimentDefinition }>(`/api/experiments/${experimentId}/tag`, {
        method: "PUT",
        body: JSON.stringify({ tag_id: tagId }),
      });
      setExperiments((items) => items.map((item) => item.id === experimentId ? result.experiment : item));
      setStatus(`“${result.experiment.title}”已移动到“${result.experiment.category_label}”`);
    } catch (reason) { setStatus(String(reason)); setStatusError(true); } finally { setSaving(""); }
  };
  const archiveExperiment = async (item: ExperimentDefinition) => {
    setSaving(`archive:${item.id}`); setStatus(""); setStatusError(false);
    try {
      const result = await api<{ experiment: ExperimentDefinition }>(`/api/experiments/${item.id}/archive`, {
        method: "PUT",
        body: JSON.stringify({ archived: !item.archived }),
      });
      setExperiments((items) => items.map((current) => current.id === item.id ? result.experiment : current));
      setStatus(`“${result.experiment.title}”${result.experiment.archived ? "已归档" : "已恢复到原分类"}`);
    } catch (reason) { setStatus(String(reason)); setStatusError(true); } finally { setSaving(""); }
  };
  const saveMetadata = async (item: ExperimentDefinition) => {
    setSaving(`metadata:${item.id}`); setStatus(""); setStatusError(false);
    try {
      const result = await api<{ experiment: ExperimentDefinition }>(`/api/experiments/${item.id}/metadata`, {
        method: "PUT",
        body: JSON.stringify({
          title: experimentTitles[item.id] || "",
          description: experimentDescriptions[item.id] || "",
        }),
      });
      setExperiments((items) => items.map((current) => current.id === item.id ? result.experiment : current));
      setExperimentTitles((current) => ({ ...current, [item.id]: result.experiment.title }));
      setExperimentDescriptions((current) => ({ ...current, [item.id]: result.experiment.description }));
      setStatus(`“${result.experiment.title}”的名称和介绍已保存`);
    } catch (reason) { setStatus(String(reason)); setStatusError(true); } finally { setSaving(""); }
  };

  return (
    <>
      <PageHead
        eyebrow="EXPERIMENT CENTER"
        title="实验中心"
        description="正式 Python 实验使用统一配置与运行入口；已归档实验仅保留卡片，代码和历史数据仍保留。"
        action={<button className="secondary" onClick={() => setManageTags((value) => !value)}>{manageTags ? "完成编辑" : "管理分类与实验信息"}</button>}
      />
      {error && <div className="alert error" role="alert">实验目录加载失败：{error}<button className="secondary" disabled={loading} onClick={() => setReload((value) => value + 1)}>重试</button></div>}
      {manageTags && (
        <section className="tag-manager">
          <div className="tag-manager-head">
            <div><small>TAG MANAGEMENT</small><h2>添加或重命名标签</h2><p>在下方卡片中修改实验信息、归档或恢复实验；归档会保留原分类。</p></div>
            <div className="tag-add"><input value={newTag} placeholder="新标签名称" maxLength={30} onChange={(event) => setNewTag(event.target.value)} /><button disabled={busy || !newTag.trim()} onClick={add}>添加标签</button></div>
          </div>
          <div className="tag-editor-list">
            {tags.map((tag) => (
              <div key={tag.id}>
                <span>{active.filter((item) => item.category === tag.id).length} 个未归档实验</span>
                <input value={tagNames[tag.id] ?? tag.label} maxLength={30} aria-label={`${tag.label}名称`} onChange={(event) => setTagNames((current) => ({ ...current, [tag.id]: event.target.value }))} />
                <button className="secondary" disabled={busy || tagNames[tag.id]?.trim() === tag.label} onClick={() => rename(tag)}>保存名称</button>
              </div>
            ))}
          </div>
          {status && <div className={`alert ${statusError ? "error" : "success"}`}>{status}</div>}
        </section>
      )}
      <div className="catalog-filters">
        <button className={!showArchived && category === "all" ? "active" : ""} onClick={() => { setShowArchived(false); setCategory("all"); }}>全部 <span>{active.length}</span></button>
        {tags.map((tag) => <button className={!showArchived && category === tag.id ? "active" : ""} key={tag.id} onClick={() => { setShowArchived(false); setCategory(tag.id); }}>{tag.label} <span>{active.filter((item) => item.category === tag.id).length}</span></button>)}
        <button className={showArchived ? "active" : ""} onClick={() => { setShowArchived(true); setCategory("all"); }}>已归档 <span>{archived.length}</span></button>
        <input className="catalog-search" type="text" value={query} placeholder="搜索实验名称或 ID…" aria-label="搜索实验" onChange={(event) => setQuery(event.target.value)} />
      </div>
      {status && !manageTags && <div className={`alert ${statusError ? "error" : "success"}`}>{status}</div>}
      {loading && <p className="alert" role="status">正在加载实验目录…</p>}
      {!loading && !error && visible.length === 0 && <p className="alert" role="status">{keyword ? "未找到匹配的实验。" : showArchived ? "暂无已归档实验。" : "当前分类没有未归档实验，可在“已归档”中查看或恢复。"}</p>}
      <div className="experiment-catalog" aria-busy={loading}>
        {!loading && !error && visible.map((item) => {
          const tasks = activeJobs.filter(job => job.kind === `experiment:${item.id}` || job.kind === `session:${item.id}`);
          const task = tasks.find(job => job.status === "running") ?? tasks[0];
          const running = task?.status === "running" && connection === "connected";
          return (
          <article key={item.id} className={`catalog-card${manageTags ? " is-managing" : ""}${running ? " is-running" : ""}`}>
            <div className="catalog-meta"><span className="catalog-category">{item.category_label}{item.archived ? " · 已归档" : ""}</span>
              {task && <span className={`catalog-running${running ? " active" : ""}`}>{connection !== "connected" ? "状态未知" : running ? "运行中" : "排队中"}</span>}
            </div>
            {manageTags ? (
              <div className="experiment-metadata-editor">
                <label><span>实验名称</span><input value={experimentTitles[item.id] ?? item.title} maxLength={80} aria-label={`${item.title}名称`} onChange={(event) => setExperimentTitles((current) => ({ ...current, [item.id]: event.target.value }))} /></label>
                <label><span>实验介绍</span><textarea value={experimentDescriptions[item.id] ?? item.description} maxLength={500} aria-label={`${item.title}介绍`} placeholder="填写这个实验的简要介绍" onChange={(event) => setExperimentDescriptions((current) => ({ ...current, [item.id]: event.target.value }))} /></label>
                <button className="secondary" disabled={busy || ((experimentTitles[item.id] ?? "").trim() === item.title && (experimentDescriptions[item.id] ?? "").trim() === item.description)} onClick={() => saveMetadata(item)}>保存名称和介绍</button>
              </div>
            ) : <h2>{item.title}</h2>}
            <div className="catalog-card-actions">
              {item.archived ? <span>仅保留卡片{item.archive_path && <> · 代码：<code>{item.archive_path}</code></>}</span>
                : <NavLink to={!manageTags && task ? experimentJobUrl(task)! : `/experiments/${item.id}`} aria-label={`进入实验：${item.title}`}>进入实验 <span aria-hidden="true">→</span></NavLink>}
              {manageTags && <>
                <label><span>所属分类</span><select value={item.category} disabled={busy} onChange={(event) => move(item.id, event.target.value)}>{tags.map((tag) => <option key={tag.id} value={tag.id}>{tag.label}</option>)}</select></label>
                {item.archive_path ? <span>恢复前需手动接回代码</span>
                  : <button className="secondary" disabled={busy} aria-label={`${item.archived ? "恢复" : "归档"}${item.title}`} onClick={() => archiveExperiment(item)}>{saving === `archive:${item.id}` ? "保存中…" : item.archived ? "恢复" : "归档"}</button>}
              </>}
            </div>
          </article>
        ); })}
      </div>
    </>
  );
}
