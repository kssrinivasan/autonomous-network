
#!/usr/bin/bash
#
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

# Get the script directory
INSTALL_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

############################################################
# Check if config.sh exists, create from template if not  #
############################################################
if [ ! -f "$INSTALL_SCRIPT_DIR/config.sh" ]; then
    echo ""
    echo "=================================================================="
    echo "config.sh not found - creating from template..."
    echo "=================================================================="

    if [ ! -f "$INSTALL_SCRIPT_DIR/config.sh.template" ]; then
        echo "ERROR: config.sh.template not found!"
        echo "Cannot create config.sh without template."
        exit 1
    fi

    cp "$INSTALL_SCRIPT_DIR/config.sh.template" "$INSTALL_SCRIPT_DIR/config.sh"
    echo ""
    echo "✓ Created config.sh from template"
    echo ""
    exit 1
fi

# Source config.sh to make variables available
# shellcheck source=./config.sh
. "$INSTALL_SCRIPT_DIR/config.sh"

############################################################
# Validate required environment variables                  #
############################################################
GOOGLE_PROJECT="${GOOGLE_PROJECT:?GOOGLE_PROJECT is required}"
GOOGLE_USER="${GOOGLE_USER:?GOOGLE_USER is required}"
GOOGLE_VM_USER="${GOOGLE_VM_USER:?GOOGLE_VM_USER is required}"
GOOGLE_REGION="${GOOGLE_REGION:?GOOGLE_REGION is required}"
GOOGLE_ZONE="${GOOGLE_ZONE:?GOOGLE_ZONE is required}"
WEBAPPS_LOGIN="${WEBAPPS_LOGIN:?WEBAPPS_LOGIN is required}"
WEBAPPS_PWD="${WEBAPPS_PWD:?WEBAPPS_PWD is required}"

############################################################
# Display current work environment information             #
############################################################
DisplayGCPEnv()
{
    echo "Gathering active GCP environment information..."
    echo "  - GCP user: $(gcloud config list account --format="value(core.account)")"
    echo "  - GCP project: $(gcloud config get-value project 2>/dev/null)"
    echo "  - Active configuration: $(gcloud config configurations list --filter="IS_ACTIVE=True" --format="value(NAME)")"
    echo "  - GKE context: $(kubectl config current-context)"
}

############################################################
# Check the work environment                               #
############################################################
CheckGCPEnv()
{
    echo; echo -n "Checking your work environment..."

    # test if gcloud exists
    if ! command -v gcloud &> /dev/null
    then
        echo "gcloud could not be found, you must install it"
        exit 1
    fi

    # envsubst is part of gettext-base package (standard on most Linux systems)
    if ! command -v envsubst &> /dev/null
    then
        echo "envsubst could not be found, you must install gettext-base package"
        exit 1
    fi

    # test if flutter exists
    if ! command -v flutter &> /dev/null
    then
        echo "flutter could not be found, install from https://flutter.dev'"
        exit 1
    fi

    # Check that we can use non shielded VMs
    shielded_vm_enforced=$(gcloud resource-manager org-policies describe compute.requireShieldedVm --project $GOOGLE_PROJECT --effective --format="value(booleanPolicy.enforced)")
    if [ "$shielded_vm_enforced" = "True" ]; then
        echo "compute.requireShieldedVm is enforced on this project. Please change this org Policy to False before proceeding"
        exit 1
    fi

    # Check that VM can IP forward (needed by the network VM)
    vm_can_ip_forward=$(gcloud resource-manager org-policies describe compute.vmCanIpForward --project $GOOGLE_PROJECT --effective --format="value(listPolicy.allValues)")
    if [ "$vm_can_ip_forward" = "DENY" ]; then
        echo "compute.vmCanIpForward is denied on this project. Please change this org Policy to ALLOW before proceeding"
        exit 1
    fi

    # Check that account can be created on service accounts
    svc_account_key_disabled=$(gcloud resource-manager org-policies describe iam.disableServiceAccountKeyCreation --project $GOOGLE_PROJECT --effective --format="value(booleanPolicy.enforced)")
    if [ "$svc_account_key_disabled" = "True" ]; then
        echo "iam.disableServiceAccountKeyCreation is enforced on this project. Please change this org Policy to False before proceeding"
        exit 1
    fi

    # Check that the OS Login flag is set to true at the project level
    # It's needed when the script ssh into the network VM
    oslogin_flag=$(gcloud compute project-info describe --project=$GOOGLE_PROJECT --format="value(commonInstanceMetadata.items.enable-oslogin)")
    if [[ $oslogin_flag == "false" ]]; then
        echo "The OS Login flag is false. It must be set to true at the project level"
        exit 1
    fi
    
    echo " all good!"
}

############################################################
# Set the work environment                                 #
############################################################
SetDemoEnv()
{
    echo "Setting your demo environment..."
    # Create a gcloud configuration for this demo project 
    gcloud_config="${GOOGLE_PROJECT}-config"
    gcloud config configurations describe $gcloud_config > /dev/null 2>&1
    if [[ $? -ne 0 ]]; then
        echo "Creating a specific gcloud config ($gcloud_config) for this project.."
        gcloud config configurations create $gcloud_config
    fi
    gcloud auth application-default set-quota-project $GOOGLE_PROJECT > /dev/null 2>&1
    gcloud config configurations activate $gcloud_config
    gcloud config set core/project $GOOGLE_PROJECT        
    gcloud config set core/account $GOOGLE_USER
    gcloud config set core/disable_usage_reporting False

    # Ensure gcloud does NOT embed --use_application_default_credentials in the
    # kubeconfig generated by 'get-credentials'. When that flag is present,
    # gke-gcloud-auth-plugin authenticates with Application Default Credentials
    # (ADC) instead of the gcloud user account. If ADC is set to a different
    # account (e.g. admin@...) kubectl will use that account regardless of which
    # user is configured in gcloud, causing Forbidden errors on kubectl operations.
    # Unsetting this config key forces get-credentials to generate a kubeconfig
    # that authenticates as the gcloud configured user ($GOOGLE_USER).
    gcloud config unset container/use_application_default_credentials 2>/dev/null || true
    unset CLOUDSDK_CONTAINER_USE_APPLICATION_DEFAULT_CREDENTIALS

    # register gcloud as a Docker credential helper
    gcloud auth configure-docker $GOOGLE_REGION-docker.pkg.dev --quiet > /dev/null 2>&1

    export GOOGLE_PROJECT_NUMBER=`gcloud projects describe $GOOGLE_PROJECT --format="value(projectNumber)"`
    if [[ "$GOOGLE_PROJECT_NUMBER" = "" ]]; then
        echo "Could not determine project number. Check that GOOGLE_PROJECT is set properly"
        exit 1
    fi

    # Source shared configuration
    # shellcheck source=./config.sh
    . "$INSTALL_SCRIPT_DIR/config.sh"

    # Fallback for config.sh copies generated before GOOGLE_ORG_NAME was added
    # to config.sh.template. head -1 because an account in several organizations
    # would otherwise yield a multi-line value.
    if [[ -z "${GOOGLE_ORG_NAME:-}" ]]; then
        export GOOGLE_ORG_NAME="$(gcloud organizations list --format='value(name)' 2>/dev/null | head -1)"
    fi

    # If running from a Cloud Shell session fix a few problems
    # with preinstalled flutter.
    # NB: this was [[ $GOOGLE_CLOUD_SHELL="true" ]] -- no spaces around '=',
    # which bash parses as a single non-empty string and is therefore always
    # true. It edited global git config on every run, on any machine.
    if [[ "$GOOGLE_CLOUD_SHELL" == "true" ]]; then
        git config --global --add safe.directory /google/flutter
        flutter --version >/dev/null # finishes the pre-installation properly
    fi

    # Display current work environment information
    DisplayGCPEnv

    echo "done!"
}

