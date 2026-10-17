# AWS Pilot Deployment and Security

The active (and only) deployment target is AWS. The old GCP/GKE Terraform, Kubernetes manifests, Helm chart and ArgoCD application were removed; if GCP resources were ever created from them, inspect and destroy them from the original Terraform state, since deleting the source does not delete cloud resources.

## AWS Terraform deployment

The AWS implementation is a separate Terraform root at [`infra/aws/terraform`](../infra/aws/terraform/).

The stack creates:

- An **air-gapped VPC**: private subnets only, with no internet gateway, NAT gateway or default route. AWS services (ECR, Logs, Secrets Manager, S3, execute-api) are reached through VPC endpoints. Security groups have no `0.0.0.0/0` rule: the agent task may reach only the AWS endpoints, S3, EFS, the LLM server, Postgres, and the private `observability_cidrs` you list.
- A **self-hosted LLM server**: a GPU EC2 instance (`llm_ami_id`, `llm_instance_type`) in a private subnet running an OpenAI-compatible inference image from a private ECR repository, with weights synced from a private S3 bucket. Its security group accepts traffic only from the agent task. The task receives `LLM_BASE_URL` pointing at it. You must build and push the inference image and upload the weights; Terraform does not.
- **RDS Postgres 16** (encrypted, private, deletion-protected, managed master password in Secrets Manager) for episodic memory and the RAG knowledge base. The password is injected as `POSTGRES_PASSWORD`. Apply `knowledge_base/migrations/*.sql` once from inside the VPC (the pgvector extension is available on RDS).
- A **private API Gateway REST API** exposed through an `execute-api` interface VPC endpoint. The API resource policy restricts calls to that VPC endpoint and the deploying AWS account; the endpoint security group accepts HTTPS only from `allowed_client_cidrs`.
- `AWS_IAM` authorization on the root and catch-all API methods. API Gateway maps its authenticated `context.identity.userArn` to `X-Authenticated-User`, overwriting any same-named incoming header.
- A VPC Link v2 with a dedicated security group, an internal ALB that accepts traffic only from that VPC Link, and an ECS/Fargate service whose application port accepts traffic only from the ALB.
- Separate managed invoke policies for SRE responders, telemetry producers, knowledge-base administrators, and platform health checks. These policies contain method- and route-specific `execute-api:Invoke` permissions; attach only the relevant policy to an existing, trusted IAM role.
- An ECR repository that retains build-SHA tags and a mutable `pilot` deployment tag. ECS uses the `pilot` tag and CI forces a new deployment after updating it, avoiding Terraform drift on every application release.
- An encrypted EFS volume mounted at `/app/data` through a scoped access point. It retains the SQLite incident store and local model files across task replacements; ECS is constrained to one task because SQLite is not safe for multi-task concurrent use.
- A task execution role limited to the configured ECR repository, application log stream, supplied Secrets Manager ARNs, and optional customer KMS keys. The application task role can access only its EFS access point by default.
- A GitHub Actions OIDC role restricted to the selected repository's `main` branch. It can push to the application ECR repository and update only the configured ECS service.

### Prepare and deploy

1. Ensure the AWS account has the standard GitHub Actions OIDC provider (`token.actions.githubusercontent.com`) and configure an AWS VPC/client network from which the private API can be reached. Use the provider ARN in `github_oidc_provider_arn`.
2. Copy `terraform.tfvars.example` to `terraform.tfvars` in `infra/aws/terraform` and configure the AWS region, GitHub repository, OIDC provider ARN, narrow trusted caller CIDRs, `llm_ami_id` (a GPU AMI with NVIDIA drivers and Docker) and `observability_cidrs`. Store credentials as Secrets Manager secrets; put their ARNs in `secret_arns`, never their values. No LLM provider key is needed.
3. Initialize Terraform and create the ECR repository as a bootstrap step:

   ```powershell
   Set-Location infra\aws\terraform
   terraform init
   terraform apply -target=aws_ecr_repository.api
   ```

4. Push a bootstrap image tagged `pilot` to the new ECR repository. For example, from the repository root in PowerShell:

   ```powershell
   $AccountId = aws sts get-caller-identity --query Account --output text
   $Region = "us-east-1"
   $Repository = "sre-agent"
   $Registry = "$AccountId.dkr.ecr.$Region.amazonaws.com"
   aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $Registry
   docker build -f infra/docker/Dockerfile -t "${Registry}/${Repository}:pilot" .
   docker push "${Registry}/${Repository}:pilot"
   ```

