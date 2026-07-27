from lab_workflows.static_sensitivity import StaticSensitivityParams


def test_static_sensitivity_schema_uses_frontend_supported_types() -> None:
    schema = StaticSensitivityParams.schema(StaticSensitivityParams.from_yaml())
    fields = {field["name"]: field for field in schema["fields"]}

    assert fields["run_tag"]["type"] == "string"
    assert fields["pump_laser_power"]["type"] == "number"
    assert fields["noise_n_avg"]["type"] == "integer"
    assert fields["gs200_current_ranges"]["type"] == "array"
    assert {field["type"] for field in schema["fields"]} <= {
        "string",
        "boolean",
        "integer",
        "number",
        "array",
    }
