import { WorkflowExperimentPage } from "./WorkflowExperimentPage";

const analysisParameterKeys = { POST_DARK_TIME_S: "POST_DARK_TIME_S" };

export function BBDutyScanBurstPage() {
  return <WorkflowExperimentPage experimentId="bb-duty-scan-burst" finishStepId="A08" runStepId="A07"
    analysisParameterKeys={analysisParameterKeys} />;
}
