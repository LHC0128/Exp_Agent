import { WorkflowExperimentPage } from "./WorkflowExperimentPage";

export function MxYRFProbeDetuningOptimizationPage() {
  return <WorkflowExperimentPage experimentId="mx-y-rf-probe-detuning-optimization" finishStepId="A09" runStepId="A07"
    analysisParameterKeys={{ FIT_R_SQUARED_MIN: "FIT_R_SQUARED_MIN",
      FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX: "FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX",
      R_BAD_POINT_STD_THRESHOLD_V: "R_BAD_POINT_STD_THRESHOLD_V", Y_RF_NT_PER_VPP: "Y_RF_NT_PER_VPP",
      LOW_FREQ_SKIP_HZ: "LOW_FREQ_SKIP_HZ", LINEAR_CHECK_GAMMA_FRACTION: "LINEAR_CHECK_GAMMA_FRACTION",
      SLOPE_AGREEMENT_TOLERANCE: "SLOPE_AGREEMENT_TOLERANCE" }} />;
}
