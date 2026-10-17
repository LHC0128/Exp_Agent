import { WorkflowExperimentPage } from "./WorkflowExperimentPage";

export function MxMainFieldCalibrationPage() {
  return <WorkflowExperimentPage experimentId="mx-main-field-calibration" finishStepId="A10" runStepId="A08"
    analysisParameterKeys={{ GYROMAGNETIC_RATIO_HZ_PER_NT: "GYROMAGNETIC_RATIO_HZ_PER_NT",
      LINEAR_R_SQUARED_MIN: "LINEAR_R_SQUARED_MIN" }} />;
}
