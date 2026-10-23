import type { ReactNode } from "react";
import type { ExperimentDefinition } from "../types/api";

type PageHeadProps = {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
  experiment?: ExperimentDefinition;
};

export function PageHead({ eyebrow, title, description, action, experiment }: PageHeadProps) {
  return (<>
    <header className="page-head">
      <div>
        <p>{eyebrow}</p>
        <h1>{title}</h1>
        {experiment ? <details className="experiment-intro">
          <summary>实验说明</summary>
          <p>{description}</p>
          {experiment.acquisition_program && <div className="experiment-program"><span>采集程序</span><code>{experiment.acquisition_program}</code></div>}
          {experiment.analysis_program && <div className="experiment-program"><span>分析程序</span><code>{experiment.analysis_program}</code></div>}
        </details> : <span>{description}</span>}
      </div>
      {action}
    </header>
    {Boolean(experiment?.safety_notes?.length) && <div className="experiment-safety" role="note" aria-label="实验安全提示">
      <strong>安全提示</strong>{experiment!.safety_notes.map(note => <p key={note}>{note}</p>)}
    </div>}
  </>);
}
