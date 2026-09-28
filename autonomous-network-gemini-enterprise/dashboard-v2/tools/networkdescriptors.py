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

"""
NetworkDescriptor management for the Supervisor agent.

Provides functions to:
  - Check whether a named network descriptor exists in Spanner.
  - Load the bundled default (telco-lab) network descriptor from the local
    JSON file shipped with the container image.
  - Save a network descriptor to Spanner (INSERT OR UPDATE).
  - Initialise the 'default' network on supervisor startup if it is absent.
"""

import json
import logging
import os

from google.cloud import spanner

from utils.database import get_database

# NetworkDescriptor lives in agentops-db under the six-database schema: it
# spans every domain, so it belongs to none of them.
DESCRIPTOR_DOMAIN = 'ops'

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_NETWORK_ID   = "network:default"
DEFAULT_NETWORK_NAME = "default"

# Path to the bundled JSON descriptor — resolved relative to this file so it
# works regardless of the working directory inside the container.
_DATA_DIR        = os.path.join(os.path.dirname(__file__), "data")
_DEFAULT_NETWORK_JSON = os.path.join(_DATA_DIR, "default-network.json")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _check_network_exists(database, network_id: str) -> bool:
    """
    Return True if a NetworkDescriptor row with the given id exists in Spanner.

    Args:
        database: An open Spanner database object.
        network_id: The primary key to check (e.g. ``network:default``).

    Returns:
        bool
    """
    query = "SELECT id FROM NetworkDescriptor WHERE id = @network_id LIMIT 1"
    params      = {"network_id": network_id}
    param_types = {"network_id": spanner.param_types.STRING}

    with database.snapshot() as snapshot:
        results = snapshot.execute_sql(query, params=params, param_types=param_types)
        for _ in results:
            return True
    return False


def _load_default_network_descriptor() -> dict:
    """
    Load the pre-built default network descriptor from the local JSON file.

    Returns:
        dict with keys: id, name, description, infrastructure, underlay,
                        vpns, traffic_tests, labels

    Raises:
        FileNotFoundError: if the JSON data file is missing.
        json.JSONDecodeError: if the file contains invalid JSON.
    """
    logger.debug("Loading default network descriptor from %s", _DEFAULT_NETWORK_JSON)
    with open(_DEFAULT_NETWORK_JSON, "r") as f:
        return json.load(f)


def _as_json(val, default):
    """
    Coerce a Spanner JSON column value to a plain Python ``dict`` or ``list``.

    The Spanner Python client returns JSON columns as
    ``google.cloud.spanner_v1.data_types.JsonObject``, a subclass of ``dict``.
    When the stored JSON is an *array* the JsonObject stores the elements in an
    internal ``_items`` attribute rather than as dict key-value pairs, so a
    plain ``isinstance(val, dict)`` check passes but iterating over the object
    yields nothing (empty dict keys).

    To recover the correct Python type we force a round-trip through the JSON
    serialiser: ``JsonObject.serialize()`` knows to emit ``[...]`` for
    array-typed objects.  For plain Python ``dict``/``list`` (exact type, not
    a subclass) we short-circuit and return immediately.

    Args:
        val:     Value from a Spanner JSON column (JsonObject, list, str, or None).
        default: Returned when ``val`` is ``None``.

    Returns:
        Plain Python ``dict`` or ``list``.
    """
    if val is None:
        return default
    # Plain Python list or dict (exact type, not a subclass) — return as-is.
    if type(val) in (list, dict):
        return val
    # Spanner JsonObject (dict subclass): round-trip through JSON to recover the
    # correct Python type, especially for array-valued columns.
    if isinstance(val, dict):
        try:
            raw = val.serialize() if hasattr(val, "serialize") else json.dumps(val)
            return json.loads(raw)
        except Exception:
            return default
    # Raw JSON string (older Spanner client versions or unit-test stubs).
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default