5. Configure `secret_arns`, `kms_key_arns`, and `application_environment` in `terraform.tfvars`. The pilot defaults to the simulator. To use a real adapter, set `TELEMETRY_CONNECTOR_MODE` to `prometheus`, `datadog`, or `splunk`, set its non-secret endpoint/settings in `application_environment`, and put API tokens in Secrets Manager using the environment variable names in `shared/config.py`.
6. Run `terraform fmt -check`, `terraform validate`, and `terraform plan -out=tfplan`; review the plan before `terraform apply tfplan`. It creates billable EC2 GPU, RDS, EFS, interface endpoint, ALB, API Gateway, and ECS resources. Configure encrypted remote state and locking before team use. This stack has not been applied to AWS.
7. Create/attach caller IAM identities through your organization's federation or IAM Identity Center; attach the smallest applicable `caller_policy_arns` policy from Terraform outputs. Do not create long-lived browser keys.
8. Set these GitHub repository variables from Terraform outputs: `AWS_DEPLOY_ENABLED=true`, `AWS_REGION`, `AWS_ECR_REPOSITORY` (`ecr_repository_name`), `AWS_ECS_CLUSTER` (`ecs_cluster_name`), and `AWS_ECS_SERVICE` (`ecs_service_name`). Set `AWS_ROLE_TO_ASSUME` as a GitHub Actions secret to `github_actions_role_arn`. Deployment stays disabled until `AWS_DEPLOY_ENABLED` is set. After configuration, pushes to `main` run tests, build the image, push both the commit SHA and `pilot` tags, force an ECS deployment, and wait for the service to become stable. Pull requests do not request AWS credentials.
9. From an authorized VPC client, sign requests to the private invoke URL from `api_invoke_url` with SigV4. Confirm an allowed route succeeds, a disallowed route is denied, a request from outside the allowed network cannot connect, and a supplied `X-Authenticated-User` value is replaced by the IAM principal ARN.

The API Gateway stage is included in private integration request paths. The ECS task receives `API_GATEWAY_STAGE_PREFIX` from Terraform, and the FastAPI middleware strips that configured prefix before route matching. Keep `api_stage_name` and the task environment setting aligned if the integration is customized.

### Readiness constraints

- The pilot uses one ECS task and EFS-backed SQLite to retain its state between task replacements. Do not scale beyond one task; move incident/approval persistence to a database designed for concurrent clients before scaling.
- The API task role is limited to its EFS access point. Add narrow workload permissions only when the application actually uses a specific AWS API.
- The ECR `pilot` tag is mutable so CI can update it and force ECS to pull the new image without Terraform drifting on every release. Commit-SHA tags are also pushed for traceability but are not registry-enforced as immutable; move to digest-pinned deployments before production.
- The air-gapped VPC means SaaS vendors (Datadog, Splunk Cloud, PagerDuty, Slack, GitHub) are unreachable unless you provide a PrivateLink endpoint or private route and list its CIDR in `observability_cidrs`. Interface endpoint and EC2 GPU hourly charges apply. The LLM server is a single instance with no failover.
- This stack does **not** authorize live remediation on its own. Keep live execution disabled until target/service allowlists, live-state preconditions, durable approval/audit records, and independent verification are implemented and tested.
- There is no automatically configured observability vendor account. Pilot ingestion remains simulator-backed until `application_environment` and the matching connector secret ARNs are configured.
- The Terraform stack deploys the FastAPI backend only. The Next.js dashboard does not yet have an AWS hosting/authentication configuration; the private API is reachable only from an authorized VPC client using SigV4.
- API Gateway VPC Link v2 support varies by AWS region. Confirm regional availability before applying.

The Terraform above configures these controls. The application expects a trusted caller identity header; that header is trustworthy only because the API origin is reachable exclusively through the configured gateway and the gateway overwrites client-supplied values.

## Request and identity path

```text
SRE or internal service
  -> API Gateway with AWS_IAM authorization (SigV4)
  -> private VPC integration / private load balancer
  -> FastAPI, with the authenticated principal mapped to X-Authenticated-User
```

The Terraform-managed API Gateway:

