import type { ReactElement } from "react";

import { ScopeCapturePage } from "./ScopeCapturePage";
import { MxYOptimalControlFrequencyPage } from "./MxYOptimalControlFrequencyPage";
import { StaticSensitivityPage } from "./StaticSensitivityPage";
import { BellBloomZCalibrationPage } from "./BellBloomZCalibrationPage";
import { NoiseSpectrumXYPage } from "./NoiseSpectrumXYPage";
import { NoiseSpectrumXYDemod3RPage } from "./NoiseSpectrumXYDemod3RPage";
import { ProjectionNoisePage } from "./ProjectionNoisePage";
import { PhotonShotNoisePage } from "./PhotonShotNoisePage";
import { RFConstXYFrequencyPage } from "./RFConstXYFrequencyPage";
import { T1CalibrationPage } from "./T1CalibrationPage";
import { T2CalibrationPage } from "./T2CalibrationPage";
import { RFDirectAWFrequencyPage } from "./RFDirectAWFrequencyPage";
import { XYDirectAWDCCalibrationPage } from "./XYDirectAWDCCalibrationPage";
import { BBPowerScanBurstPage } from "./BBPowerScanBurstPage";
import { BBDutyScanBurstPage } from "./BBDutyScanBurstPage";
import { CoilDriveCharacterizationPage } from "./CoilDriveCharacterizationPage";
import { ZFieldBandwidthPage } from "./ZFieldBandwidthPage";
import { NoiseSpectrumXYKnownNoisePage, NoiseSpectrumXYProbeAMPage } from "./NoiseSpectrumXYKnownNoisePage";
import { DetectionChainFrequencyResponsePage } from "./DetectionChainFrequencyResponsePage";
import { RFSensitivityDirectAWPage } from "./RFSensitivityDirectAWPage";
import { RFControlScaleSensitivityPage } from "./RFControlScaleSensitivityPage";
import { MxYRFSensitivityPage } from "./MxYRFSensitivityPage";
import { MxYRFProbeDetuningOptimizationPage } from "./MxYRFProbeDetuningOptimizationPage";
import { MxYRFPowerOptimizationPage } from "./MxYRFPowerOptimizationPage";
import { MxYRFFrequencyResponsePage } from "./MxYRFFrequencyResponsePage";
import { MxZOptimalControlRFSensitivityPage } from "./MxZOptimalControlRFSensitivityPage";
import { MxMainFieldCalibrationPage } from "./MxMainFieldCalibrationPage";
import { MxMainFieldScopeNoiseSpectrumPage } from "./MxMainFieldScopeNoiseSpectrumPage";
import { MxZFieldCalibrationPage } from "./MxZFieldCalibrationPage";

export type ExperimentPageRoute = () => ReactElement;

/**
 * 稳定实验 ID → 实验页面。
 *
 * 新增正式实验的前端改动只有两处：新建 `XxxPage.tsx`，再在本表加一行；分发逻辑不再改。
 * 本表未登记的 ID 由 `ExperimentPage` 回落 `GenericExperimentPage`（通用参数表单）。
 *
 * 同一组件服务多个稳定 ID 时按 ID 逐行登记，每行传入字面量 ID 并同时作为 `key`：
 * 切换 ID 时 React 按 key 重新挂载，页面内部状态不会沿用上一个变体。
 * 其余页面内部硬编码自己的稳定 ID，不接收任何 props。
 */
export const experimentPageRoutes: Readonly<Record<string, ExperimentPageRoute>> = {
  "mx-z-optimal-control-rf-sensitivity": () => <MxZOptimalControlRFSensitivityPage />,
  "mx-y-rf-sensitivity": () => <MxYRFSensitivityPage />,
  "mx-y-rf-probe-detuning-optimization": () => <MxYRFProbeDetuningOptimizationPage />,
  "mx-y-rf-power-optimization": () => <MxYRFPowerOptimizationPage />,
  "mx-y-rf-frequency-response": () => <MxYRFFrequencyResponsePage />,
  "mx-main-field-calibration": () => <MxMainFieldCalibrationPage />,
  "mx-main-field-scope-noise-spectrum": () => <MxMainFieldScopeNoiseSpectrumPage />,
  "mx-z-field-calibration": () => <MxZFieldCalibrationPage />,
  "scope-capture": () => <ScopeCapturePage />,
  "mx-y-optimal-control-rf-frequency-response": () => <MxYOptimalControlFrequencyPage />,
  "static-sensitivity": () => <StaticSensitivityPage />,
  "bell-bloom-z-field-calibration": () => <BellBloomZCalibrationPage />,
  "noise-spectrum-xy": () => <NoiseSpectrumXYPage />,
  "noise-spectrum-xy-demod3-r": () => <NoiseSpectrumXYDemod3RPage />,
  "projection-noise": () => <ProjectionNoisePage />,
  "photon-shot-noise": () => <PhotonShotNoisePage />,
  "rf-sensitivity-constxy-frequency": () => <RFConstXYFrequencyPage />,
  "t1-calibration": () => <T1CalibrationPage />,
  "t2-calibration": () => <T2CalibrationPage />,
  "rf-sensitivity-direct-aw-frequency": () => <RFDirectAWFrequencyPage />,
  "xy-direct-aw-dc-calibration": () => <XYDirectAWDCCalibrationPage />,
  "bb-power-scan-burst": () => <BBPowerScanBurstPage />,
  "bb-duty-scan-burst": () => <BBDutyScanBurstPage />,
  "coil-drive-characterization": () => <CoilDriveCharacterizationPage />,
  "z-field-bandwidth": () => <ZFieldBandwidthPage />,
  "noise-spectrum-xy-known-noise": () => <NoiseSpectrumXYKnownNoisePage />,
  "noise-spectrum-xy-uncontrolled-probe-am": () => <NoiseSpectrumXYProbeAMPage />,
  "detection-chain-frequency-response": () => <DetectionChainFrequencyResponsePage />,
  "rf-control-scale-sensitivity": () => <RFControlScaleSensitivityPage />,
  // 同一页面的两个稳定 ID：各自传字面量 experimentId 并作为 key，切换变体时整页重挂载。
  "rf-sensitivity-aw": () => <RFSensitivityDirectAWPage key="rf-sensitivity-aw" experimentId="rf-sensitivity-aw" />,
  "rf-sensitivity-aw-known-noise": () => <RFSensitivityDirectAWPage key="rf-sensitivity-aw-known-noise" experimentId="rf-sensitivity-aw-known-noise" />,
};
