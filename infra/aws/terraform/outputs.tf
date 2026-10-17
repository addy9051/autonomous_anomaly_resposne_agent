output "api_invoke_url" {
  description = "Private API Gateway stage URL. Reachable only from an allowed VPC endpoint path and with SigV4 authorization."
  value       = "https://${aws_api_gateway_rest_api.private.id}.execute-api.${data.aws_region.current.region}.amazonaws.com/${aws_api_gateway_stage.private.stage_name}"
}

output "api_vpc_endpoint_id" {
  description = "Interface VPC endpoint ID required for private API access."
  value       = aws_vpc_endpoint.execute_api.id
}

output "caller_policy_arns" {
  description = "Attach the relevant managed policy to existing trusted caller roles."
  value       = { for name, policy in aws_iam_policy.caller : name => policy.arn }
}

output "ecs_cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.api.name
}

output "ecs_service_name" {
  description = "ECS service name."
  value       = aws_ecs_service.api.name
}

output "ecr_repository_name" {
  description = "Immutable-tag ECR repository name for the GitHub Actions build."
  value       = aws_ecr_repository.api.name
}

output "ecr_repository_url" {
  description = "ECR repository URI."
  value       = aws_ecr_repository.api.repository_url
}

output "application_log_group" {
  description = "CloudWatch log group for the application container."
  value       = aws_cloudwatch_log_group.api.name
}

output "task_role_arn" {
  description = "Application task role; intentionally has no AWS permissions by default."
  value       = aws_iam_role.task.arn
}

output "llm_server_private_ip" {
  description = "Private IP of the self-hosted LLM server (used as LLM_BASE_URL)."
  value       = aws_instance.llm.private_ip
}

output "llm_ecr_repository_url" {
  description = "Push the inference-server image here."
  value       = aws_ecr_repository.llm.repository_url
}

output "db_endpoint" {
  description = "Private Postgres endpoint. Apply knowledge_base/migrations/*.sql once from inside the VPC."
  value       = aws_db_instance.memory.address
}

output "github_actions_role_arn" {
  description = "Configure as the AWS_ROLE_TO_ASSUME GitHub Actions secret."
  value       = aws_iam_role.github_actions.arn
}
