"""Tests for Fivetran MCP Server API calls."""

import base64
import os
import pytest
import httpx
from unittest.mock import patch, AsyncMock

# Set test environment variables before importing server
os.environ["FIVETRAN_APIKEY"] = "test_api_key"
os.environ["FIVETRAN_APISECRET"] = "test_api_secret"

from server import (
    get_auth_header,
    fivetran_request,
    execute_tool,
    list_tools,
    call_tool,
    BASE_URL,
    TOOLS,
)


class TestAuthHeader:
    """Tests for authentication header generation."""

    def test_get_auth_header_returns_correct_format(self):
        """Test that auth header is correctly formatted."""
        header = get_auth_header()

        assert "Authorization" in header
        assert "Accept" in header
        assert header["Accept"] == "application/json"
        assert header["Authorization"].startswith("Basic ")

    def test_get_auth_header_encodes_credentials_correctly(self):
        """Test that credentials are properly base64 encoded."""
        header = get_auth_header()

        # Decode and verify
        encoded_part = header["Authorization"].replace("Basic ", "")
        decoded = base64.b64decode(encoded_part).decode()
        assert decoded == "test_api_key:test_api_secret"

    def test_get_auth_header_raises_without_credentials(self):
        """Test that missing credentials raises ValueError."""
        with patch.dict(os.environ, {"FIVETRAN_APIKEY": "", "FIVETRAN_APISECRET": ""}):
            # Need to reload the module to pick up new env vars
            import importlib
            import server
            importlib.reload(server)

            with pytest.raises(ValueError, match="FIVETRAN_APIKEY and FIVETRAN_APISECRET must be set"):
                server.get_auth_header()

            # Restore original values
            os.environ["FIVETRAN_APIKEY"] = "test_api_key"
            os.environ["FIVETRAN_APISECRET"] = "test_api_secret"
            importlib.reload(server)


class TestFivetranRequest:
    """Tests for the base Fivetran API request function."""

    @pytest.mark.asyncio
    async def test_fivetran_request_makes_correct_get_request(self, httpx_mock):
        """Test that GET requests are made correctly."""
        httpx_mock.add_response(
            url=f"{BASE_URL}/v1/connections",
            json={"data": {"items": []}},
        )

        result = await fivetran_request("GET", "/v1/connections")

        assert result == {"data": {"items": []}}

    @pytest.mark.asyncio
    async def test_fivetran_request_includes_params(self, httpx_mock):
        """Test that query parameters are included."""
        httpx_mock.add_response(
            url=f"{BASE_URL}/v1/connections?limit=50",
            json={"data": {"items": []}},
        )

        result = await fivetran_request("GET", "/v1/connections", params={"limit": 50})

        assert result == {"data": {"items": []}}

    @pytest.mark.asyncio
    async def test_fivetran_request_raises_on_http_error(self, httpx_mock):
        """Test that HTTP errors are propagated."""
        httpx_mock.add_response(
            url=f"{BASE_URL}/v1/connections",
            status_code=401,
        )

        with pytest.raises(httpx.HTTPStatusError):
            await fivetran_request("GET", "/v1/connections")


class TestListConnections:
    """Tests for list_connections tool."""

    @pytest.mark.asyncio
    async def test_list_connections_returns_items(self, httpx_mock):
        """Test listing connections returns items."""
        expected_response = {
            "data": {
                "items": [
                    {"id": "conn_1", "name": "Connection 1", "service": "postgres"},
                    {"id": "conn_2", "name": "Connection 2", "service": "mysql"},
                ]
            }
        }
        httpx_mock.add_response(
            url=f"{BASE_URL}/v1/connections?limit=1000",
            json=expected_response,
        )

        result = await execute_tool("list_connections", {})

        # Auto-paginate wraps response
        assert "data" in result
        assert "items" in result["data"]


class TestGetConnectionDetails:
    """Tests for get_connection_details tool."""

    @pytest.mark.asyncio
    async def test_get_connection_details_success(self, httpx_mock):
        """Test getting connection details."""
        expected_response = {
            "data": {
                "id": "conn_123",
                "name": "My Connection",
                "service": "postgres",
                "status": {
                    "setup_state": "connected",
                    "sync_state": "syncing",
                },
            }
        }
        httpx_mock.add_response(
            url=f"{BASE_URL}/v1/connections/conn_123",
            json=expected_response,
        )

        result = await execute_tool("get_connection_details", {"connection_id": "conn_123"})

        assert result == expected_response

    @pytest.mark.asyncio
    async def test_get_connection_details_not_found(self, httpx_mock):
        """Test getting details for non-existent connection."""
        httpx_mock.add_response(
            url=f"{BASE_URL}/v1/connections/invalid_id",
            status_code=404,
            json={"message": "Connection not found"},
        )

        with pytest.raises(httpx.HTTPStatusError):
            await execute_tool("get_connection_details", {"connection_id": "invalid_id"})


