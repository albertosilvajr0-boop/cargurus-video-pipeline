#!/bin/bash
# Deploy CarGurus Video Pipeline to Google Cloud Run + Firebase Hosting
#
# Prerequisites:
#   - firebase login (already done)
#   - gcloud auth login
#   - gcloud config set project cargurus-pipeline
#
# Usage:
#   ./deploy.sh              # Deploy everything
#   ./deploy.sh cloudrun     # Deploy Cloud Run only
#   ./deploy.sh firebase     # Deploy Firebase Hosting only

set -e

PROJECT_ID="autovideo"
REGION="us-central1"
SERVICE_NAME="autovideo"

echo "=== AutoVideo Deployment ==="
echo "Project: $PROJECT_ID"
echo "Region:  $REGION"
echo ""

deploy_cloudrun() {
    echo "--- Deploying to Cloud Run ---"

    # Enable required APIs
    gcloud services enable run.googleapis.com --project "$PROJECT_ID" 2>/dev/null || true
    gcloud services enable cloudbuild.googleapis.com --project "$PROJECT_ID" 2>/dev/null || true

    # Deploy from source (uses Cloud Build + Dockerfile)
    gcloud run deploy "$SERVICE_NAME" \
        --source . \
        --region "$REGION" \
        --project "$PROJECT_ID" \
        --allow-unauthenticated \
        --memory 1Gi \
        --cpu 2 \
        --timeout 300 \
        --min-instances 0 \
        --max-instances 3 \
        --set-env-vars "FLASK_DEBUG=0"

    echo ""
    echo "Cloud Run deployed! Set your API keys:"
    echo "  gcloud run services update $SERVICE_NAME --region $REGION --project $PROJECT_ID \\"
    echo "    --set-env-vars GOOGLE_API_KEY=your_key,OPENAI_API_KEY=your_key"
    echo ""
}

deploy_firebase() {
    echo "--- Deploying Firebase Hosting ---"
    firebase deploy --only hosting --project "$PROJECT_ID"
    echo ""
    echo "Firebase Hosting deployed!"
    echo ""
}

case "${1:-all}" in
    cloudrun)
        deploy_cloudrun
        ;;
    firebase)
        deploy_firebase
        ;;
    all)
        deploy_cloudrun
        deploy_firebase
        ;;
    *)
        echo "Usage: $0 [cloudrun|firebase|all]"
        exit 1
        ;;
esac

echo "=== Deployment complete ==="
echo ""
echo "Your site will be live at:"
echo "  https://$PROJECT_ID.web.app"
echo "  https://$PROJECT_ID.firebaseapp.com"
echo ""
echo "Cloud Run service URL:"
gcloud run services describe "$SERVICE_NAME" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --format "value(status.url)" 2>/dev/null || echo "  (deploy Cloud Run first)"
