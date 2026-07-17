import sys
import unittest
from pathlib import Path

from pydantic import TypeAdapter, ValidationError


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "GUI"))

from backend.schemas import (
    DeviceSnapshot,
    GeneratorChannelSettingsBody,
    ScopeSettingsBody,
)


class DeviceSchemaTests(unittest.TestCase):
    def test_generator_settings_reject_unknown_fields(self):
        with self.assertRaises(ValidationError):
            GeneratorChannelSettingsBody.model_validate(
                {"settings": {"frequecy": 1000.0}}
            )

    def test_scope_settings_keep_only_explicit_fields(self):
        body = ScopeSettingsBody.model_validate(
            {"settings": {"timebase_scale": 0.001}}
        )
        self.assertEqual(
            body.settings.model_dump(exclude_unset=True),
            {"timebase_scale": 0.001},
        )

    def test_device_snapshot_is_discriminated_by_type(self):
        value = TypeAdapter(DeviceSnapshot).validate_python(
            {
                "id": "scope",
                "type": "SDS",
                "label": "示波器",
                "resource": "USB::scope",
                "short_resource": "scope",
                "options": {},
                "idn": "SDS",
                "sampling_rate": 1e6,
                "memory_depth": "1M",
                "acquire_type": "NORMal",
                "timebase_scale": 0.001,
                "timebase_delay": 0.0,
                "channels": [],
                "trigger": {
                    "mode": "AUTO",
                    "type": "EDGE",
                    "source": "C1",
                    "slope": "RISing",
                    "level": 0.0,
                },
            }
        )
        self.assertEqual(value.type, "SDS")


if __name__ == "__main__":
    unittest.main()
