import { Component, lazy, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { FlaskConical, History, House, Menu, PanelLeftClose, RefreshCw, SlidersHorizontal, Wrench, X } from "lucide-react";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";

import { ExperimentCatalogPage } from "../pages/ExperimentCatalogPage";

import { GlobalJobBanner } from "./GlobalJobBanner";
import { JobActivityProvider } from "./JobActivity";
import { Status } from "./Status";

const ExperimentPage = lazy(() => import("../pages/ExperimentPage").then((module) => ({ default: module.ExperimentPage })));
const InstrumentsPage = lazy(() => import("../pages/InstrumentsPage").then((module) => ({ default: module.InstrumentsPage })));
const OverviewPage = lazy(() => import("../pages/OverviewPage").then((module) => ({ default: module.OverviewPage })));
const RunsPage = lazy(() => import("../pages/RunsPage").then((module) => ({ default: module.RunsPage })));
const UpdatesPage = lazy(() => import("../pages/UpdatesPage").then((module) => ({ default: module.UpdatesPage })));
const ToolsPage = lazy(() => import("../pages/ToolsPage").then((module) => ({ default: module.ToolsPage })));

const nav = [
  ["/", "总览", House],
  ["/instruments", "仪器控制", SlidersHorizontal],
  ["/tools", "功能模块", Wrench],
  ["/experiments", "实验中心", FlaskConical],
  ["/runs", "历史数据", History],
  ["/updates", "检查更新", RefreshCw],
] as const;

class PageLoadBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? <div className="alert error" role="alert">页面加载失败，请刷新页面重试。<button className="secondary" onClick={() => window.location.reload()}>刷新页面</button></div> : this.props.children;
  }
}

export function Layout() {
  const location = useLocation();
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    try { return window.localStorage.getItem("exp-agent:sidebar-open") !== "false"; } catch { return true; }
  });
  const [narrow, setNarrow] = useState(() => window.matchMedia?.("(max-width: 760px)").matches ?? false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const toggle = useRef<HTMLButtonElement>(null);
  const sidebar = useRef<HTMLElement>(null);
  const visible = narrow ? mobileOpen : sidebarOpen;
  const closeMobile = () => setMobileOpen(false);
  useEffect(() => {
    try { window.localStorage.setItem("exp-agent:sidebar-open", String(sidebarOpen)); } catch { /* 隐私模式下只保留本次选择。 */ }
  }, [sidebarOpen]);
  useEffect(() => {
    const media = window.matchMedia?.("(max-width: 760px)");
    if (!media) return;
    const changed = () => { setNarrow(media.matches); setMobileOpen(false); };
    media.addEventListener("change", changed);
    return () => media.removeEventListener("change", changed);
  }, []);
  useEffect(() => {
    if (!narrow || !mobileOpen) return;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    sidebar.current?.querySelector<HTMLButtonElement>("button")?.focus();
    return () => {
      document.body.style.overflow = overflow;
      toggle.current?.focus({ preventScroll: true });
    };
  }, [narrow, mobileOpen]);
  return (
    <JobActivityProvider>
      <div className={`shell ${visible ? "nav-open" : "nav-hidden"}`}>
        {narrow && mobileOpen && <button className="nav-backdrop" aria-label="关闭导航遮罩" onClick={closeMobile} />}
        {visible && <aside id="main-navigation" ref={sidebar} aria-label="实验控制台导航" role={narrow ? "dialog" : undefined} aria-modal={narrow || undefined}
          onKeyDown={event => {
            if (!narrow) return;
            if (event.key === "Escape") { event.preventDefault(); closeMobile(); }
            if (event.key !== "Tab") return;
            const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("button, a"));
            if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1)?.focus(); }
            else if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0]?.focus(); }
          }}>
          {narrow && <button className="nav-close secondary" aria-label="关闭导航" onClick={closeMobile}><X size={18} aria-hidden="true" />关闭导航</button>}
          <div className="brand">
            <span>LHC</span>
            <div>原子磁力仪<small>实验控制台</small></div>
          </div>
          <nav aria-label="主导航">
            {nav.map(([path, label, Icon]) => (
              <NavLink key={path} to={path} end={path === "/"} onClick={() => { if (narrow) closeMobile(); }}>
                <Icon size={19} aria-hidden="true" />{label}
              </NavLink>
            ))}
          </nav>
          <div className="local-note">
            <Status>本机模式</Status>
            <p>硬件操作仅监听 127.0.0.1</p>
          </div>
        </aside>}
        <main inert={narrow && mobileOpen}>
          <div className="app-chrome">
            <div className="app-toolbar">
              <button className="secondary nav-toggle" ref={toggle} aria-controls="main-navigation" aria-expanded={visible}
                onClick={() => narrow ? setMobileOpen(value => !value) : setSidebarOpen(value => !value)}>
                {visible ? <PanelLeftClose size={18} aria-hidden="true" /> : <Menu size={18} aria-hidden="true" />}{visible ? "隐藏导航" : "显示导航"}
              </button>
              <span>实验控制台</span>
              {location.pathname.startsWith("/experiments/") && <NavLink to="/experiments">返回实验中心</NavLink>}
            </div>
            <GlobalJobBanner />
          </div>
          <div className="page-content">
          <PageLoadBoundary key={location.pathname}>
            <Suspense fallback={<p className="alert" role="status">正在加载页面…</p>}>
              <Routes>
                <Route path="/" element={<OverviewPage />} />
                <Route path="/instruments" element={<InstrumentsPage />} />
                <Route path="/tools" element={<ToolsPage />} />
                <Route path="/experiments" element={<ExperimentCatalogPage />} />
                <Route path="/experiments/:experimentId" element={<ExperimentPage />} />
                <Route path="/experiment" element={<Navigate to="/experiments/static-sensitivity" replace />} />
                <Route path="/runs" element={<RunsPage />} />
                <Route path="/updates" element={<UpdatesPage />} />
              </Routes>
            </Suspense>
          </PageLoadBoundary>
          </div>
        </main>
      </div>
    </JobActivityProvider>
  );
}
