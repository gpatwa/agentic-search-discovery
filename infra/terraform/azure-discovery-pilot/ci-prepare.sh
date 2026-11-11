#!/usr/bin/env bash
# Inputs the workflow derives at run time (nothing here is a secret):
# the image tags currently deployed, so an infrastructure run never changes what the deploy workflow shipped.
set -euo pipefail
rg="${PILOT_RESOURCE_GROUP:?PILOT_RESOURCE_GROUP is required}"
tag() { az containerapp show -g "$rg" -n "$1" --query "properties.template.containers[0].image" -o tsv | sed 's/.*://'; }
{
  echo "TF_VAR_api_image_tag=$(tag discovery-pilot-api)"
  echo "TF_VAR_web_image_tag=$(tag discovery-pilot-web)"
} >> "$GITHUB_ENV"
