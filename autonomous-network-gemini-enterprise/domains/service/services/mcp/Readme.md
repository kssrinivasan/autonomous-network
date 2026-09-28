# Multi-Domain MCP Server

**MCP (Model Context Protocol) server for Transport, Core, and Radio Network Domains**

This service provides standardized MCP tool interfaces for all domain REST APIs, enabling AI agents to interact with Intent APIs (TMF921) and Topology APIs (TMF639) across transport, core, and radio domains through high-level tool calls instead of direct HTTP requests.

## Overview

The Multi-Domain MCP Server is a thin proxy layer that exposes all domain APIs as MCP tools. It bridges the gap between AI agents (which understand MCP) and the network domain REST APIs (which implement TMF standards) across transport, core, and radio domains.

### Supported Domains

- **Transport Domain**: L3 VPN provisioning, PE router/interface topology
- **Core Domain**: 5G core network provisioning, network function topology
- **Radio Domain**: RAN provisioning, cell site/gNodeB topology

### Architecture

```
┌─────────────────┐
│   MCP Client    │  (AI Agent, Claude Desktop, etc.)
│                 │
└────────┬────────┘
         │ MCP Protocol (SSE)
         │
┌────────▼─────────────────────────────────────────────┐
│         Multi-Domain MCP Server (domain-mcp)        │
│                                                       │
│  ┌────────────────┐  ┌─────────────────┐            │
│  │ Transport Tools│  │ Core Tools      │            │
│  │ Intent+Topology│  │ Intent+Topology │            │
│  └────────┬───────┘  └────────┬────────┘            │
│           │                    │                      │
│  ┌────────┴────────┐  ┌───────┴────────┐            │
│  │ Radio Tools     │  │ HTTP Client    │            │
│  │ Intent+Topology │  │ (Cloud Run Auth│            │
│  └────────┬────────┘  │  + Retry)      │            │
│           │            └────────────────┘            │
└───────────┼─────────────────┼──────────┼─────────────┘
            │ HTTP            │ HTTP     │ HTTP
            │                 │          │
     ┌──────▼──────┐   ┌─────▼─────┐ ┌─▼──────┐
     │ Transport   │   │   Core    │ │ Radio  │
     │ Intent +    │   │  Intent + │ │Intent +│
     │ Topology    │   │  Topology │ │Topology│
     │ APIs        │   │   APIs    │ │ APIs   │
     └─────────────┘   └───────────┘ └────────┘
```

## Features

### Tool Naming Convention

Tools follow the pattern: **`{action}_{domain}_{api_type}`**

- **Domains**: `transport`, `core`, `radio`
- **Actions**: `create`, `list`, `get`, `delete`, `query`, `check`
- **API types**: `intent`, `resource` (topology), `topology_graph`, `api_schema`, `api_health`

Examples:
- `create_transport_intent` - Create L3 VPN intent
- `list_core_resources` - List 5G core network functions
- `query_radio_topology_graph` - Execute graph query on RAN topology

### Transport Domain Tools

**Intent API (TMF921):**
- `create_transport_intent(intent_data: dict)` - Create L3 VPN intent
- `list_transport_intents()` - List all transport intents
- `get_transport_intent(intent_id: str)` - Get intent by ID
- `delete_transport_intent(intent_id: str)` - Delete intent
- `get_transport_intent_api_schema()` - Get OpenAPI schema
- `check_transport_intent_api_health()` - Health check

**Topology API (TMF639):**
- `list_transport_resources(filters: dict)` - Query PE routers, interfaces, VPNs
- `get_transport_resource(resource_id: str)` - Get resource by ID
- `query_transport_topology_graph(gql_query: str)` - Execute GQL property graph query
- `get_transport_topology_api_schema()` - Get OpenAPI schema
- `check_transport_topology_api_health()` - Health check

### Core Domain Tools

**Intent API (TMF921):**
- `create_core_intent(intent_data: dict)` - Create 5G core network intent
- `list_core_intents()` - List all core intents
- `get_core_intent(intent_id: str)` - Get intent by ID
- `delete_core_intent(intent_id: str)` - Delete intent
- `get_core_intent_api_schema()` - Get OpenAPI schema
- `check_core_intent_api_health()` - Health check

**Topology API (TMF639):**
- `list_core_resources(filters: dict)` - Query network functions, subscribers, slices
- `get_core_resource(resource_id: str)` - Get resource by ID
- `query_core_topology_graph(gql_query: str)` - Execute GQL property graph query
- `get_core_topology_api_schema()` - Get OpenAPI schema
- `check_core_topology_api_health()` - Health check

### Radio Domain Tools

**Intent API (TMF921):**
- `create_radio_intent(intent_data: dict)` - Create RAN intent
- `list_radio_intents()` - List all radio intents
- `get_radio_intent(intent_id: str)` - Get intent by ID
- `delete_radio_intent(intent_id: str)` - Delete intent
- `get_radio_intent_api_schema()` - Get OpenAPI schema
- `check_radio_intent_api_health()` - Health check

