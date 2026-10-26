import { WorkflowExperimentPage } from "./WorkflowExperimentPage";

const analysisParameterKeys = Object.fromEntries([
  "WELCH_NPERSEG", "FIT_PEAK_MARGIN_HZ", "FIT_HALF_WIDTH_HZ", "FIT_GAMMA_GUESS_HZ",
  "FIT_EXAMPLE_PLOT_COUNT", "GLOBAL_2D_ANALYSIS_ENABLED", "GLOBAL_FIT_FREQUENCY_MIN_HZ",
  "GLOBAL_CORE_FIT_FREQUENCY_MIN_HZ", "GLOBAL_FIT_FREQUENCY_MAX_HZ",
  "GLOBAL_BACKGROUND_MARGIN_HZ", "GLOBAL_CV_FOLDS",
].map(key => [key, key]));

export function MxMainFieldScopeNoiseSpectrumPage() {
  return <WorkflowExperimentPage experimentId="mx-main-field-scope-noise-spectrum"
 analysisParameterKeys={analysisParameterKeys} />;
}
