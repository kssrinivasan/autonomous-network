import kopf
import logging
import kubernetes
from utils.compute import get_ip
from core.core.lifecycle_tasks import provision_subscribers

logger = logging.getLogger(__name__)

_NF_CHILD_KIND = "NetworkFunction"


#########################################################################
# FiveGCore Lifecycle Management
#########################################################################

@kopf.on.create('google.dev', 'v1', 'fivegcore')
async def create_5gcore(body, spec, name, namespace, uid, logger, **kwargs):
    """Create child NetworkFunction CRs, wait for all to be Ready, then provision subscribers."""
    logger.info(f"Creating FiveGCore: {name}")

    network_functions = spec.get('network_functions', [])
    if not network_functions:
        await _update_status(name, namespace, "Failed", "No network_functions defined in spec",
                             body=body, spec=spec, uid=uid, logger_obj=logger)
        raise kopf.PermanentError("No network_functions defined in spec")

    # Gate on ALL enabled Device CRs being Ready before creating any child NF CRs
    for _nf in network_functions:
        if not _nf.get('enabled', True):
            continue
        _container = _nf.get('container_name', '')
        if not await _get_device_ip(_container, namespace):
            await _update_status(name, namespace, "Creating", f"Waiting for Device '{_container}'",
                                 body=body, spec=spec, uid=uid, logger_obj=logger)
            raise kopf.TemporaryError(f"Device '{_container}' not ready", delay=20)

    # Resolve NRF IP so other NFs can register to it
    nrf_ip = await _find_nf_ip(network_functions, namespace, 'nrf')
    if nrf_ip is None:
        await _update_status(name, namespace, "Creating", "Waiting for NRF Device to be Ready",
                             body=body, spec=spec, uid=uid, logger_obj=logger)
        raise kopf.TemporaryError("NRF Device not ready yet", delay=20)

    nrf_uri = f"http://{nrf_ip}:8000"

    # Read deployment-wide config from top-level Core CR spec
    plmn_list = list(spec.get('plmnList', []))
    slices = list(spec.get('slices', []))
    mongodb_uri = spec.get('mongodbUri') or 'mongodb://db:27017'
    snssai_infos = _build_snssai_infos(slices)
    core_metrics = dict(spec.get('metrics') or {})

    # Resolve all UPF IPs for SMF config — wait if any UPF Device is not Ready yet
    upf_list = []
    if _has_nf_type(network_functions, 'upf'):
        upf_list = await _find_all_upf_ips(network_functions, namespace)
        if upf_list is None:
            await _update_status(name, namespace, "Creating", "Waiting for UPF Device(s) to be Ready",
                                 body=body, spec=spec, uid=uid, logger_obj=logger)
            raise kopf.TemporaryError("UPF Device not ready yet", delay=20)

    # Resolve AMF IP for N3IWF config — wait if AMF is listed but Device not Ready yet
    amf_ip = ''
    if _has_nf_type(network_functions, 'amf'):
        amf_ip = await _find_nf_ip(network_functions, namespace, 'amf')
        if not amf_ip:
            await _update_status(name, namespace, "Creating", "Waiting for AMF Device to be Ready",
                                 body=body, spec=spec, uid=uid, logger_obj=logger)
            raise kopf.TemporaryError("AMF Device not ready yet", delay=20)

    owner_ref = {
        'apiVersion': 'google.dev/v1',
        'kind': 'FiveGCore',
        'name': name,
        'uid': uid,
        'blockOwnerDeletion': True,
        'controller': True,
    }

    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    nf_api = client.resources.get(api_version='google.dev/v1', kind=_NF_CHILD_KIND)

    # NF types whose config requires an nrfUri (NRF derives its own; WebUI needs it for config but doesn't register)
    _NFS_WITH_NRF_URI = {'amf', 'smf', 'upf', 'udr', 'udm', 'ausf', 'nssf', 'pcf', 'n3iwf', 'webui'}
    # NF types whose CRD schema includes mongodbUri
    _NFS_WITH_MONGODB_URI = {'nrf', 'udr', 'pcf', 'upf', 'webui'}
    # NF types that have port as a required field in the CRD schema
    _NFS_REQUIRING_PORT = {'nrf', 'udr', 'udm', 'ausf', 'nssf', 'pcf', 'n3iwf'}

    created = []
    for nf in network_functions:
        if not nf.get('enabled', True):
            continue
        nf_type_raw = nf.get('type', '')       # original case: NRF, AMF, WebUI, etc.
        nf_type = nf_type_raw.lower()           # lowercase: for matching and as spec key
        container_name = nf.get('container_name', '')
        nf_config = dict(nf.get('config') or {})

        # Inject resolved addresses and deployment-wide config into each NF
        if nf_type in _NFS_WITH_NRF_URI:
            nf_config.setdefault('nrfUri', nrf_uri)
        if nf_type in _NFS_WITH_MONGODB_URI:
            nf_config.setdefault('mongodbUri', mongodb_uri)
        if nf_type in _NFS_REQUIRING_PORT:
            nf_config.setdefault('port', 8000)
        if plmn_list and nf_type in ('amf', 'smf', 'nrf', 'ausf', 'n3iwf'):
            nf_config.setdefault('plmnList', plmn_list)
        if snssai_infos and nf_type in ('amf', 'smf', 'n3iwf'):
            nf_config.setdefault('snssaiInfos', snssai_infos)
        if nf_type == 'smf' and upf_list:
            nf_config.setdefault('upfList', upf_list)
        if nf_type == 'upf' and not nf_config.get('dnnList'):
            nf_config['dnnList'] = _derive_upf_dnn_list_from_slices(slices)
        if nf_type == 'n3iwf' and amf_ip:
            nf_config.setdefault('amfIp', amf_ip)

        child_name = f"{name}-{nf_type}"
        child_spec = {
            'nf_type': nf_type_raw,          # must match CRD enum (NRF, AMF, WebUI, …)
            'container_name': container_name,
            nf_type: nf_config,              # spec key is lowercase (nrf, amf, webui, …)
        }
        if core_metrics:
            child_spec['metrics'] = core_metrics
        child_body = {
            'apiVersion': 'google.dev/v1',
            'kind': _NF_CHILD_KIND,
            'metadata': {
                'name': child_name,
                'namespace': namespace,
                'ownerReferences': [owner_ref],
            },
            'spec': child_spec,
        }

        try:
            nf_api.create(namespace=namespace, body=child_body)
            created.append(child_name)
            logger.info(f"Created NetworkFunction {child_name} ({nf_type})")
        except kubernetes.client.rest.ApiException as e:
            if e.status == 409:
                logger.info(f"NetworkFunction {child_name} already exists, skipping")
                created.append(child_name)
            else:
                logger.error(f"Failed to create NetworkFunction {child_name}: {e}")

    # Wait for all child NFs to be Ready before provisioning subscribers
    all_children = _list_owned_children(uid, namespace, nf_api)
    children_status = []
    for child_name in all_children:
        try:
            nf = nf_api.get(name=child_name, namespace=namespace)
            phase = nf.to_dict().get('status', {}).get('phase', 'Unknown')
            children_status.append((child_name, phase))
        except Exception:
            children_status.append((child_name, 'Unknown'))

    total = len(children_status)
    ready = sum(1 for _, p in children_status if p == 'Ready')
    failed = [(n, p) for n, p in children_status if p == 'Failed']

    if failed:
        msg = f"NF(s) failed: {[n for n, _ in failed]}"
        await _update_status(name, namespace, "Failed", msg,
                             body=body, spec=spec, uid=uid, logger_obj=logger)
        raise kopf.PermanentError(msg)

    if ready < total:
        msg = f"{ready}/{total} network functions ready"
        await _update_status(name, namespace, "Creating", msg,
                             body=body, spec=spec, uid=uid, logger_obj=logger)
        raise kopf.TemporaryError(msg, delay=15)

    # All NFs Ready — provision subscribers
    subscribers = spec.get('subscribers', [])
    if subscribers:
        networkvm_ip = await get_ip("automation", "networkvm")
        if networkvm_ip is None:
            raise kopf.TemporaryError("No IP address found on Network VM", delay=15)

        webui_ip = await _get_webui_mgmt_ip(name, namespace)
        if webui_ip is None:
            raise kopf.TemporaryError("WebUI Device management IP not available yet", delay=15)

        await _update_status(name, namespace, "Creating",
                             f"All {total} NFs ready, provisioning {len(subscribers)} subscriber(s)",
                             body=body, spec=spec, uid=uid, logger_obj=logger)
        mcc, mnc = _extract_plmn(spec)
        result = await provision_subscribers(networkvm_ip, webui_ip, mcc, mnc, subscribers)
        if not result['success']:
            raise kopf.TemporaryError(
                f"Subscriber provisioning failed: {result.get('error')}", delay=30)
        logger.info(f"FiveGCore {name}: {len(subscribers)} subscriber(s) provisioned")

    await _update_status(name, namespace, "Ready",
                         f"All {total} network functions running, {len(subscribers)} subscriber(s) provisioned",
                         body=body, spec=spec, uid=uid, logger_obj=logger)


