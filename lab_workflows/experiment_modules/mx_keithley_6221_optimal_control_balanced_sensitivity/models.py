"""Mx Keithley 6221 最优控制 平衡-灵敏度一体化实验参数。"""

from __future__ import annotations

from dataclasses import dataclass

from ...experiment_params import parameter
from ..mx_keithley_6221_optimal_control_rf_sensitivity.models import (
    MxKeithley6221OptimalControlRFParams,
)


@dataclass(slots=True)
class MxKeithley6221OptimalControlBalancedSensitivityParams(
    MxKeithley6221OptimalControlRFParams
):
    """一体化参数：先 XYZ 平衡定位工作点，再用平衡点直接测量 RF 灵敏度。

    继承 6221 RF 灵敏度实验的全部参数（控制、触发、相位校准、幅度扫描、
    噪声采集），新增 ``BALANCE_*`` 平衡阶段参数。平衡阶段自动执行
    粗扫 + 可选细扫，工作点由逐层复线性模拟合给出，并在平衡完成后
    数秒内直接进入 RF 灵敏度测量，最小化剩磁漂移引入的失配。

    父类的 ``FIXED_PARAMS.X/Y/main_magnetic_field`` 作为平衡阶段失败时
    的回退补偿值；平衡成功后由 workflow 用拟合工作点覆盖。
    """

    schema_version = 1

    balance_coarse_x_start_v: float = parameter(
        default=-0.05,
        external_name="BALANCE_COARSE_X_START_V",
        label="平衡粗扫 X 起点",
        unit="V",
        group="basic",
    )
    balance_coarse_x_stop_v: float = parameter(
        default=0.05,
        external_name="BALANCE_COARSE_X_STOP_V",
        label="平衡粗扫 X 终点",
        unit="V",
        group="basic",
    )
    balance_coarse_x_points: int = parameter(
        default=9,
        external_name="BALANCE_COARSE_X_POINTS",
        label="平衡粗扫 X 点数",
        group="basic",
        minimum=2,
    )
    balance_coarse_y_start_v: float = parameter(
        default=-0.05,
        external_name="BALANCE_COARSE_Y_START_V",
        label="平衡粗扫 Y 起点",
        unit="V",
        group="basic",
    )
    balance_coarse_y_stop_v: float = parameter(
        default=0.25,
        external_name="BALANCE_COARSE_Y_STOP_V",
        label="平衡粗扫 Y 终点",
        unit="V",
        group="basic",
        description="Y 剩磁历史漂移大，默认范围覆盖 -0.05 到 +0.25 V。",
    )
    balance_coarse_y_points: int = parameter(
        default=16,
        external_name="BALANCE_COARSE_Y_POINTS",
        label="平衡粗扫 Y 点数",
        group="basic",
        minimum=2,
    )
    balance_coarse_z_start_ma: float = parameter(
        default=-0.05,
        external_name="BALANCE_COARSE_Z_START_MA",
        label="平衡粗扫 Z 起点",
        unit="mA",
        group="basic",
        safety_key="main_magnetic_field",
    )
    balance_coarse_z_stop_ma: float = parameter(
        default=0.05,
        external_name="BALANCE_COARSE_Z_STOP_MA",
        label="平衡粗扫 Z 终点",
        unit="mA",
        group="basic",
        safety_key="main_magnetic_field",
    )
    balance_coarse_z_points: int = parameter(
        default=3,
        external_name="BALANCE_COARSE_Z_POINTS",
        label="平衡粗扫 Z 点数",
        group="basic",
        minimum=2,
    )
    balance_fine_enabled: bool = parameter(
        default=True,
        external_name="BALANCE_FINE_ENABLED",
        label="启用平衡细扫",
        group="basic",
        description="以粗扫拟合零点为中心做第二轮细网格扫描，提高工作点精度。",
    )
    balance_fine_span_v: float = parameter(
        default=0.02,
        external_name="BALANCE_FINE_SPAN_V",
        label="平衡细扫 X/Y 半宽",
        unit="V",
        group="basic",
        minimum=0.001,
        maximum=0.5,
    )
    balance_fine_points: int = parameter(
        default=11,
        external_name="BALANCE_FINE_POINTS",
        label="平衡细扫 X/Y 点数",
        group="basic",
        minimum=5,
    )
    balance_fine_z_start_ma: float = parameter(
        default=-0.03,
        external_name="BALANCE_FINE_Z_START_MA",
        label="平衡细扫 Z 起点（相对）",
        unit="mA",
        group="basic",
        description="相对粗扫拟合 z0 的偏移；细扫 z 范围为 z0+起点 到 z0+终点。",
    )
    balance_fine_z_stop_ma: float = parameter(
        default=0.03,
        external_name="BALANCE_FINE_Z_STOP_MA",
        label="平衡细扫 Z 终点（相对）",
        unit="mA",
        group="basic",
    )
    balance_fine_z_points: int = parameter(
        default=3,
        external_name="BALANCE_FINE_Z_POINTS",
        label="平衡细扫 Z 点数",
        group="basic",
        minimum=2,
    )
    balance_coupling_min_r_squared: float = parameter(
        default=0.5,
        external_name="BALANCE_COUPLING_MIN_R_SQUARED",
        label="平衡拟合最低 R² 门槛",
        group="advanced",
        minimum=0.0,
        maximum=1.0,
        description="切片拟合 R² 低于该值的层不参与耦合斜率拟合。",
    )

    def validate_model(self) -> list[str]:
        errors = MxKeithley6221OptimalControlRFParams.validate_model(self)
        for name, start, stop, points in (
            (
                "平衡粗扫 X",
                self.balance_coarse_x_start_v,
                self.balance_coarse_x_stop_v,
                self.balance_coarse_x_points,
            ),
            (
                "平衡粗扫 Y",
                self.balance_coarse_y_start_v,
                self.balance_coarse_y_stop_v,
                self.balance_coarse_y_points,
            ),
            (
                "平衡粗扫 Z",
                self.balance_coarse_z_start_ma,
                self.balance_coarse_z_stop_ma,
                self.balance_coarse_z_points,
            ),
            (
                "平衡细扫 Z",
                self.balance_fine_z_start_ma,
                self.balance_fine_z_stop_ma,
                self.balance_fine_z_points,
            ),
        ):
            if start >= stop:
                errors.append(f"{name} 必须满足起点 < 终点")
            elif points < 2:
                errors.append(f"{name} 点数必须不少于 2")
        if self.balance_fine_span_v <= 0:
            errors.append("平衡细扫半宽必须大于 0")
        if self.balance_fine_points < 5:
            errors.append("平衡细扫 X/Y 点数必须不少于 5")
        if not 0.0 <= self.balance_coupling_min_r_squared <= 1.0:
            errors.append("平衡拟合最低 R² 门槛必须在 0 到 1 之间")
        return errors