def _save_network_descriptor(database, descriptor: dict) -> None:
    """
    Write a NetworkDescriptor to Spanner using INSERT OR UPDATE semantics.

    JSON columns (infrastructure, underlay, vpns, traffic_tests, labels) are
    serialised to strings with ``json.dumps`` and passed as ``STRING`` params.
    The DML wraps each with ``PARSE_JSON()`` so Spanner stores them as the
    native JSON type.  This avoids the ``ValueError: Unknown type`` raised by
    the Spanner Python client when raw dicts are passed as param values.

    Args:
        database: An open Spanner database object.
        descriptor: dict with keys matching the NetworkDescriptor schema.
    """
    network_id     = descriptor["id"]
    name           = descriptor["name"]
    description    = descriptor.get("description", "")
    # JSON columns must be serialised to a string and converted with PARSE_JSON()
    # in the DML — the Spanner Python client does not support raw dicts as params.
    infrastructure = json.dumps(_as_json(descriptor.get("infrastructure"), {}))
    underlay       = json.dumps(_as_json(descriptor.get("underlay"), {}))
    vpns           = json.dumps(_as_json(descriptor.get("vpns"), []))
    traffic_tests  = json.dumps(_as_json(descriptor.get("traffic_tests"), []))
    labels         = json.dumps(_as_json(descriptor.get("labels"), {}))

    dml = """
        INSERT OR UPDATE NetworkDescriptor
            (id, name, description, infrastructure, underlay, vpns,
             traffic_tests, labels, created_at, updated_at)
        VALUES
            (@id, @name, @description,
             PARSE_JSON(@infrastructure), PARSE_JSON(@underlay), PARSE_JSON(@vpns),
             PARSE_JSON(@traffic_tests), PARSE_JSON(@labels),
             PENDING_COMMIT_TIMESTAMP(), PENDING_COMMIT_TIMESTAMP())
    """

    params = {
        "id":             network_id,
        "name":           name,
        "description":    description,
        "infrastructure": infrastructure,
        "underlay":       underlay,
        "vpns":           vpns,
        "traffic_tests":  traffic_tests,
        "labels":         labels,
    }

    param_types = {
        "id":             spanner.param_types.STRING,
        "name":           spanner.param_types.STRING,
        "description":    spanner.param_types.STRING,
        "infrastructure": spanner.param_types.STRING,
        "underlay":       spanner.param_types.STRING,
        "vpns":           spanner.param_types.STRING,
        "traffic_tests":  spanner.param_types.STRING,
        "labels":         spanner.param_types.STRING,
    }

    def _run(transaction):
        transaction.execute_update(dml, params=params, param_types=param_types)

    database.run_in_transaction(_run)
    logger.debug("Saved network descriptor '%s' to Spanner", network_id)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_network_descriptors() -> list:
    """
    Return a summary list of all stored NetworkDescriptor rows.

    Returns:
        list of dicts with keys: id, name, description, labels, updated_at
    """
    logger.debug("Listing all network descriptors")
    database = get_database(DESCRIPTOR_DOMAIN)
    query = """
        SELECT id, name, description, labels, updated_at
        FROM NetworkDescriptor
        ORDER BY updated_at DESC
    """
    results = []
    with database.snapshot() as snapshot:
        rows = snapshot.execute_sql(query)
        for row in rows:
            results.append({
                "id":          row[0],
                "name":        row[1],
                "description": row[2],
                "labels":      _as_json(row[3], {}),
                "updated_at":  row[4].isoformat() if row[4] else None,
            })
    logger.debug("Found %d network descriptor(s)", len(results))
    return results


def get_network_descriptor_summary(network_id: str) -> dict | None:
    """
    Return summary metadata for a single NetworkDescriptor (no full CRD bodies).

    Args:
        network_id: The primary key (e.g. ``network:default``).

    Returns:
        dict with keys: id, name, description, labels, updated_at,
                        vpn_count, traffic_test_count
        or None if not found.
    """
    logger.debug("Getting network descriptor summary for '%s'", network_id)
    database = get_database(DESCRIPTOR_DOMAIN)
    query = """
        SELECT id, name, description, labels, updated_at, vpns, traffic_tests
        FROM NetworkDescriptor
        WHERE id = @network_id
    """
    params      = {"network_id": network_id}
    param_types = {"network_id": spanner.param_types.STRING}

    with database.snapshot() as snapshot:
        rows = snapshot.execute_sql(query, params=params, param_types=param_types)
        for row in rows:
            vpns_val  = _as_json(row[5], [])
            tests_val = _as_json(row[6], [])
            return {
                "id":                 row[0],
                "name":               row[1],
                "description":        row[2],
                "labels":             _as_json(row[3], {}),
                "updated_at":         row[4].isoformat() if row[4] else None,
                "vpn_count":          len(vpns_val),
                "traffic_test_count": len(tests_val),
            }
    return None