############################################################
# Ensure environment is created (auto-run Create if needed)#
############################################################
EnsureEnvironmentCreated()
{
    if [[ ! -f "networkagent.json" ]] || [[ ! -s "networkagent.json" ]]; then
        echo "=========================================="
        echo "Environment not created (networkagent.json missing)"
        echo "Running Create function to set up environment..."
        echo "=========================================="
        Create
        echo "=========================================="
        echo "Environment created successfully"
        echo "=========================================="
    else
        # Presence alone is not enough: a key left over from a previous project
        # skips Create entirely, so the networkagent service account is never
        # made here and StartPlatform dies at its own guard several minutes
        # later. Refuse rather than guess -- this file is a live credential for
        # whatever project it names, so we do not delete it.
        KEY_PROJECT=$(python3 -c "import json;print(json.load(open('networkagent.json')).get('project_id',''))" 2>/dev/null || echo "")
        if [[ -n "$KEY_PROJECT" && "$KEY_PROJECT" != "$GOOGLE_PROJECT" ]]; then
            echo ""
            echo "**ERROR**: networkagent.json belongs to project '$KEY_PROJECT', not '$GOOGLE_PROJECT'."
            echo "  It is a service account key for another project. Move it aside, then re-run:"
            echo "    mv networkagent.json networkagent.${KEY_PROJECT}.json"
            echo ""
            exit 1
        fi
        echo "Environment already created (networkagent.json found for $GOOGLE_PROJECT)"
    fi
}

############################################################
# Validate environment exists (fail if not created)        #
############################################################
ValidateEnvironmentExists()
{
    if [[ ! -f "networkagent.json" ]] || [[ ! -s "networkagent.json" ]]; then
        echo ""
        echo "**ERROR**: Environment not created"
        echo "Run './install.sh --platform' first to create the platform"
        echo ""
        exit 1
    fi
}

############################################################
# Create keys and manifest files                           #
############################################################
Create()
{
    echo "########################################"
    echo "Setting project to $GOOGLE_PROJECT"
    echo "########################################"
    gcloud config set project $GOOGLE_PROJECT

    # Make sure the active GCP user has proper permissions
    echo "########################################"
    echo "Grant GCP permissions to GCP user: $GOOGLE_USER"
    echo "########################################"
    for role in "roles/logging.logWriter" "roles/spanner.databaseReader" \
                "roles/artifactregistry.admin" "roles/container.admin"; do
        echo "$role"
        gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="user:$GOOGLE_USER" --role="$role" --no-user-output-enabled
        # roles/spanner.databaseReader needed for the COlab Notebook to access the graph database
    done

    # enable GCP Services API needed
    echo "########################################"
    echo "Enabling required GCP services API for project $GOOGLE_PROJECT"
    echo "########################################"
    gcloud services enable --project=$GOOGLE_PROJECT artifactregistry.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT cloudbuild.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT cloudfunctions.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT eventarc.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT compute.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT container.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT gkehub.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT anthos.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT run.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT bigquery.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT spanner.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT pubsub.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT logging.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT monitoring.googleapis.com
    # For vertex AI workbench
    gcloud services enable --project=$GOOGLE_PROJECT notebooks.googleapis.com
    # for colab enterprise in addition to compute engine api
    gcloud services enable --project=$GOOGLE_PROJECT aiplatform.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT dataform.googleapis.com
    # For Free5GC london cluster resources management
    gcloud services enable --project=$GOOGLE_PROJECT cloudresourcemanager.googleapis.com
    # For Vertex AI Pipelines (KFP) and Cloud Scheduler (GNN inference trigger)
    gcloud services enable --project=$GOOGLE_PROJECT cloudscheduler.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT storage.googleapis.com
    # For the shared bearer secret mcp_server_for_ge/ge/deploy.sh keeps in Secret
    # Manager -- ge/ stays --allow-unauthenticated because Gemini Enterprise
    # cannot present an ID token, and that secret is what closes the gap.
    gcloud services enable --project=$GOOGLE_PROJECT secretmanager.googleapis.com
    # For agent registration into Gemini Enterprise. deploy_agents.py calls
    # discoveryengine.googleapis.com/v1alpha directly (both the fleet deployer
    # and domains/transport/agents/deploy_agents.py).
    gcloud services enable --project=$GOOGLE_PROJECT discoveryengine.googleapis.com

    # Configure Cloud Build service account
    echo "########################################"
    echo "Setup Cloud Build service account permissions "
    echo "########################################"
    CLOUD_BUILD_COMPUTE_SVC_ACCOUNT="${GOOGLE_PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
    echo "Granting roles to Cloud Build service account ${CLOUD_BUILD_COMPUTE_SVC_ACCOUNT}..."
    for role in "roles/storage.objectUser" "roles/logging.logWriter" "roles/artifactregistry.writer" "roles/cloudbuild.builds.builder"; do
        echo "$role"
        gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$CLOUD_BUILD_COMPUTE_SVC_ACCOUNT" \
          --role="$role" --no-user-output-enabled
    done

    # Test if google compute ssh keys exist, it not generate them
    if ! test -f google-compute; then
        echo "#############################################################"
        echo "SSH key google-compute does not exist, generating new keys..."
        echo "#############################################################"
        ssh-keygen -o -a 100 -t ed25519 -f google-compute -C networkagent -P ""
    fi

    echo "###################################################"
    echo "Found google-compute ssh keys, copying where needed"
    echo "###################################################"
    cp google-compute controller/src
    cp google-compute.pub controller/src

    echo "Add ssh key to OS login"
    if ! gcloud compute os-login ssh-keys list --project=$GOOGLE_PROJECT | grep -q "$(cat google-compute.pub)"; then
        gcloud compute os-login ssh-keys add --key-file=google-compute.pub --project=$GOOGLE_PROJECT --ttl=100d
    else
        echo "SSH key already exists in OS login."
    fi

    echo "Templating k8s manifest files"

    # grab the public ssh key for templating into VM manifests
    export GOOGLE_SSH_KEY=$(cat google-compute.pub)

    # check if SERVICE ACCOUNT exists
    export GOOGLE_SERVICE_ACCOUNT="networkagent@${GOOGLE_PROJECT}.iam.gserviceaccount.com"
    gcloud iam service-accounts describe $GOOGLE_SERVICE_ACCOUNT > /dev/null 2>&1

    # Create the service account if it doesn't exist
    if [[ $? -ne 0 ]]; then
        echo "########################################"
        echo "No Service Account, trying to create one"
        echo "########################################"
        gcloud iam service-accounts create networkagent --description="Network Agent Service Account" --display-name="Network Agent"
        if [[ $? -ne 0 ]]; then
            echo "Creation of the GKE cluster service account failed. Fix the error and re-run the install command"
            exit 1
        fi

        echo "########################################"
        echo "Waiting for service account to be available..."
        echo "########################################"
        sleep 15
        max_attempts=10
        attempt=0
        while [ $attempt -lt $max_attempts ]; do
            if gcloud iam service-accounts describe $GOOGLE_SERVICE_ACCOUNT > /dev/null 2>&1; then
                echo "Service account $GOOGLE_SERVICE_ACCOUNT is ready!"
                break
            fi
            attempt=$((attempt + 1))
            echo "Waiting for service account to be available... (attempt $attempt/$max_attempts)"
            sleep 10
        done

        if [ $attempt -eq $max_attempts ]; then
            echo "**ERROR** Service account $GOOGLE_SERVICE_ACCOUNT is not available after $max_attempts attempts"
            echo "Please check your GCP project permissions and try again"
            exit 1
        fi
    fi

    echo "Granting permissions to the GKE Cluster service account..."
    # NOTE: roles/resourcemanager.projectIamAdmin was removed here. Combined with
    # roles/editor it let this SA grant itself any role, i.e. full privilege
    # escalation. Nothing in the installer needs it.
    # roles/editor itself remains and is still far broader than necessary --
    # narrowing it is tracked separately (MIGRATION_PLAN.md 1.1).
    for role in "roles/editor" "roles/container.admin" "roles/compute.admin" \
        "roles/compute.networkAdmin" "roles/iam.serviceAccountAdmin" "roles/monitoring.metricWriter" \
        "roles/aiplatform.user" "roles/aiplatform.admin" "roles/logging.logWriter" "roles/run.admin" "roles/spanner.databaseUser" \
        "roles/pubsub.editor" "roles/pubsub.subscriber" "roles/monitoring.viewer" \
        "roles/storage.objectAdmin" "roles/iam.serviceAccountTokenCreator"; do
        echo "$role"   
        gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" \
          --role="$role" --no-user-output-enabled
    done

    # Allow deployer users and Vertex AI Service Accounts to act as the networkagent SA
    echo "Granting Service Account User permissions on $GOOGLE_SERVICE_ACCOUNT..."
    gcloud iam service-accounts add-iam-policy-binding $GOOGLE_SERVICE_ACCOUNT \
        --member="user:$GOOGLE_USER" \
        --role="roles/iam.serviceAccountUser" \
        --project=$GOOGLE_PROJECT --no-user-output-enabled

    CURRENT_GCLOUD_USER=$(gcloud config get-value account 2>/dev/null || true)
    if [ -n "$CURRENT_GCLOUD_USER" ] && [ "$CURRENT_GCLOUD_USER" != "$GOOGLE_USER" ]; then
        gcloud iam service-accounts add-iam-policy-binding $GOOGLE_SERVICE_ACCOUNT \
            --member="user:$CURRENT_GCLOUD_USER" \
            --role="roles/iam.serviceAccountUser" \
            --project=$GOOGLE_PROJECT --no-user-output-enabled
    fi

    if [ -n "${GOOGLE_PROJECT_NUMBER:-}" ]; then
        for sa in "service-${GOOGLE_PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com" \
                  "service-${GOOGLE_PROJECT_NUMBER}@gcp-sa-aiplatform.iam.gserviceaccount.com" \
                  "${GOOGLE_PROJECT_NUMBER}-compute@developer.gserviceaccount.com"; do
            gcloud iam service-accounts add-iam-policy-binding $GOOGLE_SERVICE_ACCOUNT \
                --member="serviceAccount:${sa}" \
                --role="roles/iam.serviceAccountUser" \
                --project=$GOOGLE_PROJECT --no-user-output-enabled
        done
    fi

    # if the creadential file doesn't exist or as a zero byte size 
    # then create it
    if [[ ! -f "networkagent.json" ]] || [[ ! -s "networkagent.json" ]]; then
        echo "Creating the application credential file for service account $GOOGLE_SERVICE_ACCOUNT..."
        gcloud iam service-accounts keys create "networkagent.json" --iam-account=$GOOGLE_SERVICE_ACCOUNT
        if [[ $? -ne 0 ]]; then
            echo "Creation of key for service account $GOOGLE_SERVICE_ACCOUNT failed. Exiting."
            exit 1
        fi        
    fi


    echo "####################################################"
    echo "generating environment yaml files"
    echo "####################################################"
    TEMPLATE_VARS='${GOOGLE_PROJECT}${GOOGLE_REGION}${GOOGLE_ZONE}${GOOGLE_PROJECT_NUMBER}${GOOGLE_SERVICE_ACCOUNT}'
    envsubst "$TEMPLATE_VARS" < gcp/logsink.tpl.yaml > gcp/logsink.yaml

    TEMPLATE_VARS='${GOOGLE_PROJECT}${GOOGLE_REGION}${GOOGLE_ZONE}${GOOGLE_SPANNER_INSTANCE}${GOOGLE_NAMESPACE}${GOOGLE_SPANNER_TRANSPORT_DATABASE}${GOOGLE_SPANNER_CORE_DATABASE}${GOOGLE_SPANNER_RADIO_DATABASE}${GOOGLE_SPANNER_INFRA_DATABASE}${GOOGLE_SPANNER_SERVICE_DATABASE}${GOOGLE_SPANNER_OPS_DATABASE}'
    envsubst "$TEMPLATE_VARS" < gcp/spanner.tpl.yaml > gcp/spanner.yaml

    TEMPLATE_VARS='${GOOGLE_PROJECT}${GOOGLE_REGION}${GOOGLE_ZONE}${GOOGLE_NAMESPACE}'
    envsubst "$TEMPLATE_VARS" < gcp/bigquery.tpl.yaml > gcp/bigquery.yaml

    TEMPLATE_VARS='${GOOGLE_PROJECT}${GOOGLE_REGION}${GOOGLE_ZONE}'
    envsubst "$TEMPLATE_VARS" < gcp/configconnector.tpl.yaml > gcp/configconnector.yaml

    echo "#######################################################"
    echo "Generating Cloud Build configuration files"
    echo "#######################################################"
    # Note: controller/deployment.yaml and controller/cloudbuild.yaml are generated by controller/install.sh
    # Note: gnn cloudbuild and pipeline are generated by domains/transport/gnn/install.sh
    # Note: services/sharedmcp/cloudbuild.yaml is generated by services/sharedmcp/install.sh
    TEMPLATE_VARS='${GOOGLE_VM_USER}${GOOGLE_PROJECT}${GOOGLE_REGION}${GOOGLE_ZONE}${GOOGLE_REPO}'
    envsubst "$TEMPLATE_VARS" < dashboard-v2/cloudbuild.tpl.yaml > dashboard-v2/cloudbuild.yaml
    envsubst "$TEMPLATE_VARS" < services/gitea-webhook/cloudbuild.tpl.yaml > services/gitea-webhook/cloudbuild.yaml

    TEMPLATE_VARS='${GOOGLE_PROJECT}${GOOGLE_REGION}${GOOGLE_REPO}'
    envsubst "$TEMPLATE_VARS" < traffic-agent/cloudbuild.tpl.yaml > traffic-agent/cloudbuild.yaml
    envsubst "$TEMPLATE_VARS" < services/metricscollector/cloudbuild.tpl.yaml > services/metricscollector/cloudbuild.yaml
    echo "  -> Cloud Build configs generated (controller, gnn, and sharedmcp handled by their install scripts)"

}

