import { WorkflowExperimentPage } from "./WorkflowExperimentPage";

const analysisParameterKeys = { AREA_TOLERANCE: "AREA_TOLERANCE" };

export function CoilDriveCharacterizationPage() {
  return <WorkflowExperimentPage experimentId="coil-drive-characterization"
    analysisParameterKeys={analysisParameterKeys} />;
}
