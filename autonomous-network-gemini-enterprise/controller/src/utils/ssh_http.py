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

"""SSH-proxied HTTP helper for reaching device traffic-agent daemons.

The operator runs in GKE and cannot directly reach device container IPs.
Device containers sit on Linux bridges on the network VM.  This module
proxies HTTP requests by SSH-ing into the network VM and running curl
from there, which *can* reach the management bridge IPs.

Usage:
    from utils.ssh_http import agent_request

    result = await agent_request(
        networkvm_ip  = "10.0.0.5",
        device_mgmt_ip = "192.168.100.10",
        method        = "POST",
        path          = "/v1/flows",
        body          = {"flow_id": "f1", "role": "destination", ...},
    )
    # result = {"success": True, "data": {...}} or {"success": False, "error": "..."}
"""

import asyncio
import json
import logging
import os
import shlex

import utils.constants as constants

logger = logging.getLogger(__name__)

# Timeout (seconds) for a single SSH+curl round trip.
_REQUEST_TIMEOUT = 30


async def agent_request(
    networkvm_ip: str,
    device_mgmt_ip: str,
    method: str,
    path: str,
    body: dict = None,
) -> dict:
    """Make an HTTP request to a device's traffic-agent daemon via SSH.

    Opens an SSH connection to *networkvm_ip* and executes ``curl`` from
    there to reach ``http://device_mgmt_ip:9090<path>``.

    Args:
        networkvm_ip:   IP of the network VM (reachable from the operator).
        device_mgmt_ip: Management IP of the device container.
        method:         HTTP method ("GET", "POST", "DELETE").
        path:           URL path, e.g. "/v1/flows" or "/v1/flows/my-flow".
        body:           Optional dict to JSON-encode and send as request body.

    Returns:
        dict with keys:
            success (bool): True if HTTP 2xx was returned.
            data    (dict): Parsed JSON response body (on success).
            error   (str):  Error message (on failure).
            http_code (int): HTTP status code (if available).
    """
    vm_user = os.getenv("GOOGLE_VM_USER")
    if not vm_user:
        return {"success": False, "error": "GOOGLE_VM_USER env var not set"}

    key_file = constants.basedir + "/google-compute"
    url = f"http://{device_mgmt_ip}:9090{path}"

    # Build the curl command to run on the remote VM.
    # -s: silent  -f: fail on 4xx/5xx (non-zero exit)  -w: write HTTP code to stdout last
    # We append the HTTP status code after a separator so we can parse it out.
    curl_parts = [
        "curl", "-s",
        "-o", "/tmp/.agent_resp",
        "-w", "%{http_code}",
        "-X", method.upper(),
    ]
    if body is not None:
        body_json = json.dumps(body)
        curl_parts += ["-H", "Content-Type: application/json", "-d", body_json]
    curl_parts.append(url)

    # Combine into a single shell command: run curl, capture code, cat response body.
    remote_cmd = (
        f"{shlex.join(curl_parts)}; "
        f"_rc=$?; echo '---BODY---'; cat /tmp/.agent_resp 2>/dev/null; exit $_rc"
    )

    # ControlMaster=auto: first SSH call creates a shared master socket; subsequent
    # calls to the same host reuse it without a new key exchange.  This keeps the
    # total number of TCP connections to the network VM at ~1 regardless of how many
    # concurrent agent_request / ssh_exec calls are in flight — avoiding sshd
    # MaxStartups rejections when many TrafficTests start simultaneously.
    ctl_path = f"/tmp/ssh-ctl-{vm_user}@{networkvm_ip}"
    ssh_cmd = [
        "ssh",
        "-i", key_file,
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        "-o", "ServerAliveInterval=5",
        "-o", "ControlMaster=auto",
        "-o", f"ControlPath={ctl_path}",
        "-o", "ControlPersist=120",
        f"{vm_user}@{networkvm_ip}",
        remote_cmd,
    ]

    logger.info("agent_request: %s %s via %s", method.upper(), url, networkvm_ip)

    try:
        proc = await asyncio.create_subprocess_exec(
            *ssh_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=_REQUEST_TIMEOUT
        )
    except asyncio.TimeoutError:
        logger.error("agent_request timed out after %ds: %s %s", _REQUEST_TIMEOUT, method, url)
        return {"success": False, "error": f"Request timed out after {_REQUEST_TIMEOUT}s"}
    except Exception as exc:
        logger.error("agent_request subprocess error: %s", exc)
        return {"success": False, "error": str(exc)}

    stdout = stdout_bytes.decode(errors="replace")
    stderr = stderr_bytes.decode(errors="replace").strip()

    # Split on the separator we injected
    parts = stdout.split("---BODY---", 1)
    http_code_str = parts[0].strip()
    response_body = parts[1].strip() if len(parts) > 1 else ""

    # Parse HTTP status code
    try:
        http_code = int(http_code_str)
    except ValueError:
        http_code = 0

    if proc.returncode != 0 or http_code < 200 or http_code >= 300:
        logger.error(
            "agent_request failed: rc=%d http=%d stderr=%s body=%s",
            proc.returncode, http_code, stderr[:200], response_body[:200],
        )
        error_detail = response_body or stderr or f"HTTP {http_code}"
        return {
            "success": False,
            "error": error_detail,
            "http_code": http_code,
        }

    # Parse JSON response body
    if response_body:
        try:
            data = json.loads(response_body)
        except json.JSONDecodeError as exc:
            logger.warning("agent_request: non-JSON response (http=%d): %s", http_code, response_body[:200])
            data = {"raw": response_body}
    else:
        data = {}

    logger.debug("agent_request success: http=%d data=%s", http_code, str(data)[:200])
    return {"success": True, "data": data, "http_code": http_code}


async def ssh_exec(
    networkvm_ip: str,
    remote_cmd: str,
    timeout: int = 30,
) -> tuple:
    """Run an arbitrary shell command on the network VM via SSH.

    Args:
        networkvm_ip: IP of the network VM.
        remote_cmd:   Shell command to execute on the VM.
        timeout:      Seconds to wait before giving up.

    Returns:
        (returncode, stdout, stderr) — all as str.
    """
    vm_user = os.getenv("GOOGLE_VM_USER")
    if not vm_user:
        return (1, "", "GOOGLE_VM_USER env var not set")

    key_file = constants.basedir + "/google-compute"
    ctl_path = f"/tmp/ssh-ctl-{vm_user}@{networkvm_ip}"
    ssh_cmd = [
        "ssh",
        "-i", key_file,
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        "-o", "ServerAliveInterval=5",
        "-o", "ControlMaster=auto",
        "-o", f"ControlPath={ctl_path}",
        "-o", "ControlPersist=120",
        f"{vm_user}@{networkvm_ip}",
        remote_cmd,
    ]

    logger.debug("ssh_exec: %s", remote_cmd[:200])

    try:
        proc = await asyncio.create_subprocess_exec(
            *ssh_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.error("ssh_exec timed out after %ds: %s", timeout, remote_cmd[:200])
        return (1, "", f"ssh_exec timed out after {timeout}s")
    except Exception as exc:
        logger.error("ssh_exec subprocess error: %s", exc)
        return (1, "", str(exc))

    return (
        proc.returncode,
        stdout_bytes.decode(errors="replace").strip(),
        stderr_bytes.decode(errors="replace").strip(),
    )
