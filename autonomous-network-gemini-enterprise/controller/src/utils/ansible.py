
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
import json
import logging
logger = logging.getLogger(__name__)

########################################################
# Concurrency management in ansible
# Two separate semaphores so monitoring playbooks can
# never starve create/delete/configure operations.
#   - Operational: create / delete / configure  (7 slots)
#   - Monitor:     status checks                (3 slots)
# Total = 10, same as before — but now guaranteed split.
########################################################
ANSIBLE_OPERATIONAL_LIMIT = 10
ANSIBLE_MONITOR_LIMIT     = 3

ansible_operational_semaphore = asyncio.Semaphore(ANSIBLE_OPERATIONAL_LIMIT)
ansible_monitor_semaphore     = asyncio.Semaphore(ANSIBLE_MONITOR_LIMIT)

# Keep the old name as an alias so callers that haven't been
# updated yet continue to get the operational semaphore.
ansible_semaphore = ansible_operational_semaphore
MAX_WORKERS = 10

def get_ansible_semaphore():
    """Backward-compat: returns the operational semaphore."""
    return ansible_operational_semaphore

def get_ansible_operational_semaphore():
    """For create / delete / configure playbooks (7 slots)."""
    return ansible_operational_semaphore

def get_ansible_monitor_semaphore():
    """For status / monitoring playbooks (3 slots)."""
    return ansible_monitor_semaphore

########################################################
# Ansible event handler (ansible_runner compat shim)
########################################################
# Ansible doc (see https://ansible.readthedocs.io/projects/runner/en/latest/intro/#artifactevents)
def event_handler(data):
    if "stdout" in data:
        logger.info(data['stdout'])
    elif "stderr" in data:
        logger.info(data['stderr'])
    else:
        # log the whole data structure so that
        # we see how to handle it in the log_capture
        # cloud function
        logger.info(data)


########################################################
# Ansible JSON callback output logger
########################################################

def log_ansible_output(playbook: str, raw_stdout: str, log: logging.Logger) -> None:
    """Parse and log Ansible JSON callback output as human-readable per-task lines.

    Emits one log line per task/host combination:
      - FAILED / UNREACHABLE  → ERROR  (with the error message appended)
      - changed               → INFO
      - ok / skipped          → DEBUG

    Any JSON parse error is silently swallowed (e.g. pre-flight SSH errors that
    produce plain text before the JSON document begins).
    """
    try:
        output = json.loads(raw_stdout)
    except Exception:
        # Non-JSON output — surface it as a raw debug line
        if raw_stdout and raw_stdout.strip():
            log.debug(f"[ansible/{playbook}] raw output: {raw_stdout.strip()[:400]}")
        return

    for play in output.get('plays', []):
        for task in play.get('tasks', []):
            task_name = task.get('task', {}).get('name', 'unknown task')
            for host, host_result in task.get('hosts', {}).items():
                if host_result.get('unreachable'):
                    status = 'UNREACHABLE'
                elif host_result.get('failed'):
                    status = 'FAILED'
                elif host_result.get('changed'):
                    status = 'changed'
                elif host_result.get('skipped'):
                    status = 'skipped'
                else:
                    status = 'ok'

                line = f"[ansible/{playbook}] {task_name!r} → {status} ({host})"

                if status in ('FAILED', 'UNREACHABLE'):
                    loop_results = host_result.get('results', [])
                    if loop_results:
                        failed_items = [r for r in loop_results if r.get('failed')]
                        for r in failed_items:
                            item_label = r.get('item', {})
                            if isinstance(item_label, dict):
                                item_label = item_label.get('imsi', item_label.get('label', str(item_label)))
                            http_status = r.get('status', '')
                            url = r.get('url', '')
                            msg = r.get('msg', '') or r.get('json', '') or r.get('stderr', '')
                            item_line = line + f' [item={item_label}]'
                            if http_status and url:
                                item_line += f'\n  HTTP {http_status} {url}'
                            if msg:
                                item_line += f'\n  {str(msg).strip()}'
                            log.error(item_line)
                    else:
                        detail = (
                            host_result.get('msg', '')
                            or host_result.get('stderr', '')
                            or host_result.get('stdout', '')
                        )
                        if detail:
                            line += f'\n  {detail.strip()}'
                        log.error(line)
                elif status == 'changed':
                    log.info(line)
                else:
                    log.debug(line)
