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

FROM python:3.12

RUN apt-get update && apt-get install -y gettext-base && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir aiohttp google-auth requests python-socketio[aiohttp] google-cloud-spanner google-cloud-container kubernetes ruamel.yaml py-gitea google-cloud-run

# Install shared agent library
ADD lib/ /lib/
RUN pip install --no-cache-dir /lib/

ADD dashboard-v2/server.py /dashboard-v2/server.py
ADD dashboard-v2/endpoints/ /dashboard-v2/endpoints/
ADD dashboard-v2/tools/ /dashboard-v2/tools/
ADD dashboard-v2/utils/ /dashboard-v2/utils/
ADD dashboard-v2/build/web/ /dashboard-v2/build/web/
WORKDIR /dashboard-v2

RUN echo '#!/bin/bash\n\
if [ -f /dashboard-v2/build/web/index.html.template ]; then\n\
  envsubst < /dashboard-v2/build/web/index.html.template > /dashboard-v2/build/web/index.html\n\
fi\n\
exec python3 server.py' > /entrypoint.sh && chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
