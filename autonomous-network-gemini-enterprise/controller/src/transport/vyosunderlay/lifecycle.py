import asyncio
import kopf
import logging
import time
import kubernetes
from typing import Dict, Any
from utils.vyosnetwork import patch_vyos_router, update_status, router_provisioning_lock

logger = logging.getLogger(__name__)


async def _wait_for_routers_leave_running(
    router_names: list, namespace: str, logger,
    timeout: int = 30, poll_interval: int = 3
) -> None:
    """Wait until every named router transitions AWAY from Running state.

    After a spec patch is written to the K8s API there is a brief window
    before kopf's update_vyosrouter handler fires and changes the router
    status to Updating.  If _wait_for_routers_running is called before that
    transition happens it will see stale Running status and return immediately,
    marking the underlay Ready while Ansible is still in-flight.

    This helper closes that race by blocking until each router's phase is no
    longer Running (confirming the update handler has started).  A short
    timeout is intentional — if a router stays Running for the full window
    it most likely means Ansible completed before our first poll; we log a
    warning and let the caller continue to _wait_for_routers_running.

    Raises kopf.PermanentError if any router enters Failed state.
    """
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='VyOSRouter')

    still_running = list(router_names)
    elapsed = 0
    while elapsed < timeout and still_running:
        next_still_running = []
        for rname in still_running:
            try:
                r = api.get(name=rname, namespace=namespace)
                phase = r.get('status', {}).get('phase', 'Unknown')
                if phase == 'Failed':
                    raise kopf.TemporaryError(
                        f"Router {rname} entered Failed state while applying underlay configuration; "
                        f"will retry after router is recovered",
                        delay=60,
                    )
                if phase == 'Running':
                    next_still_running.append(rname)
                # Any non-Running, non-Failed phase means the handler picked it up — good.
            except (kopf.TemporaryError, kopf.PermanentError):
                raise
            except Exception as e:
                # Treat 404 (router already deleted) as "already left Running".
                e_str = str(e)
                if "404" in e_str or "Not Found" in e_str:
                    logger.info(f"Router {rname} not found (404) — treating as already left Running state")
                else:
                    # If we can't read the router, conservatively assume still Running.
                    next_still_running.append(f"{rname}(error:{e})")

        still_running = next_still_running
        if still_running:
            logger.info(
                f"Waiting for routers to leave Running state (update handler not yet active): "
                f"{still_running} ({elapsed}s elapsed)"
            )
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

    if still_running:
        logger.warning(
            f"Routers did not transition away from Running within {timeout}s: {still_running}. "
            f"Ansible may have completed before the first poll — proceeding to Running wait."
        )
    else:
        logger.info(f"All routers have left Running state, update handlers are active: {router_names}")


async def _wait_for_routers_running(
    router_names: list, namespace: str, logger,
    timeout: int = 600, poll_interval: int = 5
) -> None:
    """Poll VyOSRouter CRs until every named router reaches Running status.

    Timeout is 600 s (10 min) to allow routers whose update handler raised a
    TemporaryError to complete multiple self-retry cycles (each up to 60 s)
    before giving up.

    On timeout a TemporaryError (not PermanentError) is raised so the underlay
    handler itself is also rescheduled rather than permanently abandoned.
    """
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='VyOSRouter')

    elapsed = 0
    not_running = []
    while elapsed < timeout:
        not_running = []
        for rname in router_names:
            try:
                r = api.get(name=rname, namespace=namespace)
                phase = r.get('status', {}).get('phase', 'Unknown')
                if phase == 'Failed':
                    # Router update handler raised PermanentError — raise
                    # TemporaryError so the underlay handler retries.  On the
                    # next retry the reconcile-trigger annotation path ensures
                    # update_vyosrouter re-fires even for no-op spec patches.
                    raise kopf.TemporaryError(
                        f"Router {rname} entered Failed state while applying underlay configuration; "
                        f"will retry after router is recovered",
                        delay=60,
                    )
                if phase != 'Running':
                    not_running.append(f"{rname}({phase})")
            except (kopf.TemporaryError, kopf.PermanentError):
                raise
            except Exception as e:
                # Treat 404 (router already deleted) as "done" — no need to wait.
                e_str = str(e)
                if "404" in e_str or "Not Found" in e_str:
                    logger.info(f"Router {rname} not found (404) — treating as done (already deleted)")
                else:
                    not_running.append(f"{rname}(error:{e})")

        if not not_running:
            logger.info(f"All routers reached Running state: {router_names}")
            return

        logger.info(f"Waiting for routers to reach Running: {not_running} ({elapsed}s elapsed)")
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    raise kopf.TemporaryError(
        f"Timed out after {timeout}s waiting for routers to reach Running state: {not_running}; "
        f"will retry",
        delay=120,
    )


