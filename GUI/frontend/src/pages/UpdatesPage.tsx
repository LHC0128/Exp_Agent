import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import { JobView } from "../components/JobView";
import { PageHead } from "../components/PageHead";
import type { Job } from "../types/api";

type SectionState<T> = { data?: T; error?: string; loading: boolean };

function useSection<T>(url: string) {
  const [state, setState] = useState<SectionState<T>>({ loading: true });
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let disposed = false;
    setState({ loading: true });
    api<T>(url)
      .then((data) => { if (!disposed) setState({ data, loading: false }); })
      .catch((reason) => { if (!disposed) setState({ error: String(reason), loading: false }); });
    return () => { disposed = true; };
  }, [url, nonce]);
  return { ...state, reload: () => setNonce((value) => value + 1) };
}

type PipRow = { name: string; current: string; latest: string; source: string };
type NpmRow = { name: string; current: string; wanted: string; latest: string };

type SectionProps<T> = {
  scope: string;
  url: string;
  eyebrow: string;
  title: string;
  description: string;
  rows: (data: T) => Array<PipRow | NpmRow>;
  extraLabel?: string;
  extraValue: (row: PipRow | NpmRow) => string;
  note?: string;
  busy: boolean;
  registerReload: (scope: string, reload: () => void) => void;
  onUpdate: (path: string, packages: string[]) => void;
};