#################################################
# Individual components (Re)deploy functions    #
#################################################
#
# All the fucntions below must be idempotent

DeploySpanner()
{
    # Delete current instance
    echo "Deleting current spanner DB..."
    kubectl delete -f gcp/spanner.yaml
    # regenerate the spanner spec file
    echo "Regenerating spanner manifest file..."
    TEMPLATE_VARS='${GOOGLE_PROJECT}${GOOGLE_REGION}${GOOGLE_ZONE}${GOOGLE_SPANNER_INSTANCE}${GOOGLE_NAMESPACE}${GOOGLE_SPANNER_TRANSPORT_DATABASE}${GOOGLE_SPANNER_CORE_DATABASE}${GOOGLE_SPANNER_RADIO_DATABASE}${GOOGLE_SPANNER_INFRA_DATABASE}${GOOGLE_SPANNER_SERVICE_DATABASE}${GOOGLE_SPANNER_OPS_DATABASE}'
    envsubst "$TEMPLATE_VARS" < gcp/spanner.tpl.yaml > gcp/spanner.yaml

    # Setup Spanner and wait until it's ready as we need it to be up and
    # running before the Operator is deployed so as not to miss any
    # creation events in the operator (especially on the networking part)
    #
    echo "####################################"
    echo "Waiting for Spanner DB to come up..."
    echo "####################################"

    echo "Creating Spanner database ${GOOGLE_SPANNER_INSTANCE}..."
    kubectl apply -f gcp/spanner.yaml -l "kind=spanner-instance"
    while [[ $(kubectl get spannerinstance $GOOGLE_SPANNER_INSTANCE -o 'jsonpath={..status.conditions[?(@.type=="Ready")].status}' 2>/dev/null) != "True" ]]; do
        sleep 20
        echo "sleeping for 20 secs..."
    done
    echo "Spanner instance ready !"

    # Work around because the edition spec is not supported in the manifest file
    # Same for backup schedule updated to None as backup creation make the DB deletion
    # more complex (not needed in this PoC)
    # (See https://b.corp.google.com/issues/372631209)
    echo "Updating Spanner instance to Enterprise Edition"
    gcloud spanner instances update $GOOGLE_SPANNER_INSTANCE --edition=ENTERPRISE &
    job_id=$!

    # Sometimes changing to Enterprise edition hangs.. but it actually does the job
    # so simply kill after a timeout
    timeout 2m sh -c "while kill -0 $job_id 2>/dev/null; do sleep 1; done"
    if [ $? -eq 124 ]; then
        kill -TERM $job_id
        # Do nothing for now
    fi

    echo "Updating Spanner instance to no backup schedule"
    gcloud spanner instances update $GOOGLE_SPANNER_INSTANCE --default-backup-schedule-type=NONE

    echo "Creating Spanner databases..."
    kubectl apply -f gcp/spanner.yaml -l "kind=spanner-database"

    # Wait for all databases to be ready
    databases=("$GOOGLE_SPANNER_TRANSPORT_DATABASE" "$GOOGLE_SPANNER_CORE_DATABASE" "$GOOGLE_SPANNER_RADIO_DATABASE" "$GOOGLE_SPANNER_INFRA_DATABASE" "$GOOGLE_SPANNER_SERVICE_DATABASE" "$GOOGLE_SPANNER_OPS_DATABASE")
    for db in "${databases[@]}"; do
        echo "Waiting for database $db to be ready..."
        while [[ $(kubectl get spannerdatabase $db -o 'jsonpath={..status.conditions[?(@.type=="Ready")].status}' 2>/dev/null) != "True" ]]; do
            sleep 20
            echo "  $db: sleeping for 20 secs..."
        done
        echo "  $db: ready!"
    done
    echo "All Spanner databases ready!"
}

