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

"""Resource pool management with Spanner persistence"""

import logging
from google.cloud import spanner
from google.cloud.spanner_v1 import param_types
from ..config import (
    GOOGLE_PROJECT, SPANNER_INSTANCE, SPANNER_TRANSPORT_DB,
    VRF_SEQ_MIN, VRF_SEQ_MAX, RT_SEQ_MIN, RT_SEQ_MAX,
    CUSTOMER_AS_MIN, CUSTOMER_AS_MAX
)

logger = logging.getLogger(__name__)


class ResourcePoolManager:
    """Manages resource allocation with Spanner-backed persistence"""

    def __init__(self):
        """Initialize Spanner client"""
        self.client = spanner.Client(project=GOOGLE_PROJECT, disable_builtin_metrics=True)
        self.instance = self.client.instance(SPANNER_INSTANCE)
        self.database = self.instance.database(SPANNER_TRANSPORT_DB)

    def allocate_vrf_sequence(self, pe_router: str) -> int:
        """Allocate next VRF sequence number for a PE router

        Args:
            pe_router: PE router name (scope for VRF sequences)

        Returns:
            Next available VRF sequence number (200-65535)

        Raises:
            RuntimeError: If pool is exhausted
        """
        return self._allocate_sequence(
            pool_name="vrf_seq",
            scope=pe_router,
            min_value=VRF_SEQ_MIN,
            max_value=VRF_SEQ_MAX,
            initial_value=VRF_SEQ_MIN
        )

    def allocate_rt_sequence(self, count: int = 1) -> int:
        """Allocate next RT sequence number(s) globally

        Args:
            count: Number of consecutive RTs to allocate (1 for any-to-any, 2 for hub-spoke/p2p)

        Returns:
            Starting RT sequence number (2000-65535)

        Raises:
            RuntimeError: If pool is exhausted
        """
        return self._allocate_sequence(
            pool_name="rt_seq",
            scope="global",
            min_value=RT_SEQ_MIN,
            max_value=RT_SEQ_MAX,
            initial_value=RT_SEQ_MIN,
            increment=count
        )

    def allocate_customer_as(self) -> int:
        """Allocate next customer AS number globally

        Returns:
            Next available customer AS (65100-65199)

        Raises:
            RuntimeError: If pool is exhausted
        """
        return self._allocate_sequence(
            pool_name="customer_as",
            scope="global",
            min_value=CUSTOMER_AS_MIN,
            max_value=CUSTOMER_AS_MAX,
            initial_value=CUSTOMER_AS_MIN
        )

    def _allocate_sequence(
        self,
        pool_name: str,
        scope: str,
        min_value: int,
        max_value: int,
        initial_value: int,
        increment: int = 1
    ) -> int:
        """Allocate next sequence number with optimistic locking

        Args:
            pool_name: Pool identifier (vrf_seq, rt_seq, customer_as)
            scope: Scope (router name or "global")
            min_value: Minimum sequence value
            max_value: Maximum sequence value
            initial_value: Starting value for new pools
            increment: Number to allocate (default 1)

        Returns:
            Allocated sequence number

        Raises:
            RuntimeError: If pool is exhausted or allocation fails after retries
        """
        max_retries = 5

        for attempt in range(max_retries):
            try:
                # Use a transaction to read current value and update atomically
                def allocate_txn(transaction):
                    # Read current pool state
                    query = """
                        SELECT last_sequence
                        FROM ResourcePools
                        WHERE pool_name = @pool_name AND scope = @scope
                    """
                    params = {"pool_name": pool_name, "scope": scope}
                    param_types_dict = {
                        "pool_name": param_types.STRING,
                        "scope": param_types.STRING
                    }

                    results = transaction.execute_sql(
                        query, params=params, param_types=param_types_dict
                    )
                    rows = list(results)

                    if rows:
                        current_sequence = rows[0][0]
                    else:
                        # Initialize new pool
                        current_sequence = initial_value - increment

                    # Calculate next sequence
                    next_sequence = current_sequence + increment

                    # Check bounds
                    if next_sequence > max_value:
                        raise RuntimeError(
                            f"Resource pool {pool_name}:{scope} exhausted "
                            f"(max: {max_value})"
                        )

                    # Update pool
                    if rows:
                        # Update existing pool
                        update_sql = """
                            UPDATE ResourcePools
                            SET last_sequence = @next_sequence,
                                updated_at = PENDING_COMMIT_TIMESTAMP()
                            WHERE pool_name = @pool_name AND scope = @scope
                        """
                    else:
                        # Insert new pool
                        update_sql = """
                            INSERT INTO ResourcePools
                            (pool_name, scope, last_sequence, updated_at)
                            VALUES (@pool_name, @scope, @next_sequence, PENDING_COMMIT_TIMESTAMP())
                        """

                    update_params = {
                        "pool_name": pool_name,
                        "scope": scope,
                        "next_sequence": next_sequence
                    }
                    update_param_types = {
                        "pool_name": param_types.STRING,
                        "scope": param_types.STRING,
                        "next_sequence": param_types.INT64
                    }

                    transaction.execute_update(
                        update_sql,
                        params=update_params,
                        param_types=update_param_types
                    )

                    return next_sequence

                # Run transaction
                allocated = self.database.run_in_transaction(allocate_txn)

                logger.info(
                    f"Allocated {pool_name}:{scope} = {allocated} "
                    f"(increment={increment}, attempt={attempt + 1})"
                )

                return allocated

            except Exception as e:
                if "exhausted" in str(e):
                    raise
                if attempt == max_retries - 1:
                    raise RuntimeError(
                        f"Failed to allocate {pool_name}:{scope} after "
                        f"{max_retries} attempts: {e}"
                    )
                logger.warning(
                    f"Allocation attempt {attempt + 1} failed, retrying: {e}"
                )

        raise RuntimeError(
            f"Failed to allocate {pool_name}:{scope} after {max_retries} attempts"
        )


# Global instance
_resource_manager = None


def get_resource_manager() -> ResourcePoolManager:
    """Get or create global ResourcePoolManager instance"""
    global _resource_manager
    if _resource_manager is None:
        _resource_manager = ResourcePoolManager()
    return _resource_manager
