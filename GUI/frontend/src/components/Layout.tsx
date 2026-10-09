import { Component, lazy, Suspense, type ReactNode } from "react";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";

import { ExperimentCatalogPage } from "../pages/ExperimentCatalogPage";

import { GlobalJobBanner } from "./GlobalJobBanner";
import { JobActivityProvider } from "./JobActivity";
import { Status } from "./Status";

const ExperimentPage = lazy(() => import("../pages/ExperimentPage").then((module) => ({ default: module.ExperimentPage })));
const InstrumentsPage = lazy(() => import("../pages/InstrumentsPage").then((module) => ({ default: module.InstrumentsPage })));
const OverviewPage = lazy(() => import("../pages/OverviewPage").then((module) => ({ default: module.OverviewPage })));
const RunsPage = lazy(() => import("../pages/RunsPage").then((module) => ({ default: module.RunsPage })));
const ToolsPage = lazy(() => import("../pages/ToolsPage").then((module) => ({ default: module.ToolsPage })));

const nav = [
  ["/", "总览", "⌂"],
  ["/instruments", "仪器控制", "◫"],
  ["/tools", "功能模块", "◇"],
  ["/experiments", "实验中心", "∿"],
  ["/runs", "历史数据", "≡"],
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
  return (
    <JobActivityProvider>
      <div className="shell">
        <aside>
          <div className="brand">
            <span>LHC</span>
            <div>原子磁力仪<small>实验控制台</small></div>
          </div>
          <nav>
            {nav.map(([path, label, icon]) => (
              <NavLink key={path} to={path} end={path === "/"}>
                <b>{icon}</b>{label}
              </NavLink>
            ))}
          </nav>
          <div className="local-note">
            <Status>本机模式</Status>
            <p>硬件操作仅监听 127.0.0.1</p>
          </div>
        </aside>
        <main>
          <GlobalJobBanner />
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
              </Routes>
            </Suspense>
          </PageLoadBoundary>
        </main>
      </div>
    </JobActivityProvider>
  );
}