async def check_infrastructure_ready(infrastructure_ref: str, namespace: str, logger) -> bool:
    """Check if the referenced VyOSInfrastructure is ready"""
    if not infrastructure_ref:
        logger.warning("No infrastructureRef specified in VyOSUnderlay spec")
        return True  # Allow creation if no infrastructure is referenced
    
    try:
        client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
        api = client.resources.get(api_version='google.dev/v1', kind='VyOSInfrastructure')
        
        infrastructure = api.get(name=infrastructure_ref, namespace=namespace)
        infrastructure_status = infrastructure.get('status', {})
        phase = infrastructure_status.get('phase', 'Unknown')
        
        logger.info(f"VyOSInfrastructure {infrastructure_ref} status: {phase}")
        
        if phase == 'Ready':
            return True
        else:
            logger.info(f"Waiting for VyOSInfrastructure {infrastructure_ref} to be Ready (current: {phase})")
            return False
            
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            logger.error(f"Referenced VyOSInfrastructure {infrastructure_ref} not found")
            raise kopf.PermanentError(f"VyOSInfrastructure {infrastructure_ref} not found")
        else:
            logger.error(f"Error checking VyOSInfrastructure {infrastructure_ref}: {e}")
            raise

@kopf.on.create('google.dev', 'v1', 'vyosunderlay')
async def create_vyosunderlay(body, spec, name, namespace, uid, logger, **kwargs):
    """Handle VyOSUnderlay creation - Core protocols patch"""
    logger.info(f"Creating VyOSUnderlay: {name}")
    
    try:
        # Check if the referenced infrastructure is ready before proceeding
        infrastructure_ref = spec.get('infrastructureRef')
        if not await check_infrastructure_ready(infrastructure_ref, namespace, logger):
            await update_status(name, namespace, "VyOSUnderlay", "Waiting", 
                              f"Waiting for VyOSInfrastructure {infrastructure_ref} to be ready")
            raise kopf.TemporaryError(f"Waiting for VyOSInfrastructure {infrastructure_ref} to be ready", delay=10)

        # Acquire the shared provisioning lock to serialize this operation against
        # all other VyOSUnderlay and VyOSL3VPN lifecycle operations.  Concurrent
        # Ansible playbooks on the same routers cause configuration races.
        logger.info(f"VyOSUnderlay {name}: waiting for provisioning lock")
        async with router_provisioning_lock:
            logger.info(f"VyOSUnderlay {name}: acquired provisioning lock, proceeding")

            await update_status(name, namespace, "VyOSUnderlay", "Processing", "Applying underlay configuration")

            routers = spec.get('routers', [])

            patched_routers = []
            for router in routers:
                router_name = router['name']

                # Read the router's current phase before patching so we can
                # detect no-op patches below (identical spec → no K8s event →
                # update_vyosrouter never fires → router stays stuck in Failed).
                try:
                    _check_client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
                    _check_api = _check_client.resources.get(api_version='google.dev/v1', kind='VyOSRouter')
                    _current = _check_api.get(name=router_name, namespace=namespace)
                    _pre_phase = (_current.get('status') or {}).get('phase')
                except Exception:
                    _pre_phase = None

                router_patch = {
                    'spec': {
                        'protocols': router.get('protocols', {})
                    }
                }
                if 'traffic_policy' in router:
                    router_patch['spec']['traffic_policy'] = router['traffic_policy']

                try:
                    await patch_vyos_router(router_name, namespace, router_patch)
                    patched_routers.append(router_name)
                except Exception as e:
                    logger.error(f"Failed to patch router {router_name} for underlay: {e}")
                    continue

                # ── Reconcile-trigger annotation ──────────────────────────────
                # If the router was already in Failed state when this retry
                # started, the spec patch above may have been a no-op (identical
                # values already stored → no resourceVersion change → no update
                # event → update_vyosrouter never re-fires → router stays stuck).
                # Setting a timestamp annotation always bumps resourceVersion,
                # guaranteeing update_vyosrouter fires even with an empty spec diff.
                if _pre_phase == 'Failed':
                    RECONCILE_ANNOTATION = 'vyos.google.dev/vpn-reconcile-trigger'
                    logger.info(
                        f"Router {router_name} was in Failed state — setting "
                        f"reconcile-trigger annotation to force update_vyosrouter"
                    )
                    try:
                        _trig_client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
                        _trig_api = _trig_client.resources.get(api_version='google.dev/v1', kind='VyOSRouter')
                        _trig_api.patch(
                            name=router_name,
                            namespace=namespace,
                            body={
                                'metadata': {
                                    'annotations': {
                                        RECONCILE_ANNOTATION: str(int(time.time()))
                                    }
                                }
                            },
                            content_type='application/merge-patch+json',
                        )
                    except Exception as ann_err:
                        logger.warning(
                            f"Could not set reconcile-trigger annotation on "
                            f"{router_name}: {ann_err}"
                        )

            if patched_routers:
                await update_status(name, namespace, "VyOSUnderlay", "Processing",
                                    f"Waiting for routers to finish reconfiguration: {patched_routers}")
                await _wait_for_routers_leave_running(patched_routers, namespace, logger)
                await _wait_for_routers_running(patched_routers, namespace, logger)

            await update_status(name, namespace, "VyOSUnderlay", "Ready", "Underlay configuration applied")
            logger.info(f"VyOSUnderlay {name}: releasing provisioning lock")

    except kopf.TemporaryError:
        raise
    except kopf.PermanentError:
        raise
    except Exception as e:
        error_msg = f"Failed to create VyOSUnderlay: {str(e)}"
        logger.error(error_msg)
        await update_status(name, namespace, "VyOSUnderlay", "Error", error_msg)
        raise kopf.PermanentError(error_msg)

