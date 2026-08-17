"""Keithley 6221 任意波与标定解析的纯逻辑测试（无硬件）。"""

import unittest

import yaml

from lab_workflows.keithley_arb import (
    KeithleyCalibration,
    convert_frequency_source,
    parse_arbitrary_text,
    parse_calibration_text,
)


def calibration_payload(**overrides):
    base = {
        "experiment_id": "mx-keithley-6221-main-field-calibration",
        "success": True,
        "K_f_Hz_per_mA": 1.5,
        "f_0mA_Hz": 1234.5,
        "frequency_linear_fit": {"r_squared": 0.999},
    }
    base.update(overrides)
    return yaml.safe_dump(base, allow_unicode=True)


class ParseArbitraryTextTests(unittest.TestCase):
    def test_parses_single_column_with_comments_and_bom(self):
        text = "\ufeff# 频率序列\n1.0\n2.0\n3.0\n"
        source = parse_arbitrary_text(text)
        self.assertEqual(source.frequency_values_hz, [1.0, 2.0, 3.0])
        self.assertEqual(source.info.source_min, 1.0)
        self.assertEqual(source.info.source_max, 3.0)
        self.assertEqual(source.info.value_label, "frequency_Hz")

    def test_parses_two_columns_and_infers_repeat_frequency(self):
        text = "time_s,frequency_Hz\n0,100\n0.001,300\n0.002,500\n0.003,100\n"
        source = parse_arbitrary_text(text)
        self.assertEqual(source.info.value_label, "frequency_Hz")
        self.assertAlmostEqual(source.info.inferred_frequency_hz, 250.0, places=6)

    def test_rejects_non_increasing_time(self):
        with self.assertRaisesRegex(ValueError, "严格递增"):
            parse_arbitrary_text("t,f\n0,1\n0.001,2\n0.0005,3\n")

    def test_rejects_constant_frequency(self):
        with self.assertRaisesRegex(ValueError, "恒定值"):
            parse_arbitrary_text("100\n100\n")

    def test_rejects_too_many_points(self):
        rows = [str(1000 + index % 7) for index in range(65536)]
        with self.assertRaisesRegex(ValueError, "65,535"):
            parse_arbitrary_text("\n".join(rows))

    def test_rejects_empty_file(self):
        with self.assertRaisesRegex(ValueError, "没有有效数据"):
            parse_arbitrary_text("# 只有注释\n")


class ParseCalibrationTextTests(unittest.TestCase):
    def test_parses_valid_calibration(self):
        calibration = parse_calibration_text(calibration_payload())
        self.assertEqual(calibration.slope_hz_per_ma, 1.5)
        self.assertEqual(calibration.intercept_hz, 1234.5)
        self.assertAlmostEqual(calibration.r_squared, 0.999)

    def test_rejects_wrong_experiment_id(self):
        with self.assertRaisesRegex(ValueError, "实验 ID 不匹配"):
            parse_calibration_text(calibration_payload(experiment_id="other"))

    def test_rejects_failed_calibration(self):
        with self.assertRaisesRegex(ValueError, "success: false"):
            parse_calibration_text(calibration_payload(success=False))

    def test_rejects_non_positive_slope(self):
        with self.assertRaisesRegex(ValueError, "正有限值"):
            parse_calibration_text(calibration_payload(**{"K_f_Hz_per_mA": 0}))


class ConvertFrequencySourceTests(unittest.TestCase):
    def test_normalizes_without_calibration(self):
        source = parse_arbitrary_text("100\n200\n300\n")
        converted = convert_frequency_source(source, None)
        self.assertEqual(converted.points, [-1.0, 0.0, 1.0])
        self.assertIsNone(converted.amplitude_ma)
        self.assertIsNone(converted.offset_ma)
        self.assertIsNone(converted.minimum_ma)
        self.assertIsNone(converted.maximum_ma)

    def test_converts_with_calibration(self):
        source = parse_arbitrary_text("1000\n3000\n5000\n")
        calibration = KeithleyCalibration(slope_hz_per_ma=2.0, intercept_hz=1000.0)
        converted = convert_frequency_source(source, calibration)
        self.assertEqual(converted.minimum_ma, 0.0)
        self.assertEqual(converted.maximum_ma, 2000.0)
        self.assertEqual(converted.offset_ma, 1000.0)
        self.assertEqual(converted.amplitude_ma, 1000.0)
        self.assertEqual(converted.points, [-1.0, 0.0, 1.0])

    def test_rejects_zero_amplitude_after_calibration(self):
        calibration = KeithleyCalibration(slope_hz_per_ma=2.0, intercept_hz=1000.0)
        with self.assertRaisesRegex(ValueError, "正的峰值幅度"):
            from lab_workflows.keithley_arb import ArbitraryFileInfo, ArbitrarySource
            degenerate = ArbitrarySource(
                frequency_values_hz=[1000.0, 1000.0],
                info=ArbitraryFileInfo(
                    source_min=1000.0, source_max=1000.0,
                    normalization_center=1000.0, normalization_scale=1.0,
                ),
            )
            convert_frequency_source(degenerate, calibration)


if __name__ == "__main__":
    unittest.main()
