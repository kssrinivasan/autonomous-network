# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import os
import json
import logging
import datetime
import re
from aiohttp import web
import aiohttp
import socketio
import google.auth
from google.auth.transport.requests import Request

# Setup dummy SocketEndpoint for tool progress emissions
from endpoints.socketendpoint import SocketEndpoint

# Import tools (synchronous Spanner / Kubernetes / Gitea clients)
from tools.topology import (
    fetch_physical_topology,
    fetch_router_details,
    fetch_device_details,
    fetch_infrastructure_state,
    fetch_vpns,
    fetch_snapshots,
    fetch_node_embeddings,
)
from tools.metrics import (
    fetch_all_last_metrics,
    fetch_all_metrics,
    fetch_last_metrics_for_id,
    fetch_all_metrics_for_id,
    clear_network_metrics,
    fetch_router_metrics_range,
    fetch_traffic_metrics_range,
    fetch_routing_metrics,
    fetch_traffic_test_metrics,
)
from tools.agents import get_available_agents
from tools.logs import fetch_log_entries, delete_logs
from tools.networkdescriptors import (
    list_network_descriptors,
    get_network_descriptor_summary,
    get_descriptor_for_deploy,
    deploy_to_git_background,
    teardown_only_background,
    initialise_default_network,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REASONING_ENGINE_ID = os.getenv(
    "REASONING_ENGINE_ID",
    "projects/YOUR_PROJECT_ID/locations/YOUR_REGION/reasoningEngines/YOUR_SUPERVISOR_ENGINE_ID"
)


def _engine_location(engine_id: str) -> str:
    """The region of the Reasoning Engine, taken from its own resource path.

    Vertex AI's regional endpoints do not serve resources from other regions —
    posting a europe-west1 engine to europe-west3-aiplatform returns 404 — so
    the endpoint has to follow the engine rather than be configured beside it.
    """
    match = re.search(r"/locations/([^/]+)/", engine_id)
    if match and not match.group(1).startswith("YOUR_"):
        return match.group(1)
    return os.getenv("GOOGLE_REGION", "europe-west1")


AI_PLATFORM_LOCATION = _engine_location(REASONING_ENGINE_ID)
logger.info(
    "Reasoning Engine %s -> %s-aiplatform.googleapis.com",
    REASONING_ENGINE_ID, AI_PLATFORM_LOCATION,
)

# Module-level credential cache — refreshed only when expired.
_credentials = None

async def _get_auth_token() -> str:
    global _credentials
    if _credentials is None:
        _credentials, _ = google.auth.default()
    if not _credentials.valid:
        await asyncio.to_thread(_credentials.refresh, Request())
    return _credentials.token

WEB_ROOT = os.path.join(os.path.dirname(__file__), "build", "web")

# ---------------------------------------------------------------------------
# Middlewares & CORS Helpers
# ---------------------------------------------------------------------------

@web.middleware
async def spa_middleware(request, handler):
    """Production Single Page Application (SPA) middleware: redirects root access
    and client-side sub-routes (/login, /settings) directly to Flutter's compiled index.html."""
    try:
        # Pass through API and Webhook paths, let handlers/404 handle them.
        if (request.path.startswith('/webhook/') or 
            request.path.startswith('/metrics/') or 
            request.path.startswith('/traffictests/') or
            request.path.startswith('/topology/') or
            request.path.startswith('/router/') or
            request.path.startswith('/device/') or
            request.path.startswith('/vpns') or
            request.path.startswith('/snapshots') or
            request.path.startswith('/embeddings/') or
            request.path.startswith('/networks') or
            request.path.startswith('/listagents') or
            request.path.startswith('/agents/') or
            request.path.startswith('/infrastructure/') or
            request.path == '/streamQuery' or
            request.path == '/socket.io/'):
            return await handler(request)

        if request.path == '/' or request.path == '/index.html':
            return web.FileResponse(os.path.join(WEB_ROOT, 'index.html'))
            
        response = await handler(request)
        if response.status == 404 or response.status == 403:
            return web.FileResponse(os.path.join(WEB_ROOT, 'index.html'))
        return response
    except web.HTTPException as ex:
        if ex.status == 404 or ex.status == 403:
            return web.FileResponse(os.path.join(WEB_ROOT, 'index.html'))
        raise

async def handle_options(request):
    return web.Response(
        status=200,
        headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization',
        }
    )

