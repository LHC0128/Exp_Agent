import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";

import { api } from "../api";
import { PageHead } from "../components/PageHead";
import type {
  ExperimentCatalogConfig,
  ExperimentDefinition,
  ExperimentTag,
} from "../types/api";

export function ExperimentCatalogPage() {
  const [experiments, setExperiments] = useState<ExperimentDefinition[]>([]);
  const [tags, setTags] = useState<ExperimentTag[]>([]);
  const [category, setCategory] = useState("all");
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [manageTags, setManageTags] = useState(false);
  const [newTag, setNewTag] = useState("");
  const [tagNames, setTagNames] = useState<Record<string, string>>({});
  const [experimentTitles, setExperimentTitles] = useState<Record<string, string>>({});
  const [experimentDescriptions, setExperimentDescriptions] = useState<Record<string, string>>({});
  const [status, setStatus] = useState("");
  const [statusError, setStatusError] = useState(false);
  const [saving, setSaving] = useState("");

  const loadCatalog = () => Promise.all([
    api<ExperimentDefinition[]>("/api/experiments"),
    api<ExperimentCatalogConfig>("/api/experiment-tags"),
  ]).then(([items, config]) => {
    setExperiments(items);
    setTags(config.tags);
    setTagNames(Object.fromEntries(config.tags.map((tag) => [tag.id, tag.label])));
    setExperimentTitles(Object.fromEntries(items.map((item) => [item.id, item.title])));
    setExperimentDescriptions(Object.fromEntries(items.map((item) => [item.id, item.description])));
    setError("");
  });

  useEffect(() => { loadCatalog().catch((reason) => setError(String(reason))); }, []);
  const keyword = query.trim().toLowerCase();
  const visible = (category === "all" ? experiments : experiments.filter((item) => item.category === category))
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
        description="所有正式 Python 实验使用统一参数、预检、运行与分析入口。"
        action={<button className="secondary" onClick={() => setManageTags((value) => !value)}>{manageTags ? "完成编辑" : "管理分类与实验信息"}</button>}
      />
      {error && <div className="alert error">实验目录加载失败：{error}<button className="secondary" onClick={() => window.location.reload()}>刷新页面</button></div>}
      {manageTags && (
        <section className="tag-manager">
          <div className="tag-manager-head">
            <div><small>TAG MANAGEMENT</small><h2>添加或重命名标签</h2><p>在下方每张实验卡片中修改该实验自己的名称、介绍和所属分类。</p></div>
            <div className="tag-add"><input value={newTag} placeholder="新标签名称" maxLength={30} onChange={(event) => setNewTag(event.target.value)} /><button disabled={saving === "new" || !newTag.trim()} onClick={add}>添加标签</button></div>
          </div>
          <div className="tag-editor-list">
            {tags.map((tag) => (
              <div key={tag.id}>
                <span>{experiments.filter((item) => item.category === tag.id).length} 个实验</span>
                <input value={tagNames[tag.id] ?? tag.label} maxLength={30} aria-label={`${tag.label}名称`} onChange={(event) => setTagNames((current) => ({ ...current, [tag.id]: event.target.value }))} />
                <button className="secondary" disabled={saving === tag.id || tagNames[tag.id]?.trim() === tag.label} onClick={() => rename(tag)}>保存名称</button>
              </div>
            ))}
          </div>
          {status && <div className={`alert ${statusError ? "error" : "success"}`}>{status}</div>}
        </section>
      )}
      <div className="catalog-filters">
        <button className={category === "all" ? "active" : ""} onClick={() => setCategory("all")}>全部 <span>{experiments.length}</span></button>
        {tags.map((tag) => <button className={category === tag.id ? "active" : ""} key={tag.id} onClick={() => setCategory(tag.id)}>{tag.label} <span>{experiments.filter((item) => item.category === tag.id).length}</span></button>)}
        <input className="catalog-search" type="text" value={query} placeholder="搜索实验名称或 ID…" aria-label="搜索实验" onChange={(event) => setQuery(event.target.value)} />
      </div>
      {status && !manageTags && <div className={`alert ${statusError ? "error" : "success"}`}>{status}</div>}
      <div className="experiment-catalog">
        {visible.map((item) => (
          <article key={item.id}>
            <div className="catalog-meta"><span>{item.category_label}</span><i>{item.execution_mode === "typed_workflow" ? "新模式" : "旧模式"}</i></div>
            {manageTags ? (
              <div className="experiment-metadata-editor">
                <label><span>实验名称</span><input value={experimentTitles[item.id] ?? item.title} maxLength={80} aria-label={`${item.title}名称`} onChange={(event) => setExperimentTitles((current) => ({ ...current, [item.id]: event.target.value }))} /></label>
                <label><span>实验介绍</span><textarea value={experimentDescriptions[item.id] ?? item.description} maxLength={500} aria-label={`${item.title}介绍`} placeholder="填写这个实验的简要介绍" onChange={(event) => setExperimentDescriptions((current) => ({ ...current, [item.id]: event.target.value }))} /></label>
                <button className="secondary" disabled={saving === `metadata:${item.id}` || ((experimentTitles[item.id] ?? "").trim() === item.title && (experimentDescriptions[item.id] ?? "").trim() === item.description)} onClick={() => saveMetadata(item)}>保存名称和介绍</button>
              </div>
            ) : <><h2>{item.title}</h2><p>{item.description || "暂未填写实验介绍。"}</p></>}
            <div className="catalog-programs"><div><span>采集</span><code>{item.acquisition_program}</code></div><div><span>分析</span><code>{item.analysis_program || "无独立分析程序"}</code></div></div>
            <div className="catalog-card-actions">
              <NavLink to={`/experiments/${item.id}`}>配置实验 →</NavLink>
              {manageTags && <label><span>所属分类</span><select value={item.category} disabled={saving === item.id} onChange={(event) => move(item.id, event.target.value)}>{tags.map((tag) => <option key={tag.id} value={tag.id}>{tag.label}</option>)}</select></label>}
            </div>
          </article>
        ))}
      </div>
    </>
  );
}