class TestGetConnectionState:
    """Tests for get_connection_state tool."""

    @pytest.mark.asyncio
    async def test_get_connection_state_success(self, httpx_mock):
        """Test getting connection state."""
        expected_response = {
            "data": {
                "connection_id": "conn_123",
                "schema_state": {"schema1": {"enabled": True}},
            }
        }
        httpx_mock.add_response(
            url=f"{BASE_URL}/v1/connections/conn_123/state",
            json=expected_response,
        )

        result = await execute_tool("get_connection_state", {"connection_id": "conn_123"})

        assert result == expected_response


class TestGetConnectionSchemaConfig:
    """Tests for get_connection_schema_config tool."""

    @pytest.mark.asyncio
    async def test_get_connection_schema_config_success(self, httpx_mock):
        """Test getting connection schema configuration."""
        expected_response = {
            "data": {
                "schemas": {
                    "public": {
                        "enabled": True,
                        "tables": {
                            "users": {"enabled": True},
                            "orders": {"enabled": False},
                        }
                    }
                }
            }
        }
        httpx_mock.add_response(
            url=f"{BASE_URL}/v1/connections/conn_123/schemas",
            json=expected_response,
        )

        result = await execute_tool("get_connection_schema_config", {"connection_id": "conn_123"})

        assert result == expected_response


class TestListDestinations:
    """Tests for list_destinations tool."""

    @pytest.mark.asyncio
    async def test_list_destinations_returns_items(self, httpx_mock):
        """Test listing destinations returns items."""
        expected_response = {
            "data": {
                "items": [
                    {"id": "dest_1", "service": "snowflake", "region": "us-east-1"},
                    {"id": "dest_2", "service": "bigquery", "region": "us-central1"},
                ]
            }
        }
        httpx_mock.add_response(
            url=f"{BASE_URL}/v1/destinations?limit=1000",
            json=expected_response,
        )

        result = await execute_tool("list_destinations", {})

        assert "data" in result
        assert "items" in result["data"]


class TestGetDestinationDetails:
    """Tests for get_destination_details tool."""

    @pytest.mark.asyncio
    async def test_get_destination_details_success(self, httpx_mock):
        """Test getting destination details."""
        expected_response = {
            "data": {
                "id": "dest_123",
                "service": "snowflake",
                "region": "us-east-1",
                "config": {
                    "host": "account.snowflakecomputing.com",
                    "database": "analytics",
                }
            }
        }
        httpx_mock.add_response(
            url=f"{BASE_URL}/v1/destinations/dest_123",
            json=expected_response,
        )

        result = await execute_tool("get_destination_details", {"destination_id": "dest_123"})

        assert result == expected_response


class TestListGroups:
    """Tests for list_groups tool."""

    @pytest.mark.asyncio
    async def test_list_groups_returns_items(self, httpx_mock):
        """Test listing groups returns items."""
        expected_response = {
            "data": {
                "items": [
                    {"id": "group_1", "name": "Production"},
                    {"id": "group_2", "name": "Development"},
                ]
            }
        }
        httpx_mock.add_response(
            url=f"{BASE_URL}/v1/groups?limit=1000",
            json=expected_response,
        )

        result = await execute_tool("list_groups", {})

        assert "data" in result
        assert "items" in result["data"]


class TestGetGroupDetails:
    """Tests for get_group_details tool."""

    @pytest.mark.asyncio
    async def test_get_group_details_success(self, httpx_mock):
        """Test getting group details."""
        expected_response = {
            "data": {
                "id": "group_123",
                "name": "Production",
                "created_at": "2024-01-01T00:00:00Z",
            }
        }
        httpx_mock.add_response(
            url=f"{BASE_URL}/v1/groups/group_123",
            json=expected_response,
        )

        result = await execute_tool("get_group_details", {"group_id": "group_123"})

        assert result == expected_response