#: Forces a re-render when the spec is unchanged but the routers have drifted.
UNDERLAY_RECONCILE_ANNOTATION = 'vyos.google.dev/reconcile-trigger'


@kopf.on.update('google.dev', 'v1', 'vyosunderlay')
async def update_vyosunderlay(body, spec, name, namespace, uid, logger, diff, **kwargs):
    """Re-apply the underlay, but only when the spec actually changed.

    Config Sync stamps the current commit SHA onto every object it manages, so
    without this filter any commit to the GitOps repo -- even one that only
    adds an unrelated file -- reconfigures OSPF, MPLS/LDP and iBGP across all
    ten backbone routers. Same filter as update_vyosrouter and
    update_vyosinfrastructure.
    """
    relevant = any(
        (len(fp) > 0 and fp[0] == 'spec') or
        fp == ('metadata', 'annotations', UNDERLAY_RECONCILE_ANNOTATION)
        for _, fp, _, _ in diff
    )
    if not relevant:
        logger.debug(
            f"VyOSUnderlay {name}: update event touches no spec fields "
            f"and carries no reconcile trigger — skipping re-render"
        )
        return

    logger.info(f"Updating VyOSUnderlay: {name}")
    # Re-apply configuration
    await create_vyosunderlay(body, spec, name, namespace, uid, logger, **kwargs)


@kopf.on.delete('google.dev', 'v1', 'vyosunderlay')
async def delete_vyosunderlay(body, spec, name, namespace, logger, **kwargs):
    """Handle VyOSUnderlay deletion — clear underlay config from all affected routers.

    For each router listed in spec.routers, clears the protocols and
    traffic_policy fields that were applied by this underlay, then waits for
    every router to finish its Ansible reconfiguration before allowing kopf to
    release the finalizer.

    Errors are logged but not re-raised so that the CR is still removed from
    Kubernetes even if a router fails mid-cleanup.
    """
    logger.info(f"Deleting VyOSUnderlay: {name} — clearing underlay config from routers")
    await update_status(name, namespace, "VyOSUnderlay", "Deleting", "Deleting VyOSUnderlay")

    routers = spec.get('routers', [])
    if not routers:
        logger.info(f"VyOSUnderlay {name}: no routers listed in spec, nothing to clean up")
        return

    # Acquire the shared provisioning lock to serialize this delete against
    # any concurrent VyOSL3VPN or VyOSUnderlay lifecycle operations.
    logger.info(f"VyOSUnderlay {name}: waiting for provisioning lock (delete)")
    async with router_provisioning_lock:
        logger.info(f"VyOSUnderlay {name}: provisioning lock acquired (delete)")

        patched_routers = []
        for router in routers:
            router_name = router.get('name')
            if not router_name:
                continue

            router_patch = {
                'spec': {
                    'protocols': {},
                }
            }
            if 'traffic_policy' in router:
                router_patch['spec']['traffic_policy'] = {}

            try:
                await patch_vyos_router(router_name, namespace, router_patch)
                patched_routers.append(router_name)
                logger.info(f"Cleared underlay config from VyOSRouter {router_name}")
            except Exception as e:
                logger.error(f"Failed to clear underlay config from VyOSRouter {router_name}: {e}")

        logger.info(f"VyOSUnderlay {name}: underlay config cleared from all routers — releasing provisioning lock")