**Topology API (TMF639):**
- `list_radio_resources(filters: dict)` - Query cell sites, gNodeBs, UEs
- `get_radio_resource(resource_id: str)` - Get resource by ID
- `query_radio_topology_graph(gql_query: str)` - Execute GQL property graph query
- `get_radio_topology_api_schema()` - Get OpenAPI schema
- `check_radio_topology_api_health()` - Health check

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TRANSPORT_INTENT_API_URL` | Optional* | Base URL of the Transport Intent API service |
| `TRANSPORT_TOPOLOGY_API_URL` | Optional* | Base URL of the Transport Topology API service |
| `CORE_INTENT_API_URL` | Optional* | Base URL of the Core Intent API service |
| `CORE_TOPOLOGY_API_URL` | Optional* | Base URL of the Core Topology API service |
| `RADIO_INTENT_API_URL` | Optional* | Base URL of the Radio Intent API service |
| `RADIO_TOPOLOGY_API_URL` | Optional* | Base URL of the Radio Topology API service |
| `PORT` | No | Server port (default: 8080) |

**\*Optional with graceful degradation**: The MCP server deploys successfully even if some domain APIs are not available. Tools for missing APIs will raise a clear error when called (`ValueError: {Domain} {API} not configured`).

The URLs are automatically discovered during deployment via `gcloud run services describe`.

## Deployment

### Prerequisites

Deploy the domain APIs you want to use (optional - server works with partial deployment):

**Transport Domain:**
```bash
cd domains/transport/services/apiserver && ./install.sh
cd domains/transport/services/topologyserver && ./install.sh
```

**Core Domain** (when available):
```bash
cd domains/core/services/apiserver && ./install.sh
cd domains/core/services/topologyserver && ./install.sh
```

**Radio Domain** (when available):
```bash
cd domains/radio/services/apiserver && ./install.sh
cd domains/radio/services/topologyserver && ./install.sh
```

### Deploy MCP Server

```bash
cd domains/service/services/mcp
./install.sh
```

The deployment script will:
1. Build the Docker container image via Cloud Build as `domain-mcp`
2. Discover all domain API URLs via `gcloud run services describe`
3. Deploy to Cloud Run with discovered URLs as environment variables
4. Output the MCP server URL, SSE endpoint, and connected API status

### Verify Deployment

```bash
# Get service URL
SERVICE_URL=$(gcloud run services describe domain-mcp \
    --region=$GOOGLE_REGION \
    --format='value(status.url)')

# Test SSE endpoint
curl $SERVICE_URL/sse
```

### Uninstall

```bash
cd domains/service/services/mcp
./uninstall.sh
```

## Usage

### Connect as MCP Client

#### Option 1: Using Python MCP Client SDK

```python
from mcp import ClientSession
from mcp.client.sse import sse_client

# Connect to MCP server
async with sse_client(url="https://domain-mcp-xxx.run.app/sse") as (read, write):
    async with ClientSession(read, write) as session:
        # Initialize
        await session.initialize()
        
        # List available tools
        tools = await session.list_tools()
        for tool in tools.tools:
            print(f"Tool: {tool.name}")
        
        # Call a tool (transport domain example)
        result = await session.call_tool(
            "list_transport_resources",
            arguments={"filters": {"resourceType": "PERouter"}}
        )
        print(result.content)
```

#### Option 2: Using Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "network-domains": {
      "url": "https://domain-mcp-xxx.run.app/sse",
      "transport": "sse"
    }
  }
}
```

### Example Tool Calls

#### 1. List Transport PE Routers

```python
result = await session.call_tool(
    "list_transport_resources",
    arguments={
        "filters": {
            "resourceType": "PERouter",
            "role": "PE"
        }
    }
)
```

#### 2. Create Transport L3 VPN Intent

```python
intent_data = {
    "expectationType": "L3ConnectivityExpectation",
    "endpoints": [
        {"deviceName": "vyos-pe-1", "interfaceId": "eth2"},
        {"deviceName": "vyos-pe-2", "interfaceId": "eth2"}
    ],
    "ipv4AddressPrefix": "10.100.0.0/16",
    "routingProtocol": "OSPF"
}

result = await session.call_tool(
    "create_transport_intent",
    arguments={"intent_data": intent_data}
)
```

#### 3. Query Transport Topology Graph

```python
gql_query = """
MATCH (r:PERouter)-[:HAS_INTERFACE]->(i:Interface)
WHERE r.name = 'vyos-pe-1'
RETURN r.name, i.name, i.ipAddress
"""

result = await session.call_tool(
    "query_transport_topology_graph",
    arguments={"gql_query": gql_query}
)
```

#### 4. List Core Network Functions

```python
result = await session.call_tool(
    "list_core_resources",
    arguments={
        "filters": {
            "resourceType": "NetworkFunction",
            "nfType": "AMF"
        }
    }
)
```

#### 5. Get Transport Intent API Schema