class TestListConnectionsInGroup:
    """Tests for list_connections_in_group tool."""

    @pytest.mark.asyncio
    async def test_list_connections_in_group_success(self, httpx_mock):
        """Test listing connections in a group."""
        expected_response = {
            "data": {
                "items": [
                    {"id": "conn_1", "name": "Connection 1"},
                    {"id": "conn_2", "name": "Connection 2"},
                ]
            }
        }
        httpx_mock.add_response(
            url=f"{BASE_URL}/v1/groups/group_123/connections?limit=1000",
            json=expected_response,
        )

        result = await execute_tool("list_connections_in_group", {"group_id": "group_123"})

        assert "data" in result
        assert "items" in result["data"]


class TestUnknownTool:
    """Tests for unknown tool handling."""

    @pytest.mark.asyncio
    async def test_unknown_tool_raises_error(self):
        """Test that unknown tools raise KeyError for unknown tools."""
        with pytest.raises(KeyError):
            await execute_tool("nonexistent_tool", {})


class TestCallTool:
    """Tests for the call_tool handler."""

    @pytest.mark.asyncio
    async def test_call_tool_validates_schema_file(self):
        """Test that call_tool requires correct schema_file."""
        result = await call_tool("list_groups", {"schema_file": "wrong/path.json"})

        assert len(result) == 1
        assert "Invalid schema_file" in result[0].text or "Error" in result[0].text

    @pytest.mark.asyncio
    async def test_call_tool_handles_unknown_tool(self):
        """Test that unknown tools are handled gracefully."""
        result = await call_tool("unknown_tool", {})

        assert len(result) == 1
        assert "Error" in result[0].text or "Unknown tool" in result[0].text


class TestListTools:
    """Tests for the list_tools handler."""

    @pytest.mark.asyncio
    async def test_list_tools_returns_tools(self):
        """Test that tools are returned."""
        tools = await list_tools()

        # Should have all tools defined in TOOLS dict
        assert len(tools) == len(TOOLS)

        tool_names = [t.name for t in tools]
        # Check some core tools exist
        assert "list_connections" in tool_names
        assert "get_connection_details" in tool_names
        assert "list_destinations" in tool_names
        assert "list_groups" in tool_names

    @pytest.mark.asyncio
    async def test_list_tools_have_descriptions(self):
        """Test that all tools have descriptions."""
        tools = await list_tools()

        for tool in tools:
            assert tool.description
            assert len(tool.description) > 10

    @pytest.mark.asyncio
    async def test_list_tools_have_input_schemas(self):
        """Test that all tools have input schemas."""
        tools = await list_tools()

        for tool in tools:
            assert tool.inputSchema
            assert "type" in tool.inputSchema
            assert tool.inputSchema["type"] == "object"

    @pytest.mark.asyncio
    async def test_list_tools_require_schema_file(self):
        """Test that all tools require schema_file parameter."""
        tools = await list_tools()

        for tool in tools:
            assert "schema_file" in tool.inputSchema.get("required", [])
            assert "schema_file" in tool.inputSchema.get("properties", {})


# Pytest fixtures
@pytest.fixture
def httpx_mock(monkeypatch):
    """Fixture to mock httpx requests."""
    class MockTransport(httpx.MockTransport):
        def __init__(self):
            self.responses = []
            super().__init__(self._handler)

        def _handler(self, request):
            for response_config in self.responses:
                if self._matches(request, response_config):
                    return httpx.Response(
                        status_code=response_config.get("status_code", 200),
                        json=response_config.get("json"),
                    )
            raise Exception(f"No mock configured for {request.method} {request.url}")

        def _matches(self, request, config):
            expected_url = config["url"]
            actual_url = str(request.url)
            # Normalize URLs for comparison (handle query param order)
            return expected_url == actual_url or self._urls_match(expected_url, actual_url)

        def _urls_match(self, expected, actual):
            from urllib.parse import urlparse, parse_qs
            exp_parsed = urlparse(expected)
            act_parsed = urlparse(actual)

            if exp_parsed.scheme != act_parsed.scheme:
                return False
            if exp_parsed.netloc != act_parsed.netloc:
                return False
            if exp_parsed.path != act_parsed.path:
                return False

            exp_params = parse_qs(exp_parsed.query)
            act_params = parse_qs(act_parsed.query)
            return exp_params == act_params

        def add_response(self, url, json=None, status_code=200):
            self.responses.append({
                "url": url,
                "json": json,
                "status_code": status_code,
            })

    mock = MockTransport()

    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs['transport'] = mock
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    return mock
