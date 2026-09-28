# Copyright 2024-2025 Google LLC
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

FROM python:3.13.2
ADD ./requirements.txt /
RUN pip install -r /requirements.txt

ADD ./src/ /controller

# gRPC fork-safety: linuxnetwork/lifecycle_tasks.py calls ansible-playbook via
# subprocess.run() (fork+exec) instead of ansible_runner.run_async() (fork-only
# multiprocessing).  After exec() the child's address space is replaced so gRPC
# never runs there — the ev_poll_posix / wakeup_fd crash is impossible.
#
# The env-var approach below is kept as a belt-and-suspenders guard for the
# remaining ansible_runner usages elsewhere in the operator, but on its own it
# was not sufficient to prevent the crash in all gRPC versions.
ENV GRPC_ENABLE_FORK_SUPPORT=1
ENV GRPC_POLL_STRATEGY=poll

CMD kopf run /controller/main.py
