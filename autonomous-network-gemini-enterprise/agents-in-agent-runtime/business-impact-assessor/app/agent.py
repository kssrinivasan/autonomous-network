# ruff: noqa
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
import datetime
import os
from zoneinfo import ZoneInfo

import google.auth
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from google.cloud import bigquery
import pandas as pd

_, _default_project_id = google.auth.default()
PROJECT_ID = _default_project_id or "anthosbm-292404"
LOCATION = "us-central1"
DATASET_ID = "network_analytics"

os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

client = bigquery.Client(project=PROJECT_ID)
# ---------------------------------------------------------
# 3. Calculation Query
# ---------------------------------------------------------
CUSTOMER_EXPOSURE_QUERY = f"""
WITH link_severity AS (
  SELECT
    link_id,
    source_node,
    dest_node,
    link_type,
    utilization_p95,
    expected_breach_hours_per_month,
    GREATEST(0.0, LEAST(1.0, (utilization_p95 - 0.75) / 0.25)) AS congestion_severity
  FROM
    `{PROJECT_ID}.{DATASET_ID}.dim_links`
),

customer_link_exposure AS (
  SELECT
    l.link_id,
    l.link_type,
    l.source_node,
    l.dest_node,
    l.utilization_p95,
    c.customer_id,
    c.customer_name,
    c.ltv,
    p.traffic_share_ratio,
    
    ROUND(
      c.ltv * LEAST(1.0, (c.baseline_churn_prob * c.churn_sensitivity_beta * l.congestion_severity)) * p.traffic_share_ratio, 
      2
    ) AS churn_exposure_usd,
    
    ROUND(
      (c.sla_penalty_hourly_rate * CAST(l.expected_breach_hours_per_month AS NUMERIC)) * CAST(p.traffic_share_ratio AS NUMERIC) * l.congestion_severity, 
      2
    ) AS sla_exposure_usd
  FROM
    `{PROJECT_ID}.{DATASET_ID}.fct_circuit_link_paths` p
  INNER JOIN
    `{PROJECT_ID}.{DATASET_ID}.dim_customers` c ON p.customer_id = c.customer_id
  INNER JOIN
    link_severity l ON p.link_id = l.link_id
)

SELECT
  link_id,
  link_type,
  CONCAT(source_node, ' -> ', dest_node) AS link_endpoints,
  utilization_p95,
  COUNT(DISTINCT customer_id) AS impacted_customer_count,
  SUM(churn_exposure_usd) AS total_churn_exposure_usd,
  SUM(sla_exposure_usd) AS total_sla_exposure_usd,
  SUM(churn_exposure_usd + sla_exposure_usd) AS total_customer_exposure_usd
FROM
  customer_link_exposure
GROUP BY
  link_id,
  link_type,
  link_endpoints,
  utilization_p95
ORDER BY
  total_customer_exposure_usd DESC;
"""
MODEL = "gemini-3.7-flash"


def rank_by_customer_exposure() -> str:
    query_job = client.query(CUSTOMER_EXPOSURE_QUERY)
    
    # Materialize BigQuery result directly into a pandas DataFrame
    df = query_job.to_dataframe()
    return df.to_json(orient='table')


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction="""You are a helpful AI assistant to provide the business impact of specific network resources such as links.
You may be asked questions simialr to the following.  Your user may not use the same words or language, but something along these lines:
-Rank, assess or prioritize  the links or the link upgrade jobs based on customer, business or revenue impact
-Prioritize the network upgrades or the upgrade jobs
For **any** of these questions, **always** use the 'rank_by_customer_exposure' tool.  
The tool will retirn a JSON string.  Format it as an easy to read and visually appealing table.  Your responses should be helpful and professional in tone.""",
    tools=[rank_by_customer_exposure],
)

app = App(
    root_agent=root_agent,
    name="app",
)