############################################################
# Start GKE, config connector and customer sites           #
############################################################
StartPlatform()
{
    echo "####################################"
    echo "Starting the network agent services"
    echo "####################################"

   # Create artifact repository
    echo "###########################"
    echo "Create Artifact Repository "
    echo "############################"
    gcloud artifacts repositories describe $GOOGLE_REPO --location=$GOOGLE_REGION > /dev/null 2>&1
    if [[ $? -ne 0 ]]; then
        gcloud artifacts repositories create $GOOGLE_REPO --repository-format=docker --location=$GOOGLE_REGION --description="Network Agent Repository" --quiet
    fi

    # check if SERVICE ACCOUNT exists
    export GOOGLE_SERVICE_ACCOUNT=`gcloud iam service-accounts list --format="value(email)" --filter="networkagent@${GOOGLE_PROJECT}."`
    echo "GKE Cluster Service Account: $GOOGLE_SERVICE_ACCOUNT"

    # Create the service account if it doesnt exist
    if [ -z "${GOOGLE_SERVICE_ACCOUNT}" ]; then
        echo "Cannot find the service account - run this script with the -c option first"
        exit 1
    fi

    echo "#####################"
    echo "Creating mgmt network"
    echo "#####################"
    (gcloud compute networks describe mgmt > /dev/null 2>&1) || \
        gcloud compute networks create mgmt --subnet-mode=custom
    (gcloud compute networks subnets describe mgmt-subnet --region=$GOOGLE_REGION > /dev/null 2>&1) || \
        gcloud compute networks subnets create mgmt-subnet --network=mgmt --range=10.0.100.0/24 --region=$GOOGLE_REGION
    (gcloud compute firewall-rules describe mgmt-ingress > /dev/null 2>&1) || \
        gcloud compute firewall-rules create mgmt-ingress --network=mgmt --allow=tcp,udp,icmp --source-ranges="10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    (gcloud compute firewall-rules describe mgmt-iap-ssh > /dev/null 2>&1) || \
        gcloud compute firewall-rules create mgmt-iap-ssh --network=mgmt --allow=tcp:22 --source-ranges=35.235.240.0/20 --description="Allow SSH via IAP"
    (gcloud compute routers describe mgmt --region=$GOOGLE_REGION > /dev/null 2>&1) || \
        gcloud compute routers create mgmt --network mgmt --region=$GOOGLE_REGION
    (gcloud compute routers nats describe mgmt --router=mgmt --region=$GOOGLE_REGION > /dev/null 2>&1) || \
        gcloud compute routers nats create mgmt --router=mgmt --region=$GOOGLE_REGION --auto-allocate-nat-external-ips --nat-all-subnet-ip-ranges --enable-logging

    # create the GKE cluster
    echo "###################################################"
    echo "Creating GKE cluster - this will take a few minutes"
    echo "###################################################"

    # NOTE: release channel to None is the only way to prevent GKE control plane 
    # auto upgrade
    (gcloud container clusters describe networkautomation --zone=$GOOGLE_ZONE > /dev/null 2>&1) || \
    gcloud container clusters create networkautomation \
        --enable-private-nodes \
        --no-enable-private-endpoint \
        --no-enable-master-authorized-networks \
        --master-ipv4-cidr "172.16.0.0/28" \
        --enable-shielded-nodes \
        --enable-dataplane-v2 \
        --no-enable-autoupgrade \
        --release-channel=None \
        --addons ConfigConnector \
        --enable-ip-alias \
        --service-account $GOOGLE_SERVICE_ACCOUNT\
        --scopes "default,storage-full,cloud-platform,bigquery" \
        --workload-pool $GOOGLE_PROJECT.svc.id.goog \
        --zone $GOOGLE_ZONE\
        --node-locations $GOOGLE_ZONE \
        --num-nodes 2 \
        --machine-type "n1-standard-4" \
        --enable-fleet \
        --network mgmt \
        --subnetwork mgmt-subnet 

    if [ $? -ne 0 ]; then
      echo "ERROR while creating the networkautomation cluster. Exiting"
    fi

    # disable auto upgrade
    gcloud container node-pools update default-pool \
    --cluster networkautomation \
    --location $GOOGLE_ZONE \
    --no-enable-autoupgrade

    # On glinux machines gcloud components cannot be installed
    # through gcloud. apt must be used instead
    if [[ `uname -v` =~ "rodete" ]]; then
        for p in kubectl google-cloud-cli; do
            (dpkg -s $p &> /dev/null) || sudo apt install $p
        done
        # gke-gcloud-auth-plugin might not be in apt repos, but is already in gcloud SDK
        gcloud components install gke-gcloud-auth-plugin --quiet 2>/dev/null || true
    else
        gcloud components install kubectl
        gcloud components install kpt
        gcloud components install gke-gcloud-auth-plugin # for GKE 1.26+
    fi

    gcloud container clusters get-credentials networkautomation --region=$GOOGLE_ZONE

    # Give the Kubernetes ServiceAccount access to impersonate the IAM service account
    # See https://cloud.google.com/kubernetes-engine/docs/how-to/workload-identity#authenticating_to
    gcloud iam service-accounts add-iam-policy-binding \
    $GOOGLE_SERVICE_ACCOUNT \
        --member="serviceAccount:$GOOGLE_PROJECT.svc.id.goog[cnrm-system/cnrm-controller-manager]" \
        --role="roles/iam.workloadIdentityUser"

    # Setup the GKE namespace we'll be using
    kubectl create namespace $GOOGLE_NAMESPACE
    kubectl annotate namespace $GOOGLE_NAMESPACE cnrm.cloud.google.com/project-id=$GOOGLE_PROJECT
    kubectl config set-context --current --namespace $GOOGLE_NAMESPACE

    # Setup the GKE network namespace to deploy network resources to
    kubectl create namespace network

    # create and attach operator service account to networkagent service account for workload identity
    kubectl create serviceaccount networkoperator-account --namespace $GOOGLE_NAMESPACE
    gcloud iam service-accounts add-iam-policy-binding $GOOGLE_SERVICE_ACCOUNT \
        --role roles/iam.workloadIdentityUser \
        --member "serviceAccount:$GOOGLE_PROJECT.svc.id.goog[$GOOGLE_NAMESPACE/networkoperator-account]"

    # Grant access permissions to the GKE cluster
    # See https://cloud.google.com/spanner/docs/connect-gke-cluster
    # For an unknown reason granting to the service account (line below) doesn't work...
    # gcloud projects add-iam-policy-binding ${GOOGLE_PROJECT} \
    #  --member="principal://iam.googleapis.com/projects/${GOOGLE_PROJECT_NUMBER}/locations/global/workloadIdentityPools/${GOOGLE_PROJECT}.svc.id.goog/subject/ns/${GOOGLE_NAMESPACE}/sa/${GOOGLE_SERVICE_ACCOUNT}" \
    #  --role=roles/spanner.databaseUser --condition=None
    #
    # So here is a variant that grants the spanner permission to all service accounts
    # in the designated namespace. This one works.
    #
    # Same to give the operator access to the Vertex AI prediction API
    for role in "roles/spanner.databaseUser" "roles/aiplatform.user" "roles/monitoring.metricWriter"; do
        gcloud projects add-iam-policy-binding ${GOOGLE_PROJECT} \
            --member="principalSet://iam.googleapis.com/projects/${GOOGLE_PROJECT_NUMBER}/locations/global/workloadIdentityPools/${GOOGLE_PROJECT}.svc.id.goog/namespace/${GOOGLE_NAMESPACE}" \
            --role="$role" --condition=None --no-user-output-enabled
    done   
    echo "done."

    # Setup the one config connector we will be using
    kubectl apply -f gcp/configconnector.yaml

    echo "################################################"
    echo "Waiting for cnrm-controller-manager-0 to start... "
    echo "################################################"

    # kubectl wait -n cnrm-system --for=condition=Ready pod cnrm-controller-manager-0
    while [[ $(kubectl get pods -n cnrm-system cnrm-controller-manager-0 -o 'jsonpath={..status.conditions[?(@.type=="Ready")].status}' 2>/dev/null) != "True" ]]; do
        sleep 20
        echo "sleeping for 20 secs..."
    done
    echo "Ready !"

    # Install ConfigSync CRDs and operator (required for RootSync/RepoSync resources)
    # Version should match the version reported by: gcloud beta container fleet config-management version
    kubectl apply -f https://github.com/GoogleContainerTools/kpt-config-sync/releases/download/v1.24.0/config-sync-manifest.yaml

    # Start ConfigSync operator in cluster
    gcloud beta container fleet config-management enable --project=$GOOGLE_PROJECT
    gcloud beta container fleet config-management apply --membership=networkautomation --config=./gcp/configsync.yaml --project=$GOOGLE_PROJECT

    # Wait for ConfigSync reconciler-manager to be ready
    kubectl wait --for=condition=ready pod -l app=reconciler-manager -n config-management-system --timeout=120s

    DeploySpanner

    DeployBigQuery

    DeployOperator

    # I tried hard to create the Log Sink to BQ or PubSub with Config Connector
    # to no avail. I couldn't fix the dataset or topic access permission problem :-(
    # That's why it is created from the shell script
    # kubectl apply -f gcp/logsink.yaml
    DeployLogCapture

    DeployMetricsCollector

    # Must precede the network VM: its 3gpp role builds the device image, which
    # pulls traffic-agent:latest from Artifact Registry as a COPY --from stage.
    DeployTrafficAgent

    # start the network VM
    kubectl apply -f gcp/networkvm.yaml

    echo "############################################################################"
    echo "Waiting for VyosVM networkvm to be ready (this can take up to 15 minutes)..."
    echo "############################################################################"
    while [[ $(kubectl get vyosvm networkvm -n $GOOGLE_NAMESPACE -o 'jsonpath={..status.phase}' 2>/dev/null) != "Ready" ]]; do
        sleep 60
        echo "waiting for networkvm to be ready, sleeping for 60 secs..."
    done
    echo "VyosVM networkvm is Ready!"
}