@kopf.on.delete('google.dev', 'v1', 'fivegcore')
async def delete_5gcore(body, spec, name, namespace, uid, logger, **kwargs):
    """Delete all owned NetworkFunction child CRs, then release the finalizer."""
    logger.info(f"Deleting FiveGCore: {name}")

    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    nf_api = client.resources.get(api_version='google.dev/v1', kind=_NF_CHILD_KIND)

    # Issue delete on any owned children not yet terminating
    try:
        items = nf_api.get(namespace=namespace)
        for nf in (items.items or []):
            nf_dict = nf.to_dict()
            owners = nf_dict.get('metadata', {}).get('ownerReferences') or []
            if not any(ref.get('uid') == uid for ref in owners):
                continue
            if nf_dict.get('metadata', {}).get('deletionTimestamp'):
                continue
            child_name = nf_dict['metadata']['name']
            try:
                nf_api.delete(name=child_name, namespace=namespace)
                logger.info(f"Issued delete for NetworkFunction/{child_name}")
            except kubernetes.client.rest.ApiException as e:
                if e.status != 404:
                    logger.warning(f"Failed to delete NetworkFunction/{child_name}: {e}")
    except kubernetes.client.rest.ApiException as e:
        if e.status != 404:
            logger.warning(f"Could not list NetworkFunction resources: {e}")

    # Wait for all owned children to disappear
    remaining = _list_owned_children(uid, namespace, nf_api)
    if remaining:
        logger.info(f"FiveGCore {name}: waiting for {len(remaining)} child(ren): {remaining}")
        raise kopf.TemporaryError(f"Waiting for children: {remaining}", delay=5)

    logger.info(f"FiveGCore {name}: all children gone, releasing finalizer")


