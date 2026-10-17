variable "aws_region" {
  description = "AWS region for the deployment."
  type        = string
}

variable "github_repository" {
  description = "GitHub owner/repository allowed to deploy from its main branch using OIDC."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must use the owner/repository format."
  }
}

variable "github_oidc_provider_arn" {
  description = "ARN of the account's existing GitHub Actions OIDC provider."
  type        = string

  validation {
    condition     = can(regex("^arn:[^:]+:iam::[0-9]{12}:oidc-provider/token\\.actions\\.githubusercontent\\.com$", var.github_oidc_provider_arn))
    error_message = "github_oidc_provider_arn must be the IAM OIDC provider ARN for token.actions.githubusercontent.com."
  }
}

variable "name" {
  description = "Short name used to prefix AWS resources."
  type        = string
  default     = "sre-agent"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.name))
    error_message = "name must be 2-21 lowercase letters, numbers, or hyphens, and start with a letter."
  }
}

variable "vpc_cidr" {
  description = "IPv4 /16 CIDR block for the deployment VPC; the module allocates four /24 subnets from it."
  type        = string
  default     = "10.40.0.0/16"

  validation {
    condition     = can(cidrnetmask(var.vpc_cidr)) && can(regex("/16$", var.vpc_cidr))
    error_message = "vpc_cidr must be a valid IPv4 /16 CIDR block."
  }
}

variable "allowed_client_cidrs" {
  description = "CIDR blocks allowed to connect to the private API Gateway VPC endpoint on HTTPS."
  type        = list(string)

  validation {
    condition     = length(var.allowed_client_cidrs) > 0
    error_message = "At least one trusted caller CIDR must be supplied."
  }

  validation {
    condition     = alltrue([for cidr in var.allowed_client_cidrs : can(cidrnetmask(cidr))])
    error_message = "allowed_client_cidrs must contain only valid IPv4 CIDR blocks."
  }
}

variable "secret_arns" {
  description = "Environment variable name to Secrets Manager secret ARN mappings for application credentials."
  type        = map(string)
  default     = {}
  sensitive   = true
}

variable "observability_cidrs" {
  description = "Private CIDRs of in-network observability backends (Prometheus, Splunk, PrivateLink endpoints) the agent may reach."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for c in var.observability_cidrs : can(cidrnetmask(c)) && c != "0.0.0.0/0"])
    error_message = "observability_cidrs must be valid CIDRs and must not be 0.0.0.0/0."
  }
}

variable "observability_ports" {
  description = "TCP ports allowed to observability_cidrs."
  type        = list(number)
  default     = [443]
}

variable "llm_ami_id" {
  description = "GPU AMI with NVIDIA drivers, Docker and the NVIDIA container toolkit (e.g. a Deep Learning Base AMI) available in this account."
  type        = string
}

variable "llm_instance_type" {
  description = "GPU instance type for the inference server."
  type        = string
  default     = "g5.12xlarge"
}

variable "llm_root_volume_gb" {
  description = "Root volume size; must hold the model weights."
  type        = number
  default     = 300
}

variable "llm_image_tag" {
  description = "Tag of the inference-server image (vLLM or similar) pushed to the private LLM ECR repository."
  type        = string
  default     = "latest"
}

variable "llm_models_bucket" {
  description = "Private S3 bucket holding model weights, synced to the server at boot. Empty to skip."
  type        = string
  default     = ""
}

variable "db_engine_version" {
  description = "RDS Postgres engine version (needs pgvector)."
  type        = string
  default     = "16"
}

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.medium"
}

variable "db_storage_gb" {
  description = "RDS allocated storage in GB."
  type        = number
  default     = 50
}

variable "db_multi_az" {
  description = "Run RDS in multiple AZs."
  type        = bool
  default     = false
}

variable "kms_key_arns" {
  description = "Optional customer-managed KMS key ARNs used to encrypt secrets in secret_arns."
  type        = list(string)
  default     = []
}

variable "api_stage_name" {
  description = "API Gateway REST API stage name."
  type        = string
  default     = "prod"

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,128}$", var.api_stage_name))
    error_message = "api_stage_name may contain only letters, numbers, hyphens, and underscores."
  }
}

variable "desired_count" {
  description = "Number of ECS tasks. SQLite incident storage supports only one task."
  type        = number
  default     = 1

  validation {
    condition     = var.desired_count == 1
    error_message = "desired_count must remain 1 while application state is stored in SQLite."
  }
}

variable "application_environment" {
  description = "Additional non-secret application environment variables, such as connector URLs and model selection."
  type        = map(string)
  default     = {}

  validation {
    condition = alltrue([
      for name in keys(var.application_environment) :
      !contains([
        "APP_ENV",
        "API_HOST",
        "API_PORT",
        "LOG_LEVEL",
        "REQUIRE_GATEWAY_AUTH",
        "TRUSTED_IDENTITY_HEADER",
        "API_GATEWAY_STAGE_PREFIX",
        "ENABLE_SYNTHETIC_HEARTBEAT",
        "LLM_BASE_URL",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
      ], name)
    ])
    error_message = "application_environment cannot override deployment-managed security or runtime settings."
  }
}

variable "task_cpu" {
  description = "Fargate task CPU units."
  type        = number
  default     = 1024
}

variable "task_memory" {
  description = "Fargate task memory in MiB."
  type        = number
  default     = 2048
}

variable "log_retention_days" {
  description = "Retention period for ECS application logs."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Additional tags applied to AWS resources."
  type        = map(string)
  default     = {}
}
