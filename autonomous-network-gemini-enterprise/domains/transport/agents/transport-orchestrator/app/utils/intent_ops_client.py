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

"""Sync Spanner client for intent state updates in agentops-db.

Used by orchestrator after_agent_callbacks to write progress phases back to the
IntentRecords table so the intent server can serve status without querying the
orchestrator.
"""

import logging
from typing import Optional
from google.cloud import spanner
from google.cloud.spanner_v1 import param_types
from ..config import GOOGLE_PROJECT, SPANNER_OPS_INSTANCE, SPANNER_OPS_DB

logger = logging.getLogger(__name__)


class IntentOpsClient:
    """Sync Spanner client for writing intent phase updates to agentops-db."""

    def __init__(self):
        import google.auth
        try:
            _creds, _proj = google.auth.default()
            _sa = getattr(_creds, 'service_account_email', None) or getattr(_creds, '_service_account_email', 'unknown')
            logger.warning(f"[SPANNER DIAG] ADC project={_proj} sa={_sa} GOOGLE_PROJECT={GOOGLE_PROJECT} instance={SPANNER_OPS_INSTANCE} db={SPANNER_OPS_DB}")
        except Exception as e:
            logger.warning(f"[SPANNER DIAG] google.auth.default() failed: {e}")
        self._client = spanner.Client(project=GOOGLE_PROJECT, disable_builtin_metrics=True)
        self._instance = self._client.instance(SPANNER_OPS_INSTANCE)
        self._database = self._instance.database(SPANNER_OPS_DB)

    def update_phase(self, intent_id: str, phase: str, cr_name: Optional[str] = None) -> None:
        """Update the intent phase (state column) and optionally the CR name.

        Args:
            intent_id: Intent UUID
            phase: New phase value (Planning, Designing, Deploying, Completed, Rejected)
            cr_name: Kubernetes CR name (service_name from validation), set when known
        """
        try:
            if cr_name is not None:
                columns = ["id", "state", "crName", "updatedAt"]
                values = [[intent_id, phase, cr_name, spanner.COMMIT_TIMESTAMP]]
            else:
                columns = ["id", "state", "updatedAt"]
                values = [[intent_id, phase, spanner.COMMIT_TIMESTAMP]]

            with self._database.batch() as batch:
                batch.update(
                    table="IntentRecords",
                    columns=columns,
                    values=values,
                )

            logger.info("Intent phase updated via orchestrator callback", extra={
                "intent_id": intent_id,
                "phase": phase,
                "cr_name": cr_name,
            })

        except Exception as e:
            logger.error("Failed to update intent phase from orchestrator callback", extra={
                "intent_id": intent_id,
                "phase": phase,
                "error": str(e),
            }, exc_info=True)


_client: Optional[IntentOpsClient] = None


def get_intent_ops_client() -> IntentOpsClient:
    """Return a module-level singleton IntentOpsClient."""
    global _client
    if _client is None:
        _client = IntentOpsClient()
    return _client