@kopf.on.update('google.dev', 'v1', 'fivegcore')
async def update_5gcore(body, spec, name, namespace, uid, diff, logger, **kwargs):
    """Reconcile on spec changes: re-provision subscribers or rebuild SMF."""
    changed = {str(field) for op, field, _, _ in diff if op in ('add', 'change', 'remove')}
    subscribers_changed = any('subscribers' in f for f in changed)
    nf_changed = any('network_functions' in f for f in changed)

    if not subscribers_changed and not nf_changed:
        return

    if subscribers_changed:
        subscribers = spec.get('subscribers', [])
        if subscribers:
            networkvm_ip = await get_ip("automation", "networkvm")
            if networkvm_ip is None:
                raise kopf.TemporaryError("No IP address found on Network VM", delay=15)
            webui_ip = await _get_webui_mgmt_ip(name, namespace)
            if webui_ip is None:
                raise kopf.TemporaryError("WebUI Device management IP not available yet", delay=15)
            mcc, mnc = _extract_plmn(spec)
            result = await provision_subscribers(networkvm_ip, webui_ip, mcc, mnc, subscribers)
            if not result['success']:
                raise kopf.TemporaryError(
                    f"Subscriber re-provisioning failed: {result.get('error')}", delay=30)
            logger.info(f"FiveGCore {name}: {len(subscribers)} subscriber(s) re-provisioned")

    if not nf_changed:
        return

    network_functions = spec.get('network_functions', [])
    if not _has_nf_type(network_functions, 'upf') or not _has_nf_type(network_functions, 'smf'):
        return

    smf_name = f"{name}-smf"
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    nf_api = client.resources.get(api_version='google.dev/v1', kind=_NF_CHILD_KIND)

    # Phase 1: delete SMF child CR if it still exists; TemporaryError causes a retry
    try:
        nf_api.get(name=smf_name, namespace=namespace)
        nf_api.delete(name=smf_name, namespace=namespace)
        logger.info(f"Deleted SMF {smf_name} for UPF reconciliation; will recreate on next retry")
        raise kopf.TemporaryError("Waiting for SMF deletion before recreate", delay=5)
    except kubernetes.client.rest.ApiException as e:
        if e.status != 404:
            raise kopf.TemporaryError(f"Error checking SMF CR: {e}", delay=10)
        # 404 — SMF is gone, fall through to recreate

    # Phase 2: SMF is gone; resolve fresh state and recreate
    nrf_ip = await _find_nf_ip(network_functions, namespace, 'nrf')
    if not nrf_ip:
        raise kopf.TemporaryError("NRF not ready", delay=20)

    upf_list = await _find_all_upf_ips(network_functions, namespace)
    if upf_list is None:
        raise kopf.TemporaryError("UPF Device(s) not ready", delay=20)

    plmn_list = list(spec.get('plmnList', []))
    slices = list(spec.get('slices', []))
    mongodb_uri = spec.get('mongodbUri') or 'mongodb://db:27017'
    snssai_infos = _build_snssai_infos(slices)

    smf_nf = next(nf for nf in network_functions if nf.get('type', '').lower() == 'smf')
    nf_config = dict(smf_nf.get('config') or {})
    nf_config.setdefault('nrfUri', f"http://{nrf_ip}:8000")
    nf_config.setdefault('mongodbUri', mongodb_uri)
    nf_config['upfList'] = upf_list
    if plmn_list:
        nf_config.setdefault('plmnList', plmn_list)
    if snssai_infos:
        nf_config.setdefault('snssaiInfos', snssai_infos)
    core_metrics = dict(spec.get('metrics') or {})

    owner_ref = {
        'apiVersion': 'google.dev/v1', 'kind': 'FiveGCore',
        'name': name, 'uid': uid,
        'blockOwnerDeletion': True, 'controller': True,
    }
    child_spec = {'nf_type': 'SMF', 'container_name': smf_nf['container_name'], 'smf': nf_config}
    if core_metrics:
        child_spec['metrics'] = core_metrics
    child_body = {
        'apiVersion': 'google.dev/v1', 'kind': _NF_CHILD_KIND,
        'metadata': {'name': smf_name, 'namespace': namespace, 'ownerReferences': [owner_ref]},
        'spec': child_spec,
    }
    nf_api.create(namespace=namespace, body=child_body)
    logger.info(f"Recreated SMF {smf_name} with UPF list: {[u['name'] for u in upf_list]}")




