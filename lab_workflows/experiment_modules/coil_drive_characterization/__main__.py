"""运行目录入口：python -m lab_workflows.experiment_modules.coil_drive_characterization --gain-db <面板增益>

连接 Z_magnetic_field 信号源、Aigtek 1200B、DHO900，依次测功放零偏、正弦扫频和脉冲阶跃。
真实仪器只在带有效 .lab_runtime 的运行目录连接；开发目录在门控处拒绝。
"""

from __future__ import annotations

import argparse
from datetime import datetime

from aigtek_1200b import Aigtek1200BInstrument
from oscilloscope import create_oscilloscope, create_scope_acquisition

from ...common import data_root, load_mapping, load_safety_limits
from ...devices import create_signal_generator
from ...instrument_config import load_device_definitions
from ...steps import synchronize_connected_clocks
from ...steps.safety_shutdown import DisconnectTarget, disconnect_devices
from .measurement import SOURCE_KEY, CoilDriveCharacterization, CoilDriveParams

AMPLIFIER_ID = "aigtek_1200b_cp210x_0001"
SCOPE_ID = "rigol_dho900_192_168_124_21"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Coil drive electrical characterization")
    parser.add_argument("--gain-db", type=float, required=True, help="1200B 面板当前增益 (dB)，脚本不修改")
    parser.add_argument("--sine-current-a", type=float, default=0.1)
    parser.add_argument("--pulse-current-a", type=float, default=0.1)
    parser.add_argument("--coil-ohm", type=float, default=0.0, help="线圈直流电阻；0 偏保守")
    parser.add_argument("--freq-stop-hz", type=float, default=500e3)
    parser.add_argument("--tag", default="coil_drive")
    args = parser.parse_args(argv)
    params = CoilDriveParams(amplifier_gain_db=args.gain_db, sine_peak_current_a=args.sine_current_a,
                             pulse_current_a=args.pulse_current_a, coil_resistance_ohm=args.coil_ohm,
                             frequency_stop_hz=args.freq_stop_hz)
    limits = load_safety_limits()
    definitions = load_device_definitions()
    amplifier_config = definitions[AMPLIFIER_ID].to_config()
    directory = data_root() / "Coil_Drive_Characterization" / f"{datetime.now():%m%d_%H%M}_{args.tag}"

    source = create_signal_generator(SOURCE_KEY)
    scope = create_oscilloscope(definitions[SCOPE_ID].to_config())
    holder: dict[str, CoilDriveCharacterization] = {}
    amplifier = Aigtek1200BInstrument(
        amplifier_config["resource"], serial_number=amplifier_config["connection"]["serial_number"],
        safety_check=lambda operation, value: holder["run"].amplifier_safety_check(operation, value))
    targets = (DisconnectTarget("DHO900", scope), DisconnectTarget("1200B", amplifier),
               DisconnectTarget("Z 信号源", source))
    try:
        source.connect()
        scope.connect()
        amplifier.connect()
        synchronize_connected_clocks({"z_source": source}, load_mapping(), {"z_source": SOURCE_KEY})
    except BaseException:
        # 连接阶段尚未打开任何输出；只释放通信。
        disconnect_devices(targets)
        raise
    run = CoilDriveCharacterization(params, source=source, amplifier=amplifier,
                                    scope_acquisition=create_scope_acquisition(scope),
                                    directory=directory, limits=limits, disconnect_targets=targets)
    holder["run"] = run
    record = run.run()
    print(f"完成：{record.directory}")
    print(f"收尾：{record.shutdown}")


if __name__ == "__main__":
    main()
