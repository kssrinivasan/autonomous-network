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

import sys
import os
import base64
import json
import re
import functions_framework
from cloudevents.http import CloudEvent

# Attach the Cloud Logging handler to the Python root logger 
# by calling the setup_logging method. By doing so Cloud Logging
# will properly report the logs severity for instance. If we do it
# directly (as above) all logs are classified with ERROR severity
# (see https://cloud.google.com/logging/docs/setup/python)
import google.cloud.logging
logging_client = google.cloud.logging.Client()
logging_client.setup_logging()

import logging
logger = logging.getLogger(__name__)

# After importing the Python standard logging library we end up with 2 log
# handlers at the root level causing duplicate log entries to appear
# in Cloud Logging, one that comes from the Cloud Logging Structured
# handler and the other from the standard Python StreamHandler
# Logger root handlers: [<StreamHandler <stderr> (NOTSET)>, <StructuredLogHandler <stderr> (NOTSET)>]
# Remove the standard Python logging handler to avoid duplicate (first handler in the list)
#del logging.getLogger().handlers[0]

# Imports the Google Cloud Spanner Client Library.
from google.cloud import spanner
import os

# Get Spanner configuration from environment variables
SPANNER_INSTANCE = os.environ.get('GOOGLE_SPANNER_INSTANCE', 'networktopology-instance')
SPANNER_DATABASE = os.environ.get('GOOGLE_SPANNER_TRANSPORT_DATABASE', 'transporttopology-db')

# This is to generate KG node embeddings
from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel
# Parameters for vertex AI embedding
TASK_TYPE = "QUESTION_ANSWERING"
EMBEDDING_MODEL_NAME="text-embedding-005"
EMBEDDING_MODEL = TextEmbeddingModel.from_pretrained(EMBEDDING_MODEL_NAME)

# See GCP LogEntry documentation
# https://cloud.google.com/logging/docs/reference/v2/rest/v2/LogEntry
Severity = {
  0   : "DEFAULT",	 # The log entry has no assigned severity level.
  100 : "DEBUG",	   # Debug or trace information.
  200 : "INFO",	     # Routine information, such as ongoing status or performance.
  300 : "NOTICE",	   # Normal but significant events, such as start up, shut down, or a configuration change.
  400 : "WARNING",	 # Warning events might cause problems.
  500 : "ERROR",	   # Error events are likely to cause problems.
  600 : "CRITICAL",  # Critical events cause more severe problems or outages.
  700 : "ALERT",	   # A person must take an action immediately.
  800 : "EMERGENCY", # One or more systems are unusable.
}

# Connect to Spanner database
def spanner_connect():
  spanner_client = spanner.Client()
  instance = spanner_client.instance(SPANNER_INSTANCE)
  database = instance.database(SPANNER_DATABASE)
  return database

database = spanner_connect()

# Structure of the CloudEvent object received
# See https://cloud.google.com/eventarc/docs/cloudevents-json#pubsub
# See also https://cloud.google.com/functions/docs/tutorials/pubsub#functions_helloworld_pubsub_tutorial-python
"""
{
  'attributes': {
    'specversion': '1.0', 
    'id': '13722152750867042', 
    'source': '//pubsub.googleapis.com/projects/networkagent-434609/topics/nwoplogs-topic',
    'type': 'google.cloud.pubsub.topic.v1.messagePublished',
    'datacontenttype': 'application/json',
    'time': '2025-02-11T11:46:24.190Z'
  },
  'data': {
    'message': {
      'attributes': {
        'logging.googleapis.com/timestamp': '2025-02-11T11:46:19.219751861Z'
        },
      'data': 'eyJodHRwUmVxdWVzdCI6.....TE4NjFaIn0=', 
      'messageId': '13722152750867042', 
      'message_id': '13722152750867042', 
      'publishTime': '2025-02-11T11:46:24.19Z', 
      'publish_time': '2025-02-11T11:46:24.19Z'
    }, 
    'subscription': 'projects/networkagent-434609/subscriptions/eventarc-europe-west1-capture-log-375017-sub-476'
  }
}
"""

# ------------------------------------------
# Clean up simple ANSI escape codes
# ------------------------------------------
def remove_color_ansi_codes(text):
    """
    Removes simple ANSI escape codes of the form '[<number>m' from a string.

    Args:
        text (str): The input string containing potential ANSI codes.

    Returns:
        str: The string with simple ANSI codes removed.
    """
    # Regular expression to match simple ANSI escape codes like '[36m', '[0m', '[1m'
    # It looks for:
    #   \[        - A literal opening square bracket
    #   \d+       - One or more digits (for the numerical parameter)
    #   m         - A literal 'm' character
    pattern = r'\x1b\[\d{1,3}m'
    
    # Use re.sub to replace all occurrences of the pattern with an empty string
    cleaned_text = re.sub(pattern, '', text)
    return cleaned_text
 
