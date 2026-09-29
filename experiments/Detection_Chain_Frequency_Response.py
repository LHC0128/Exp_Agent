"""探测链路频率响应标定采集薄入口。"""

from lab_workflows.experiment_modules.detection_chain_frequency_response.definition import ADAPTER


def main() -> int:
    return ADAPTER.run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