############################################################
# Delete GKE, config connector and customer sites          #
############################################################
Delete()
{
    if [[ $YES_FLAG != "y" ]] && [[ $NO_FLAG != "y" ]]; then
        read -p "Are you sure you want to delete the environment configuration (y/n)? " choice
        case "$choice" in 
            y|Y ) echo "proceeding to delete environment configuration";;
            n|N ) exit 0;;
            * ) echo "please enter y/n";;
        esac
    elif [[ $NO_FLAG == "y" ]]; then
        echo "Skipping environment configuration deletion (NO_FLAG set)"
        exit 0
    fi

    echo "######################"
    echo "Deleting Artifact Repo"
    echo "######################"
    (gcloud artifacts repositories describe $GOOGLE_REPO --location=$GOOGLE_REGION > /dev/null 2>&1) && \
    gcloud artifacts repositories delete $GOOGLE_REPO --location=$GOOGLE_REGION --quiet

    echo "##############################"
    echo "Deleting environment keys"
    echo "##############################"
    rm -f controller/src/google-compute* \
        networkagent.json \
        google-compute* \
        domains/transport/gnn/src/pipeline/submit_pipeline.py

}

############################################################
# Kill the environment resources                           #
############################################################
Kill()
{
    if [[ $YES_FLAG != "y" ]] && [[ $NO_FLAG != "y" ]]; then
        read -p "Are you sure you want to kill the environment(y/n)? " choice
        case "$choice" in 
            y|Y ) echo "proceeding to kill the environment";;
            n|N ) exit 0;;
            * ) echo "please enter y/n";;
        esac
    elif [[ $NO_FLAG == "y" ]]; then
        echo "Skipping environment kill (NO_FLAG set)"
        exit 0
    fi
    echo "##############################################"
    echo "Killing the environment - will take a few mins"
    echo "##############################################"
    kubectl config set-context --current --namespace $GOOGLE_NAMESPACE

    # 1. Undeploy dashboard (dashboard-v2 has no uninstall.sh; delete the service)
    echo "Undeploying dashboard-v2..."
    gcloud run services delete network-dashboard-v2 --region="$GOOGLE_REGION" --quiet 2>/dev/null || true

    # 2. Undeploy domain agents and domain services
    echo "Undeploying domain agents and services..."
    for domain in transport core radio service; do
        if [ -f "domains/$domain/agents/uninstall.sh" ]; then
            echo "Undeploying $domain domain agents..."
            (cd domains/$domain/agents && ./uninstall.sh)
        fi

        if [ -d "domains/$domain/services" ]; then
            for svc_dir in domains/$domain/services/*/; do
                svc_name=$(basename "$svc_dir")
                if [ -f "${svc_dir}uninstall.sh" ]; then
                    echo "Undeploying $domain domain service: $svc_name..."
                    (cd "$svc_dir" && ./uninstall.sh)
                fi
            done
        fi
    done

    # 3. Undeploy supporting services (supervisor, gitea, gitea-webhook, sharedmcp)
    echo "Undeploying supporting services..."
    if [ -f "services/gitea-webhook/uninstall.sh" ]; then
        (cd services/gitea-webhook && ./uninstall.sh)
    fi
    if [ -f "services/gitea/uninstall.sh" ]; then
        (cd services/gitea && ./uninstall.sh)
    fi
    if [ -f "services/sharedmcp/uninstall.sh" ]; then
        (cd services/sharedmcp && ./uninstall.sh)
    fi

    # 4. Undeploy controller / network operators
    echo "Undeploying controller..."
    if [ -f "controller/uninstall.sh" ]; then
        (cd controller && ./uninstall.sh)
    fi

    # 5. Delete log collector
    echo "Undeploying log collector..."
    if [ -f "services/logcollector/uninstall.sh" ]; then
        (cd services/logcollector && ./uninstall.sh)
    fi

    # 6. Delete Vertex AI GNN resources
    CleanupGNN

    # 7. Undeploy the Data Platform
    # BigQuery dataset/tables first. There is no Dataflow pipeline to tear down:
    # services/metricscollector writes both Spanner and BigQuery directly.
    kubectl delete -f gcp/bigquery.yaml --ignore-not-found

    # Network simulator VM third
    kubectl delete -f gcp/networkvm.yaml --ignore-not-found

    # Spanner databases/instance last
    kubectl delete -f gcp/spanner.yaml --ignore-not-found

    echo "#####################"
    echo "Deleting GKE Cluster"
    echo "#####################"
    gcloud container clusters delete networkautomation --region=$GOOGLE_ZONE --quiet
    kubectl config unset current-context

    echo "#####################"
    echo "Deleting mgmt network"
    echo "#####################"
    gcloud compute routers delete mgmt --region=$GOOGLE_REGION --quiet
    for r in $(gcloud compute firewall-rules list --filter="name~'^mgmt-'" --format="value(name)" --project=$GOOGLE_PROJECT); do
        gcloud compute firewall-rules delete $r --project=$GOOGLE_PROJECT --quiet
    done
    gcloud compute networks subnets delete mgmt-subnet --region=$GOOGLE_REGION --quiet
    gcloud compute networks delete mgmt --project=$GOOGLE_PROJECT --quiet
}

############################################################
# Build and deploy the operator                            #
############################################################
DeployOperator()
{
    if [ -f "controller/install.sh" ]; then
        (cd controller && ./install.sh)
    else
        echo "Error: controller/install.sh not found"
        exit 1
    fi
}

############################################################
# Build and deploy the gitea server and git repo           #
############################################################
DeployGit()
{
    echo "#####################################"
    echo "Deploy/Update Gitea server (Cloud Run)"
    echo "#####################################"

    # All deployment logic is in services/gitea/install.sh
    (cd services/gitea && ./install.sh)
}

############################################################
# Build and deploy the Gitea webhook service              #
############################################################
DeployGiteaWebhook()
{
    if [ -f "services/gitea-webhook/install.sh" ]; then
        (cd services/gitea-webhook && ./install.sh)
    else
        echo "Error: services/gitea-webhook/install.sh not found"
        exit 1
    fi
}

############################################################
# Build and deploy BigQuery dataset and tables             #
############################################################
DeployBigQuery()
{
    echo "####################################"
    echo "Deploying BigQuery dataset and tables..."
    echo "####################################"
    TEMPLATE_VARS='${GOOGLE_PROJECT}${GOOGLE_REGION}${GOOGLE_ZONE}${GOOGLE_NAMESPACE}'
    envsubst "$TEMPLATE_VARS" < gcp/bigquery.tpl.yaml > gcp/bigquery.yaml
    kubectl apply -f gcp/bigquery.yaml
    echo "BigQuery resources applied!"
}

############################################################
# Build and deploy the log capture                         #
############################################################
DeployLogCapture()
{
    if [ -f "services/logcollector/install.sh" ]; then
        (cd services/logcollector && ./install.sh)
    else
        echo "Error: services/logcollector/install.sh not found"
        exit 1
    fi
}

############################################################
# Deploy the metrics collector                             #
############################################################
# The only writer of RouterSystemMetrics, RouterRoutingSeries and
# TrafficFlowSeries. Without it the dashboard's whole metrics surface --
# performance graphs, link utilization, capacity -- queries empty tables and
# renders blank, with no error to explain why.
DeployMetricsCollector()
{
    if [ -f "services/metricscollector/install.sh" ]; then
        (cd services/metricscollector && ./install.sh)
    else
        echo "Error: services/metricscollector/install.sh not found"
        exit 1
    fi
}

############################################################
# Build and push the traffic agent image                   #
############################################################
# The network VM's device image does
#   COPY --from=${GOOGLE_REGION}-docker.pkg.dev/${GOOGLE_PROJECT}/networkagent/traffic-agent:latest
# (controller/src/vyosvm/playbooks/roles/3gpp/{files/free5gc/device/Dockerfile,
# templates/Makefile:12}), so "make device" on the VM fails outright if this
# image is not in Artifact Registry first. Nothing else builds it: the render
# block writes traffic-agent/cloudbuild.yaml but nobody submitted it.
DeployTrafficAgent()
{
    IMAGE_URI="${GOOGLE_REGION}-docker.pkg.dev/${GOOGLE_PROJECT}/${GOOGLE_REPO}/traffic-agent:latest"

    if [[ $NO_FLAG == "y" ]] && gcloud artifacts docker images describe "$IMAGE_URI" >/dev/null 2>&1; then
        echo "traffic-agent image already exists - not rebuilding (NO_FLAG set)"
        return
    fi

    echo "Building traffic-agent image..."
    gcloud builds submit --region="$GOOGLE_REGION" --config traffic-agent/cloudbuild.yaml .
}

############################################################
# Reasoning Engine ID lookup                               #
############################################################
# Each deployed agent writes its full resource path
# (projects/N/locations/R/reasoningEngines/ID) to deployment_metadata.json.
# Absent metadata is not fatal -- callers treat an empty result as
# "that agent is not deployed yet".
ReadEngineId()
{
    python3 -c "import json;print(json.load(open('agents-in-agent-runtime/$1/deployment_metadata.json')).get('remote_agent_runtime_id',''))" 2>/dev/null || echo ""
}

############################################################
# Build and deploy the dashboard (dashboard-v2)            #
############################################################
# dashboard-v2 replaces the Socket.IO dashboard + supervisor pair. Its
# aiohttp backend proxies straight to the Reasoning Engine over IAM
# (server.py:548 -> reasoningEngines/{id}:streamQuery), so there is no
# separate supervisor service to deploy or to point it at.
DeployDashboardV2()
{
    IMAGE_URI="${GOOGLE_REGION}-docker.pkg.dev/${GOOGLE_PROJECT}/${GOOGLE_REPO}/dashboard-v2:latest"

    # REASONING_ENGINE_ID is the supervisor agent -- the one the backend proxies
    # to. It is derived from deployment metadata, not configured in config.sh,
    # so it is only available after the agents have been deployed.
    REASONING_ENGINE_ID=$(ReadEngineId supervisor)
    DETECTIVE_ENGINE_ID=$(ReadEngineId detective)
    IMPACT_ENGINE_ID=$(ReadEngineId impact)
    DESIGNER_ENGINE_ID=$(ReadEngineId designer)
    TESTER_ENGINE_ID=$(ReadEngineId tester)
    CHAOS_ENGINE_ID=$(ReadEngineId chaos)

    if [[ -z "$REASONING_ENGINE_ID" ]]; then
        echo "**WARNING** No supervisor Reasoning Engine ID found in"
        echo "            agents-in-agent-runtime/supervisor/deployment_metadata.json."
        echo "            The dashboard will deploy but cannot reach the supervisor."
        echo "            Deploy the agents first, then: ./install.sh --dashboard"
    fi

    # Derived, matching the old installer (install-new.sh:1520-1524).
    # This used to read the Gitea CRD's external_ip_address. That CRD was
    # removed, so the lookup always failed and the dashboard shipped with
    # GITEA_URL=https://localhost:3000 baked into the JS bundle.
    GITEA_URL=$(bash "${INSTALL_SCRIPT_DIR}/services/gitea/gitea-url.sh")
    TRAIN_GNN_URI="${GOOGLE_REGION}-docker.pkg.dev/${GOOGLE_PROJECT}/${GOOGLE_REPO}/train-gnn:latest"
    SERVE_GNN_URI="${GOOGLE_REGION}-docker.pkg.dev/${GOOGLE_PROJECT}/${GOOGLE_REPO}/serve-gnn:latest"

    # The Flutter web bundle must be compiled before the image build:
    # dashboard-v2/Dockerfile does "ADD dashboard-v2/build/web/", and build/
    # is a build output that is not tracked in git. Skipping this step works
    # on a machine that has built before and fails on a fresh clone.
    #
    # No WEBAPPS_LOGIN/WEBAPPS_PWD dart-defines: a --dart-define lands in the
    # bundle as a plaintext constant, so passing the password here published
    # it to anyone who could fetch the JS. Access control is IAP, applied by
    # --no-allow-unauthenticated below.
    echo "Compiling Flutter web bundle..."
    (
        cd dashboard-v2 || exit 1
        flutter clean
        flutter build web --profile --source-maps \
            --dart-define=GCP_PROJECT="${GOOGLE_PROJECT}" \
            --dart-define=GITEA_URL="${GITEA_URL}" \
            --dart-define=TRAIN_GNN_URI="${TRAIN_GNN_URI}" \
            --dart-define=SERVE_GNN_URI="${SERVE_GNN_URI}" \
            --dart-define=REASONING_ENGINE_ID="${REASONING_ENGINE_ID}" \
            --dart-define=DETECTIVE_ENGINE_ID="${DETECTIVE_ENGINE_ID}" \
            --dart-define=IMPACT_ENGINE_ID="${IMPACT_ENGINE_ID}" \
            --dart-define=DESIGNER_ENGINE_ID="${DESIGNER_ENGINE_ID}" \
            --dart-define=TESTER_ENGINE_ID="${TESTER_ENGINE_ID}" \
            --dart-define=CHAOS_ENGINE_ID="${CHAOS_ENGINE_ID}"
    ) || {
        echo "Error: Flutter web build failed"
        exit 1
    }

    # Render our own build config, so --dashboard works standalone without
    # having to re-run Create().
    envsubst '${GOOGLE_VM_USER}${GOOGLE_PROJECT}${GOOGLE_REGION}${GOOGLE_ZONE}${GOOGLE_REPO}' \
        < dashboard-v2/cloudbuild.tpl.yaml > dashboard-v2/cloudbuild.yaml

    echo "Building dashboard-v2 image..."
    gcloud builds submit --region="$GOOGLE_REGION" --config dashboard-v2/cloudbuild.yaml . || {
        echo "Error: dashboard-v2 image build failed"
        exit 1
    }

    PROJECT_NUMBER=$(gcloud projects describe "$GOOGLE_PROJECT" --format="value(projectNumber)")

    # max-instances 5: a handful of operators, and each instance holds Spanner
    # sessions plus an open Vertex AI stream for the duration of a chat turn.
    gcloud run deploy network-dashboard-v2 \
        --image "$IMAGE_URI" \
        --region "$GOOGLE_REGION" \
        --service-account "$GOOGLE_SERVICE_ACCOUNT" \
        --max-instances 5 \
        --update-env-vars GOOGLE_PROJECT="$GOOGLE_PROJECT" \
        --update-env-vars GOOGLE_REGION="$GOOGLE_REGION" \
        --update-env-vars GOOGLE_ZONE="$GOOGLE_ZONE" \
        --update-env-vars GOOGLE_SPANNER_INSTANCE="$GOOGLE_SPANNER_INSTANCE" \
        --update-env-vars GOOGLE_SPANNER_TRANSPORT_DATABASE="$GOOGLE_SPANNER_TRANSPORT_DATABASE" \
        --update-env-vars GOOGLE_SPANNER_CORE_DATABASE="$GOOGLE_SPANNER_CORE_DATABASE" \
        --update-env-vars GOOGLE_SPANNER_RADIO_DATABASE="$GOOGLE_SPANNER_RADIO_DATABASE" \
        --update-env-vars GOOGLE_SPANNER_INFRA_DATABASE="$GOOGLE_SPANNER_INFRA_DATABASE" \
        --update-env-vars GOOGLE_SPANNER_SERVICE_DATABASE="$GOOGLE_SPANNER_SERVICE_DATABASE" \
        --update-env-vars GOOGLE_SPANNER_OPS_DATABASE="$GOOGLE_SPANNER_OPS_DATABASE" \
        --update-env-vars REASONING_ENGINE_ID="${REASONING_ENGINE_ID}" \
        --no-allow-unauthenticated \
        --iap || {
        echo "Error: dashboard-v2 deploy failed"
        exit 1
    }

    # IAP fronts the service, so the IAP service agent needs run.invoker.
    gcloud run services add-iam-policy-binding network-dashboard-v2 \
        --region="$GOOGLE_REGION" \
        --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-iap.iam.gserviceaccount.com" \
        --role="roles/run.invoker" \
        --quiet

    # Grant the user's domain access through IAP.
    DOMAIN_NAME=$(echo "$GOOGLE_USER" | cut -d'@' -f2)
    gcloud iap web add-iam-policy-binding \
        --region="$GOOGLE_REGION" \
        --resource-type=cloud-run \
        --service=network-dashboard-v2 \
        --member="domain:${DOMAIN_NAME}" \
        --role="roles/iap.httpsResourceAccessor" \
        --quiet

    DASHBOARD_URL=$(gcloud run services describe network-dashboard-v2 --region="$GOOGLE_REGION" --format="value(status.url)")
    echo "Dashboard v2 URL is ${DASHBOARD_URL}"
}

############################################################
# Clean up GNN resources (endpoints, models, scheduler)    #
############################################################
CleanupGNN()
{
    if [ -f "domains/transport/gnn/uninstall.sh" ]; then
        (cd domains/transport/gnn && ./uninstall.sh)
    else
        echo "Warning: domains/transport/gnn/uninstall.sh not found - skipping GNN cleanup"
    fi
}

############################################################
# Build and deploy the GNN services (Vertex AI Pipelines)  #
############################################################
DeployGNN()
{
    # Clean up existing GNN resources before deployment
    CleanupGNN
    echo ""

    if [ -f "domains/transport/gnn/install.sh" ]; then
        (cd domains/transport/gnn && ./install.sh)
    else
        echo "Error: domains/transport/gnn/install.sh not found"
        exit 1
    fi
}

############################################################
# Build and deploy agents and dashboard                    #
############################################################
DeployAgents()
{
    # check if flutter installed locally for now
    if ! command -v flutter &> /dev/null
    then
        echo "flutter could not be found in your path, you must install it"
        exit 1
    fi

    export GOOGLE_SERVICE_ACCOUNT=`gcloud iam service-accounts list --format="value(email)" --filter="networkagent@${GOOGLE_PROJECT}."`

    # Deploy Gitea first (provides GitOps repository)
    echo "#####################################"
    echo "Deploy/Update Gitea"
    echo "#####################################"
    DeployGit

    # Deploy the MCP tools (sharedmcp)
    echo "#####################################"
    echo "Deploy/Update MCP Tools (Cloud Run)"
    echo "#####################################"
    (cd services/sharedmcp && ./install.sh)

    # Get sharedmcp URL for domain agents
    TOOLS_URL=$(gcloud run services describe sharedmcp --region=$GOOGLE_REGION --format="value(status.url)" 2>/dev/null)
    if [[ -z "$TOOLS_URL" ]]; then
        echo
        echo "**ERROR** cannot determine the sharedmcp URL required by other agents. Exiting"
        exit 1
    fi

    # Deploy domain-specific orchestrator agents.
    # Transport is the only domain with a real agent. The core/radio/service
    # orchestrators were the unmodified ADK starter sample (get_weather()) and
    # have been removed, so this list is not "not yet enabled" -- there is
    # nothing there to enable. Add a domain here once it has an actual agent.
    echo "################################################"
    echo "Deploying domain orchestrator agents..."
    echo "################################################"

    for domain in transport; do
        if [ -f "domains/$domain/agents/install.sh" ]; then
            echo "Deploying $domain domain agents..."
            (cd domains/$domain/agents && ./install.sh)
        else
            echo "Warning: domains/$domain/agents/install.sh not found - skipping"
        fi
    done

    # Deploy domain-specific services. The loop is deliberately transport-only:
    # domains/service/services/mcp also has an install.sh, but it proxies the core
    # and radio intent/topology APIs, which have no deployed servers. The uninstall
    # loop still sweeps 'service' so a previously deployed copy gets cleaned up.
    echo "################################################"
    echo "Deploying domain services..."
    echo "################################################"

    for domain in transport; do
        if [ -d "domains/$domain/services" ]; then
            for svc_dir in domains/$domain/services/*/; do
                svc_name=$(basename "$svc_dir")
                if [ -f "${svc_dir}install.sh" ]; then
                    echo "Deploying $domain domain service: $svc_name..."
                    (cd "$svc_dir" && ./install.sh)
                else
                    echo "Warning: ${svc_dir}install.sh not found - skipping"
                fi
            done
        fi
    done

    # Wire the GitOps review loop: designer pushes CRs to the network repo's
    # 'update' branch, Gitea posts the push to this service, and the trust agent
    # reviews the diff and merges to main on PROCEED.
    #
    # This is deliberately last of the backend steps, because it needs the trust
    # agent's engine id, which agents-in-agent-runtime/deploy_agents.py writes to
    # trust/deployment_metadata.json. That fleet deploy is a separate manual step
    # (see AD_DEPLOY.md), so on a first run from scratch the trust agent will not
    # exist yet -- the script below warns and deploys an unwired service. Re-run
    # `cd services/gitea-webhook && ./install.sh` once the fleet is up to finish
    # the wiring.
    echo "#####################################"
    echo "Deploy/Update Gitea webhook (trust review loop)"
    echo "#####################################"
    DeployGiteaWebhook

    # Build and deploy the network dashboard.
    # No supervisor step: dashboard-v2 talks to the Reasoning Engine directly.
    echo "#####################################"
    echo "Deploy/Update Dashboard v2"
    echo "#####################################"
    DeployDashboardV2
}

