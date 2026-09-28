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

import asyncio
import logging
import os
import time
from typing import List, Callable, Dict, Any
from .startup_config import startup_config

logger = logging.getLogger(__name__)

class StartupPhase:
    """Represents a startup phase with its associated modules and configuration."""
    
    def __init__(self, name: str, description: str, priority: int = 0):
        self.name = name
        self.description = description
        self.priority = priority
        self.modules: List[Callable] = []
        self.enabled = True
        
    def add_module(self, module_loader: Callable):
        """Add a module loader function to this phase."""
        self.modules.append(module_loader)
        
    def is_enabled(self) -> bool:
        """Check if this phase should be executed based on environment variables."""
        return self.enabled

class StartupCoordinator:
    """
    Coordinates the startup of lifecycle watchers to prevent API storms.
    Implements phased initialization with health checks and delays.
    """
    
    def __init__(self):
        self.phases: Dict[str, StartupPhase] = {}
        self.startup_start_time = None
        self.api_client = None
        
    def register_phase(self, phase: StartupPhase):
        """Register a startup phase."""
        self.phases[phase.name] = phase
        logger.debug(f"Registered startup phase: {phase.name}")
        
    async def check_api_health(self) -> bool:
        """
        Check if the Kubernetes API is responsive before starting watchers.
        """
        try:
            import kubernetes
            
            # Initialize client if not already done
            if self.api_client is None:
                kubernetes.config.load_incluster_config()
                self.api_client = kubernetes.client.CoreV1Api()
            
            # Simple API call to check connectivity
            start_time = time.time()
            namespaces = self.api_client.list_namespace(limit=1)
            response_time = time.time() - start_time
            
            if response_time > 5.0:
                logger.warning(f"API response time is high: {response_time:.2f}s")
                return False
                
            logger.debug(f"API health check passed in {response_time:.2f}s")
            return True
            
        except Exception as e:
            logger.error(f"API health check failed: {e}")
            return False
    
    async def wait_for_api_readiness(self) -> bool:
        """
        Wait for the Kubernetes API to be ready with retries.
        """
        logger.info("Checking API readiness before starting watchers...")
        
        for attempt in range(startup_config.max_health_check_retries):
            if await self.check_api_health():
                logger.info(f"API is ready (attempt {attempt + 1})")
                return True
                
            if attempt < startup_config.max_health_check_retries - 1:
                wait_time = startup_config.api_health_check_interval
                logger.warning(f"API not ready, retrying in {wait_time}s (attempt {attempt + 1}/{startup_config.max_health_check_retries})")
                await asyncio.sleep(wait_time)
        
        logger.error("API readiness check failed after all retries")
        return False
    
    async def execute_phase(self, phase: StartupPhase) -> bool:
        """
        Execute a single startup phase with proper error handling.
        """
        if not phase.is_enabled():
            logger.debug(f"Phase {phase.name} is disabled, skipping")
            return True
            
        logger.info(f"Starting phase: {phase.name} - {phase.description}")
        phase_start_time = time.time()
        
        try:
            for i, module_loader in enumerate(phase.modules):
                logger.debug(f"Loading module {i+1}/{len(phase.modules)} in phase {phase.name}")
                
                # Execute the module loader
                if asyncio.iscoroutinefunction(module_loader):
                    await module_loader()
                else:
                    module_loader()
                
                # Add delay between modules within a phase
                if i < len(phase.modules) - 1 and startup_config.watcher_delay > 0:
                    logger.debug(f"Waiting {startup_config.watcher_delay}s before next module")
                    await asyncio.sleep(startup_config.watcher_delay)
            
            phase_duration = time.time() - phase_start_time
            logger.info(f"Phase {phase.name} completed successfully in {phase_duration:.2f}s")
            return True
            
        except Exception as e:
            phase_duration = time.time() - phase_start_time
            logger.error(f"Phase {phase.name} failed after {phase_duration:.2f}s: {e}")
            return False
    
    async def execute_startup_sequence(self) -> bool:
        """
        Execute the complete startup sequence with coordination.
        """
        self.startup_start_time = time.time()
        logger.info("Starting coordinated lifecycle watcher initialization...")
        
        # Wait for API readiness first
        if not await self.wait_for_api_readiness():
            logger.error("API readiness check failed, aborting coordinated startup")
            return False
        
        # Sort phases by priority
        sorted_phases = sorted(self.phases.values(), key=lambda p: p.priority)
        
        # Execute phases in order
        for i, phase in enumerate(sorted_phases):
            success = await self.execute_phase(phase)
            
            if not success:
                logger.error(f"Phase {phase.name} failed, aborting startup sequence")
                return False
            
            # Add delay between phases (except after the last one)
            if i < len(sorted_phases) - 1 and startup_config.phase_delay > 0:
                logger.info(f"Waiting {startup_config.phase_delay}s before next phase")
                await asyncio.sleep(startup_config.phase_delay)
        
        total_duration = time.time() - self.startup_start_time
        logger.info(f"Coordinated startup completed successfully in {total_duration:.2f}s")
        return True

# Global coordinator instance
startup_coordinator = StartupCoordinator()

# Phase registration functions
def register_core_infrastructure_phase():
    """Register the core infrastructure phase (highest priority)."""
    phase = StartupPhase(
        name="core_infrastructure",
        description="Core infrastructure and basic services",
        priority=1
    )
    
    # Add core modules that should start first
    if os.getenv("GRAPH") is not None:
        def load_graph():
            logger.info("Loading GRAPH lifecycle")
            import graph.lifecycle
        phase.add_module(load_graph)
    
    startup_coordinator.register_phase(phase)

def register_network_services_phase():
    """Register the network services phase (medium priority)."""
    phase = StartupPhase(
        name="network_services", 
        description="VPN and network connectivity services",
        priority=2
    )
    
    if os.getenv("VPN") is not None:
        def load_vpn():
            logger.info("Loading VPN lifecycle")
            import vyosvm.lifecycle
            import transport.vyosunderlay.lifecycle
            import transport.vyosvpn.lifecycle
            import transport.vyosrouter.lifecycle
            import infrastructure.vyosinfrastructure.lifecycle
            import infrastructure.linuxnetwork.lifecycle
            import infrastructure.device.lifecycle
            import infrastructure.traffictest.lifecycle
            import networkfailure.lifecycle
            import opsagent.lifecycle
        phase.add_module(load_vpn)

    startup_coordinator.register_phase(phase)

def register_application_services_phase():
    """Register the application services phase (lowest priority)."""
    phase = StartupPhase(
        name="application_services",
        description="Application-level services and workloads",
        priority=3
    )

    def load_free5gc():
        import importlib
        logger.info("Loading FREE5GC lifecycle")
        importlib.import_module('core.xnf.lifecycle_tasks')
        importlib.import_module('core.xnf.lifecycle')
        importlib.import_module('core.core.lifecycle')
    phase.add_module(load_free5gc)

    def load_radio():
        import importlib
        logger.info("Loading RADIO lifecycle")
        importlib.import_module('radio.lifecycle')
    phase.add_module(load_radio)

    startup_coordinator.register_phase(phase)
