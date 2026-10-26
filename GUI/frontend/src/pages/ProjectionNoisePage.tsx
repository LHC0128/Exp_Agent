import { WorkflowExperimentPage } from "./WorkflowExperimentPage";

const analysisParameterKeys = Object.fromEntries([
  "WELCH_NPERSEG", "FIT_HALF_WIDTH_HZ",
].map(key => [key, key]));

export function ProjectionNoisePage() {
  return <WorkflowExperimentPage experimentId="projection-noise"
 analysisParameterKeys={analysisParameterKeys} />;
}