############################################################
# Demo information                                         #
############################################################
DisplayDemoInfo()
{
    echo "Gathering demo information..."
    DASHBOARD_URL=$(gcloud run services describe network-dashboard-v2 --region=$GOOGLE_REGION --format="value(status.url)" 2>/dev/null || echo "")
    GITEA_URL=$(bash "${INSTALL_SCRIPT_DIR}/services/gitea/gitea-url.sh")
    GITEA_WEBHOOK_URL=$(gcloud run services describe gitea-webhook --region=$GOOGLE_REGION --format="value(status.url)" 2>/dev/null || echo "")
    TOOLS_URL=$(gcloud run services describe sharedmcp --region=$GOOGLE_REGION --format="value(status.url)" 2>/dev/null || echo "")

    REASONING_ENGINE_ID=$(ReadEngineId supervisor)

    echo "============================================================"
    echo "=                Demo information Summary                  ="
    echo "============================================================"
    echo ""
    echo "Cloud Run Services:"
    echo "-------------------"
    [[ -n "$DASHBOARD_URL" ]] && echo "  Dashboard:       ${DASHBOARD_URL}" || echo "  Dashboard:       (not deployed)"
    [[ -n "$REASONING_ENGINE_ID" ]] && echo "  Supervisor RE:   ${REASONING_ENGINE_ID}" || echo "  Supervisor RE:   (not deployed)"
    [[ -n "$GITEA_WEBHOOK_URL" ]] && echo "  Gitea Webhook:   ${GITEA_WEBHOOK_URL}" || echo "  Gitea Webhook:   (not deployed)"
    [[ -n "$TOOLS_URL" ]] && echo "  Network Tools:   ${TOOLS_URL}" || echo "  Network Tools:   (not deployed)"
    echo ""

    # Gitea is on GKE, not Cloud Run, so it gets its own block. The certificate
    # is self-signed against the IP -- there is no domain to get a trusted one
    # for -- hence http.sslVerify=false on the clone.
    if [[ -n "$GITEA_URL" ]]; then
        echo "Gitea (GKE):"
        echo "------------"
        echo "  Host: ${GITEA_URL}"
        echo "  git clone ${GITEA_URL}/${WEBAPPS_LOGIN}/network -c http.sslVerify=false"
        echo ""
    else
        echo "  Gitea:           (not deployed)"
        echo ""
    fi

    echo "Credentials:"
    echo "------------"
    echo "  Username: ${WEBAPPS_LOGIN}"
    echo "  Password: ${WEBAPPS_PWD}"
}

