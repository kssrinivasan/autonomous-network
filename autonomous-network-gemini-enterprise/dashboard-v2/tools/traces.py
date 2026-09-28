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

import logging
import asyncio
import datetime
from google.cloud import spanner
import json

from utils.database import get_database

logger = logging.getLogger(__name__)

# AgentTrace lives in agentops-db under the six-database schema, alongside the
# other cross-domain operational tables. It has no property graph.
TRACE_DOMAIN = 'ops'
CHANGE_STREAM_NAME = 'AgentTraceStream'

class TraceStreamListener:
    """Listens to Spanner Change Streams for AgentTrace table changes"""
    
    def __init__(self, socket_endpoint):
        """
        Args:
            socket_endpoint: SocketEndpoint instance to emit events to clients
        """
        self.socket_endpoint = socket_endpoint
        self.running = False
        self.task = None
        self.client_count = 0  # Track number of clients with traces enabled
        self.lock = asyncio.Lock()  # Prevent race conditions
        self.last_seen_id = None  # Track last processed trace event ID
        self.last_seen_timestamp = None  # Track last processed trace timestamp
        self._reset_timestamp = None  # Timestamp for filtering out old traces
        
        # Connect to Spanner
        self.database = get_database(TRACE_DOMAIN)
        
    async def add_client(self):
        """Called when a client enables traces"""
        logger.info("added client")
        async with self.lock:
            self.client_count += 1
            logger.info(f"Client added to trace listener (total: {self.client_count})")
            if self.client_count == 1 and not self.running:
                await self._start_internal()
                
    async def remove_client(self):
        """Called when a client disables traces or disconnects"""
        logger.info("removed client")
        async with self.lock:
            self.client_count = max(0, self.client_count - 1)
            logger.info(f"Client removed from trace listener (remaining: {self.client_count})")
            if self.client_count == 0 and self.running:
                await self._stop_internal()
        
    async def _start_internal(self):
        """Internal method to start listening to the change stream"""
        logger.info("start listening")
        if self.running:
            logger.warning("TraceStreamListener already running")
            return
            
        self.running = True
        self.task = asyncio.create_task(self._listen())
        logger.info("✓ TraceStreamListener started (Spanner Change Stream active)")
        
    async def _stop_internal(self):
        """Internal method to stop listening to the change stream"""
        logger.info("stop listening")
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("✓ TraceStreamListener stopped (no clients need traces)")
        
    async def reset_cursor(self, timestamp=None):
        """Reset the trace cursor to fetch events from a specific time onwards"""
        async with self.lock:
            logger.info(f"Resetting trace cursor with timestamp: {timestamp}")
            self.last_seen_id = None
            self._reset_timestamp = timestamp
            logger.info("✓ Trace cursor reset - will fetch new events from the specified time")
    
    async def _listen(self):
        """Main loop to poll for new trace events"""
        logger.info("Starting trace event polling loop")
        
        try:
            while self.running:
                try:
                    logger.debug("Polling for new trace events...")
                    
                    # Query for new trace events
                    with self.database.snapshot() as snapshot:
                        if self.last_seen_id is None:
                            # First query - get recent events from last 30 seconds or from the reset timestamp
                            query = """
                                SELECT id, event_type, trace_id, span_id, parent_span_id,
                                       operation_name, timestamp, details
                                FROM AgentTrace
                                WHERE timestamp >= @start_time
                                ORDER BY timestamp ASC, id ASC
                                LIMIT 1000
                            """
                            start_time = self._reset_timestamp or (datetime.datetime.utcnow() - datetime.timedelta(seconds=30)).isoformat()
                            results = snapshot.execute_sql(
                                query,
                                params={"start_time": start_time},
                                param_types={"start_time": spanner.param_types.STRING}
                            )
                        else:
                            # Subsequent queries - get events with timestamp > last_timestamp
                            # Use timestamp for chronological ordering instead of UUID string comparison
                            query = """
                                SELECT id, event_type, trace_id, span_id, parent_span_id,
                                       operation_name, timestamp, details
                                FROM AgentTrace
                                WHERE timestamp > @last_timestamp
                                ORDER BY timestamp ASC, id ASC
                                LIMIT 1000
                            """
                            results = snapshot.execute_sql(
                                query,
                                params={"last_timestamp": self.last_seen_timestamp},
                                param_types={"last_timestamp": spanner.param_types.STRING}
                            )
                        
                        event_count = 0
                        for row in results:
                            if not self.running:
                                break
                            
                            # Format the trace event
                            trace_event = {
                                'id': row[0],
                                'event_type': row[1],
                                'trace_id': row[2],
                                'span_id': row[3],
                                'parent_span_id': row[4],
                                'operation_name': row[5],
                                'timestamp': row[6].isoformat() if row[6] else None,
                                'details': row[7]
                            }
                            
                            # Update last seen id and timestamp
                            self.last_seen_id = row[0]
                            self.last_seen_timestamp = row[6].isoformat() if row[6] else None
                            
                            # Log and send to clients, filtering by reset_timestamp if set
                            if self._reset_timestamp and row[6] and row[6].isoformat() < self._reset_timestamp:
                                continue
                                
                            logger.info(f"New trace event: {trace_event['event_type']} - {trace_event['operation_name']} (id: {trace_event['id']})")
                            await self._emit_to_clients(trace_event)
                            event_count += 1
                        
                        if event_count > 0:
                            logger.info(f"✓ Processed and emitted {event_count} new trace events")
                        else:
                            logger.debug("No new trace events found")
                            
                except Exception as e:
                    logger.error(f"Error polling for trace events: {e}", exc_info=True)
                
                # Poll every 500ms for near real-time updates
                await asyncio.sleep(0.5)
                        
        except Exception as e:
            logger.error(f"Fatal error in TraceStreamListener: {e}", exc_info=True)
            
            
    async def _emit_to_clients(self, trace_event):
        """Emit trace event to all clients with traces enabled"""
        try:
            from endpoints.socketendpoint import clients_state
            
            logger.info(f"Attempting to emit trace event {trace_event['id']} to clients")
            logger.info(f"Total connected clients: {len(clients_state)}")
            
            # Find all clients with traces enabled
            clients_with_traces = []
            for sid, state in clients_state.items():
                logger.debug(f"Client {sid} state: {state}")
                if state.get('traces', {}).get('enabled', False):
                    clients_with_traces.append(sid)
                    logger.info(f"Emitting to client {sid}...")
                    await self.socket_endpoint.sio.emit(
                        'trace_update', 
                        trace_event, 
                        room=sid
                    )
                    logger.info(f"✓ Emitted trace_update to client {sid}")
            
            if len(clients_with_traces) > 0:
                logger.info(f"✓ Successfully emitted trace event to {len(clients_with_traces)} client(s)")
            else:
                logger.warning("No clients with traces enabled found!")
                logger.warning(f"Client states: {clients_state}")
            
        except Exception as e:
            logger.error(f"Error emitting to clients: {e}", exc_info=True)


def fetch_recent_traces(limit=50):
    """
    Fetch recent trace events from Spanner
    
    Args:
        limit: Maximum number of traces to fetch
        
    Returns:
        List of trace event dictionaries
    """
    database = get_database(TRACE_DOMAIN)

    with database.snapshot() as snapshot:
        try:
            sql = f"""
                SELECT id, event_type, trace_id, span_id, parent_span_id, 
                       operation_name, timestamp, details 
                FROM AgentTrace 
                ORDER BY timestamp DESC 
                LIMIT {limit}
            """
            results = snapshot.execute_sql(sql)
            
            traces = []
            for row in results:
                traces.append({
                    'id': row[0],
                    'event_type': row[1],
                    'trace_id': row[2],
                    'span_id': row[3],
                    'parent_span_id': row[4],
                    'operation_name': row[5],
                    'timestamp': row[6].isoformat() if row[6] else None,
                    'details': row[7]
                })
                
            return traces
            
        except Exception as e:
            logger.error(f"Error fetching recent traces: {e}", exc_info=True)
            return []