#########################################################################
# Helpers
#########################################################################

def _has_nf_type(network_functions, nf_type):
    return any(
        nf.get('type', '').lower() == nf_type and nf.get('enabled', True)
        for nf in network_functions
    )


async def _find_nf_ip(network_functions, namespace, nf_type):
    """Return the Device ip_address for a specific NF type, or None if not ready."""
    for nf in network_functions:
        if nf.get('type', '').lower() == nf_type:
            return await _get_device_ip(nf.get('container_name', ''), namespace)
    return None


def _build_snssai_infos(slices):
    """Convert top-level FiveGCore slices to free5gc snssaiInfos format (used by SMF, AMF, N3IWF)."""
    return [
        {'sNssai': {'sst': sl.get('sst', 1), 'sd': sl.get('sd', '')},
         'dnnInfos': sl.get('dnnInfos', [])}
        for sl in slices
    ]


def _derive_upf_dnn_list_from_slices(slices):
    """Build UPF dnnList from top-level slices — unique DNNs with first pool CIDR each."""
    seen = {}
    for sl in slices:
        for dnn_info in sl.get('dnnInfos', []):
            dnn = dnn_info.get('dnn', '')
            if dnn and dnn not in seen:
                pools = dnn_info.get('pools') or []
                cidr = pools[0].get('cidr', '') if pools else ''
                seen[dnn] = {'dnn': dnn, 'cidr': cidr}
    return list(seen.values())


async def _find_all_upf_ips(network_functions, namespace):
    """Return list of {name, nodeID, addr} for all UPF NFs, or None if any is not ready."""
    result = []
    for nf in network_functions:
        if nf.get('type', '').lower() != 'upf':
            continue
        container_name = nf.get('container_name', '')
        ip = await _get_device_ip(container_name, namespace)
        if not ip:
            return None
        result.append({'name': container_name, 'nodeID': ip, 'addr': ip})
    return result


