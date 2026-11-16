import { WorkflowExperimentPage } from "./WorkflowExperimentPage";

const analysisParameterKeys = {
  LINEAR_R_SQUARED_MIN: "LINEAR_R_SQUARED_MIN",
  GYROMAGNETIC_RATIO_HZ_PER_NT: "GYROMAGNETIC_RATIO_HZ_PER_NT",
  MIN_ACCEPTED_POINTS: "MIN_ACCEPTED_POINTS",
  MIN_BRANCH_POINTS: "MIN_BRANCH_POINTS",
  BRANCH_SLOPE_TOLERANCE: "BRANCH_SLOPE_TOLERANCE",
  NEAR_ZERO_FIT_FLOOR_HZ: "NEAR_ZERO_FIT_FLOOR_HZ",
};

export function CoilConstantCalibrationPage() {
  return <WorkflowExperimentPage experimentId="coil-constant-calibration"
    analysisParameterKeys={analysisParameterKeys} />;
}
