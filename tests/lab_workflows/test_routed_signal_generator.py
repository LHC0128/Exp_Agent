from copy import deepcopy
from unittest.mock import MagicMock, patch

from lab_workflows.common import load_mapping
from lab_workflows.experiments import list_experiments
from lab_workflows.steps import (
    DeviceSession,
    connect_signal_generator_routes,
    synchronize_connected_clocks,
)


def _config(resource: str, channel: int, device_id: str) -> dict:
    return {
        "instrument": "signal_generator",
        "model": "DG900",
        "resource": resource,
        "channel": channel,
        "device_id": device_id,
    }


def test_routes_same_channel_number_to_independent_devices() -> None:
    first = MagicMock()
    second = MagicMock()
    first.connect = MagicMock()
    second.connect = MagicMock()
    configs = {
        "x": _config("USB0::A::INSTR", 1, "a"),
        "y": _config("USB0::B::INSTR", 1, "b"),
    }
    with patch(
        "lab_workflows.steps.routed_signal_generator.create_signal_generator",
        side_effect=[first, second],
    ):
        routed, channels = connect_signal_generator_routes(
            DeviceSession(),
            "xy_field",
            {
                "x_field": ("x", configs["x"]),
                "y_field": ("y", configs["y"]),
            },
        )

    assert channels["x_field"] == 1
    assert channels["y_field"] != 1
    routed.setup_dc(0.1, channel=channels["x_field"])
    routed.setup_dc(0.2, channel=channels["y_field"])
    first.setup_dc.assert_called_once_with(0.1, channel=1)
    second.setup_dc.assert_called_once_with(0.2, channel=1)


def test_routes_reuse_one_session_for_same_resource() -> None:
    device = MagicMock()
    config_x = _config("USB0::A::INSTR", 1, "a")
    config_y = _config("USB0::A::INSTR", 2, "a")
    with patch(
        "lab_workflows.steps.routed_signal_generator.create_signal_generator",
        return_value=device,
    ) as factory:
        routed, channels = connect_signal_generator_routes(
            DeviceSession(),
            "xy_field",
            {
                "x_field": ("x", config_x),
                "y_field": ("y", config_y),
            },
        )

    assert channels == {"x_field": 1, "y_field": 2}
    assert len(routed.iter_physical_routes()) == 1
    factory.assert_called_once_with(config_x)
    device.connect.assert_called_once()


def test_clock_sync_expands_routed_physical_devices() -> None:
    first = MagicMock()
    second = MagicMock()
    for device in (first, second):
        device.get_ref_clock_source.return_value = "EXT"
    mapping = {
        "x": _config("USB0::VENDOR::MODEL::A::INSTR", 1, "a"),
        "y": _config("USB0::VENDOR::MODEL::B::INSTR", 1, "b"),
    }
    with patch(
        "lab_workflows.steps.routed_signal_generator.create_signal_generator",
        side_effect=[first, second],
    ):
        routed, _ = connect_signal_generator_routes(
            DeviceSession(),
            "xy_field",
            {
                "x_field": ("x", mapping["x"]),
                "y_field": ("y", mapping["y"]),
            },
        )

    records = synchronize_connected_clocks(
        {"xy_field": routed},
        mapping,
        {"xy_field": "x"},
        profile={"__default__": "EXT", "A": "EXT", "B": "EXT"},
        settle_s=0,
    )
    assert set(records) == {"xy_field", "xy_field:y"}
    first.set_ref_clock_source.assert_called_once_with("EXTernal")
    second.set_ref_clock_source.assert_called_once_with("EXTernal")


def test_all_typed_workflows_preflight_dg4000_and_dg900_mappings() -> None:
    base = load_mapping()
    definitions = [
        item for item in list_experiments()
        if item.execution_mode == "typed_workflow"
    ]
    assert len(definitions) == 20
    assert all(item.required_mapping_keys for item in definitions)

    for model, minimum, maximum in (
        ("DG4000", 2, 16_384),
        ("DG900", 32, 16 * 1024 * 1024),
    ):
        mapping = deepcopy(base)
        for config in mapping.values():
            if config.get("instrument") != "signal_generator":
                continue
            config["model"] = model
            config["capabilities"] = {
                "channels": [1, 2],
                "min_arb_points": minimum,
                "max_arb_points": maximum,
                "supports_arbitrary": True,
                "supports_infinite_burst": True,
            }
        with patch("lab_workflows.common.load_mapping", return_value=mapping):
            failures = {
                item.id: item._mapping_errors()
                for item in definitions
                if item._mapping_errors()
            }
        assert failures == {}, (model, failures)


def test_experiment_device_labels_follow_current_mapping() -> None:
    definition = next(
        item for item in list_experiments()
        if item.id == "mx-y-rf-sensitivity"
    )
    mapping = deepcopy(load_mapping())
    mapping["Z_magnetic_field"].update({
        "device_id": "replacement",
        "label": "替换 Z 信号源",
        "model": "DG900",
    })
    with patch("lab_workflows.common.load_mapping", return_value=mapping):
        required = definition.public()["required_devices"]
    assert "替换 Z 信号源 (DG900)" in required