# ---------------------------------------------------------------------------
# Socket.IO Server Setup & Event Handlers
# ---------------------------------------------------------------------------

sio = socketio.AsyncServer(
    async_mode='aiohttp',
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False
)

clients_state = {}

@sio.event
async def connect(sid, environ):
    logger.info(f"Socket.IO: Client connected {sid}")
    clients_state[sid] = {}

@sio.event
async def disconnect(sid):
    logger.info(f"Socket.IO: Client disconnected {sid}")
    if sid in clients_state:
        del clients_state[sid]

@sio.event
async def get_all_last_metrics(sid):
    logger.info(f"Socket.IO: get_all_last_metrics for {sid}")
    try:
        # Spanner read is synchronous; run in executor
        loop = asyncio.get_event_loop()
        metrics = await loop.run_in_executor(None, fetch_all_last_metrics)
        await sio.emit('all_last_metrics_update', metrics, room=sid)
    except Exception as e:
        logger.error(f"Socket.IO: Error fetching metrics: {e}")
        await sio.emit('all_last_metrics_update', {'error': f"Error fetching metrics: {str(e)}"}, room=sid)

@sio.event
async def get_all_metrics(sid):
    logger.info(f"Socket.IO: get_all_metrics for {sid}")
    try:
        loop = asyncio.get_event_loop()
        metrics = await loop.run_in_executor(None, fetch_all_metrics)
        await sio.emit('all_metrics_update', metrics, room=sid)
    except Exception as e:
        logger.error(f"Socket.IO: Error fetching metrics: {e}")
        await sio.emit('all_metrics_update', {'error': f"Error fetching metrics: {str(e)}"}, room=sid)

@sio.event
async def get_last_metrics_for_id(sid, data):
    node_id = data.get('id')
    logger.info(f"Socket.IO: get_last_metrics_for_id for {sid}: {node_id}")
    try:
        loop = asyncio.get_event_loop()
        metrics = await loop.run_in_executor(None, fetch_last_metrics_for_id, node_id)
        await sio.emit('last_metrics_update_for_id', metrics, room=sid)
    except Exception as e:
        logger.error(f"Socket.IO: Error fetching metrics for {node_id}: {e}")
        await sio.emit('last_metrics_update_for_id', {'error': f"Error fetching metrics: {str(e)}"}, room=sid)

@sio.event
async def get_all_metrics_for_id(sid, data):
    node_id = data.get('id')
    logger.info(f"Socket.IO: get_all_metrics_for_id for {sid}: {node_id}")
    try:
        loop = asyncio.get_event_loop()
        metrics = await loop.run_in_executor(None, fetch_all_metrics_for_id, node_id)
        await sio.emit('all_metrics_update_for_id', metrics, room=sid)
    except Exception as e:
        logger.error(f"Socket.IO: Error fetching metrics for {node_id}: {e}")
        await sio.emit('all_metrics_update_for_id', {'error': f"Error fetching metrics: {str(e)}"}, room=sid)

@sio.event
async def reset_metrics(sid):
    logger.info(f"Socket.IO: reset_metrics for {sid}")
    try:
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, clear_network_metrics)
        if success:
            await sio.emit('all_last_metrics_update', {}, room=sid)
            await sio.emit('all_metrics_update', {}, room=sid)
            logger.info("Socket.IO: Reset metrics complete")
        else:
            raise Exception("Metrics deletion in database failed.")
    except Exception as e:
        logger.error(f"Socket.IO: Error resetting metrics: {e}")
        await sio.emit('all_metrics_update', {'error': f"Error resetting metrics: {str(e)}"}, room=sid)

