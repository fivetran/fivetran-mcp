"""Coverage for split_openapi_by_endpoint.py's per-service config merging.

Regression test for a bug where every connector's `schema_format_*` allOf ref (which
carries the destination `schema`/`schema_prefix`/`table` field(s) and the one genuine
unconditional `required` in the whole config) was silently dropped because it isn't
service-prefixed, and — once included — for a second bug where two allOf sources both
contributing a `config` property would have one overwrite the other instead of merging.
"""
import json
from pathlib import Path

import split_openapi_by_endpoint as split_mod

REPO_ROOT = Path(__file__).resolve().parent.parent


def _fake_components():
    return {
        "schemas": {
            "widget_config_V1": {
                "properties": {
                    "config": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string", "description": "The host"},
                        },
                        "description": "",
                    },
                },
            },
            "schema_format_schema": {
                "properties": {
                    "config": {
                        "required": ["schema"],
                        "type": "object",
                        "properties": {
                            "schema": {
                                "title": "Destination schema name",
                                "type": "string",
                                "description": "Destination schema name.",
                            },
                        },
                    },
                },
            },
            "widget_NewConnectorRequestV1": {
                "allOf": [
                    {"$ref": "#/components/schemas/NewConnectorRequestV1"},
                    {"$ref": "#/components/schemas/widget_config_V1"},
                    {"$ref": "#/components/schemas/schema_format_schema"},
                ],
            },
        },
    }


def test_schema_format_ref_is_included_and_merged():
    cfg = split_mod._merge_service_config(
        "widget", "widget_NewConnectorRequestV1", _fake_components()
    )
    assert cfg is not None
    config_props = cfg["properties"]["config"]["properties"]

    # Both the service-specific field and the shared schema-format field survive —
    # the merge doesn't let one clobber the other.
    assert "host" in config_props
    assert "schema" in config_props
    assert cfg["properties"]["config"]["required"] == ["schema"]
    assert "schema_format_schema" in cfg["x-sources"]


def test_azure_service_bus_real_spec_has_schema_required():
    spec_path = REPO_ROOT / "fivetran-open-api-definition.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    components = spec["components"]

    cfg = split_mod._merge_service_config(
        "azure_service_bus", "azure_service_bus_NewConnectorRequestV1", components
    )
    assert cfg is not None
    config = cfg["properties"]["config"]
    assert "schema" in config["properties"]
    assert config["required"] == ["schema"]


def test_azure_sql_db_real_spec_has_schema_prefix_required():
    spec_path = REPO_ROOT / "fivetran-open-api-definition.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    components = spec["components"]

    cfg = split_mod._merge_service_config(
        "azure_sql_db", "azure_sql_db_NewConnectorRequestV1", components
    )
    assert cfg is not None
    config = cfg["properties"]["config"]
    assert "schema_prefix" in config["properties"]
    assert config["required"] == ["schema_prefix"]
