#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# Google Cloud Run 1-Command Deployment Script
# True Serverless ($0/mo idle cost, 2M free requests/mo)
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

PROJECT_ID=${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project)}
REGION=${CLOUD_RUN_REGION:-"us-central1"}
SERVICE_NAME="sre-agent-saas"

echo "Deploying ${SERVICE_NAME} to Google Cloud Run in ${REGION} (Project: ${PROJECT_ID})..."

# 1. Build and push image to Google Artifact Registry
gcloud builds submit --tag "gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest" .

# 2. Deploy to Cloud Run (scales to 0 instances when idle)
gcloud run deploy "${SERVICE_NAME}" \
  --image "gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest" \
  --platform managed \
  --region "${REGION}" \
  --allow-unauthenticated \
  --min-instances 0 \
  --max-instances 10 \
  --memory 1Gi \
  --cpu 1 \
  --set-env-vars "APP_ENV=production,LLM_PROVIDER=openai,LLM_MODEL=gpt-4o-mini"

echo "Deployment complete! Cloud Run URL:"
gcloud run services describe "${SERVICE_NAME}" --platform managed --region "${REGION}" --format 'value(status.url)'
