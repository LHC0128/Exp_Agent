import argparse
import logging
import sys
from pathlib import Path

from sds_acquisition.config import AcquisitionConfig
from sds_acquisition.instrument import SDSInstrument
from sds_acquisition.acquire import SDSAcquisition
from sds_acquisition.save import save_to_csv, save_to_npz, save_to_mat, save_to_h5, save_to_bin

logger = logging.getLogger(__name__)

DEFAULT_RESOURCE = "USB0::0xF4EC::0x1015::SDSEV82X900704::INSTR"
DEFAULT_CONFIG = "params/default_config.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SDS 系列数字示波器数据采集程序")
    parser.add_argument("--config", "-c", type=str, default=DEFAULT_CONFIG,
                        help="配置文件路径 (YAML)")
    parser.add_argument("--output", "-o", type=str, default="data/",
                        help="输出目录或文件路径")
    parser.add_argument("--format", "-f", choices=["csv", "npz", "mat", "h5", "bin"],
                        default="npz", help="""数据格式:
                        npz (默认, 紧凑, ~2 B/点),
                        h5 (HDF5+gzip, 适合大数据),
                        bin (原始二进制, 极致精简),
                        csv (大, 可读),
                        mat (MATLAB兼容)""")
    parser.add_argument("--save-mode", "-m",
                        choices=["raw", "voltage_time", "voltage_only", "all"],
                        default="raw", help="NPZ 保存内容 (默认 raw=仅 raw+元数据)")
    parser.add_argument("--resource", "-r", type=str,
                        default=DEFAULT_RESOURCE,
                        help="VISA 资源地址")
    parser.add_argument("--timeout", "-t", type=int, default=10000,
                        help="VISA 超时时间 (ms)")
    parser.add_argument("--plot", action="store_true",
                        help="采集后生成快速预览图")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="显示调试日志")
    return parser


def _quick_plot(results, output_dir: str) -> None:
    """生成采集数据的快速预览图。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib 未安装，跳过绘图")
        return

    fig, ax = plt.subplots(figsize=(10, 6))
    for r in results:
        ax.plot(r.time * 1e3, r.voltage, label=f"CH{r.channel}", lw=0.8)
    ax.set_xlabel("Time (ms)")
    ax.set_ylabel("Voltage (V)")
    ax.legend()
    ax.grid(True, alpha=0.3)

    out_dir = Path(output_dir) if Path(output_dir).is_dir() else Path(output_dir).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    plot_path = out_dir / "acquisition_preview.png"
    fig.savefig(str(plot_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info("预览图已保存: %s", plot_path)


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.verbose)

    # 加载配置
    config_path = Path(args.config)
    if config_path.exists():
        config = AcquisitionConfig.from_yaml(str(config_path))
        logger.info("已加载配置: %s", config_path)
    else:
        logger.warning("配置文件 %s 不存在，使用默认配置", config_path)
        config = AcquisitionConfig()

    # 连接并采集
    inst = SDSInstrument(resource_string=args.resource, timeout=args.timeout)
    try:
        inst.connect()
        logger.info("已连接: %s", inst.idn())

        acq = SDSAcquisition(inst)
        results = acq.acquire_all(config)

        if not results:
            logger.error("未采集到任何通道数据")
            sys.exit(1)

        logger.info("采集完成: %d 个通道", len(results))
        for r in results:
            logger.info("  CH%d: %d 点, %.3f s 时长",
                        r.channel, len(r.voltage),
                        r.time[-1] - r.time[0] if len(r.time) > 1 else 0)

        # 保存数据
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)

        if args.format == "csv":
            for r in results:
                save_to_csv(args.output, r)
        elif args.format == "mat":
            save_to_mat(args.output, results, config.to_dict())
        elif args.format == "h5":
            save_to_h5(args.output, results, config.to_dict())
        elif args.format == "bin":
            save_to_bin(args.output, results, config.to_dict())
        else:
            save_to_npz(args.output, results, config.to_dict(),
                         save_mode=args.save_mode)

        # 快速预览图
        if args.plot:
            _quick_plot(results, args.output)

    except Exception as e:
        logger.error("采集失败: %s", e, exc_info=True)
        sys.exit(1)
    finally:
        inst.disconnect()