# ------------------------------------------
# Given a piece of text return the embedding (Array of Float64)
# ------------------------------------------
def get_embedding(text, task_type, model):
  try:
    text_embedding_input = TextEmbeddingInput(task_type=task_type, text=text)
    embeddings = model.get_embeddings([text_embedding_input])
    return embeddings[0].values
  except Exception as e:
    logger.error(f"Embedding error: {e}")
    return []

# ------------------------------------------
# Main Cloud Function entry point
# ------------------------------------------
@functions_framework.cloud_event
def capture_log(cloud_event: CloudEvent) -> None:
  def insert_log_entry(transaction):
    transaction.execute_update(
      "INSERT INTO KgLogEntryNode "
      "(id, timestamp, severity, source, message, content, embedding)"
      "VALUES (@id, @timestamp, @severity, @source, @message, @content, @embedding)",
      params={
        "id": insert_id,
        "timestamp": timestamp, 
        "severity": severity, 
        "source": source,
        "message": message, 
        "content": content, 
        "embedding": embedding },
      param_types={
        "id": spanner.param_types.STRING, 
        "timestamp": spanner.param_types.TIMESTAMP,
        "severity": spanner.param_types.STRING,
        "source": spanner.param_types.STRING,
        "message": spanner.param_types.STRING, 
        "content": spanner.param_types.STRING, 
        "embedding": spanner.param_types.Array(spanner.param_types.FLOAT64) },
    )

  logger.info(f">>> Received cloud event. Message: {cloud_event}")

  nwop_logging_data = cloud_event.data['message']['data']
  nwop_logging_json_string = base64.b64decode(nwop_logging_data).decode('utf-8')

  nwop_logging_json = json.loads(nwop_logging_json_string)
  logger.info(nwop_logging_json_string)

  insert_id = nwop_logging_json["insertId"]
  if "severity" in nwop_logging_json:
    severity = nwop_logging_json["severity"] 
  else:
    severity = "DEFAULT"

  # Source field - General case (see specific cases below)
  source = ''
  if 'resource' in nwop_logging_json:
    if 'labels' in nwop_logging_json['resource']:
      if 'container_name' in nwop_logging_json['resource']['labels']:
        source = nwop_logging_json['resource']['labels']['container_name']

  # The log message can either be in textPayload or in a jsonPayLoad
  message = ''
  try:
    if "jsonPayload" in nwop_logging_json:
      if "message" in nwop_logging_json["jsonPayload"]:
        message = nwop_logging_json["jsonPayload"]["message"]

      elif "msg" in nwop_logging_json["jsonPayload"]:
        message = nwop_logging_json["jsonPayload"]["msg"]
        if "error" in nwop_logging_json["jsonPayload"]:
          message += ': ' + nwop_logging_json["jsonPayload"]["error"]

      # VYOS router messages - fields set directly in jsonPayload by the Ops Agent
      # (See data_documentation/cloud_monitoring_vyos_log_sample.json for structure)
      elif "source" in nwop_logging_json["jsonPayload"] and nwop_logging_json["jsonPayload"]["source"] == "vyos":
        vyos_json = nwop_logging_json["jsonPayload"]
        app_name = vyos_json.get("app_name", "")
        host = vyos_json.get("host", "")
        # Prepend app_name (e.g. bgpd, ospfd, zebra) so the message is self-contained
        message = f"{app_name}: {vyos_json['msg_body']}" if app_name else vyos_json.get("msg_body", "")
        # In Spanner encode router name and daemon for easy filtering
        source = f"vyos:{host}:{app_name}" if app_name else f"vyos:{host}"

    elif "textPayload" in nwop_logging_json:
      message = nwop_logging_json["textPayload"]

  except Exception as e:
    logger.error(f"Log processing error: {e}")

  if message == '': 
    logger.error(f"Log processing error. No msg payload found: {nwop_logging_json_string}")
    return
  
  timestamp = nwop_logging_json["timestamp"]
  content = nwop_logging_json_string
  embedding = get_embedding(content, TASK_TYPE, EMBEDDING_MODEL)

  # In rare cases the vector generation fails and embedding 
  # comes back empty. Do not insert this log entry in Spanner
  # as it will result in error when searching by similarity
  if embedding:
    max_retries = 3
    global database
    for attempt in range(max_retries):
      try:
        database.run_in_transaction(insert_log_entry)
        break
      except Exception as e:
        logger.error(f"Log insert error (attempt {attempt+1}/{max_retries}): {e}")
        if attempt < max_retries - 1:
          logger.info("Attempting to reconnect to Spanner...")
          database = spanner_connect()
        else:
          logger.error("Final attempt failed. Log entry lost.")