def _get_full_network_descriptor(database, network_id: str) -> dict | None:
    """
    Return the complete NetworkDescriptor including all parsed CRD bodies.

    Args:
        database: An open Spanner database object.
        network_id: The primary key.

    Returns:
        Full descriptor dict or None if not found.
    """
    query = """
        SELECT id, name, description, labels, updated_at,
               infrastructure, underlay, vpns, traffic_tests
        FROM NetworkDescriptor
        WHERE id = @network_id
    """
    params      = {"network_id": network_id}
    param_types = {"network_id": spanner.param_types.STRING}

    with database.snapshot() as snapshot:
        rows = snapshot.execute_sql(query, params=params, param_types=param_types)
        for row in rows:
            return {
                "id":             row[0],
                "name":           row[1],
                "description":    row[2],
                "labels":         _as_json(row[3], {}),
                "updated_at":     row[4].isoformat() if row[4] else None,
                "infrastructure": _as_json(row[5], {}),
                "underlay":       _as_json(row[6], {}),
                "vpns":           _as_json(row[7], []),
                "traffic_tests":  _as_json(row[8], []),
            }
    return None


async def apply_crds_background(descriptor: dict) -> None:
    """
    Apply all CRDs from a network descriptor to the Kubernetes cluster.

    Intended to be run as a fire-and-forget background task via
    ``asyncio.create_task(apply_crds_background(descriptor))``.

    Uses the same ``kubernetes.dynamic.DynamicClient`` approach as
    ``engineering.py`` in the NetworkAgent project — the dynamic client
    resolves api_version + kind to the correct server endpoint automatically,
    removing the need to specify explicit group/version/plural strings.

    Apply order: infrastructure → underlay → vpns → traffic_tests.

    Args:
        descriptor: Full descriptor dict (as returned by ``_get_full_network_descriptor``).
    """
    network_id = descriptor.get("id", "unknown")
    logger.debug("Background CRD deploy started for '%s'", network_id)

    try:
        import kubernetes
        from kubernetes.client.rest import ApiException
        from agent_library import get_client as get_k8s_client

        dyn_client = kubernetes.dynamic.DynamicClient(get_k8s_client())

        # Build ordered list of CRD bodies in dependency order
        resources = []
        if descriptor.get("infrastructure"):
            resources.append(descriptor["infrastructure"])
        if descriptor.get("underlay"):
            resources.append(descriptor["underlay"])
        for vpn in descriptor.get("vpns", []):
            resources.append(vpn)
        for test in descriptor.get("traffic_tests", []):
            resources.append(test)

        applied = []
        failed  = []

        for body in resources:
            kind      = body.get("kind", "Unknown")
            name      = body.get("metadata", {}).get("name", "unknown")
            namespace = body.get("metadata", {}).get("namespace", "network")
            try:
                resource_api = dyn_client.resources.get(
                    api_version="google.dev/v1", kind=kind
                )
                resource_api.create(body, namespace=namespace)
                applied.append(f"{kind}/{name}")
                logger.debug("Created %s/%s", kind, name)
            except ApiException as exc:
                if exc.status == 409:
                    # Already exists — merge-patch
                    try:
                        resource_api.patch(
                            body=body,
                            name=name,
                            namespace=namespace,
                            content_type='application/merge-patch+json'
                        )
                        applied.append(f"{kind}/{name} (updated)")
                        logger.debug("Updated %s/%s", kind, name)
                    except ApiException as patch_exc:
                        failed.append(f"{kind}/{name}: {patch_exc.reason}")
                        logger.error("Failed to patch %s/%s: %s", kind, name, patch_exc)
                else:
                    failed.append(f"{kind}/{name}: {exc.reason}")
                    logger.error("Failed to create %s/%s: %s", kind, name, exc)
            except Exception as resource_exc:
                # Catch non-ApiException errors (e.g. ResourceNotFoundError from the
                # dynamic client when the CRD kind is not registered) so that a failure
                # on one resource does not abort deployment of all subsequent resources.
                failed.append(f"{kind}/{name}: {resource_exc}")
                logger.error(
                    "Unexpected error creating %s/%s: %s",
                    kind, name, resource_exc, exc_info=True,
                )

        logger.debug(
            "Deploy of '%s' complete — applied: %d, failed: %d | %s | %s",
            network_id, len(applied), len(failed), applied, failed
        )

    except Exception as exc:
        logger.error(
            "Unhandled error in background CRD deploy for '%s': %s",
            network_id, exc, exc_info=True
        )