1. Requires `AWS_IAM` authorization on every mutating method and on any read method exposing sensitive incident or operational data.
2. Uses a resource policy that limits invocation to approved AWS accounts, roles, and network paths.
3. Maps a gateway-authenticated identity value (for example, the IAM user or assumed-role ARN) to the `X-Authenticated-User` integration header. Do not accept an identity value supplied by the client.
4. Replaces or removes any incoming `X-Authenticated-User` header before forwarding the request.

The FastAPI origin has no public ingress. It is placed in private subnets behind the private integration; security groups restrict the ALB to the VPC Link and the ECS task to the ALB. A VPC by itself does not authenticate callers. Do not add any alternate path that can reach FastAPI, or callers could forge the identity header.

Set `APP_ENV=production` in every deployed environment. The application defaults to production and rejects sensitive requests that lack the configured trusted identity header outside development. Terraform configures `TRUSTED_IDENTITY_HEADER` consistently with the gateway mapping and sets `REQUIRE_GATEWAY_AUTH=true`.

## Least-privilege access

Attach IAM permissions to caller roles for only the API Gateway route ARNs and HTTP methods they need. Use separate roles for human responders, telemetry/event producers, administrators, and the agent workload. Example responsibility split:

| Caller role | Example permitted operations |
|---|---|
| SRE responder | Read assigned incident data; approve or reject pending proposals |
| Telemetry producer | Submit events only |
| Demo operator | Run simulations only; never grant live action execution |
| Knowledge-base administrator | Seed or administer runbooks |
| Agent workload | Access only configured telemetry, persistence, and automation integrations |

Configure API Gateway method permissions along these lines:

| Method and route | Minimum caller role |
|---|---|
| `POST /api/v1/events/process` | Telemetry producer or authorized operator |
| `POST /api/v1/demo/run`, `POST /api/v1/demo/scenarios/{scenario_name}/run`, `POST /api/v1/stream/start` | Demo operator, restricted to non-production |
| `POST /api/v1/incidents/{incident_id}/approve` | SRE responder |
| `POST /api/v1/knowledge/seed` | Knowledge-base administrator |
| `GET /api/v1/incidents*`, `GET /api/v1/telemetry/recent`, `GET /api/v1/feedback/*` | Authorized SRE / operations roles |
| `GET /health`, `GET /api/v1/health/detailed` | Platform health-check role; expose only the minimum needed |

API Gateway IAM authorization authenticates the caller and gates route access. The deterministic in-application policy remains responsible for deciding whether a remediation is allowed. The current policy validates the action catalog, parameters, and environment but does not yet enforce per-service/target allowlists or live-state preconditions; keep production live execution disabled until those checks are implemented and tested. Do not give the model AWS credentials or allow a route permission to bypass that policy.

## Approval integrity

Approval requests contain only the decision and optional reason. The server loads the pending action and exact parameters from the persisted incident proposal, verifies its proposal hash, and atomically consumes the pending approval. The authenticated identity is taken from the trusted gateway context, never from the request body. A changed action or parameter set requires a new proposal and approval.

The proposal hash detects accidental or unauthorized mutation of the stored action data, but it is not a replacement for database access controls, encrypted backups, audit retention, or protection of the incident store itself.

## Local development

`APP_ENV=development` permits local unauthenticated calls when `REQUIRE_GATEWAY_AUTH` is false and no `API_AUTH_TOKEN` is configured. This behavior is for local development only. Setting `API_AUTH_TOKEN` enables the development `X-API-Key` fallback; requests using that shared key are attributed to a generic development identity, not an individual responder.

Synthetic heartbeat processing is enabled automatically only in development. Enable it elsewhere only for a deliberate test environment using `ENABLE_SYNTHETIC_HEARTBEAT=true`. Compose password fallbacks are development-only; provide unique secrets through environment/secret management and do not expose the development stack to untrusted networks.

## Deployment verification checklist

- [ ] Confirm FastAPI has no public IP or public load-balancer path after deployment.
- [ ] Confirm API Gateway requires SigV4/IAM authorization and its policies restrict callers to the expected AWS account and VPC endpoint.
- [ ] Attach only the generated least-privilege caller policy needed for each role.
- [ ] Confirm the gateway overwrites the identity header using authenticated context.
- [ ] Confirm security groups and routes prevent bypassing the gateway.
- [ ] Confirm the deployed service sets `APP_ENV=production` and requires gateway identity.
- [ ] The agent workload role is separate from human caller roles and narrowly scoped.
- [ ] Live action execution remains disabled until approval binding, policy rechecks, and independent verification have passed deployment tests.
