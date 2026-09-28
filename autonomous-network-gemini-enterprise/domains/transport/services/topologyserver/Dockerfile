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

FROM python:3.13-slim

# Install uv package manager
RUN pip install --no-cache-dir uv==0.8.13

WORKDIR /code

# Copy dependency files and source code
COPY domains/transport/services/topologyserver/pyproject.toml ./
COPY domains/transport/services/topologyserver/src/ ./src/

# Install dependencies from pyproject.toml
RUN uv pip install --system fastapi uvicorn pydantic google-cloud-spanner websockets httpx python-dotenv

# Environment
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/code/src

EXPOSE 8080

# Run FastAPI server
CMD ["python", "src/main.py"]