# Reverse dependency order — must be deleted before the resources they depend on.
_TEARDOWN_KINDS = [
    "TrafficTest",       # depends on VyOSL3VPN
    "VyOSL3VPN",         # depends on VyOSUnderlay
    "VyOSUnderlay",      # depends on VyOSInfrastructure
    "VyOSInfrastructure",
]


async def _emit_progress(sio, network_id: str, **fields) -> None:
    """
    Emit a ``deploy_progress`` Socket.IO event to all connected clients.

    All keyword arguments are merged into the event payload alongside
    ``network_id``.  Silently no-ops when ``sio`` is ``None``.
    """
    if sio is None:
        return
    try:
        await sio.emit("deploy_progress", {"network_id": network_id, **fields})
    except Exception as emit_exc:
        logger.warning("Failed to emit deploy_progress: %s", emit_exc)


async def _wait_for_cr_gone(
    resource_api,
    name: str,
    namespace: str,
    *,
    sio=None,
    network_id: str = "",
    kind: str = "",
    timeout: float = 120.0,
    poll_interval: float = 2.0,
) -> bool:
    """
    Poll until a CR disappears from the API server (HTTP 404) or the timeout
    expires.

    Emits a ``deploy_progress`` Socket.IO event on every poll tick so the UI
    can display a live "waiting for deletion" message.

    Args:
        resource_api:   kubernetes dynamic resource API handle.
        name:           CR name to watch.
        namespace:      Namespace of the CR.
        sio:            Optional Socket.IO server instance for progress events.
        network_id:     Passed through to progress events.
        kind:           Passed through to progress events.
        timeout:        Maximum seconds to wait (default 120).
        poll_interval:  Seconds between polls (default 2).

    Returns:
        ``True`` if the CR is gone within the timeout, ``False`` otherwise.
    """
    import asyncio
    import time
    from kubernetes.client.rest import ApiException

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resource_api.get(name=name, namespace=namespace)
            # Still present — tell the UI we're still waiting.
            await _emit_progress(
                sio, network_id,
                stage="waiting",
                kind=kind,
                name=name,
            )
            await asyncio.sleep(poll_interval)
        except ApiException as exc:
            if exc.status == 404:
                return True
            # Unexpected API error — propagate.
            raise

    logger.warning(
        "_wait_for_cr_gone timed out after %.0fs for %s/%s", timeout, kind, name
    )
    return False


async def teardown_existing_crds(
    namespace: str = "network",
    *,
    sio=None,
    network_id: str = "",
) -> dict:
    """
    Delete all existing network CRs and wait for each to be fully gone.

    Resources are deleted in reverse dependency order (TrafficTest first,
    VyOSInfrastructure last) so that dependent resources are removed before
    the resources they reference.

    For each CR the function:
      1. Issues the delete request.
      2. Emits a ``deploy_progress`` Socket.IO event with ``stage="deleting"``.
      3. Polls the API server until the CR disappears (HTTP 404).
      4. Emits a ``deploy_progress`` event with ``stage="deleted"`` once gone.

    Only after *all* CRs of a given kind have been confirmed gone does the
    function move on to the next kind.

    Args:
        namespace:  Kubernetes namespace to target. Defaults to ``"network"``.
        sio:        Optional Socket.IO server instance for progress events.
        network_id: Forwarded to every progress event.

    Returns:
        dict with keys ``deleted`` (list[str]) and ``failed`` (list[str]).
    """
    logger.debug("Teardown started for namespace '%s'", namespace)
    deleted = []
    failed  = []

    try:
        import kubernetes
        from kubernetes.client.rest import ApiException
        from agent_library import get_client as get_k8s_client

        dyn_client = kubernetes.dynamic.DynamicClient(get_k8s_client())

        for kind in _TEARDOWN_KINDS:
            try:
                resource_api = dyn_client.resources.get(
                    api_version="google.dev/v1", kind=kind
                )
                items = resource_api.get(namespace=namespace)
                cr_list = items.items if hasattr(items, "items") else []

                if not cr_list:
                    logger.debug("Teardown: no %s resources found in '%s'", kind, namespace)
                    continue

                # ── Step 1: Issue deletes for all CRs of this kind ──────────
                names_to_watch: list[str] = []
                for cr in cr_list:
                    name = cr.metadata.name
                    try:
                        resource_api.delete(name=name, namespace=namespace)
                        names_to_watch.append(name)
                        await _emit_progress(
                            sio, network_id,
                            stage="deleting",
                            kind=kind,
                            name=name,
                        )
                        logger.debug("Issued delete for %s/%s", kind, name)
                    except ApiException as del_exc:
                        if del_exc.status == 404:
                            logger.debug("%s/%s already gone", kind, name)
                        else:
                            failed.append(f"{kind}/{name}: {del_exc.reason}")
                            logger.error(
                                "Failed to delete %s/%s: %s", kind, name, del_exc
                            )

                # ── Step 2: Wait for each CR to be fully gone ────────────────
                for name in names_to_watch:
                    gone = await _wait_for_cr_gone(
                        resource_api,
                        name,
                        namespace,
                        sio=sio,
                        network_id=network_id,
                        kind=kind,
                    )
                    if gone:
                        deleted.append(f"{kind}/{name}")
                        await _emit_progress(
                            sio, network_id,
                            stage="deleted",
                            kind=kind,
                            name=name,
                        )
                        logger.debug("Confirmed %s/%s fully gone", kind, name)
                    else:
                        failed.append(f"{kind}/{name}: deletion timed out")
                        logger.error("Timed out waiting for %s/%s to be gone", kind, name)

            except ApiException as list_exc:
                # CRD may not be installed yet — treat as no-op.
                logger.warning(
                    "Could not list %s resources (CRD missing?): %s", kind, list_exc
                )
            except Exception as kind_exc:
                logger.error(
                    "Unexpected error during teardown of %s: %s", kind, kind_exc,
                    exc_info=True,
                )

    except Exception as exc:
        logger.error("Unhandled error during teardown: %s", exc, exc_info=True)

    logger.debug(
        "Teardown complete — deleted: %d, failed: %d | %s | %s",
        len(deleted), len(failed), deleted, failed,
    )
    return {"deleted": deleted, "failed": failed}