@sio.event
async def get_logs(sid, data):
    logger.info(f"Socket.IO: get_logs for {sid}: {data}")
    try:
        if sid not in clients_state:
            clients_state[sid] = {}
        clients_state[sid]['logs'] = data
        
        enabled = data.get('enabled', True)
        source_filter = data.get('source_filter', 'all')
        severity_filter = data.get('severity_filter', 'all')
        row_limit = int(data.get('row_limit', 50))
        minutes_limit = data.get('minutes_limit')
        if minutes_limit is not None:
            minutes_limit = int(minutes_limit)
        
        if enabled:
            loop = asyncio.get_event_loop()
            logs = await loop.run_in_executor(
                None, 
                lambda: fetch_log_entries(
                    source_filter=source_filter, 
                    severity_filter=severity_filter, 
                    row_limit=row_limit,
                    minutes_limit=minutes_limit
                )
            )
            await sio.emit('logs_update', logs, room=sid)
    except Exception as e:
        logger.error(f"Socket.IO: Error fetching logs: {e}")
        await sio.emit('logs_update', {'error': f"Error fetching logs: {str(e)}"}, room=sid)

@sio.event
async def reset_logs(sid):
    logger.info(f"Socket.IO: reset_logs for {sid}")
    try:
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, delete_logs)
        if success:
            await sio.emit('logs_update', [], room=sid)
        else:
            raise Exception("Logs deletion in database failed.")
    except Exception as e:
        logger.error(f"Socket.IO: Error resetting logs: {e}")
        await sio.emit('logs_update', {'error': f"Error resetting logs: {str(e)}"}, room=sid)


# ---------------------------------------------------------------------------
# REST API Request Handlers
# ---------------------------------------------------------------------------

async def list_agents_route(request):
    """Return a static, clean list representing the Reasoning Engine suite."""
    logger.info("REST: list agents")
    mock_agents = [
        {"id": "agent-supervisor", "name": "supervisor", "description": "Orchestrates SRE troubleshooting loops", "url": ""},
        {"id": "agent-detective", "name": "detective", "description": "Fetches logs, faults, and active network states", "url": ""},
        {"id": "agent-impact", "name": "impact", "description": "Simulates traffic changes and OSPF cost updates", "url": ""},
        {"id": "agent-designer", "name": "designer", "description": "Generates underlay and overlay configurations", "url": ""},
        {"id": "agent-tester", "name": "tester", "description": "Executes traffic tests and OSPF link down scripts", "url": ""},
        {"id": "agent-chaos", "name": "chaos", "description": "Injects network faults and router link drops", "url": ""},
        {"id": "agent-trust", "name": "trust", "description": "Validates configuration changes against design documentation", "url": ""}
    ]
    return web.json_response(mock_agents, headers={"Access-Control-Allow-Origin": "*"})

async def get_available_agents_route(request):
    """Dynamically list active Cloud Run services matching project/region."""
    logger.info("REST: get available agents")
    try:
        agents = await get_available_agents()
        # Transform run_v2 Service list to expected format
        formatted = []
        for a in agents:
            formatted.append({
                "id": a.get("name"),
                "name": a.get("name"),
                "description": "Active Cloud Run agent microservice",
                "url": a.get("url", "")
            })
        return web.json_response(formatted, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"Error fetching available agents: {e}")
        return web.json_response({"error": str(e)}, status=500, headers={"Access-Control-Allow-Origin": "*"})

async def get_topology_physical(request):
    """Return physical topology graph nodes and links (with timetravel)."""
    logger.info("REST: get topology physical")
    try:
        timestamp = request.query.get("timestamp")
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, fetch_physical_topology, timestamp)
        return web.json_response(data, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"Error fetching topology: {e}")
        return web.json_response({"error": str(e)}, status=500, headers={"Access-Control-Allow-Origin": "*"})

async def get_router_details_route(request):
    """Return configuration details for a single router (with timetravel)."""
    router_id = request.match_info.get("router_id")
    logger.info(f"REST: get router details for {router_id}")
    try:
        timestamp = request.query.get("timestamp")
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, fetch_router_details, router_id, timestamp)
        if not data:
            return web.json_response({"error": "Router not found"}, status=404, headers={"Access-Control-Allow-Origin": "*"})
        return web.json_response(data, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"Error fetching router details: {e}")
        return web.json_response({"error": str(e)}, status=500, headers={"Access-Control-Allow-Origin": "*"})

