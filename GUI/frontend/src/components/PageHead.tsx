import type { ReactNode } from "react";
import type { ExperimentDefinition } from "../types/api";

type PageHeadProps = {
  eyebrow: string;
  title: string;
  description?: string;
  action?: ReactNode;
  experiment?: ExperimentDefinition;
};

export function PageHead({ eyebrow, title, description, action, experiment }: PageHeadProps) {
  return (<>
    <header className="page-head">
      <div>
        <p>{eyebrow}</p>
        <h1>{title}</h1>
        {/* 实验页只列出采集与分析程序路径，不展示定义中的长段说明。 */}
        {experiment ? (experiment.acquisition_program || experiment.analysis_program) && <div className="experiment-intro">
          {experiment.acquisition_program && <div className="experiment-program"><span>采集程序</span><code>{experiment.acquisition_program}</code></div>}
          {experiment.analysis_program && <div className="experiment-program"><span>分析程序</span><code>{experiment.analysis_program}</code></div>}
        </div> : description ? <span>{description}</span> : null}
      </div>
      {action}
    </header>
  </>);
}
