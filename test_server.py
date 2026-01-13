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
    _execute_tool,
    _format_result,
    list_tools,
    call_tool,
    BASE_URL,
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
            url=f"{BASE_URL}/connections",
            json={"data": {"items": []}},
        )

        result = await fivetran_request("GET", "/connections")

        assert result == {"data": {"items": []}}

    @pytest.mark.asyncio
    async def test_fivetran_request_includes_params(self, httpx_mock):
        """Test that query parameters are included."""
        httpx_mock.add_response(
            url=f"{BASE_URL}/connections?limit=50",
            json={"data": {"items": []}},
        )

        result = await fivetran_request("GET", "/connections", params={"limit": 50})

        assert result == {"data": {"items": []}}

    @pytest.mark.asyncio
    async def test_fivetran_request_raises_on_http_error(self, httpx_mock):
        """Test that HTTP errors are propagated."""
        httpx_mock.add_response(
            url=f"{BASE_URL}/connections",
            status_code=401,
        )

        with pytest.raises(httpx.HTTPStatusError):
            await fivetran_request("GET", "/connections")


class TestListConnections:
    """Tests for list_connections tool."""

    @pytest.mark.asyncio
    async def test_list_connections_no_params(self, httpx_mock):
        """Test listing connections without parameters."""
        expected_response = {
            "data": {
                "items": [
                    {"id": "conn_1", "name": "Connection 1", "service": "postgres"},
                    {"id": "conn_2", "name": "Connection 2", "service": "mysql"},
                ]
            }
        }
        httpx_mock.add_response(
            url=f"{BASE_URL}/connections",
            json=expected_response,
        )

        result = await _execute_tool("list_connections", {})

        assert result == expected_response

    @pytest.mark.asyncio
    async def test_list_connections_with_limit(self, httpx_mock):
        """Test listing connections with limit parameter."""
        httpx_mock.add_response(
            url=f"{BASE_URL}/connections?limit=10",
            json={"data": {"items": []}},
        )

        result = await _execute_tool("list_connections", {"limit": 10})

        assert result == {"data": {"items": []}}

    @pytest.mark.asyncio
    async def test_list_connections_with_cursor(self, httpx_mock):
        """Test listing connections with pagination cursor."""
        httpx_mock.add_response(
            url=f"{BASE_URL}/connections?cursor=abc123",
            json={"data": {"items": []}, "next_cursor": "def456"},
        )

        result = await _execute_tool("list_connections", {"cursor": "abc123"})

        assert "next_cursor" in result


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
            url=f"{BASE_URL}/connections/conn_123",
            json=expected_response,
        )

        result = await _execute_tool("get_connection_details", {"connection_id": "conn_123"})

        assert result == expected_response

    @pytest.mark.asyncio
    async def test_get_connection_details_not_found(self, httpx_mock):
        """Test getting details for non-existent connection."""
        httpx_mock.add_response(
            url=f"{BASE_URL}/connections/invalid_id",
            status_code=404,
            json={"message": "Connection not found"},
        )

        with pytest.raises(httpx.HTTPStatusError):
            await _execute_tool("get_connection_details", {"connection_id": "invalid_id"})


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
            url=f"{BASE_URL}/connections/conn_123/state",
            json=expected_response,
        )

        result = await _execute_tool("get_connection_state", {"connection_id": "conn_123"})

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
            url=f"{BASE_URL}/connections/conn_123/schemas",
            json=expected_response,
        )

        result = await _execute_tool("get_connection_schema_config", {"connection_id": "conn_123"})

        assert result == expected_response


class TestListDestinations:
    """Tests for list_destinations tool."""

    @pytest.mark.asyncio
    async def test_list_destinations_no_params(self, httpx_mock):
        """Test listing destinations without parameters."""
        expected_response = {
            "data": {
                "items": [
                    {"id": "dest_1", "service": "snowflake", "region": "us-east-1"},
                    {"id": "dest_2", "service": "bigquery", "region": "us-central1"},
                ]
            }
        }
        httpx_mock.add_response(
            url=f"{BASE_URL}/destinations",
            json=expected_response,
        )

        result = await _execute_tool("list_destinations", {})

        assert result == expected_response

    @pytest.mark.asyncio
    async def test_list_destinations_with_pagination(self, httpx_mock):
        """Test listing destinations with pagination."""
        httpx_mock.add_response(
            url=f"{BASE_URL}/destinations?limit=5&cursor=page2",
            json={"data": {"items": []}},
        )

        result = await _execute_tool("list_destinations", {"limit": 5, "cursor": "page2"})

        assert result == {"data": {"items": []}}


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
            url=f"{BASE_URL}/destinations/dest_123",
            json=expected_response,
        )

        result = await _execute_tool("get_destination_details", {"destination_id": "dest_123"})

        assert result == expected_response


