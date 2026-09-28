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

"""Spanner client for Intent record storage"""

import logging
import time
from google.cloud.spanner_v1 import AsyncClient
from google.cloud.spanner_v1.types import Type, TypeCode
from google.cloud import spanner
import json
from typing import Optional, List

logger = logging.getLogger(__name__)


class SpannerIntentStore:
    """Spanner-backed intent record storage"""

    def __init__(self, project_id: str, instance_id: str, database_id: str):
        """Initialize Spanner intent store

        Args:
            project_id: GCP project ID
            instance_id: Spanner instance ID
            database_id: Spanner database ID
        """
        self.project_id = project_id
        self.instance_id = instance_id
        self.database_id = database_id
        self._client = None
        self._database = None

    async def get_database(self):
        """Get or create Spanner database connection"""
        if self._database is None:
            logger.info("Initializing Spanner database connection", extra={
                "project_id": self.project_id,
                "instance_id": self.instance_id,
                "database_id": self.database_id,
            })

            start_time = time.time()

            try:
                self._client = AsyncClient(project=self.project_id, disable_builtin_metrics=True)
                instance = self._client.instance(self.instance_id)
                self._database = await instance.database(self.database_id)

                duration_ms = (time.time() - start_time) * 1000

                logger.info("Spanner database connection established", extra={
                    "project_id": self.project_id,
                    "instance_id": self.instance_id,
                    "database_id": self.database_id,
                    "duration_ms": round(duration_ms, 2),
                })

            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                logger.error("Failed to establish Spanner database connection", extra={
                    "project_id": self.project_id,
                    "instance_id": self.instance_id,
                    "database_id": self.database_id,
                    "duration_ms": round(duration_ms, 2),
                    "error": str(e),
                }, exc_info=True)
                raise

        return self._database

    async def update_phase(self, intent_id: str, phase: str, cr_name: Optional[str] = None):
        """Update intent phase (granular state) and optionally the CR name

        Args:
            intent_id: Intent UUID
            phase: New phase (Planning, Designing, Deploying, Completed, Rejected, etc.)
            cr_name: K8s CR name to set, if known

        Raises:
            Exception: If update fails
        """
        logger.debug("Spanner: updating intent phase", extra={
            "intent_id": intent_id,
            "phase": phase,
            "cr_name": cr_name,
        })

        db = await self.get_database()
        start_time = time.time()

        try:
            if cr_name is not None:
                async with db.batch() as batch:
                    batch.update(
                        table="IntentRecords",
                        columns=["id", "state", "crName", "updatedAt"],
                        values=[[intent_id, phase, cr_name, spanner.COMMIT_TIMESTAMP]]
                    )
            else:
                async with db.batch() as batch:
                    batch.update(
                        table="IntentRecords",
                        columns=["id", "state", "updatedAt"],
                        values=[[intent_id, phase, spanner.COMMIT_TIMESTAMP]]
                    )

            duration_ms = (time.time() - start_time) * 1000
            logger.info("Spanner: intent phase updated", extra={
                "intent_id": intent_id,
                "phase": phase,
                "duration_ms": round(duration_ms, 2),
            })

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error("Spanner: failed to update intent phase", extra={
                "intent_id": intent_id,
                "phase": phase,
                "duration_ms": round(duration_ms, 2),
                "error": str(e),
            }, exc_info=True)
            raise

    async def create_intent(self, intent: dict, cr_name: str = ""):
        """Store intent record

        Args:
            intent: TMF921 intent envelope (with id assigned)
            cr_name: L3VPN service name

        Raises:
            Exception: If insert fails
        """
        db = await self.get_database()

        # Extract serviceId from intent
        expectation = intent.get("intentExpression", {}).get("expectation", [{}])[0]
        service_id = expectation.get("expectationObject", {}).get("serviceId", "")

        logger.debug("Spanner: creating intent record", extra={
            "intent_id": intent["id"],
            "service_id": service_id,
            "cr_name": cr_name,
        })

        start_time = time.time()

        try:
            async with db.batch() as batch:
                batch.insert(
                    table="IntentRecords",
                    columns=["id", "serviceId", "crName", "state", "intentEnvelope", "createdAt", "updatedAt"],
                    values=[[
                        intent["id"],
                        service_id,
                        cr_name,
                        intent.get("state", "Acknowledged"),
                        json.dumps(intent),
                        spanner.COMMIT_TIMESTAMP,
                        spanner.COMMIT_TIMESTAMP
                    ]]
                )

            duration_ms = (time.time() - start_time) * 1000

            logger.info("Spanner: intent record created", extra={
                "intent_id": intent["id"],
                "service_id": service_id,
                "cr_name": cr_name,
                "duration_ms": round(duration_ms, 2),
            })

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error("Spanner: failed to create intent record", extra={
                "intent_id": intent["id"],
                "service_id": service_id,
                "cr_name": cr_name,
                "duration_ms": round(duration_ms, 2),
                "error": str(e),
            }, exc_info=True)
            raise

    def _parse_envelope(self, val) -> dict:
        if isinstance(val, dict):
            return val
        if isinstance(val, str):
            return json.loads(val)
        if hasattr(val, 'items'):
            return dict(val)
        if hasattr(val, 'to_dict'):
            return val.to_dict()
        return json.loads(str(val))

    async def get_intent(self, intent_id: str) -> Optional[dict]:
        """Retrieve intent by ID

        Args:
            intent_id: Intent UUID

        Returns:
            Intent record dict or None if not found
        """
        logger.debug("Spanner: retrieving intent", extra={
            "intent_id": intent_id,
        })

        db = await self.get_database()
        start_time = time.time()

        try:
            async with db.snapshot() as snapshot:
                results = await snapshot.execute_sql(
                    "SELECT id, serviceId, crName, state, intentEnvelope, createdAt, updatedAt "
                    "FROM IntentRecords WHERE id = @id",
                    params={"id": intent_id},
                    param_types={"id": Type(code=TypeCode.STRING)}
                )

                row = await results.one_or_none()
                duration_ms = (time.time() - start_time) * 1000

                if not row:
                    logger.debug("Spanner: intent not found", extra={
                        "intent_id": intent_id,
                        "duration_ms": round(duration_ms, 2),
                    })
                    return None

                result = {
                    "id": row[0],
                    "serviceId": row[1],
                    "crName": row[2],
                    "state": row[3],
                    "envelope": self._parse_envelope(row[4]),
                    "createdAt": row[5].isoformat() if row[5] else None,
                    "updatedAt": row[6].isoformat() if row[6] else None
                }

                logger.debug("Spanner: intent retrieved", extra={
                    "intent_id": intent_id,
                    "service_id": result["serviceId"],
                    "cr_name": result["crName"],
                    "state": result["state"],
                    "duration_ms": round(duration_ms, 2),
                })

                return result

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error("Spanner: failed to retrieve intent", extra={
                "intent_id": intent_id,
                "duration_ms": round(duration_ms, 2),
                "error": str(e),
            }, exc_info=True)
            raise

    async def list_intents(self) -> List[dict]:
        """List all active intents (state != 'Inactive')

        Returns:
            List of intent records
        """
        logger.debug("Spanner: listing active intents")

        db = await self.get_database()
        start_time = time.time()

        try:
            async with db.snapshot() as snapshot:
                results = await snapshot.execute_sql(
                    "SELECT id, serviceId, crName, state, intentEnvelope, createdAt, updatedAt "
                    "FROM IntentRecords WHERE state != 'Inactive' ORDER BY createdAt DESC"
                )

                intents = []
                async for row in results:
                    intents.append({
                        "id": row[0],
                        "serviceId": row[1],
                        "crName": row[2],
                        "state": row[3],
                        "envelope": self._parse_envelope(row[4]),
                        "createdAt": row[5].isoformat() if row[5] else None,
                        "updatedAt": row[6].isoformat() if row[6] else None
                    })

                duration_ms = (time.time() - start_time) * 1000

                logger.debug("Spanner: active intents listed", extra={
                    "count": len(intents),
                    "duration_ms": round(duration_ms, 2),
                })

                return intents

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error("Spanner: failed to list intents", extra={
                "duration_ms": round(duration_ms, 2),
                "error": str(e),
            }, exc_info=True)
            raise

    async def update_state(self, intent_id: str, state: str):
        """Update intent state

        Args:
            intent_id: Intent UUID
            state: New TMF921 state

        Raises:
            Exception: If update fails
        """
        logger.debug("Spanner: updating intent state", extra={
            "intent_id": intent_id,
            "new_state": state,
        })

        db = await self.get_database()
        start_time = time.time()

        try:
            async with db.batch() as batch:
                batch.update(
                    table="IntentRecords",
                    columns=["id", "state", "updatedAt"],
                    values=[[intent_id, state, spanner.COMMIT_TIMESTAMP]]
                )

            duration_ms = (time.time() - start_time) * 1000

            logger.info("Spanner: intent state updated", extra={
                "intent_id": intent_id,
                "new_state": state,
                "duration_ms": round(duration_ms, 2),
            })

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error("Spanner: failed to update intent state", extra={
                "intent_id": intent_id,
                "new_state": state,
                "duration_ms": round(duration_ms, 2),
                "error": str(e),
            }, exc_info=True)
            raise

    async def check_service_conflict(self, service_id: str) -> bool:
        """Check if serviceId already exists in active intents

        Args:
            service_id: L3VPNService serviceId

        Returns:
            True if conflict exists, False otherwise
        """
        logger.debug("Spanner: checking for service conflicts", extra={
            "service_id": service_id,
        })

        db = await self.get_database()
        start_time = time.time()

        try:
            async with db.snapshot() as snapshot:
                results = await snapshot.execute_sql(
                    "SELECT COUNT(*) FROM IntentRecords WHERE serviceId = @serviceId AND state NOT IN ('Inactive', 'Rejected')",
                    params={"serviceId": service_id},
                    param_types={"serviceId": Type(code=TypeCode.STRING)}
                )

                row = await results.one()
                count = row[0]
                duration_ms = (time.time() - start_time) * 1000

                conflict = count > 0

                logger.debug("Spanner: conflict check complete", extra={
                    "service_id": service_id,
                    "conflict": conflict,
                    "existing_count": count,
                    "duration_ms": round(duration_ms, 2),
                })

                return conflict

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error("Spanner: failed to check for conflicts", extra={
                "service_id": service_id,
                "duration_ms": round(duration_ms, 2),
                "error": str(e),
            }, exc_info=True)
            raise
