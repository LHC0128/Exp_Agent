import { Navigate, NavLink, Route, Routes } from "react-router-dom";

import { ExperimentCatalogPage } from "../pages/ExperimentCatalogPage";
import { ExperimentPage } from "../pages/ExperimentPage";
import { InstrumentsPage } from "../pages/InstrumentsPage";
import { OverviewPage } from "../pages/OverviewPage";
import { RunsPage } from "../pages/RunsPage";
import { ToolsPage } from "../pages/ToolsPage";
import { GlobalJobBanner } from "./GlobalJobBanner";
import { JobActivityProvider } from "./JobActivity";
import { Status } from "./Status";

const nav = [
  ["/", "总览", "⌂"],
  ["/instruments", "仪器控制", "◫"],
  ["/tools", "功能模块", "◇"],
  ["/experiments", "实验中心", "∿"],
  ["/runs", "历史数据", "≡"],
] as const;

export function Layout() {
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
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/instruments" element={<InstrumentsPage />} />
            <Route path="/tools" element={<ToolsPage />} />
            <Route path="/experiments" element={<ExperimentCatalogPage />} />
            <Route path="/experiments/:experimentId" element={<ExperimentPage />} />
            <Route path="/experiment" element={<Navigate to="/experiments/static-sensitivity" replace />} />
            <Route path="/runs" element={<RunsPage />} />
          </Routes>
        </main>
      </div>
    </JobActivityProvider>
  );
}