function PackageSection<T>({ scope, url, eyebrow, title, description, rows,
  extraLabel, extraValue, note, busy, registerReload, onUpdate }: SectionProps<T>) {
  const section = useSection<{ items: ReturnType<typeof rows> }>(url);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  useEffect(() => { setSelected(new Set()); }, [section.data]);
  useEffect(() => { registerReload(scope, section.reload); }, [scope, section.reload, registerReload]);

  const items = section.data?.items ?? [];
  const allSelected = items.length > 0 && items.every((row) => selected.has(row.name));
  const toggle = (name: string) => setSelected((current) => {
    const next = new Set(current);
    if (next.has(name)) next.delete(name); else next.add(name);
    return next;
  });
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(items.map((row) => row.name)));

  return (
    <article>
      <small>{eyebrow}</small><h2>{title}</h2>
      <p>{description}</p>
      {note && <p><em>{note}</em></p>}
      {section.loading && <p className="alert" role="status">正在检查…</p>}
      {section.error && <div className="alert error">{section.error}</div>}
      {section.data && (
        items.length === 0 ? (
          <p className="alert success">全部均为最新版本。</p>
        ) : (
          <div className="job-result-table-wrap">
            <table className="job-result-table">
              <thead>
                <tr>
                  <th><input type="checkbox" checked={allSelected} onChange={toggleAll} aria-label="全选" /></th>
                  <th>包</th><th>当前</th><th>最新</th>{extraLabel && <th>{extraLabel}</th>}
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr key={row.name}>
                    <td><input type="checkbox" checked={selected.has(row.name)} onChange={() => toggle(row.name)} aria-label={`选择 ${row.name}`} /></td>
                    <td>{row.name}</td>
                    <td>{row.current}</td>
                    <td>{row.latest}</td>
                    {extraLabel && <td>{extraValue(row)}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
      <div className="updates-actions">
        <button className="secondary" onClick={section.reload} disabled={busy || section.loading}>重新检查</button>
        <button
          disabled={busy || selected.size === 0 || section.loading}
          onClick={() => onUpdate(`/api/updates/${scope}`, [...selected])}
        >
          更新选中（{selected.size}）
        </button>
      </div>
    </article>
  );
}

type GitStatus = {
  branch: string | null; upstream: string | null; behind: number | null; ahead: number | null;
  clean: boolean | null; head: string | null; error: string | null;
};

function GitSection({ busy, registerReload, onUpdate }: {
  busy: boolean; registerReload: (scope: string, reload: () => void) => void; onUpdate: (path: string, packages: string[]) => void;
}) {
  const section = useSection<GitStatus>("/api/updates/git");
  useEffect(() => { registerReload("git-pull", section.reload); }, [section.reload, registerReload]);
  const data = section.data;
  const canPull = Boolean(data && data.behind && !data.error);
  return (
    <article>
      <small>REPOSITORY</small><h2>Git 仓库</h2>
      <p>与 GitHub 远端比较；仅在本地干净且可 fast-forward 时执行 git pull --ff-only。</p>
      {section.loading && <p className="alert" role="status">正在检查…</p>}
      {section.error && <div className="alert error">{section.error}</div>}
      {data && (
        <div className="updates-git">
          <p>分支：<strong>{data.branch ?? "未知"}</strong></p>
          {data.head && <p>HEAD：{data.head}</p>}
          <p>
            落后远端 <strong>{data.behind ?? "?"}</strong> 个提交，领先 <strong>{data.ahead ?? "?"}</strong> 个提交；
            工作区{data.clean ? "干净" : "有未提交修改"}
          </p>
          {data.error && <div className="alert error">{data.error}</div>}
          {!data.clean && <div className="alert">工作区有未提交修改时拒绝拉取；请先提交或 stash。</div>}
          {Boolean(data.ahead) && <div className="alert">本地有领先提交，直接拉取会产生合并，请手动 git pull。</div>}
        </div>
      )}
      <div className="updates-actions">
        <button className="secondary" onClick={section.reload} disabled={busy || section.loading}>重新检查</button>
        <button disabled={busy || !canPull || section.loading} onClick={() => onUpdate("/api/updates/git-pull", [])}>
          拉取更新{data?.behind ? `（${data.behind}）` : ""}
        </button>
      </div>
    </article>
  );
}

function SkillsSection() {
  const section = useSection<{ items: Array<{ name: string; description: string; dirs: string[] }>; note?: string }>(
    "/api/updates/skills");
  return (
    <article>
      <small>AI SKILLS</small><h2>AI Skills</h2>
      <p>{section.data?.note ?? "本地 Skill 清单，无版本源，仅展示。"}</p>
      {section.loading && <p className="alert" role="status">正在读取…</p>}
      {section.error && <div className="alert error">{section.error}</div>}
      {section.data && (
        <ul className="updates-skills">
          {section.data.items.map((skill) => (
            <li key={skill.name}>
              <strong>{skill.name}</strong>
              <small>{skill.dirs.join(" / ")}</small>
              <p>{skill.description}</p>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

function ToolchainSection() {
  const section = useSection<{ items: Array<{ name: string; version: string | null; error: string | null }> }>(
    "/api/updates/toolchain");
  return (
    <article>
      <small>TOOLCHAIN</small><h2>工具链版本</h2>
      <p>当前使用的解释器与命令行工具版本。</p>
      {section.loading && <p className="alert" role="status">正在检查…</p>}
      {section.error && <div className="alert error">{section.error}</div>}
      {section.data && (
        <ul className="updates-toolchain">
          {section.data.items.map((item) => (
            <li key={item.name}><strong>{item.name}</strong><span>{item.version ?? item.error ?? "未知"}</span></li>
          ))}
        </ul>
      )}
    </article>
  );
}

export function UpdatesPage() {
  const [job, setJob] = useState<Job>();
  const [error, setError] = useState("");
  const reloaders = useRef<Record<string, () => void>>({});
  const registerReload = (scope: string, reload: () => void) => {
    reloaders.current[scope] = reload;
  };
  const jobBusy = Boolean(job && ["queued", "running"].includes(job.status));

  const startUpdate = async (path: string, packages: string[]) => {
    setError("");
    try {
      setJob(packages.length
        ? await api<Job>(path, { method: "POST", body: JSON.stringify({ packages }) })
        : await api<Job>(path, { method: "POST" }));
    } catch (reason) {
      setError(String(reason));
    }
  };

  // 更新任务结束后自动重新检查对应分区。
  const handled = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (!job || !["completed", "failed", "cancelled"].includes(job.status)) return;
    if (handled.current === job.id) return;
    handled.current = job.id;
    if (job.kind.startsWith("update:")) reloaders.current[job.kind.slice("update:".length)]?.();
  }, [job]);

  return (
    <>
      <PageHead
        eyebrow="SYSTEM UPDATES"
        title="检查更新"
        description="检测 Git 仓库、Python 依赖、npm 包与工具链版本，并提供一键更新；更新任务不占用硬件队列。"
      />
      {error && <div className="alert error">{error}</div>}
      <div className="tool-grid updates-grid">
        <GitSection busy={jobBusy} registerReload={registerReload} onUpdate={startUpdate} />
        <PackageSection<{ items: PipRow[] }>
          scope="pip" url="/api/updates/pip" eyebrow="PYTHON VENV" title="Python 依赖"
          description="整个 agent_exp_env 的过期包列表，并标注来源声明文件；更新后自动运行 pip check。"
          rows={(data) => data.items} extraLabel="来源" extraValue={(row) => (row as PipRow).source}
          note="更新 fastapi / uvicorn / pydantic 后需重启 GUI 后端。"
          busy={jobBusy} registerReload={registerReload} onUpdate={startUpdate}
        />
        <PackageSection<{ items: NpmRow[] }>
          scope="npm-frontend" url="/api/updates/npm-frontend" eyebrow="FRONTEND NPM" title="前端依赖"
          description="GUI/frontend 的 npm 过期包；更新到 latest 并写入 package.json。"
          rows={(data) => data.items} extraLabel="wanted" extraValue={(row) => (row as NpmRow).wanted}
          note="更新后开发模式需重启 Vite，dist 部署需重新 npm run build。"
          busy={jobBusy} registerReload={registerReload} onUpdate={startUpdate}
        />
        <PackageSection<{ items: NpmRow[] }>
          scope="npm-global" url="/api/updates/npm-global" eyebrow="GLOBAL CLI" title="全局 CLI 工具"
          description="全局 npm 白名单包：codegraph（含 MCP server）、playwright cli、pi 本体、mineru。"
          rows={(data) => data.items} extraLabel="wanted" extraValue={(row) => (row as NpmRow).wanted}
          note="更新 @agegr/pi-web 后需重启 pi；更新 codegraph 同时更新其 MCP server。"
          busy={jobBusy} registerReload={registerReload} onUpdate={startUpdate}
        />
        <PackageSection<{ items: NpmRow[] }>
          scope="npm-olcli" url="/api/updates/npm-olcli" eyebrow="OLCLI SANDBOX" title="olcli 隔离环境"
          description="~/.local/olcli-ustc 隔离前缀中的 npm 包：olcli-ustc、codex、pnpm。"
          rows={(data) => data.items} extraLabel="wanted" extraValue={(row) => (row as NpmRow).wanted}
          note="仅使用命令级 npm_config_prefix，不修改用户级 .npmrc。"
          busy={jobBusy} registerReload={registerReload} onUpdate={startUpdate}
        />
        <SkillsSection />
        <ToolchainSection />
      </div>
      <JobView job={job} onUpdate={setJob} />
    </>
  );
}
