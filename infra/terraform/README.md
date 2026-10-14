# Inactive GCP deployment configuration

AWS ECS/Fargate is the active pilot deployment target. This Terraform root and the GKE manifests under `infra/k8s`, `infra/helm_charts`, and `infra/argocd` are no longer used by GitHub Actions.

These files are retained temporarily so existing GCP resources can be inspected and, if necessary, safely destroyed with their original Terraform state. Do not run `terraform apply` from this directory as part of the AWS pilot deployment.

Obsolete checked-in GCP plan binaries have been removed. The remaining checked-in GCP variable file is environment-specific; review it and the actual GCP state securely before using this Terraform root.

Before deleting this configuration:

1. Check the relevant GCP project for running GKE clusters, Cloud SQL instances, Pub/Sub topics/subscriptions, storage buckets, service accounts, and Artifact Registry repositories.
2. Identify the authoritative Terraform state and determine whether the resources are shared or still needed. The old Terraform backend and checked-in local configuration may not reflect the current state.
3. Back up and review the state securely; it can contain sensitive values. Do not commit Terraform state or plan files.
4. Get explicit approval from the GCP project owner before destroying any resources. Deleting this source directory does not delete cloud resources.
