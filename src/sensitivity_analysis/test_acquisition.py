"""离线采样率读取的历史兼容和无效记录回归。"""

from pathlib import Path
import tempfile
import unittest

import numpy as np

from .acquisition import load_acquisition_rate


class AcquisitionMetadataTests(unittest.TestCase):
    def test_saved_rate_overrides_fallback_and_rejects_invalid_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "waveform_C0000.npy"
            self.assertEqual(load_acquisition_rate(path, 100), 100)
            metadata = path.with_name(path.stem + "_acquisition.npz")
            np.savez(metadata, actual_rate_Sa_s=200)
            self.assertEqual(load_acquisition_rate(path, 100), 200)
            for rate in (0, -1, float("nan"), float("inf")):
                with self.subTest(rate=rate):
                    np.savez(metadata, actual_rate_Sa_s=rate)
                    with self.assertRaisesRegex(ValueError, "实际采样率"):
                        load_acquisition_rate(path, 100)


if __name__ == "__main__":
    unittest.main()
