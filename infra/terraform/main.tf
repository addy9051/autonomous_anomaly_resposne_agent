# ═══════════════════════════════════════════════════════════════
#  Terraform — GCP Infrastructure (Production)
#  Payment Reliability Agent System
# ═══════════════════════════════════════════════════════════════

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  backend "gcs" {
    bucket = "anomaly-agent-tf-state"
    prefix = "terraform/state"
  }
}

# ─── Variables ───────────────────────────────────────────────

variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "GCP Region"
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Environment (dev, staging, prod)"
  type        = string
  default     = "dev"
}

# ─── Provider ────────────────────────────────────────────────

provider "google" {
  project = var.project_id
  region  = var.region
}

# ─── Enable APIs ─────────────────────────────────────────────

resource "google_project_service" "apis" {
  for_each = toset([
    "container.googleapis.com",      # GKE
    "aiplatform.googleapis.com",     # Vertex AI
    "pubsub.googleapis.com",         # Pub/Sub
    "spanner.googleapis.com",        # Cloud Spanner
    "artifactregistry.googleapis.com", # Artifact Registry
    "cloudbuild.googleapis.com",     # Cloud Build
    "run.googleapis.com",            # Cloud Run
    "monitoring.googleapis.com",     # Cloud Monitoring
    "logging.googleapis.com",        # Cloud Logging
    "secretmanager.googleapis.com",  # Secret Manager
  ])

  service            = each.value
  disable_on_destroy = false
}

# ─── GKE Autopilot Cluster ───────────────────────────────────

resource "google_container_cluster" "agents" {
  name     = "anomaly-agents-${var.environment}"
  location = var.region

  enable_autopilot = true

  ip_allocation_policy {}

  release_channel {
    channel = "REGULAR"
  }

  depends_on = [google_project_service.apis]
}

# ─── Pub/Sub Topics ──────────────────────────────────────────

resource "google_pubsub_topic" "anomaly_events" {
  name = "anomaly-events-${var.environment}"
}

resource "google_pubsub_topic" "action_results" {
  name = "action-results-${var.environment}"
}

resource "google_pubsub_topic" "feedback_events" {
  name = "feedback-events-${var.environment}"
}

# ─── Pub/Sub Subscriptions ───────────────────────────────────

resource "google_pubsub_subscription" "diagnosis_sub" {
  name  = "diagnosis-agent-sub-${var.environment}"
  topic = google_pubsub_topic.anomaly_events.name

  ack_deadline_seconds = 30

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
}

resource "google_pubsub_subscription" "action_sub" {
  name  = "action-agent-sub-${var.environment}"
  topic = google_pubsub_topic.action_results.name

  ack_deadline_seconds = 30
}

# ─── Artifact Registry ──────────────────────────────────────

resource "google_artifact_registry_repository" "agents" {
  location      = var.region
  repository_id = "anomaly-agents-${var.environment}"
  format        = "DOCKER"
}

# ─── Service Account for Agents ──────────────────────────────

resource "google_service_account" "agent_runtime" {
  account_id   = "agent-runtime-${var.environment}"
  display_name = "Agent Runtime Service Account"
}

resource "google_project_iam_member" "agent_roles" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/pubsub.publisher",
    "roles/pubsub.subscriber",
    "roles/spanner.databaseUser",
    "roles/secretmanager.secretAccessor",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.agent_runtime.email}"
}

# ─── Cloud Armor WAF Policies ────────────────────────────────

resource "google_compute_security_policy" "agent_waf" {
  name        = "agent-waf-${var.environment}"
  description = "WAF policy securing external-facing Agent API gateways"

  rule {
    action   = "allow"
    priority = "2147483647"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    description = "Default allow rule"
  }

  rule {
    action   = "deny(403)"
    priority = "1000"
    match {
      expr {
        expression = "evaluatePreconfiguredExpr('sqli-v33-stable')"
      }
    }
    description = "Block SQL injection attacks"
  }
}

# NOTE: Access Context Manager and VPC Service Perimeters require a GCP Organization.
# Since this is a standalone account, these resources are commented out.
# ─── VPC Service Controls (ORGO ONLY) ─────────────────────────
/*
resource "google_access_context_manager_access_policy" "access_policy" {
  parent = "organizations/123456789012" # Required parent for VPC SC
  title  = "agent-access-policy-${var.environment}"
}

resource "google_access_context_manager_service_perimeter" "agent_perimeter" {
  parent         = "accessPolicies/${google_access_context_manager_access_policy.access_policy.name}"
  name           = "accessPolicies/${google_access_context_manager_access_policy.access_policy.name}/servicePerimeters/agent_perimeter"
  title          = "Agent Production Perimeter"
  perimeter_type = "PERIMETER_TYPE_REGULAR"

  status {
    restricted_services = [
      "spanner.googleapis.com",
      "aiplatform.googleapis.com",
      "pubsub.googleapis.com"
    ]
    
    resources = [
      "projects/${var.project_id}"
    ]
  }
}
*/

# ─── Outputs ─────────────────────────────────────────────────

output "gke_cluster_name" {
  value = google_container_cluster.agents.name
}

output "agent_service_account" {
  value = google_service_account.agent_runtime.email
}

output "artifact_registry" {
  value = google_artifact_registry_repository.agents.id
}