```python
result = await session.call_tool("get_transport_intent_api_schema", arguments={})
schema = json.loads(result.content[0].text)

# Explore Intent model schema
intent_schema = schema["components"]["schemas"]["Intent"]
print(f"Intent properties: {intent_schema['properties'].keys()}")
```

## Tool Reference

### Intent Tools

#### `createIntent(intent_json: str) -> str`

Submit a new L3 VPN service intent.

**Parameters:**
- `intent_json`: TMF921 Intent envelope as JSON string

**Returns:**
- JSON string containing created Intent with ID and state=Acknowledged

#### `listIntents() -> str`

List all active L3 VPN service intents.

**Returns:**
- JSON string containing IntentList with all active intents

#### `getIntent(intent_id: str) -> str`

Get the current state of a specific intent.

**Parameters:**
- `intent_id`: Intent UUID

**Returns:**
- JSON string containing Intent with live state and CR status

#### `deleteIntent(intent_id: str) -> str`

Revoke an intent and delete the VPN service.

**Parameters:**
- `intent_id`: Intent UUID

**Returns:**
- JSON string containing Intent with state=Inactive

### Topology Tools

#### `listResources(...) -> str`

Query transport resources with filtering.

**Parameters:**
- `resource_specification`: Resource type (PhysicalRouter, PhysicalInterface, L3VPNService)
- `resource_status`: Status filter (Active, Available, InUse, etc.)
- `role`: PE router role filter
- `location_city`: City filter
- `router_id`: Parent router filter for interfaces
- `interface_type`: Interface type filter (customer)
- `limit`: Page size (default 20, max 100)
- `offset`: Page offset (default 0)

**Returns:**
- JSON string containing ResourceInventoryList

#### `executeGraphQuery(gql: str) -> str`

Execute GQL property graph query.

**Parameters:**
- `gql`: GQL query string (read-only)

**Returns:**
- JSON string containing GraphQueryResult with rows

**Security:**
- Only read-only queries allowed
- Write operations (INSERT/UPDATE/DELETE) rejected with HTTP 403

## References

- **TMF921 Intent Management API**: [TMForum Specification](https://www.tmforum.org/resources/specification/tmf921-intent-management-api-user-guide-v1-0-0/)
- **TMF639 Resource Inventory Management API**: [TMForum Specification](https://www.tmforum.org/resources/specification/tmf639-resource-inventory-management-api-rest-specification-r19-0-1/)
- **TMF688 Event Management API**: [TMForum Specification](https://www.tmforum.org/resources/specification/tmf688-event-management-api-rest-specification-r19-0-0/)
- **Model Context Protocol (MCP)**: [Anthropic Documentation](https://modelcontextprotocol.io/)
- **Transport Intent API**: [../apiserver/Readme.md](../../../transport/services/intentserver/Readme.md)
- **Transport Topology API**: [../topologyserver/Readme.md](../../../transport/services/topologyserver/Readme.md)

## Development

### Local Development

```bash
# Install dependencies
cd domains/service/services/mcp
uv sync

# Set environment variables (configure only domains you're testing)
export TRANSPORT_INTENT_API_URL="http://localhost:8081"
export TRANSPORT_TOPOLOGY_API_URL="http://localhost:8082"
export CORE_INTENT_API_URL="http://localhost:8083"
export CORE_TOPOLOGY_API_URL="http://localhost:8084"
export RADIO_INTENT_API_URL="http://localhost:8085"
export RADIO_TOPOLOGY_API_URL="http://localhost:8086"

# Run server
uv run python src/main.py
```

### Testing

Test the MCP server with curl:

```bash
# Test SSE endpoint
curl https://domain-mcp-xxx.run.app/sse

# Test via MCP client
mcp connect sse https://domain-mcp-xxx.run.app/sse
```

## Architecture Notes

### Why MCP?

The MCP layer provides several benefits over direct HTTP calls:

1. **Standardized Interface**: AI agents can discover tools via `listTools()` and schemas via domain-specific schema tools
2. **Type Safety**: MCP tool schemas provide parameter validation
3. **Abstraction**: Agents don't need to know HTTP endpoints, authentication, or error handling details
4. **Multi-Domain Orchestration**: Single MCP server provides unified access to transport, core, and radio domain APIs
5. **Graceful Degradation**: Server works even when some domain APIs aren't deployed yet

### Schema Synchronization

The schema tools (e.g., `get_transport_intent_api_schema`, `get_core_topology_api_schema`) proxy the `/openapi.json` endpoints from the respective domain Intent and Topology APIs. This ensures schemas stay in sync with the source of truth (the API services) without duplication.

### Error Handling

HTTP errors from the Intent/Topology APIs are propagated to MCP clients as JSON error responses:

```json
{
  "error": "HTTP 404: Intent 'abc-123' not found"
}
```

### Retry Logic

The HTTP client implements exponential backoff retry for 5xx server errors (up to 3 retries). 4xx client errors are not retried.

## License

Copyright 2024-2028 Google LLC. Licensed under the Apache License, Version 2.0.