async def teardown_and_deploy_background(descriptor: dict) -> None:
    """
    Tear down all existing network CRDs then deploy a new network descriptor.

    This is the entry-point for a "replace" deploy — it ensures a clean slate
    before the new descriptor's resources are applied.  Socket.IO progress
    events are emitted throughout so the dashboard UI can show live status.

    Steps:
        1. Obtain the Socket.IO server from ``SocketEndpoint._instance``.
        2. Determine the target namespace from the descriptor's infrastructure
           metadata (falls back to ``"default"``).
        3. Emit ``stage="teardown_started"`` progress event.
        4. Delete all existing CRs and wait for each to be gone.
        5. Emit ``stage="teardown_complete"`` progress event.
        6. Apply the new descriptor's CRDs.
        7. Emit ``stage="deploy_complete"`` or ``stage="deploy_failed"``.

    Args:
        descriptor: Full descriptor dict (as returned by
                    ``get_descriptor_for_deploy``).
    """
    network_id = descriptor.get("id", "unknown")
    logger.debug("Teardown-and-deploy started for '%s'", network_id)

    # Obtain the Socket.IO server lazily to avoid circular imports.
    sio = None
    try:
        from endpoints.socketendpoint import SocketEndpoint
        if SocketEndpoint._instance is not None:
            sio = SocketEndpoint._instance.sio
    except Exception as sio_exc:
        logger.warning("Could not obtain sio instance: %s", sio_exc)

    # Derive namespace from the infrastructure body if available.
    infra = descriptor.get("infrastructure") or {}
    namespace = infra.get("metadata", {}).get("namespace", "network")

    await _emit_progress(sio, network_id, stage="teardown_started")

    result = await teardown_existing_crds(namespace, sio=sio, network_id=network_id)

    await _emit_progress(
        sio, network_id,
        stage="teardown_complete",
        deleted=len(result["deleted"]),
        failed=len(result["failed"]),
    )

    try:
        await _emit_progress(sio, network_id, stage="deploying")
        await apply_crds_background(descriptor)
        await _emit_progress(sio, network_id, stage="deploy_complete")
    except Exception as deploy_exc:
        logger.error(
            "Deploy step failed for '%s': %s", network_id, deploy_exc, exc_info=True
        )
        await _emit_progress(
            sio, network_id,
            stage="deploy_failed",
            error=str(deploy_exc),
        )