async def _get_device_ip(container_name, namespace):
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='Device')
    try:
        device = api.get(name=container_name, namespace=namespace)
        d = device.to_dict()
        status = d.get('status', {})
        if status.get('phase') != 'Ready':
            return None
        return d.get('spec', {}).get('ip_address')
    except Exception:
        return None


def _list_owned_children(uid, namespace, nf_api):
    remaining = []
    try:
        items = nf_api.get(namespace=namespace)
        for nf in (items.items or []):
            nf_dict = nf.to_dict()
            owners = nf_dict.get('metadata', {}).get('ownerReferences') or []
            if any(ref.get('uid') == uid for ref in owners):
                remaining.append(nf_dict['metadata']['name'])
    except Exception:
        pass
    return remaining


async def _get_nf_ip(nf_name, namespace):
    """Return (ip_address, None) if NetworkFunction is Ready, or (None, error_msg)."""
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='NetworkFunction')
    try:
        nf = api.get(name=nf_name, namespace=namespace)
        d = nf.to_dict()
        status = d.get('status', {})
        if status.get('phase') != 'Ready':
            return None, f"NetworkFunction '{nf_name}' phase is '{status.get('phase', 'Unknown')}'"
        ip = status.get('ip_address', '')
        if not ip:
            return None, f"NetworkFunction '{nf_name}' has no ip_address"
        return ip, None
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return None, f"NetworkFunction '{nf_name}' not found"
        return None, f"Error querying NetworkFunction '{nf_name}': {e}"
    except Exception as e:
        return None, str(e)


async def _get_webui_mgmt_ip(core_name, namespace):
    """Return the WebUI Device's management IP (reachable from the network VM host)."""
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    try:
        nf_api = client.resources.get(api_version='google.dev/v1', kind='NetworkFunction')
        nf = nf_api.get(name=f"{core_name}-webui", namespace=namespace)
        container_name = nf.to_dict().get('spec', {}).get('container_name', '')
        if not container_name:
            return None
        device_api = client.resources.get(api_version='google.dev/v1', kind='Device')
        device = device_api.get(name=container_name, namespace=namespace)
        return device.to_dict().get('spec', {}).get('mgmt_ip') or None
    except Exception:
        return None


def _extract_plmn(spec):
    """Derive MCC/MNC from the top-level FiveGCore plmnList."""
    plmn_list = spec.get('plmnList', [])
    if plmn_list:
        return plmn_list[0].get('mcc', '208'), plmn_list[0].get('mnc', '93')
    return '208', '93'



async def _update_status(name, namespace, phase, message,
                         body: dict = None, spec: dict = None, uid: str = None, logger_obj = None):
    """Update FiveGCore status in K8s and sync to Spanner.

    Args:
        name, namespace, phase, message: Status fields to update
        body, spec, uid, logger_obj: If provided, sync to Spanner after K8s update
    """
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='FiveGCore')

    status_update = {'phase': phase, 'message': message}

    try:
        api.status.patch(
            namespace=namespace,
            name=name,
            body={'status': status_update},
            content_type='application/merge-patch+json',
        )
    except kubernetes.client.rest.ApiException as e:
        if e.status == 422 and "status" in str(e):
            logger.warning(f"Status subresource not enabled for FiveGCore {name}")
        else:
            logger.error(f"Failed to update status for FiveGCore {name}: {e}")

    # Sync to Spanner if parameters provided (following transport pattern)
    if body is not None and spec is not None and uid is not None and logger_obj is not None:
        try:
            # Import here to avoid circular dependency
            from graph.core_tasks import sync_fivegcore

            # Create a modified copy of body with updated status for Spanner sync
            body_dict = dict(body)
            if 'status' not in body_dict or body_dict['status'] is None:
                body_dict['status'] = {}
            else:
                body_dict['status'] = dict(body_dict['status'])

            body_dict['status'].update(status_update)
            # Sync to Spanner
            await sync_fivegcore(body_dict, spec, name, uid, logger_obj)
        except Exception as e:
            if logger_obj:
                logger_obj.error(f"Failed to sync status to Spanner for FiveGCore {name}: {e}")
