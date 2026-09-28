#!/bin/bash
#
# Log Collector Installation Script
# Deploys operator log sink and Cloud Function for log capture
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Source common utilities if available
if [ -f "$SCRIPT_DIR/../../config.sh" ]; then
    # shellcheck source=../../config.sh
    . "$SCRIPT_DIR/../../config.sh"
else
    # Fallback: define minimal utilities
    section() { echo ""; echo "### $1"; }
    ok() { echo "✓ $1"; }
    warn() { echo "⚠ $1"; }
fi

# Log capture configuration
SINK_NAME="${SINK_NAME:-nwoplogs-sink}"
TOPIC_NAME="${TOPIC_NAME:-nwoplogs-topic}"
CAPTURE_LOG_FUNCTION="${CAPTURE_LOG_FUNCTION:-capture_log}"

section "Log Collector Deployment — Operator logs to Spanner"

# Create the Pub/Sub topic if it doesn't exist yet
if ! gcloud pubsub topics describe $TOPIC_NAME > /dev/null 2>&1; then
    echo "Creating Pub/Sub topic '${TOPIC_NAME}'..."
    gcloud pubsub topics create $TOPIC_NAME --project=${GOOGLE_PROJECT}
else
    echo "Pub/Sub topic '${TOPIC_NAME}' already exists"
fi

# Create the logging sink if it doesn't exist yet
# The log sink filter captures:
# 1) all logs from the network operator except kopf logs
# 2) the error logs from the config manager in case something goes wrong
# 3) all logs from the vyos router containers (hosted on the networkvm VM)
log_filter=$(cat <<EOF
resource.labels.project_id=${GOOGLE_PROJECT} AND
((resource.labels.container_name=${NETWORK_OPERATOR} AND labels.python_logger!=kopf._cogs.clients.watching) OR logName="projects/${GOOGLE_PROJECT}/logs/vyos_syslog")
EOF
)

if ! gcloud logging sinks describe $SINK_NAME >/dev/null 2>&1; then
    echo "Creating Logging sink '${SINK_NAME}'..."
    gcloud logging sinks create $SINK_NAME pubsub.googleapis.com/projects/${GOOGLE_PROJECT}/topics/${TOPIC_NAME} \
        --description="Network operator logs sink" \
        --log-filter="${log_filter}"
else
    # Update the log filter in case we updated it in the meantime
    echo "Updating existing sink '${SINK_NAME}' with filter..."
    gcloud logging sinks update $SINK_NAME --log-filter="${log_filter}"
fi

# Grant the Cloud Logging service account used by the Log sink the right to publish
# log entries to the Pub/Sub topic
echo "Granting IAM permissions..."
gcloud projects add-iam-policy-binding ${GOOGLE_PROJECT} \
    --member="serviceAccount:service-${GOOGLE_PROJECT_NUMBER}@gcp-sa-logging.iam.gserviceaccount.com" \
    --role="roles/pubsub.publisher" --condition=None --no-user-output-enabled

# Give the eventarc service account (by default the compute service account of the
# project) the permission to invoke the cloud run function
gcloud projects add-iam-policy-binding ${GOOGLE_PROJECT} \
    --member="serviceAccount:${GOOGLE_PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/run.invoker" --condition=None --no-user-output-enabled

# Give the Cloud Function service account the permission to use (read/write) Spanner
gcloud projects add-iam-policy-binding ${GOOGLE_PROJECT} \
    --member="serviceAccount:${GOOGLE_PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/spanner.databaseUser" --condition=None --no-user-output-enabled

# Give the Cloud Function service account the permission to use Vertex AI (e.g. embedding generation)
gcloud projects add-iam-policy-binding ${GOOGLE_PROJECT} \
    --member="serviceAccount:${GOOGLE_PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/aiplatform.user" --condition=None --no-user-output-enabled

# Deploy the Cloud Function that receives the eventarc
# events from Pub/Sub and feeds the Spanner DB.
# --max-instances bounds Spanner write concurrency: every invocation writes to
# transporttopology-db, and the platform default of 100 turns any log burst into
# a mutation-limit incident.
echo "Deploying Log capture Cloud Function..."
gcloud functions deploy $CAPTURE_LOG_FUNCTION \
    --source "$SCRIPT_DIR" \
    --runtime python312 \
    --trigger-topic $TOPIC_NAME \
    --entry-point=capture_log \
    --memory=512MB \
    --max-instances=10 \
    --set-env-vars GOOGLE_CLOUD_DISABLE_METRICS_EXPORT=true \
    --set-env-vars GOOGLE_SPANNER_INSTANCE=${GOOGLE_SPANNER_INSTANCE} \
    --set-env-vars GOOGLE_SPANNER_TRANSPORT_DATABASE=${GOOGLE_SPANNER_TRANSPORT_DATABASE} \
    --project=$GOOGLE_PROJECT \
    --region=$GOOGLE_REGION

ok "Log collector deployed"