async def teardown_only_background() -> None:
    """
    Tear down a deployed network without deploying a replacement.

    Uses the GitOps path:
      1. Deletes all YAML files for the network from the Gitea ``network``
         repo — Config Sync sees the removals and starts deleting K8s CRs.
      2. Polls the Kubernetes API until the ``VyOSInfrastructure`` CR
         disappears, confirming that Config Sync has completed the teardown.

    Intended to be run as a fire-and-forget background task via
    ``asyncio.create_task(teardown_only_background())``.

    Emits ``deploy_progress`` Socket.IO events throughout so the dashboard
    UI can display live deletion status in the progress banner.

    Stages emitted:
        teardown_started → git_deleted (per file) →
        waiting (while polling for VyOSInfrastructure) →
        teardown_only_complete  (or teardown_failed on error)

    """
    logger.debug("Standalone GitOps teardown started")

    # Obtain the Socket.IO server lazily to avoid circular imports.
    sio = None
    try:
        from endpoints.socketendpoint import SocketEndpoint
        if SocketEndpoint._instance is not None:
            sio = SocketEndpoint._instance.sio
    except Exception as sio_exc:
        logger.warning("Could not obtain sio instance: %s", sio_exc)

    try:
        await _emit_progress(sio, "", stage="teardown_started")

        # ── Step 1: Delete ALL YAML files currently in the Gitea repo ────
        from agent_library import delete_all_git_files
        from utils.git_utils import NETWORK_REPO
        message = f"Teardown all network resources"
        logger.debug("Deleting all files from '%s' repo ", NETWORK_REPO)
        deleted_files, failed_files = delete_all_git_files(message, repo_name=NETWORK_REPO)

        for fname in deleted_files:
            await _emit_progress(sio, "", stage="git_deleted", file=fname)
        for fname in failed_files:
            await _emit_progress(sio, "", stage="git_delete_failed", file=fname)

        logger.debug(
            "Git teardown for '%s' — deleted: %s, failed: %s",
            "default", deleted_files, failed_files,
        )

        # ── Step 2: Wait for all VyOSInfrastructure CRs to disappear ─────
        namespace = "network"
        try:
            import kubernetes
            from agent_library import get_client as get_k8s_client
            dyn_client = kubernetes.dynamic.DynamicClient(get_k8s_client())
            resource_api = dyn_client.resources.get(
                api_version="google.dev/v1", kind="VyOSInfrastructure"
            )
            items = resource_api.get(namespace=namespace)
            cr_list = items.items if hasattr(items, "items") else []
            for cr in cr_list:
                infra_name = cr.metadata.name
                await _wait_for_cr_gone(
                    resource_api, infra_name, namespace,
                    sio=sio, network_id="", kind="VyOSInfrastructure",
                )
                logger.debug(
                    "VyOSInfrastructure/%s confirmed gone",
                    infra_name,
                )
        except Exception as wait_exc:
            logger.warning(
                "Could not confirm VyOSInfrastructure CRs are gone: %s", wait_exc,
            )

        await _emit_progress(
            sio, "",
            stage="teardown_only_complete",
            deleted=len(deleted_files),
            failed=len(failed_files),
        )
        logger.debug(
            "Standalone GitOps teardown complete — git deleted: %d, git failed: %d",
            len(deleted_files), len(failed_files),
        )
    except Exception as exc:
        logger.error("Unhandled error in standalone teardown: %s", exc, exc_info=True)
        await _emit_progress(
            sio, "",
            stage="teardown_failed",
            error=str(exc),
        )


async def deploy_to_git_background(descriptor: dict) -> None:
    """
    Deploy a network descriptor via the GitOps path.

    This is the primary entry-point for network deployment.  Rather than
    applying CRDs directly to Kubernetes, the function:

      1. Deletes all existing YAML files for the network from the Gitea
         ``network`` repository — Config Sync starts removing the old K8s CRs.
      2. Polls the Kubernetes API until the ``VyOSInfrastructure`` CR
         disappears, confirming a clean slate.
      3. Commits the new descriptor's CRD bodies as YAML files to the Gitea
         ``network`` repository — Config Sync applies them to the cluster.

    Socket.IO ``deploy_progress`` events are emitted throughout so the
    dashboard UI can display a live status banner.

    Stages emitted:
        teardown_started →
        git_deleted / git_delete_failed (per file) →
        waiting (while polling VyOSInfrastructure) →
        teardown_complete →
        deploying →
        git_committed / git_commit_failed (per file) →
        deploy_complete  (or deploy_failed on error)

    Args:
        descriptor: Full descriptor dict (as returned by
                    ``get_descriptor_for_deploy``).
    """
    network_id = descriptor.get("id", "unknown")
    logger.debug("GitOps deploy started for '%s'", network_id)

    # Obtain the Socket.IO server lazily to avoid circular imports.
    sio = None
    try:
        from endpoints.socketendpoint import SocketEndpoint
        if SocketEndpoint._instance is not None:
            sio = SocketEndpoint._instance.sio
    except Exception as sio_exc:
        logger.warning("Could not obtain sio instance: %s", sio_exc)

    from utils.git_utils import deploy_descriptor_to_git

    # ── Phase 1: Teardown ─────────────────────────────────────────────────
    await teardown_only_background()

    # ── Phase 2: Deploy ───────────────────────────────────────────────────
    try:
        await _emit_progress(sio, network_id, stage="deploying")

        git_deploy = await deploy_descriptor_to_git(
            descriptor, sio=sio, network_id=network_id
        )
        logger.debug(
            "Git deploy for '%s' — committed: %s, failed: %s",
            network_id, git_deploy["committed"], git_deploy["failed"],
        )

        await _emit_progress(
            sio, network_id,
            stage="deploy_complete",
            committed=len(git_deploy["committed"]),
            failed=len(git_deploy["failed"]),
        )

    except Exception as deploy_exc:
        logger.error(
            "Git deploy step failed for '%s': %s", network_id, deploy_exc, exc_info=True
        )
        await _emit_progress(
            sio, network_id,
            stage="deploy_failed",
            error=str(deploy_exc),
        )


