#!/bin/bash
#
# Shared "should I rebuild this image?" decision for the service installers.
#
# Four installers had the same block copied out, and the same defect in it: a
# bare `read -p` under `set -euo pipefail`. With no TTY — CI, a backgrounded
# run, `install.sh --webhook` from a pipeline — `read` returns 1 at EOF and the
# script exits at the prompt with no message and no failing command to point at.
# It looks exactly like a silent hang followed by a silent death.
#
# Source this and call:
#
#     if should_build_image "$IMAGE_URI" "Webhook"; then
#         gcloud builds submit ...
#     fi
#
# Decision table, unchanged from the four copies apart from the TTY row:
#
#   image absent        -> build
#   YES_FLAG=y          -> build
#   NO_FLAG=y           -> skip
#   no TTY              -> skip, and say so (was: silent exit)
#   otherwise           -> ask

# should_build_image <image-uri> <human-label>
should_build_image() {
    local image_uri="$1"
    local label="${2:-Image}"

    if ! gcloud artifacts docker images describe "$image_uri" >/dev/null 2>&1; then
        return 0
    fi

    if [[ ${YES_FLAG:-} == "y" ]]; then
        return 0
    fi

    if [[ ${NO_FLAG:-} == "y" ]]; then
        echo "$label image already exists - not rebuilding (NO_FLAG set)"
        return 1
    fi

    if [[ ! -t 0 ]]; then
        echo "$label image already exists and there is no TTY to ask on - not rebuilding."
        echo "Pass -y to force a rebuild, or -n to make this the explicit choice."
        return 1
    fi

    read -r -p "$label image already exists. Rebuild? (y/n) " -n 1 REPLY
    echo
    [[ $REPLY =~ ^[Yy]$ ]]
}
