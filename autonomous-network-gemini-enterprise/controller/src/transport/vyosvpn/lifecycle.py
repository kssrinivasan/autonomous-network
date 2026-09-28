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
    releasing the VPN reconcile lock too early.

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
                        f"Router {rname} entered Failed state while applying VPN configuration; "
                        f"will retry after router is recovered",
                        delay=60
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

    On timeout a TemporaryError (not PermanentError) is raised so the VPN
    handler itself is also rescheduled rather than permanently abandoned —
    a subsequent retry will re-patch the routers and resume waiting.
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
                    # TemporaryError so the VPN handler retries.  On the next
                    # retry the VPN handler will re-patch the router, which (via
                    # the reconcile-trigger annotation path) re-fires the update
                    # handler to attempt recovery.
                    raise kopf.TemporaryError(
                        f"Router {rname} entered Failed state while applying VPN configuration; "
                        f"will retry after router is recovered",
                        delay=60
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


async def check_underlay_ready(underlay_ref: str, namespace: str, logger) -> bool:
    """Check if the referenced VyOSUnderlay is ready"""
    if not underlay_ref:
        logger.warning("No underlayRef specified in VyOSL3VPN spec")
        return True  # Allow creation if no underlay is referenced
    
    try:
        client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
        api = client.resources.get(api_version='google.dev/v1', kind='VyOSUnderlay')
        
        underlay = api.get(name=underlay_ref, namespace=namespace)
        underlay_status = underlay.get('status', {})
        phase = underlay_status.get('phase', 'Unknown')
        
        logger.info(f"VyOSUnderlay {underlay_ref} status: {phase}")
        
        if phase == 'Ready':
            return True
        else:
            logger.info(f"Waiting for VyOSUnderlay {underlay_ref} to be Ready (current: {phase})")
            return False
            
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            logger.error(f"Referenced VyOSUnderlay {underlay_ref} not found")
            raise kopf.PermanentError(f"VyOSUnderlay {underlay_ref} not found")
        else:
            logger.error(f"Error checking VyOSUnderlay {underlay_ref}: {e}")
            raise

@kopf.on.create('google.dev', 'v1', 'vyosl3vpn')
async def create_vyosl3vpn(body, spec, name, namespace, uid, logger, **kwargs):
    """Handle VyOSL3VPN creation - Service/VRF patch"""
    logger.info(f"Creating VyOSL3VPN: {name}")

    # Check underlay readiness BEFORE acquiring the lock so that temporary
    # retry waits don't block other VPN reconciliations unnecessarily.
    try:
        underlay_ref = spec.get('underlayRef')
        if not await check_underlay_ready(underlay_ref, namespace, logger):
            await update_status(name, namespace, "VyOSL3VPN", "Waiting",
                                f"Waiting for VyOSUnderlay {underlay_ref} to be ready")
            raise kopf.TemporaryError(
                f"Waiting for VyOSUnderlay {underlay_ref} to be ready", delay=10)
    except kopf.TemporaryError:
        raise
    except kopf.PermanentError:
        raise

    # Acquire the shared provisioning lock to serialize this operation against
    # all other VyOSUnderlay and VyOSL3VPN lifecycle operations.  Concurrent
    # Ansible playbooks on the same PE routers cause configuration races.
    logger.info(f"VyOSL3VPN {name}: waiting for provisioning lock")
    async with router_provisioning_lock:
        logger.info(f"VyOSL3VPN {name}: acquired provisioning lock, proceeding")
        try:
            await update_status(name, namespace, "VyOSL3VPN", "Processing", "Applying L3VPN configuration")

            routers = spec.get('routers', [])

            for router in routers:
                router_name = router['name']

                # VRFs and BGP config declared by this VPN for this router
                vrfs = router.get('vrfs', [])
                vpn_bgp = router.get('bgp', {})

                try:
                    import json as _json

                    # --- Merge VRFs and BGP VRFs with the existing router spec ---
                    # Using a Kubernetes merge-patch on a list field *replaces* the
                    # entire list.  We must therefore read the current spec first and
                    # accumulate VRFs from all VPNs so that applying VPN-B does not
                    # silently discard VPN-A's VRFs from the stored K8s spec (and
                    # consequently from Spanner and the UI).
                    #
                    # IMPORTANT: We convert the Kubernetes object to a plain Python
                    # dict via JSON round-trip BEFORE doing any merging.  This avoids
                    # two classes of bugs:
                    #   1. K8s ResourceField objects don't always serialize correctly
                    #      when put back into a patch body.
                    #   2. .get('key', default) only uses the default when the key is
                    #      *absent*; if the field exists but is null (e.g. vrfs: null),
                    #      .get() returns None, causing TypeErrors in set/list
                    #      comprehensions.  Plain Python dicts from JSON deserialization
                    #      preserve null as None, which our `or []` guards handle safely.
                    router_k8s_client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
                    router_api = router_k8s_client.resources.get(api_version='google.dev/v1', kind='VyOSRouter')
                    current_router = router_api.get(name=router_name, namespace=namespace)

                    # Serialize to plain Python dict via the API client's sanitizer
                    # so all nested ResourceField objects become standard dicts/lists.
                    _sanitized = kubernetes.client.ApiClient().sanitize_for_serialization(
                        current_router.to_dict()
                    )
                    current_dict = _json.loads(_json.dumps(_sanitized))
                    current_spec = current_dict.get('spec') or {}
                    current_phase = (current_dict.get('status') or {}).get('phase')

                    # VRF merge: keep every VRF not owned by this VPN, then add/replace
                    # this VPN's VRFs (identified by name).
                    # null-safe: use `or []` in case the field is present but null.
                    current_vrfs = current_spec.get('vrfs') or []
                    # Ensure vrfs from VPN spec is also null-safe
                    vpn_vrfs = vrfs or []
                    vpn_vrf_names = {v['name'] for v in vpn_vrfs}
                    merged_vrfs = [v for v in current_vrfs if v.get('name') not in vpn_vrf_names]
                    merged_vrfs.extend(vpn_vrfs)

                    # BGP merge: preserve global BGP settings (AS number, global
                    # neighbors, route-reflector flag, etc.) and only replace the
                    # VRF-specific BGP entries that belong to this VPN.
                    # null-safe: use `or {}` / `or []` throughout.
                    current_bgp = (current_spec.get('protocols') or {}).get('bgp') or {}
                    vpn_bgp_vrfs = (vpn_bgp.get('vrfs') or []) if vpn_bgp else []
                    vpn_bgp_vrf_names = {v['name'] for v in vpn_bgp_vrfs}
                    existing_bgp_vrfs = [
                        v for v in (current_bgp.get('vrfs') or [])
                        if v.get('name') not in vpn_bgp_vrf_names
                    ]
                    merged_bgp = dict(current_bgp)
                    merged_bgp['vrfs'] = existing_bgp_vrfs + vpn_bgp_vrfs

                    logger.info(
                        f"Router {router_name}: merging VRFs "
                        f"existing={[v.get('name') for v in current_vrfs]} "
                        f"adding={[v.get('name') for v in vpn_vrfs]} "
                        f"→ merged={[v.get('name') for v in merged_vrfs]}"
                    )

                    # Construct the patch with the fully-merged VRF and BGP config
                    router_patch = {
                        'spec': {
                            'vrfs': merged_vrfs,
                            'protocols': {
                                'bgp': merged_bgp
                            }
                        }
                    }

                    # First, apply merged VRF and BGP configuration
                    await patch_vyos_router(router_name, namespace, router_patch)

                    # Then, update interface VRF assignments using JSON patch.
                    # Re-read the router after the merge-patch so that interface
                    # indices reflect the latest spec (including any changes made by
                    # the patch above).
                    for vrf in vpn_vrfs:
                        vrf_name = vrf['name']
                        vrf_interfaces = vrf.get('interfaces') or []

                        if vrf_interfaces:
                            # Re-read router to get up-to-date interface list / indices
                            client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
                            api = client.resources.get(api_version='google.dev/v1', kind='VyOSRouter')
                            current_router = api.get(name=router_name, namespace=namespace)
                            current_interfaces = current_router.get('spec', {}).get('interfaces') or []

                            # Build JSON patch operations for each interface that needs VRF assignment
                            json_patch = []
                            for intf_name in vrf_interfaces:
                                # Find the index of this interface
                                for idx, intf in enumerate(current_interfaces):
                                    if intf.get('name') == intf_name:
                                        json_patch.append({
                                            'op': 'add',
                                            'path': f'/spec/interfaces/{idx}/vrf',
                                            'value': vrf_name
                                        })
                                        break

                            # Apply JSON patch if we have operations
                            if json_patch:
                                import json
                                api.patch(
                                    name=router_name,
                                    namespace=namespace,
                                    body=json_patch,
                                    content_type='application/json-patch+json'
                                )
                                logger.info(f"Assigned interfaces {vrf_interfaces} to VRF {vrf_name} on router {router_name}")

                    # ── Reconcile-trigger annotation ──────────────────────────
                    # If the router was already in Failed state when this retry
                    # started, the spec patches above may have been no-ops
                    # (identical values already stored in K8s → no resourceVersion
                    # change → no update event → update_vyosrouter never fires →
                    # router stays stuck in Failed forever).
                    #
                    # Setting a timestamp annotation is always a genuine write
                    # (annotations are unrestricted by the CRD schema and any
                    # annotation change bumps resourceVersion), which guarantees
                    # update_vyosrouter fires and Ansible is re-run even when the
                    # spec diff is empty.  The handler ignores this annotation for
                    # all purposes other than recognising "something changed".
                    if current_phase == 'Failed':
                        RECONCILE_ANNOTATION = 'vyos.google.dev/vpn-reconcile-trigger'
                        logger.info(
                            f"Router {router_name} was in Failed state — setting "
                            f"reconcile-trigger annotation to force update_vyosrouter"
                        )
                        try:
                            trigger_client = kubernetes.dynamic.DynamicClient(
                                kubernetes.client.ApiClient()
                            )
                            trigger_api = trigger_client.resources.get(
                                api_version='google.dev/v1', kind='VyOSRouter'
                            )
                            trigger_api.patch(
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

                except Exception as e:
                    logger.error(f"Failed to patch router {router_name} for L3VPN: {e}")

            # Apply CE router eBGP configuration
            ce_routers = spec.get('ce_routers', [])
            for ce_router in ce_routers:
                ce_router_name = ce_router['name']
                ce_patch = {
                    'spec': {
                        'protocols': ce_router.get('protocols', {})
                    }
                }
                try:
                    await patch_vyos_router(ce_router_name, namespace, ce_patch)
                    logger.info(f"Applied CE eBGP config to {ce_router_name}")
                except Exception as e:
                    logger.error(f"Failed to patch CE router {ce_router_name}: {e}")

            # Wait for every patched router (PE + CE) to finish its Ansible
            # reconfiguration and reach Running status before releasing the
            # lock.  This prevents the next VPN from starting its patches
            # while the current VPN's Ansible playbooks are still in-flight
            # on the same routers (which was causing rr2 to fail).
            all_patched_routers = (
                [r['name'] for r in routers] +
                [r['name'] for r in ce_routers]
            )
            if all_patched_routers:
                await update_status(
                    name, namespace, "VyOSL3VPN", "Processing",
                    f"Waiting for routers to finish reconfiguration: {all_patched_routers}"
                )
                # Phase 1: wait for routers to leave Running so we know the
                # update handler has picked up the spec change.  Without this,
                # the phase-2 poll below can see stale Running status from
                # before the patch was processed and exit immediately, releasing
                # the lock while Ansible is still in-flight on the routers.
                await _wait_for_routers_leave_running(all_patched_routers, namespace, logger)
                # Phase 2: wait for routers to return to Running (Ansible done).
                await _wait_for_routers_running(all_patched_routers, namespace, logger)

            await update_status(name, namespace, "VyOSL3VPN", "Ready", "L3VPN configuration applied")
            logger.info(f"VyOSL3VPN {name}: releasing provisioning lock")

        except kopf.TemporaryError:
            raise
        except kopf.PermanentError:
            raise
        except Exception as e:
            error_msg = f"Failed to create VyOSL3VPN: {str(e)}"
            logger.error(error_msg)
            await update_status(name, namespace, "VyOSL3VPN", "Error", error_msg)
            raise kopf.PermanentError(error_msg)

#: Forces a re-render when the spec is unchanged but the PEs have drifted --
#: this is the documented recovery after anything rewrites router interface
#: config out from under the VRF bindings.
VPN_RECONCILE_ANNOTATION = 'vyos.google.dev/reconcile-trigger'


@kopf.on.update('google.dev', 'v1', 'vyosl3vpn')
async def update_vyosl3vpn(body, spec, name, namespace, uid, logger, diff, **kwargs):
    """Re-apply the VPN, but only when the spec actually changed.

    Config Sync stamps the current commit SHA onto every object it manages, so
    without this filter any commit to the GitOps repo re-renders both VPNs and
    bounces every PE-CE eBGP session. Same filter as update_vyosrouter,
    update_vyosinfrastructure and update_vyosunderlay.
    """
    relevant = any(
        (len(fp) > 0 and fp[0] == 'spec') or
        fp == ('metadata', 'annotations', VPN_RECONCILE_ANNOTATION)
        for _, fp, _, _ in diff
    )
    if not relevant:
        logger.debug(
            f"VyOSL3VPN {name}: update event touches no spec fields "
            f"and carries no reconcile trigger — skipping re-render"
        )
        return

    logger.info(f"Updating VyOSL3VPN: {name}")
    # Re-apply configuration
    await create_vyosl3vpn(body, spec, name, namespace, uid, logger, **kwargs)


@kopf.on.delete('google.dev', 'v1', 'vyosl3vpn')
async def delete_vyosl3vpn(body, spec, name, namespace, logger, **kwargs):
    """Handle VyOSL3VPN deletion — remove VRF/BGP config from all affected routers.

    Reverses exactly what create_vyosl3vpn applied:
      • PE routers: removes the VPN's VRFs from spec.vrfs, clears spec.protocols.bgp,
        and removes the 'vrf' field from any interface that was assigned by this VPN.
        All of this is done in a SINGLE merge-patch per router (combining what was
        previously two separate patches) to halve the number of Ansible runs.
      • CE routers: clears spec.protocols.

    All router patches are issued concurrently (asyncio.gather) so their Ansible
    runs start simultaneously.  The handler then waits for every router to reach
    Running state before releasing the lock, matching the create handler's behaviour.

    Uses the same reconcile lock and router-ready helpers as create so that a
    concurrent VPN creation cannot race against the cleanup patches.
    """
    import json as _json

    logger.info(f"Deleting VyOSL3VPN: {name} — removing router VRF/BGP config")

    # Names of VRFs owned by this VPN, keyed by router name.
    # Used to identify which VRFs to remove and which interfaces to un-assign.
    pe_routers = spec.get('routers', [])
    ce_routers = spec.get('ce_routers', [])

    all_patched_routers = (
        [r['name'] for r in pe_routers] +
        [r['name'] for r in ce_routers]
    )
    if not all_patched_routers:
        logger.info(f"VyOSL3VPN {name}: no routers listed in spec, nothing to clean up")
        return

    # Enforce sequential deletion ordering using deletionTimestamp.
    #
    # When multiple VyOSL3VPNs are deleted simultaneously, kopf fires all their
    # delete handlers concurrently.  Checking status.phase for "Deleting" has a
    # TOCTOU race: two handlers can both read "no other VPN is Deleting" before
    # either has written its own Deleting status, and both proceed in parallel.
    #
    # deletionTimestamp is set atomically by the K8s API server at nanosecond
    # precision when the delete request is received — it never changes and no
    # two objects can share the same value.  We use it as a stable, race-free
    # queue: a VPN only proceeds if no other VPN has an *earlier*
    # deletionTimestamp (i.e. was deleted before us and is still present in
    # etcd).  Once the earlier VPN's finalizer is released its CR disappears
    # from the list and we naturally advance.
    my_deletion_ts = (body.get('metadata') or {}).get('deletionTimestamp', '')
    try:
        _check_client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
        _l3vpn_api = _check_client.resources.get(api_version='google.dev/v1', kind='VyOSL3VPN')
        _all_vpns = _l3vpn_api.get(namespace=namespace)
        for _vpn in (_all_vpns.items or []):
            _vpn_dict = _vpn.to_dict() if hasattr(_vpn, 'to_dict') else _vpn
            _other_name = (_vpn_dict.get('metadata') or {}).get('name', '')
            if _other_name == name:
                continue  # skip self
            _other_deletion_ts = (_vpn_dict.get('metadata') or {}).get('deletionTimestamp', '')
            # Only block on VPNs that are also being deleted AND were deleted before us.
            if _other_deletion_ts and my_deletion_ts and _other_deletion_ts < my_deletion_ts:
                logger.info(
                    f"VyOSL3VPN {name}: another VPN ({_other_name}) was deleted earlier "
                    f"(deletionTimestamp {_other_deletion_ts} < {my_deletion_ts}) — "
                    f"waiting for it to be fully removed first"
                )
                await update_status(
                    name, namespace, "VyOSL3VPN", "Queued",
                    f"Waiting for VyOSL3VPN {_other_name} to finish deleting"
                )
                raise kopf.TemporaryError(
                    f"Waiting for VyOSL3VPN {_other_name} (deleted earlier) to finish first",
                    delay=10,
                )
    except (kopf.TemporaryError, kopf.PermanentError):
        raise
    except Exception as _check_exc:
        # Non-fatal — if we can't list VPNs, proceed rather than block deletion.
        logger.warning(f"VyOSL3VPN {name}: could not check for concurrent deletes: {_check_exc}")

    # Mark as actively deleting only once we are first in the deletion queue.
    await update_status(name, namespace, "VyOSL3VPN", "Deleting", "Deleting VyOSL3VPN")

    logger.info(f"VyOSL3VPN {name}: waiting for provisioning lock (delete)")
    async with router_provisioning_lock:
        logger.info(f"VyOSL3VPN {name}: provisioning lock acquired (delete)")

        try:
            k8s_client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
            router_api = k8s_client.resources.get(api_version='google.dev/v1', kind='VyOSRouter')

            # ── Helper: build and apply a single combined patch for one PE router ──
            async def _cleanup_pe_router(router_spec):
                router_name = router_spec.get('name')
                if not router_name:
                    return

                vpn_vrf_names = {v['name'] for v in router_spec.get('vrfs', [])}

                # Collect which interfaces this VPN assigned to a VRF.
                vpn_assigned_interfaces = set()
                for vrf in router_spec.get('vrfs', []):
                    for iface_name in vrf.get('interfaces', []):
                        vpn_assigned_interfaces.add(iface_name)

                try:
                    current_router_obj = router_api.get(name=router_name, namespace=namespace)
                    # Convert to plain Python dict (same as create handler) to avoid
                    # ResourceField serialization errors when putting the cleaned list
                    # back into the patch body.
                    _sanitized = kubernetes.client.ApiClient().sanitize_for_serialization(
                        current_router_obj.to_dict()
                    )
                    current_spec = _json.loads(_json.dumps(_sanitized)).get('spec') or {}
                except Exception as e:
                    logger.error(f"Cannot read VyOSRouter {router_name}: {e} — skipping")
                    return

                # ── Remove VPN-owned VRFs from the current list ──────────────
                current_vrfs = current_spec.get('vrfs') or []
                cleaned_vrfs = [v for v in current_vrfs if v.get('name') not in vpn_vrf_names]
                logger.info(
                    f"VyOSRouter {router_name}: removing VRFs {vpn_vrf_names} "
                    f"({len(current_vrfs)} → {len(cleaned_vrfs)} VRFs remain)"
                )

                # ── Also remove this VPN's BGP VRF entries ───────────────────
                # The Ansible template renders `protocols.bgp.vrfs` separately
                # from `vrfs`.  If BLUE_SPOKE is removed from spec.vrfs but its
                # BGP VRF entry is still in spec.protocols.bgp.vrfs, the template
                # will try to configure BGP for a VRF that was just deleted,
                # causing VyOS to reject the commit and roll back the whole
                # configure session (leaving both VRFs still present).
                current_bgp = (current_spec.get('protocols') or {}).get('bgp') or {}
                current_bgp_vrfs = current_bgp.get('vrfs') or []
                cleaned_bgp_vrfs = [
                    v for v in current_bgp_vrfs
                    if v.get('name') not in vpn_vrf_names
                ]
                cleaned_bgp = dict(current_bgp)
                cleaned_bgp['vrfs'] = cleaned_bgp_vrfs

                logger.info(
                    f"VyOSRouter {router_name}: removing BGP VRFs {vpn_vrf_names} "
                    f"({len(current_bgp_vrfs)} → {len(cleaned_bgp_vrfs)} BGP VRFs remain)"
                )

                # ── Strip 'vrf' from affected interfaces in-memory ────────────
                # Previously this required a second K8s patch (JSON-patch) after
                # the merge-patch, triggering a second Ansible run per PE router.
                # By including the cleaned interface list in the same merge-patch
                # we reduce two Ansible runs to one per PE router.
                current_interfaces = current_spec.get('interfaces') or []
                cleaned_interfaces = []
                for iface in current_interfaces:
                    iface_copy = dict(iface)
                    if iface_copy.get('name') in vpn_assigned_interfaces:
                        iface_copy.pop('vrf', None)
                    cleaned_interfaces.append(iface_copy)

                if vpn_assigned_interfaces:
                    logger.info(
                        f"VyOSRouter {router_name}: removing 'vrf' field from "
                        f"interfaces {vpn_assigned_interfaces} (combined into single patch)"
                    )

                # ── Single combined merge-patch: VRFs + BGP VRFs + interfaces ─
                # Global iBGP (AS number, neighbors, route-reflector) is
                # preserved because we carry it forward unchanged in cleaned_bgp.
                # Only the VRF-specific BGP entries added by this VPN are removed.
                router_patch = {
                    'spec': {
                        'vrfs': cleaned_vrfs,
                        'protocols': {
                            'bgp': cleaned_bgp,
                        },
                        'interfaces': cleaned_interfaces,
                    }
                }
                try:
                    await patch_vyos_router(router_name, namespace, router_patch)
                    logger.info(
                        f"Patched VyOSRouter {router_name}: removed VPN VRFs {vpn_vrf_names} "
                        f"and interface VRF assignments in a single patch"
                    )
                except Exception as e:
                    logger.error(f"Failed to patch VyOSRouter {router_name}: {e}")

            # ── Helper: clear CE router protocols ────────────────────────────
            async def _cleanup_ce_router(ce_spec):
                ce_name = ce_spec.get('name')
                if not ce_name:
                    return
                ce_patch = {'spec': {'protocols': {}}}
                try:
                    await patch_vyos_router(ce_name, namespace, ce_patch)
                    logger.info(f"VyOSRouter {ce_name}: cleared CE protocols config")
                except Exception as e:
                    logger.error(f"Failed to patch CE router {ce_name}: {e}")

            # ── Patch all PE and CE routers concurrently ─────────────────────
            # Previously routers were patched sequentially, so their Ansible runs
            # were staggered.  Gathering them concurrently means all Ansible runs
            # start at the same time; we then wait for the slowest one to finish.
            logger.info(
                f"VyOSL3VPN {name}: patching {len(pe_routers)} PE router(s) and "
                f"{len(ce_routers)} CE router(s) concurrently"
            )
            await asyncio.gather(
                *[_cleanup_pe_router(r) for r in pe_routers],
                *[_cleanup_ce_router(r) for r in ce_routers],
                return_exceptions=True,
            )

            # ── Wait for all routers to finish their Ansible reconfiguration ──
            # This mirrors the create handler's two-phase wait so that the lock
            # is not released while Ansible is still in-flight on the routers.
            if all_patched_routers:
                await update_status(
                    name, namespace, "VyOSL3VPN", "Deleting",
                    f"Waiting for routers to finish reconfiguration: {all_patched_routers}"
                )
                await _wait_for_routers_leave_running(all_patched_routers, namespace, logger)
                # Use a shorter timeout for the delete path (120s vs 600s for create).
                # Removing VRF/BGP config is faster than adding it, and we don't need
                # to wait as long before releasing the lock.
                await _wait_for_routers_running(all_patched_routers, namespace, logger, timeout=120)

            logger.info(
                f"VyOSL3VPN {name}: VRF/BGP config removed from all routers — "
                f"releasing provisioning lock"
            )

        except kopf.PermanentError:
            # Do not block deletion even on permanent errors — log and continue.
            logger.error(
                f"Permanent error during VyOSL3VPN {name} cleanup; "
                f"router config may need manual cleanup"
            )
        except Exception as e:
            # Same: log but don't raise so the CR is still deleted.
            logger.error(
                f"Unexpected error during VyOSL3VPN {name} cleanup: {e}",
                exc_info=True,
            )