def get_descriptor_for_deploy(network_id: str) -> dict | None:
    """
    Load the full network descriptor from Spanner, ready for deployment.

    Args:
        network_id: The primary key (e.g. ``network:default``).

    Returns:
        Full descriptor dict or None if not found.
    """
    database = get_database(DESCRIPTOR_DOMAIN)
    return _get_full_network_descriptor(database, network_id)


# ---------------------------------------------------------------------------
# VPN delete lifecycle (single-VPN delete with concurrency guard)
# ---------------------------------------------------------------------------

import asyncio as _asyncio

_vpn_delete_lock = _asyncio.Lock()
_vpn_deleting_name: str | None = None


def get_vpn_delete_status() -> dict:
    """
    Return the current VPN-delete status.

    Returns:
        dict: { in_progress: bool, vpn_name: str | None }
    """
    return {
        "in_progress": _vpn_delete_lock.locked(),
        "vpn_name":    _vpn_deleting_name,
    }


async def delete_vpn_with_tests_background(
    vpn_name:  str,
    namespace: str = "network",
) -> None:
    """
    Delete all TrafficTests linked to *vpn_name* first, then delete the VPN
    itself, emitting ``vpn_delete_progress`` Socket.IO events throughout.

    Acquires ``_vpn_delete_lock`` before starting work so that only one VPN
    delete can be in flight at a time (the REST endpoint returns HTTP 409 if
    the lock is already held).

    Stages emitted via ``vpn_delete_progress``:
        deleting_tests  → (per-test) deleting / deleted / skipped
        deleting_vpn    → deleting / deleted
        complete        → success
        failed          → error message

    Args:
        vpn_name:  Name of the VyOSL3VPN CR to delete.
        namespace: Kubernetes namespace (default: ``"network"``).
    """
    global _vpn_deleting_name

    # Obtain sio lazily to avoid circular imports.
    sio = None
    try:
        from endpoints.socketendpoint import SocketEndpoint
        if SocketEndpoint._instance is not None:
            sio = SocketEndpoint._instance.sio
    except Exception as sio_exc:
        logger.warning("Could not obtain sio for vpn_delete: %s", sio_exc)

    async def _emit(stage: str, **extra) -> None:
        if sio is None:
            return
        try:
            await sio.emit(
                "vpn_delete_progress",
                {"vpn_name": vpn_name, "stage": stage, **extra},
            )
        except Exception as e:
            logger.warning("Failed to emit vpn_delete_progress: %s", e)

    async with _vpn_delete_lock:
        _vpn_deleting_name = vpn_name
        logger.debug("VPN delete background started for '%s'", vpn_name)

        try:
            import kubernetes
            from kubernetes.client.rest import ApiException
            from agent_library import get_client as get_k8s_client

            dyn_client = kubernetes.dynamic.DynamicClient(get_k8s_client())

            # ── Step 1: Delete linked TrafficTests ───────────────────────────
            await _emit("deleting_tests")
            try:
                tt_api = dyn_client.resources.get(
                    api_version="google.dev/v1", kind="TrafficTest"
                )
                items = tt_api.get(namespace=namespace)
                cr_list = items.items if hasattr(items, "items") else []

                linked = [
                    cr for cr in cr_list
                    if (cr.spec or {}).get("vpnRef") == vpn_name
                ]
                logger.debug(
                    "VPN delete: found %d linked TrafficTest(s) for '%s'",
                    len(linked), vpn_name,
                )

                for cr in linked:
                    tname = cr.metadata.name
                    try:
                        tt_api.delete(name=tname, namespace=namespace)
                        await _emit("deleting", resource_kind="TrafficTest", resource_name=tname)
                        gone = await _wait_for_cr_gone(
                            tt_api, tname, namespace,
                            sio=sio, network_id=vpn_name, kind="TrafficTest",
                        )
                        if gone:
                            await _emit("deleted", resource_kind="TrafficTest", resource_name=tname)
                            logger.debug("Deleted TrafficTest/%s", tname)
                        else:
                            logger.warning("Timed out waiting for TrafficTest/%s to be gone", tname)
                    except ApiException as exc:
                        if exc.status == 404:
                            await _emit("skipped", resource_kind="TrafficTest", resource_name=tname)
                        else:
                            logger.error("Error deleting TrafficTest/%s: %s", tname, exc)

            except Exception as tt_exc:
                logger.error("Error listing/deleting TrafficTests for VPN '%s': %s", vpn_name, tt_exc)

            # ── Step 2: Delete the VPN CRD ───────────────────────────────────
            await _emit("deleting_vpn")
            try:
                vpn_api = dyn_client.resources.get(
                    api_version="google.dev/v1", kind="VyOSL3VPN"
                )
                vpn_api.delete(name=vpn_name, namespace=namespace)
                await _emit("deleting", resource_kind="VyOSL3VPN", resource_name=vpn_name)
                gone = await _wait_for_cr_gone(
                    vpn_api, vpn_name, namespace,
                    sio=sio, network_id=vpn_name, kind="VyOSL3VPN",
                )
                if gone:
                    await _emit("deleted", resource_kind="VyOSL3VPN", resource_name=vpn_name)
                    logger.debug("Deleted VyOSL3VPN/%s", vpn_name)
                else:
                    await _emit("failed", message=f"Timed out waiting for VPN {vpn_name} to be deleted")
                    logger.warning("Timed out waiting for VyOSL3VPN/%s to be gone", vpn_name)
                    return
            except ApiException as exc:
                if exc.status == 404:
                    await _emit("deleted", resource_kind="VyOSL3VPN", resource_name=vpn_name)
                else:
                    await _emit("failed", message=str(exc))
                    logger.error("Error deleting VyOSL3VPN/%s: %s", vpn_name, exc)
                    return

            await _emit("complete")
            logger.debug("VPN delete background complete for '%s'", vpn_name)

        except Exception as exc:
            logger.error("Unhandled error in delete_vpn_with_tests_background: %s", exc, exc_info=True)
            await _emit("failed", message=str(exc))
        finally:
            _vpn_deleting_name = None