############################################################
# Deploy platform infrastructure                           #
############################################################
DeployPlatform()
{
    echo "########################################"
    echo "Deploying platform infrastructure..."
    echo "########################################"

    EnsureEnvironmentCreated
    StartPlatform

    echo "########################################"
    echo "Platform deployment completed!"
    echo "########################################"
}

############################################################
# Deploy all agents and dashboard                          #
############################################################
DeployAllAgents()
{
    echo "########################################"
    echo "Deploying all agents and dashboard..."
    echo "########################################"

    ValidateEnvironmentExists
    DeployAgents

    echo "########################################"
    echo "Agent deployment completed!"
    echo "########################################"
}

############################################################
# Install everything - comprehensive setup                 #
############################################################
InstallEverything()
{
    echo "########################################"
    echo "Starting comprehensive installation..."
    echo "########################################"

    # Deploy platform infrastructure (includes environment creation, GKE, Spanner, controller, logs, metrics)
    DeployPlatform

    # Deploy all agents and dashboard (includes Gitea)
    DeployAgents

    # Display demo information summary
    DisplayDemoInfo

    echo "########################################"
    echo "Comprehensive installation completed!"
    echo "########################################"
}

############################################################
# Help                                                     #
############################################################
Help()
{
   echo "Network Agent environment manager"
   echo
   echo "Syntax: install.sh [COMMAND] [FLAGS]"
   echo
   echo "DEPLOYMENT COMMANDS:"
   echo "  --platform    Create and start platform infrastructure"
   echo "                (auto-creates environment, deploys GKE, Spanner, controller, logs, metrics)"
   echo
   echo "  --agents      Deploy all agents, dashboard, and Gitea"
   echo "                (requires platform, deploys Gitea + sharedmcp + domain agents +"
   echo "                 gitea-webhook + dashboard-v2)"
   echo
   echo "  --dashboard   Redeploy the dashboard (dashboard-v2) only"
   echo "                (requires platform; run after the agents are deployed)"
   echo
   echo "  --webhook     Redeploy the Gitea webhook and re-register the trust hook"
   echo "                (run after deploy_agents.py, or after rebuilding Gitea)"
   echo
   echo "  --gnn         Deploy GNN (Graph Neural Network)"
   echo "                (requires platform)"
   echo
   echo "  --all         Complete installation"
   echo "                (platform + agents)"
   echo
   echo "MANAGEMENT COMMANDS:"
   echo "  -k, --kill    Stop and delete runtime (GKE, VMs, databases)"
   echo "  -d, --delete  Delete environment files (keys, manifests, networkagent.json)"
   echo
   echo "INFORMATION COMMANDS:"
   echo "  -g, --env     Display active GCP environment info"
   echo "  -i, --info    Display deployed service URLs and credentials"
   echo "  -h, --help    Display this help message"
   echo
   echo "FLAGS:"
   echo "  -y, --yes     Answer 'yes' to all prompts (automation mode)"
   echo "  -n, --no      Answer 'no' to all prompts (skip rebuilds)"
   echo
   echo "EXAMPLES:"
   echo "  ./install.sh --all                  # Install everything from scratch"
   echo "  ./install.sh --all -y               # Install everything, no prompts"
   echo "  ./install.sh --platform             # Create platform only"
   echo "  ./install.sh --agents               # Deploy agents only (platform must exist)"
   echo "  ./install.sh --gnn                  # Deploy GNN (platform must exist)"
   echo "  ./install.sh --kill; ./install.sh --platform   # Rebuild platform"
   echo "  ./install.sh --dashboard            # Redeploy dashboard only"
   echo "  ./install.sh --webhook              # Re-wire the Gitea -> trust review loop"
   echo "  cd controller && ./install.sh       # Redeploy controller only"
   echo
   echo "COMPONENT DEPLOYMENT:"
   echo "  To deploy individual components, run their install.sh scripts:"
   echo "    cd controller && ./install.sh"
   echo "    cd services/logcollector && ./install.sh"
   echo "    cd services/metricscollector && ./install.sh"
   echo "    cd services/gitea && ./install.sh"
   echo "    cd services/sharedmcp && ./install.sh"
   echo "    cd domains/transport/agents && ./install.sh   # the only domain with an agent"
   echo "    cd mcp_server_for_ge && ./deploy.sh           # the re/ge MCP twins"
   echo
   echo "  The Vertex AI agent fleet is deployed separately (supervisor last):"
   echo "    cd agents-in-agent-runtime && python3 deploy_agents.py"
}

