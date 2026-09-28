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
from typing import Dict, Any

class StartupConfig:
    """
    Configuration class for operator startup parameters.
    Allows environment variable overrides for easy tuning.
    """
    
    def __init__(self):
        # API Health Check Settings - more tolerant defaults
        self.api_health_check_interval = int(os.getenv("STARTUP_API_HEALTH_INTERVAL", "3"))
        self.max_health_check_retries = int(os.getenv("STARTUP_MAX_HEALTH_RETRIES", "8"))
        
        # Phase and Watcher Timing
        self.phase_delay = int(os.getenv("STARTUP_PHASE_DELAY", "10"))
        self.watcher_delay = int(os.getenv("STARTUP_WATCHER_DELAY", "2"))
        
        # Kopf Settings
        self.connect_timeout = int(os.getenv("KOPF_CONNECT_TIMEOUT", "120"))
        self.server_timeout = int(os.getenv("KOPF_SERVER_TIMEOUT", "900"))
        self.client_timeout = int(os.getenv("KOPF_CLIENT_TIMEOUT", "60"))
        
        # Batching Settings
        self.worker_idle_timeout = float(os.getenv("KOPF_WORKER_IDLE_TIMEOUT", "5.0"))
        self.worker_batch_size = int(os.getenv("KOPF_WORKER_BATCH_SIZE", "1"))
        self.worker_exit_timeout = float(os.getenv("KOPF_WORKER_EXIT_TIMEOUT", "30.0"))
        
        # Execution Settings
        # max_workers controls how many kopf handler coroutines can be in-flight
        # simultaneously across ALL resource types.
        #
        # For VPN (default) mode the handler landscape is:
        #   Ansible-heavy (use Ansible semaphore, long-running):
        #     linuxnetwork, vyosrouter, device, traffictest, vyosvm (install phase),
        #     vyosinfrastructure, vyosunderlay, vyosvpn
        #   Non-Ansible (fast K8s/GCP API calls, no semaphore):
        #     networkfailure, graph, vyosvm (GCP provisioning phase)
        #
        #   Ansible operational semaphore = 7 slots, so at most 7 Ansible-heavy
        #   handlers run at once.  The remaining slots must be available for non-Ansible
        #   handlers that need a kopf worker to run concurrently.
        #
        #   Setting max_workers = 15 gives:
        #     7  Ansible-heavy handlers actually running (semaphore limit)
        #     3  Ansible-heavy handlers waiting on semaphore (pre-acquire, in "Creating")
        #     5  non-Ansible handlers free to run concurrently
        #
        #   This balances throughput against the "stuck in Creating" risk: resources
        #   waiting on the semaphore have already set their status to "Creating".
        #   The thread.join(600s) timeout ensures any hung Ansible operation eventually
        #   unblocks, and the idempotency guards ensure clean recovery on operator restart.
        #   Avoid setting this >> 15 for VPN mode to keep the semaphore wait queue small.
        #
        #   More handlers with varied workloads; 20 gives enough headroom.
        self.max_workers_default = int(os.getenv("KOPF_MAX_WORKERS_DEFAULT", "30"))
        self.max_workers_free5gc = int(os.getenv("KOPF_MAX_WORKERS_FREE5GC", "30"))
        
        # Networking Settings
        self.request_timeout = int(os.getenv("KOPF_REQUEST_TIMEOUT", "60"))
        self.network_connect_timeout = int(os.getenv("KOPF_NETWORK_CONNECT_TIMEOUT", "30"))
        
        # Watcher Resilience Settings (for post-startup 429 handling)
        self.watcher_retry_delay = float(os.getenv("KOPF_WATCHER_RETRY_DELAY", "2.0"))
        self.watcher_backoff_factor = float(os.getenv("KOPF_WATCHER_BACKOFF_FACTOR", "1.5"))
        self.watcher_max_delay = float(os.getenv("KOPF_WATCHER_MAX_DELAY", "30.0"))
        self.watcher_reconnect_backoff = float(os.getenv("KOPF_WATCHER_RECONNECT_BACKOFF", "5.0"))
        
        # Resource Watching Settings
        self.resource_timeout = int(os.getenv("KOPF_RESOURCE_TIMEOUT", "300"))
        self.watch_namespace_timeout = int(os.getenv("KOPF_WATCH_NAMESPACE_TIMEOUT", "600"))
        
        # Startup Mode
        self.enable_coordinated_startup = os.getenv("ENABLE_COORDINATED_STARTUP", "true").lower() == "true"
        self.startup_mode = os.getenv("STARTUP_MODE", "phased")  # "phased" or "legacy"
        
    def get_kopf_settings(self) -> Dict[str, Any]:
        """
        Get Kopf settings as a dictionary for easy application.
        """
        return {
            'watching': {
                'connect_timeout': self.connect_timeout,
                'server_timeout': self.server_timeout,
                'client_timeout': self.client_timeout,
                'resource_timeout': self.resource_timeout,
                'namespace_timeout': self.watch_namespace_timeout,
                'retry_delay': self.watcher_retry_delay,
                'backoff_factor': self.watcher_backoff_factor,
                'max_delay': self.watcher_max_delay,
                'reconnect_backoff': self.watcher_reconnect_backoff,
            },
            'batching': {
                'worker_idle_timeout': self.worker_idle_timeout,
                'worker_batch_size': self.worker_batch_size,
                'worker_exit_timeout': self.worker_exit_timeout,
            },
            'networking': {
                'request_timeout': self.request_timeout,
                'connect_timeout': self.network_connect_timeout,
            }
        }
    
    def log_configuration(self, logger):
        """
        Log the current configuration for debugging.
        """
        logger.info("Startup Configuration:")
        logger.info(f"  Coordinated Startup: {self.enable_coordinated_startup}")
        logger.info(f"  Startup Mode: {self.startup_mode}")
        logger.info(f"  Phase Delay: {self.phase_delay}s")
        logger.info(f"  Watcher Delay: {self.watcher_delay}s")
        logger.info(f"  API Health Check Interval: {self.api_health_check_interval}s")
        logger.info(f"  Max Health Check Retries: {self.max_health_check_retries}")
        logger.info(f"  Kopf Connect Timeout: {self.connect_timeout}s")
        logger.info(f"  Kopf Server Timeout: {self.server_timeout}s")
        logger.info(f"  Worker Batch Size: {self.worker_batch_size}")
        logger.info(f"  Watcher Retry Delay: {self.watcher_retry_delay}s")
        logger.info(f"  Watcher Max Delay: {self.watcher_max_delay}s")
        logger.info(f"  Watcher Reconnect Backoff: {self.watcher_reconnect_backoff}s")

# Global configuration instance
startup_config = StartupConfig()