def initialise_default_network() -> None:
    """
    Ensure the 'default' network descriptor exists in Spanner.

    Called once during supervisor startup.  If the ``network:default`` row is
    already present the function returns immediately (idempotent).  If it is
    absent the bundled telco-lab descriptor is loaded from disk and written to
    Spanner.

    Any error is logged but does **not** propagate — a missing descriptor is
    not fatal to the supervisor's other functions.
    """
    logger.debug("Checking for default network descriptor (id=%s) …", DEFAULT_NETWORK_ID)
    try:
        database = get_database(DESCRIPTOR_DOMAIN)

        if _check_network_exists(database, DEFAULT_NETWORK_ID):
            logger.debug(
                "Default network descriptor already exists in Spanner — skipping initialisation."
            )
            return

        logger.debug(
            "Default network descriptor not found — loading telco-lab descriptor …"
        )
        descriptor = _load_default_network_descriptor()

        vpn_names  = [v.get("metadata", {}).get("name", "?") for v in descriptor.get("vpns", [])]
        test_names = [t.get("metadata", {}).get("name", "?") for t in descriptor.get("traffic_tests", [])]
        logger.debug(
            "Loaded descriptor '%s': %d VPN(s) %s, %d traffic test(s) %s",
            descriptor.get("name"),
            len(vpn_names), vpn_names,
            len(test_names), test_names,
        )

        _save_network_descriptor(database, descriptor)
        logger.debug("✓ Default network descriptor initialised successfully.")

    except Exception as exc:
        logger.error(
            "Failed to initialise default network descriptor: %s",
            exc,
            exc_info=True,
        )