############################################################
# Process the input options                                #
############################################################
# Parse flags
YES_FLAG="n"
NO_FLAG="n"
func_calls=""

# Simple case statement for main options
case $1 in
    --platform)
        shift
        func_calls="CheckGCPEnv SetDemoEnv DeployPlatform"
        ;;
    --agents)
        shift
        func_calls="CheckGCPEnv SetDemoEnv DeployAllAgents"
        ;;
    --all)
        shift
        func_calls="CheckGCPEnv SetDemoEnv InstallEverything"
        ;;
    --gnn)
        shift
        func_calls="CheckGCPEnv SetDemoEnv DeployGNN"
        ;;
    --dashboard)
        shift
        func_calls="CheckGCPEnv SetDemoEnv DeployDashboardV2"
        ;;
    --webhook)
        shift
        func_calls="CheckGCPEnv SetDemoEnv DeployGiteaWebhook"
        ;;
    -k|--kill)
        shift
        func_calls="CheckGCPEnv SetDemoEnv Kill"
        ;;
    -d|--delete)
        shift
        func_calls="CheckGCPEnv SetDemoEnv Delete"
        ;;
    -g|--env)
        shift
        func_calls="DisplayGCPEnv"
        ;;
    -i|--info)
        shift
        func_calls="CheckGCPEnv SetDemoEnv DisplayDemoInfo"
        ;;
    -h|--help|"")
        Help
        exit 0
        ;;
    *)
        echo "Error: Invalid option '$1'"
        Help
        exit 1
        ;;
esac

# Parse remaining flags (-y, -N)
while [[ $# -gt 0 ]]; do
    case $1 in
        -y|--yes)
            YES_FLAG="y"
            shift
            ;;
        -n|--no)
            NO_FLAG="y"
            shift
            ;;
        *)
            echo "Error: Invalid flag '$1'"
            Help
            exit 1
            ;;
    esac
done

# Export flags so component install scripts can use them
export YES_FLAG
export NO_FLAG

# Execute the chosen functions
for f in $func_calls; do
  $f
done
exit 0
