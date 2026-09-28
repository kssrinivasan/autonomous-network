# Copyright 2024-2028 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import sys

# gRPC fork-safety: must be set BEFORE any google-cloud / grpcio import.
# Both variables are required together — see Dockerfile comment for full detail.
#   GRPC_ENABLE_FORK_SUPPORT=1  → registers proper at-fork cleanup handlers
#   GRPC_POLL_STRATEGY=poll     → uses poll() engine whose PostforkChild() is
#                                 fork-safe (epoll1's ConsumeWakeup() is not)
# The Dockerfile sets these via ENV for the container; setdefault here covers
# local runs outside Docker.
os.environ.setdefault('GRPC_ENABLE_FORK_SUPPORT', '1')
os.environ.setdefault('GRPC_POLL_STRATEGY', 'poll')

import utils.constants as constants

# Attach the Cloud Logging handler to the Python root logger 
# by calling the setup_logging method. By doing so Cloud Logging
# will properly report the logs severity for instance. If we do it
# directly (as above) all logs are classified with ERROR severity
# (see https://cloud.google.com/logging/docs/setup/python)
import google.cloud.logging
logging_client = google.cloud.logging.Client()
import logging
logging_client.setup_logging(log_level=logging.INFO)

logger = logging.getLogger(__name__)

# After importing the Python standard logging library we end up with 2 log
# handlers at the root level causing duplicate log entries to appear
# in Cloud Logging, one that comes from the Cloud Logging Structured
# handler and the other from the standard Python StreamHandler
# Logger root handlers: [<StreamHandler <stderr> (NOTSET)>, <StructuredLogHandler <stderr> (NOTSET)>]
# Remove the standard Python logging handler to avoid duplicate (first handler in the list)
del logging.getLogger().handlers[0]

import kopf
import asyncio
from utils.startup_coordinator import (
    startup_coordinator,
    register_core_infrastructure_phase,
    register_network_services_phase,
    register_application_services_phase
)
from utils.startup_config import startup_config

# get base directory to figure out where playbooks are located
if os.getenv("BASEDIR")==None:
    constants.basedir=os.getcwd()
else:
    constants.basedir=os.getenv("BASEDIR")
logger.info("Base directory is %s", constants.basedir)

# Register startup phases instead of immediate imports
logger.info("Registering lifecycle phases for coordinated startup...")
register_core_infrastructure_phase()
register_network_services_phase()
register_application_services_phase()

if os.getenv("GOOGLE_REGION") is None or os.getenv("GOOGLE_ZONE") is None or os.getenv("GOOGLE_PROJECT") is None:
    logger.error("You must set GOOGLE_REGION/GOOGLE_ZONE/GOOGLE_PROJECT environment variables")
    sys.exit(0)

def legacy_import_modules():
    """Fallback to legacy immediate import mode"""
    logger.info("Using legacy startup mode - importing all modules immediately")
    
    if os.getenv("VPN") is not None:
        logger.info("VPN Lifecycle")
        import infrastructure.vyosinfrastructure.lifecycle
        import infrastructure.linuxnetwork.lifecycle
        import infrastructure.device.lifecycle
        import infrastructure.traffictest.lifecycle
        import transport.vyosunderlay.lifecycle
        import transport.vyosvpn.lifecycle
        import transport.vyosrouter.lifecycle
        import networkfailure.lifecycle
        import vyosvm.lifecycle
        import opsagent.lifecycle

    if os.getenv("GRAPH") is not None:
        import graph.lifecycle

@kopf.on.startup()
async def configure(settings: kopf.OperatorSettings, **_):
    # Log configuration for debugging
    startup_config.log_configuration(logger)

    # Dynamic OS Login initialization for GKE service account identity.
    # Where the org enforces OS Login, the ssh-keys metadata utils/compute.py
    # writes is ignored, and the controller's SA has no POSIX account to log in
    # as. This registers google-compute.pub against the SA's OS Login profile
    # and rewrites GOOGLE_VM_USER to the POSIX name it is given, which every
    # lifecycle_tasks.py then picks up as ansible_user. No-op off GCP.
    try:
        from utils.oslogin import setup_oslogin_if_needed
        setup_oslogin_if_needed()
    except Exception as e:
        logger.error(f"Failed to setup OS Login during startup: {e}")

    # Enhanced settings for rate limiting and stability
    settings.posting.level = logging.DEBUG
    settings.posting.enabled = True
    
    # Apply configuration-driven settings
    settings.watching.connect_timeout = startup_config.connect_timeout
    settings.watching.server_timeout = startup_config.server_timeout
    settings.watching.client_timeout = startup_config.client_timeout
    settings.watching.resource_timeout = startup_config.resource_timeout
    settings.watching.namespace_timeout = startup_config.watch_namespace_timeout
    
    # Enhanced watcher resilience for 429 handling
    # --------------------------------------------
    # settings.watching.retry_delay = startup_config.watcher_retry_delay
    # settings.watching.backoff_factor = startup_config.watcher_backoff_factor
    # settings.watching.max_delay = startup_config.watcher_max_delay
    # settings.watching.reconnect_backoff = startup_config.watcher_reconnect_backoff
    
    # Rate limiting settings
    # ----------------------
    # settings.batching.worker_idle_timeout = startup_config.worker_idle_timeout
    # settings.batching.worker_batch_size = startup_config.worker_batch_size
    # settings.batching.worker_exit_timeout = startup_config.worker_exit_timeout
    
    # Execution settings
    if os.getenv("FREE5GC") is not None:
        settings.execution.max_workers = startup_config.max_workers_free5gc
    else:
        settings.execution.max_workers = startup_config.max_workers_default
    
    # Networking settings for better resilience
    settings.networking.request_timeout = startup_config.request_timeout
    settings.networking.connect_timeout = startup_config.network_connect_timeout
    
    # Choose startup mode based on configuration
    if startup_config.enable_coordinated_startup and startup_config.startup_mode == "phased":
        # Execute coordinated startup sequence
        logger.info("Starting coordinated lifecycle watcher initialization...")
        success = await startup_coordinator.execute_startup_sequence()
        
        if not success:
            logger.warning("Coordinated startup failed - falling back to legacy mode")
            legacy_import_modules()
        else:
            logger.info("Coordinated startup completed successfully")
    else:
        # Use legacy immediate import mode
        legacy_import_modules()

    # Manually trigger the anti-entropy sync for TrafficTests
    # because kopf.on.startup can be missed in phased startup
    if os.getenv("VPN") is not None:
        try:
            import infrastructure.traffictest.lifecycle as TrafficTest
            await TrafficTest.initial_setup(logger)
        except Exception as e:
            logger.error(f"Failed to run TrafficTest initial setup: {e}")
    
# Login with k8s client
@kopf.on.login()
def login_fn(**kwargs):
    return kopf.login_via_client(**kwargs)

@kopf.on.probe()
def get_readiness(memo: kopf.Memo, **kwargs):
    # Add checks for dependencies like Spanner, etc.
    # For now, we'll just return the time
    from datetime import datetime
    return {"ready": True, "time": str(datetime.utcnow())}
