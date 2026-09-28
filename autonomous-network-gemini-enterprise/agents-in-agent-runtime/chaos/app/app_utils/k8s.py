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

import logging
import google.auth
import urllib.request
from google.cloud.container_v1 import ClusterManagerClient
from kubernetes import client,config
from ruamel.yaml import YAML
from pathlib import Path
import os

logger = logging.getLogger(__name__)

def external_service_account():
    credentials, _ = get_credentials()
    project_id = discover_project_id()
    if not project_id:
        raise ValueError("GCP Project ID could not be determined from environment, credentials, or Metadata Server.")

    cluster_manager_client = ClusterManagerClient(credentials=credentials)

    target_cluster_name = "networkautomation"
    target_zone = os.getenv("GOOGLE_ZONE")  # Allow manual override first

    if not target_zone:
        logger.info("Auto-discovering GKE cluster zone location...")
        try:
            parent = f"projects/{project_id}/locations/-"
            response = cluster_manager_client.list_clusters(parent=parent)
            for cluster in response.clusters:
                if cluster.name == target_cluster_name:
                    target_zone = cluster.location
                    logger.info(f"Discovered zone: {target_zone} for cluster: {target_cluster_name}")
                    break
        except Exception as e:
            logger.error(f"Failed to auto-discover GKE cluster zone: {e}")

    if not target_zone:
        # Fallback to a default zone if discovery fails completely
        target_zone = "europe-west3-a"
        logger.warning(f"Using default fallback zone: {target_zone}")

    name = f"projects/{project_id}/locations/{target_zone}/clusters/{target_cluster_name}"
    cluster = cluster_manager_client.get_cluster(name=name)

    SERVER = cluster.endpoint
    CERT = cluster.master_auth.cluster_ca_certificate

    NAME=f"gke_{project_id}_{target_zone}_networkautomation" # arbitrary
    CONFIG=f"""
    apiVersion: v1
    kind: Config
    clusters:
    - name: {NAME}
      cluster:
        certificate-authority-data: {CERT}
        server: https://{SERVER}
    contexts:
    - name: {NAME}
      context:
        cluster: {NAME}
        namespace: automation
        user: {NAME}
    current-context: {NAME}
    users:
    - name: {NAME}
      user:
        auth-provider:
          name: gcp
          config:
            scopes: "https://www.googleapis.com/auth/cloud-platform"
    """

    logger.debug(CONFIG)
    yaml = YAML(typ='safe', pure=True)
    KUBECONFIG = yaml.load(CONFIG)

    configuration = client.Configuration()
    loader = config.kube_config.KubeConfigLoader(KUBECONFIG)
    loader.load_and_set(configuration)
    apiclient = client.ApiClient(configuration=configuration)

    return apiclient

# global credentials
credentials = None
project_id = None

def get_credentials():
    """
    Get a cached credentials object and project ID.
    """
    global credentials, project_id
    if credentials is None:
        cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "/agent/networkagent.json")
        if os.path.exists(cred_path):
            credentials, project_id = google.auth.load_credentials_from_file(cred_path, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        else:
            credentials, project_id = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return credentials, project_id

def discover_project_id():
    """
    Dynamically discover the GCP Project ID across local sandbox and Vertex AI.
    """
    # 1. Check environment variables
    project = os.getenv("GOOGLE_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")
    if project:
        return project

    # 2. Check cached credentials project ID
    _, cred_project = get_credentials()
    if cred_project:
        return cred_project

    # 3. Query GCP Metadata Server (natively available inside Vertex AI container)
    try:
        req = urllib.request.Request(
            "http://metadata.google.internal/computeMetadata/v1/project/project-id",
            headers={"Metadata-Flavor": "Google"}
        )
        with urllib.request.urlopen(req, timeout=2) as conn:
            return conn.read().decode("utf-8").strip()
    except Exception as e:
        logger.warning(f"Metadata server project ID lookup failed: {e}")
        return None


# cached k8s client
k8s_client = None

def get_client():
    """
    Retrieve a cached k8s client
    Returns:
      kubernetes client object
    """
    global k8s_client

    # init client if not already
    if k8s_client == None:
      # check if kubeconfig path exists
      if os.path.exists(Path.home()/".kube"):
          logger.info("loading kube config")
          config.load_kube_config()
          k8s_client = client.ApiClient()
      else:
          logger.info("loading config from service account")
          try:
            logger.info("trying k8s service account")
            config.load_incluster_config()
            k8s_client = client.ApiClient()
          except Exception as e:
            logger.info("falling back to GCP service account")
            k8s_client = external_service_account()

    return k8s_client
