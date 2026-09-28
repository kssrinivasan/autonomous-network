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

"""Which Gitea repositories the MCP tools read and write.

The Gitea plumbing itself -- URL discovery, the ``py-gitea`` monkeypatches, the
commit/delete retry loops -- lives in ``agent_library.gitea_utils`` and is
shared with the agents and dashboard-v2. It used to be duplicated here and in
``utils/gitea_extension.py``, which meant every Gitea fix had to be made in
four or five places and, twice, was not.

``lib/`` is outside this service's ``gcloud run deploy --source`` context, so
``deploy.sh`` copies ``lib/src/agent_library`` to ``src/agent_library`` before
deploying; the copy is git-ignored and listed in ``.gcloudignore`` as an
explicit inclusion. All this module contributes now is the repository names and
a re-export, so the tools keep importing from one place.
"""

from agent_library import (  # noqa: F401  -- re-exported for the tools
    MASTER_BRANCH,
    PWD,
    USER,
    commit_git_file,
    delete_git_file,
    find_crd_git_path,
    get_git_file,
    get_gitea_url,
    git_file_exists,
)

#: Domain design documents and failure-analysis notes, read-only from here.
DESIGN_REPO = 'networkdesign'

#: Incident write-ups.
INCIDENT_REPO = 'incidents'

#: The GitOps repository Config Sync reconciles the cluster from. Everything
#: the mutating tools write goes here.
SERVICE_REPO = 'network'
