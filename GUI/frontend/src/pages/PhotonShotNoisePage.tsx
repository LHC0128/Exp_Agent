import { WorkflowExperimentPage } from "./WorkflowExperimentPage";

const analysisParameterKeys = {
  PSD_NPERSEG: "PSD_NPERSEG",
  FLAT_FREQ_FRACTION: "FLAT_FREQ_FRACTION",
  FIT_START_IDX: "FIT_START_IDX",
};

export function PhotonShotNoisePage() {
  return <WorkflowExperimentPage experimentId="photon-shot-noise"
    analysisParameterKeys={analysisParameterKeys} />;
}
