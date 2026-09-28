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
import urllib.request
import os
from googleapiclient import discovery
import google.auth

logger = logging.getLogger(__name__)

def get_metadata_sa_email():
  """Retrieves service account email from metadata server."""
  try:
    req = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email",
        headers={"Metadata-Flavor": "Google"}
    )
    with urllib.request.urlopen(req, timeout=5) as response:
      email = response.read().decode('utf-8').strip()
      logger.info(f"Retrieved service account email from metadata server: {email}")
      return email
  except Exception as e:
    logger.warning(f"Could not get service account email from metadata server: {e}")
    return None

def register_oslogin_key(pub_key_path, sa_email):
  """Registers public SSH key to the SA's OS Login profile and returns username."""
  try:
    credentials, project = google.auth.default()
    service = discovery.build('oslogin', 'v1', credentials=credentials)
    
    with open(pub_key_path, 'r') as f:
      pub_key = f.read().strip()
      
    user_name = f"users/{sa_email}"
    body = {
        "key": pub_key
    }
    
    logger.info(f"Registering SSH public key {pub_key_path} for OS Login user {user_name}...")
    res = service.users().importSshPublicKey(parent=user_name, body=body).execute()
    
    login_profile = res.get("loginProfile", {})
    posix_accounts = login_profile.get("posixAccounts", [])
    if posix_accounts:
      # Find the account associated with the project or just use the first one
      username = posix_accounts[0].get("username")
      logger.info(f"Successfully registered OS Login SSH key. Username is: {username}")
      return username
    else:
      logger.error("OS Login profile does not contain posixAccounts.")
      return None
  except Exception as e:
    logger.error(f"Failed to register OS Login SSH key: {e}")
    return None

def setup_oslogin_if_needed():
  """Sets up OS Login registration if running in GCP and updates GOOGLE_VM_USER."""
  logger.info("Initializing OS Login verification...")
  pub_key_path = os.path.join(os.getenv("BASEDIR", os.getcwd()), "google-compute.pub")
  
  if not os.path.exists(pub_key_path):
    logger.warning(f"SSH public key {pub_key_path} not found. Skipping OS Login registration.")
    return

  sa_email = get_metadata_sa_email()
  if not sa_email:
    logger.info("Metadata server service account email not found. Assuming local run, skipping OS Login registration.")
    return

  username = register_oslogin_key(pub_key_path, sa_email)
  if username:
    logger.info(f"Overriding GOOGLE_VM_USER env variable with OS Login username: {username}")
    os.environ["GOOGLE_VM_USER"] = username
  else:
    logger.warning("OS Login registration returned no username. Retaining existing GOOGLE_VM_USER.")
