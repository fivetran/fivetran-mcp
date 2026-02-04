"""Tests for the optimized Fivetran MCP server."""

import os
import pytest
import httpx
from unittest.mock import patch

# Set test environment variables
os.environ["FIVETRAN_API_KEY"] = "test_api_key"
os.environ["FIVETRAN_API_SECRET"] = "test_api_secret"

from server import get_auth_header, list_tools, call_tool, TOOLS


class TestOptimizedServer:
    """Test suite for the optimized server."""

    @pytest.mark.asyncio
    async def test_list_tools_loads_successfully(self):
        """Test that all tools load without schema file requirements."""
        tools = await list_tools()
        
        # Should have all the core tools
        assert len(tools) >= 30
        
        # Verify no schema_file parameters required
        for tool in tools:
            required_params = tool.inputSchema.get("required", [])
            assert "schema_file" not in required_params
            
            properties = tool.inputSchema.get("properties", {})
            assert "schema_file" not in properties

    def test_auth_header_generation(self):
        """Test authentication header generation."""
        header = get_auth_header()
        
        assert "Authorization" in header
        assert header["Authorization"].startswith("Basic ")
        assert "Accept" in header
        assert header["Accept"] == "application/json"

    @pytest.mark.asyncio
    async def test_tool_descriptions_enhanced(self):
        """Test that tool descriptions include smart enhancements."""
        tools = await list_tools()
        
        # Find a connection creation tool
        create_tool = next(t for t in tools if t.name == "create_connection")
        
        # Should have enhanced description with operation warning
        assert "WRITE OPERATION" in create_tool.description
        assert "connection_id" in create_tool.description or "group_id" in create_tool.description

    @pytest.mark.asyncio
    async def test_natural_language_interface(self, httpx_mock):
        """Test that tools can be called naturally without schema complexity."""
        # Mock successful API response
        httpx_mock.add_response(
            url="https://api.fivetran.com/v1/account/info",
            json={"code": "Success", "data": {"name": "Test Account"}}
        )

        # This should work without any schema file parameter
        result = await call_tool("get_account_info", {})
        
        assert len(result) == 1
        response_data = eval(result[0].text)
        assert response_data["data"]["name"] == "Test Account"

    def test_tool_registry_completeness(self):
        """Test that the tool registry includes essential operations."""
        tool_names = set(TOOLS.keys())
        
        # Essential operations should be present
        essential_tools = {
            "get_account_info",
            "list_connections", 
            "get_connection_details",
            "create_connection",
            "list_destinations",
            "create_destination",
            "list_groups",
            "create_group",
            "list_users",
            "create_user",
            "list_webhooks",
            "create_account_webhook"
        }
        
        missing_tools = essential_tools - tool_names
        assert not missing_tools, f"Missing essential tools: {missing_tools}"


@pytest.fixture
def httpx_mock(monkeypatch):
    """Mock httpx for testing without making real API calls."""
    class MockTransport(httpx.MockTransport):
        def __init__(self):
            self.responses = []
            super().__init__(self._handler)

        def _handler(self, request):
            for response_config in self.responses:
                if self._matches_request(request, response_config):
                    return httpx.Response(
                        status_code=response_config.get("status_code", 200),
                        json=response_config.get("json")
                    )
            raise Exception(f"No mock configured for {request.method} {request.url}")

        def _matches_request(self, request, config):
            return str(request.url) == config["url"]

        def add_response(self, url, json=None, status_code=200):
            self.responses.append({
                "url": url,
                "json": json, 
                "status_code": status_code
            })

    mock = MockTransport()
    
    original_init = httpx.AsyncClient.__init__
    def patched_init(self, *args, **kwargs):
        kwargs['transport'] = mock
        original_init(self, *args, **kwargs)
    
    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)
    return mock