async def get_device_details_route(request):
    """Return configuration details for a customer CE device (with timetravel)."""
    device_id = request.match_info.get("device_id")
    logger.info(f"REST: get device details for {device_id}")
    try:
        timestamp = request.query.get("timestamp")
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, fetch_device_details, device_id, timestamp)
        if not data:
            return web.json_response({"error": "Device not found"}, status=404, headers={"Access-Control-Allow-Origin": "*"})
        return web.json_response(data, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"Error fetching device details: {e}")
        return web.json_response({"error": str(e)}, status=500, headers={"Access-Control-Allow-Origin": "*"})

async def get_vpns_route(request):
    """List deployed VPN sessions (with timetravel)."""
    logger.info("REST: list VPNs")
    try:
        timestamp = request.query.get("timestamp")
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, fetch_vpns, timestamp)
        return web.json_response(data, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"Error listing VPNs: {e}")
        return web.json_response({"error": str(e)}, status=500, headers={"Access-Control-Allow-Origin": "*"})

async def get_snapshots_route(request):
    """List available snapshots from database."""
    logger.info("REST: get snapshots list")
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, fetch_snapshots)
        return web.json_response(data, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"Error fetching snapshots: {e}")
        return web.json_response({"error": str(e)}, status=500, headers={"Access-Control-Allow-Origin": "*"})

async def get_infrastructure_state_route(request):
    """Consolidated state query fetching vpns, tests, and infra (with timetravel)."""
    logger.info("REST: get infrastructure state")
    try:
        timestamp = request.query.get("timestamp")
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, fetch_infrastructure_state, timestamp)
        return web.json_response(data, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"Error fetching infrastructure state: {e}")
        return web.json_response({"error": str(e)}, status=500, headers={"Access-Control-Allow-Origin": "*"})

async def get_all_last_metrics_route(request):
    logger.info("REST: get all last metrics")
    try:
        timestamp = request.query.get("timestamp")
        loop = asyncio.get_event_loop()
        # Note: fetch_all_last_metrics optionally filters by timestamp if supported
        metrics = await loop.run_in_executor(None, lambda: fetch_all_last_metrics(timestamp_str=timestamp))
        return web.json_response(metrics, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"Error: {e}")
        return web.json_response({"error": str(e)}, status=500, headers={"Access-Control-Allow-Origin": "*"})

async def get_last_metrics_for_id_route(request):
    node_id = request.match_info.get("node_id")
    logger.info(f"REST: get last metrics for {node_id}")
    try:
        timestamp = request.query.get("timestamp")
        loop = asyncio.get_event_loop()
        metrics = await loop.run_in_executor(None, lambda: fetch_last_metrics_for_id(node_id, timestamp_str=timestamp))
        return web.json_response(metrics, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"Error: {e}")
        return web.json_response({"error": str(e)}, status=500, headers={"Access-Control-Allow-Origin": "*"})

async def get_routing_metrics_route(request):
    node_id = request.match_info.get("node_id")
    logger.info(f"REST: get routing metrics for {node_id}")
    try:
        timestamp = request.query.get("timestamp")
        loop = asyncio.get_event_loop()
        metrics = await loop.run_in_executor(None, lambda: fetch_routing_metrics(node_id, timestamp_str=timestamp))
        return web.json_response(metrics, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"Error: {e}")
        return web.json_response({"error": str(e)}, status=500, headers={"Access-Control-Allow-Origin": "*"})

async def get_node_embeddings_route(request):
    node_id = request.match_info.get("node_id")
    logger.info(f"REST: get node embeddings for {node_id}")
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, fetch_node_embeddings, node_id)
        return web.json_response(data, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"Error: {e}")
        return web.json_response({"error": str(e)}, status=500, headers={"Access-Control-Allow-Origin": "*"})

async def get_traffic_test_metrics_route(request):
    test_name = request.match_info.get("test_name")
    logger.info(f"REST: get traffic test metrics for {test_name}")
    try:
        timestamp = request.query.get("timestamp")
        loop = asyncio.get_event_loop()
        metrics = await loop.run_in_executor(None, lambda: fetch_traffic_test_metrics(test_name, timestamp_str=timestamp))
        return web.json_response(metrics, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"Error: {e}")
        return web.json_response({"error": str(e)}, status=500, headers={"Access-Control-Allow-Origin": "*"})

