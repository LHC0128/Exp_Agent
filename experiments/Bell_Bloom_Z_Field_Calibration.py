# %% Bell Bloom Z 磁场频率标定：从版本化默认参数启动采集
from lab_workflows.experiment_modules.bell_bloom_z_field_calibration.definition import ADAPTER


def main() -> int:
    return ADAPTER.run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
