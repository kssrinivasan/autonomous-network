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
from google.cloud.container_v1 import ClusterManagerClient
from kubernetes import client,config
from ruamel.yaml import YAML
import os
from pathlib import Path
from .creds import get_credentials

logger = logging.getLogger(__name__)


def external_service_account():
    credentials, _ = get_credentials()
    cluster_manager_client = ClusterManagerClient(credentials=credentials)

    GOOGLE_PROJECT = os.getenv("GOOGLE_PROJECT")
    if not GOOGLE_PROJECT:
        _, GOOGLE_PROJECT = google.auth.default()

    logger.info(f"Dynamically scanning all locations in project {GOOGLE_PROJECT} for cluster 'networkautomation'...")
    parent = f"projects/{GOOGLE_PROJECT}/locations/-"
    clusters = cluster_manager_client.list_clusters(parent=parent).clusters

    target_cluster = next((c for c in clusters if c.name == "networkautomation"), None)
    if not target_cluster:
        raise ValueError(f"GKE Cluster 'networkautomation' not found in project {GOOGLE_PROJECT}")

    SERVER = target_cluster.endpoint
    CERT = target_cluster.master_auth.cluster_ca_certificate
    LOCATION = target_cluster.location

    NAME = f"gke_{GOOGLE_PROJECT}_{LOCATION}_networkautomation"
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