class TestListGroups:
    """Tests for list_groups tool."""

    @pytest.mark.asyncio
    async def test_list_groups_no_params(self, httpx_mock):
        """Test listing groups without parameters."""
        expected_response = {
            "data": {
                "items": [
                    {"id": "group_1", "name": "Production"},
                    {"id": "group_2", "name": "Development"},
                ]
            }
        }
        httpx_mock.add_response(
            url=f"{BASE_URL}/groups",
            json=expected_response,
        )

        result = await _execute_tool("list_groups", {})

        assert result == expected_response

    @pytest.mark.asyncio
    async def test_list_groups_with_limit(self, httpx_mock):
        """Test listing groups with limit."""
        httpx_mock.add_response(
            url=f"{BASE_URL}/groups?limit=25",
            json={"data": {"items": []}},
        )

        result = await _execute_tool("list_groups", {"limit": 25})

        assert result == {"data": {"items": []}}


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
            url=f"{BASE_URL}/groups/group_123",
            json=expected_response,
        )

        result = await _execute_tool("get_group_details", {"group_id": "group_123"})

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
            url=f"{BASE_URL}/groups/group_123/connections",
            json=expected_response,
        )

        result = await _execute_tool("list_connections_in_group", {"group_id": "group_123"})

        assert result == expected_response

    @pytest.mark.asyncio
    async def test_list_connections_in_group_with_pagination(self, httpx_mock):
        """Test listing connections in a group with pagination."""
        httpx_mock.add_response(
            url=f"{BASE_URL}/groups/group_123/connections?limit=10&cursor=next",
            json={"data": {"items": []}},
        )

        result = await _execute_tool(
            "list_connections_in_group",
            {"group_id": "group_123", "limit": 10, "cursor": "next"}
        )

        assert result == {"data": {"items": []}}


class TestUnknownTool:
    """Tests for unknown tool handling."""

    @pytest.mark.asyncio
    async def test_unknown_tool_raises_error(self):
        """Test that unknown tools raise ValueError."""
        with pytest.raises(ValueError, match="Unknown tool: nonexistent_tool"):
            await _execute_tool("nonexistent_tool", {})


class TestFormatResult:
    """Tests for result formatting."""

    def test_format_result_returns_json_string(self):
        """Test that results are formatted as JSON."""
        result = _format_result({"key": "value", "nested": {"a": 1}})

        assert '"key": "value"' in result
        assert '"nested"' in result

    def test_format_result_handles_empty_dict(self):
        """Test formatting empty dictionary."""
        result = _format_result({})

        assert result == "{}"


class TestCallTool:
    """Tests for the call_tool handler."""

    @pytest.mark.asyncio
    async def test_call_tool_returns_text_content(self, httpx_mock):
        """Test that call_tool returns TextContent list."""
        httpx_mock.add_response(
            url=f"{BASE_URL}/groups",
            json={"data": {"items": []}},
        )

        result = await call_tool("list_groups", {})

        assert len(result) == 1
        assert result[0].type == "text"
        assert "items" in result[0].text

    @pytest.mark.asyncio
    async def test_call_tool_handles_http_error(self, httpx_mock):
        """Test that HTTP errors are handled gracefully."""
        httpx_mock.add_response(
            url=f"{BASE_URL}/connections/bad_id",
            status_code=404,
            json={"message": "Not found"},
        )

        result = await call_tool("get_connection_details", {"connection_id": "bad_id"})

        assert len(result) == 1
        assert "404" in result[0].text or "error" in result[0].text.lower()

    @pytest.mark.asyncio
    async def test_call_tool_handles_generic_error(self):
        """Test that generic errors are handled gracefully."""
        result = await call_tool("unknown_tool", {})

        assert len(result) == 1
        assert "Error" in result[0].text


class TestListTools:
    """Tests for the list_tools handler."""

    @pytest.mark.asyncio
    async def test_list_tools_returns_all_tools(self):
        """Test that all 9 tools are returned."""
        tools = await list_tools()

        assert len(tools) == 9

        tool_names = [t.name for t in tools]
        assert "list_connections" in tool_names
        assert "get_connection_details" in tool_names
        assert "get_connection_state" in tool_names
        assert "get_connection_schema_config" in tool_names
        assert "list_destinations" in tool_names
        assert "get_destination_details" in tool_names
        assert "list_groups" in tool_names
        assert "get_group_details" in tool_names
        assert "list_connections_in_group" in tool_names

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
