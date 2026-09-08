"""Z 线圈电感效应频率响应采集入口。"""

from lab_workflows.experiment_modules.z_coil_inductance_frequency_response import (
    ADAPTER,
)


if __name__ == "__main__":
    raise SystemExit(ADAPTER.run_cli())