async def get_router_metrics_range_route(request):
    logger.info("REST: get router metrics range")
    try:
        start_ts = request.query.get("start")
        end_ts   = request.query.get("end")
        if not start_ts or not end_ts:
            return web.json_response({"error": "start and end query parameters required"}, status=400, headers={"Access-Control-Allow-Origin": "*"})
        
        nodes_param = request.query.get("nodes")
        node_names = [n.strip() for n in nodes_param.split(",") if n.strip()] if nodes_param else None

        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, fetch_router_metrics_range, start_ts, end_ts, node_names)
        return web.json_response(data, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"Error: {e}")
        return web.json_response({"error": str(e)}, status=500, headers={"Access-Control-Allow-Origin": "*"})

async def get_traffic_metrics_range_route(request):
    logger.info("REST: get traffic metrics range")
    try:
        start_ts    = request.query.get("start")
        end_ts      = request.query.get("end")
        tests_param = request.query.get("tests")

        if not start_ts or not end_ts or not tests_param:
            return web.json_response({"error": "start, end and tests query parameters required"}, status=400, headers={"Access-Control-Allow-Origin": "*"})

        test_names = [t.strip() for t in tests_param.split(",") if t.strip()]
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, fetch_traffic_metrics_range, test_names, start_ts, end_ts)
        return web.json_response(data, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"Error: {e}")
        return web.json_response({"error": str(e)}, status=500, headers={"Access-Control-Allow-Origin": "*"})

async def list_networks_route(request):
    logger.info("REST: list network descriptors")
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, list_network_descriptors)
        return web.json_response(data, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"Error listing network descriptors: {e}")
        return web.json_response({"error": str(e)}, status=500, headers={"Access-Control-Allow-Origin": "*"})

async def get_network_descriptor_route(request):
    network_id = request.match_info.get("network_id")
    logger.info(f"REST: get network descriptor summary for {network_id}")
    try:
        loop = asyncio.get_event_loop()
        summary = await loop.run_in_executor(None, get_network_descriptor_summary, network_id)
        if summary is None:
            return web.json_response({"error": "Network descriptor not found"}, status=404, headers={"Access-Control-Allow-Origin": "*"})
        return web.json_response(summary, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"Error getting network descriptor: {e}")
        return web.json_response({"error": str(e)}, status=500, headers={"Access-Control-Allow-Origin": "*"})

async def deploy_network_descriptor_route(request):
    network_id = request.match_info.get("network_id")
    logger.info(f"REST: deploy network descriptor {network_id}")
    try:
        loop = asyncio.get_event_loop()
        descriptor = await loop.run_in_executor(None, get_descriptor_for_deploy, network_id)
        if descriptor is None:
            return web.json_response({"error": "Descriptor not found"}, status=404, headers={"Access-Control-Allow-Origin": "*"})

        # Run GitOps deploy in background task (fire-and-forget)
        asyncio.create_task(deploy_to_git_background(descriptor))
        
        return web.json_response({
            "status": "deploying",
            "network_id": network_id,
            "name": descriptor.get("name"),
            "vpn_count": len(descriptor.get("vpns", [])),
            "traffic_test_count": len(descriptor.get("traffic_tests", []))
        }, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"Error triggering deployment: {e}")
        return web.json_response({"error": str(e)}, status=500, headers={"Access-Control-Allow-Origin": "*"})

async def teardown_deployment_route(request):
    logger.info("REST: teardown deployment")
    try:
        asyncio.create_task(teardown_only_background())
        return web.json_response({"status": "tearing_down"}, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"Error triggering teardown: {e}")
        return web.json_response({"error": str(e)}, status=500, headers={"Access-Control-Allow-Origin": "*"})

# ---------------------------------------------------------------------------
# Vertex AI Reasoning Engine Proxy Route
# ---------------------------------------------------------------------------

async def proxy_stream_query(request):
    """Proxy /streamQuery to Vertex AI Reasoning Engine with server-side IAM auth."""
    try:
        payload = await request.json()

        token = await _get_auth_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        target_url = (
            f"https://{AI_PLATFORM_LOCATION}-aiplatform.googleapis.com/v1beta1"
            f"/{REASONING_ENGINE_ID}:streamQuery"
        )

        sse_response = web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'Access-Control-Allow-Origin': '*',
            }
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                target_url, headers=headers, json=payload, timeout=300
            ) as upstream_resp:
                if upstream_resp.status != 200:
                    err_text = await upstream_resp.text()
                    logger.error(f"Upstream Vertex AI returned HTTP {upstream_resp.status}: {err_text}")
                    return web.json_response(
                        {"error": f"Upstream HTTP {upstream_resp.status}: {err_text}"},
                        status=502,
                        headers={"Access-Control-Allow-Origin": "*"}
                    )

                await sse_response.prepare(request)

                buf = b""
                async for chunk, _ in upstream_resp.content.iter_chunks():
                    if not chunk:
                        continue
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        line = line.strip()
                        if line:
                            logger.info(f"SSE chunk: {line[:400].decode('utf-8', errors='replace')}")
                            await sse_response.write(b"data: " + line + b"\n\n")
                if buf.strip():
                    await sse_response.write(b"data: " + buf.strip() + b"\n\n")

        return sse_response
    except Exception as e:
        logger.error(f"Proxy execution exception: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500, headers={"Access-Control-Allow-Origin": "*"})

# ---------------------------------------------------------------------------
# Application Initialization & Lifecycle Hooks
# ---------------------------------------------------------------------------

async def on_startup(app):
    """Initialise default network descriptor in Spanner and setup SocketEndpoint."""
    logger.info("Initializing SocketEndpoint bridging instance...")
    SocketEndpoint(sio)
    
    loop = asyncio.get_event_loop()
    logger.info("Checking & Initializing default network descriptor in Spanner...")
    try:
        await loop.run_in_executor(None, initialise_default_network)
        logger.info("✓ Default network descriptor initialization check complete")
    except Exception as e:
        logger.error(f"Failed to initialize default network: {e}", exc_info=True)

app = web.Application(middlewares=[spa_middleware])
sio.attach(app)

app.on_startup.append(on_startup)

# Options pre-flights
app.router.add_options('/streamQuery', handle_options)
app.router.add_options('/{tail:.*}', handle_options)

# Stream Query REST Proxy
app.router.add_post('/streamQuery', proxy_stream_query)

# REST API Routes
app.router.add_get('/listagents', list_agents_route)
app.router.add_get('/agents/available', get_available_agents_route)
app.router.add_get('/topology/physical', get_topology_physical)
app.router.add_get('/router/{router_id}', get_router_details_route)
app.router.add_get('/device/{device_id}', get_device_details_route)
app.router.add_get('/vpns', get_vpns_route)
app.router.add_get('/snapshots', get_snapshots_route)
app.router.add_get('/infrastructure/state', get_infrastructure_state_route)
app.router.add_get('/metrics/last', get_all_last_metrics_route)
app.router.add_get('/metrics/last/{node_id}', get_last_metrics_for_id_route)
app.router.add_get('/metrics/routing/{node_id}', get_routing_metrics_route)
app.router.add_get('/embeddings/{node_id}', get_node_embeddings_route)
app.router.add_get('/metrics/range', get_router_metrics_range_route)
app.router.add_get('/traffictests/metrics/range', get_traffic_metrics_range_route)
app.router.add_get('/traffictests/{test_name}/metrics', get_traffic_test_metrics_route)
app.router.add_get('/networks', list_networks_route)
app.router.add_get('/networks/{network_id}', get_network_descriptor_route)
app.router.add_post('/networks/{network_id}/deploy', deploy_network_descriptor_route)
app.router.add_post('/networks/teardown', teardown_deployment_route)

# SPA Static Fallback Mount
if os.path.exists(WEB_ROOT):
    logger.info(f"📁 Mounting static web root: {WEB_ROOT}")
    app.router.add_static('/', WEB_ROOT, show_index=False)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    logger.info(f"🏛️ Starting dashboard-v2 microservice on port {port}...")
    web.run_app(app, host="0.0.0.0", port=port)
