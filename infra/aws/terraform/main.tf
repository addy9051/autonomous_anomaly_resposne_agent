data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_region" "current" {}

data "aws_caller_identity" "current" {}

locals {
  availability_zones = slice(data.aws_availability_zones.available.names, 0, 2)
  private_subnet_cidrs = [
    for index in range(2) : cidrsubnet(var.vpc_cidr, 8, index + 10)
  ]
  llm_port   = 8001
  embed_port = 8002
  egress_targets = {
    for pair in setproduct(var.observability_cidrs, var.observability_ports) :
    "${pair[0]}:${pair[1]}" => { cidr = pair[0], port = pair[1] }
  }
  caller_route_access = {
    sre_responder = [
      "GET /api/v1/incidents",
      "GET /api/v1/incidents/*",
      "GET /api/v1/episodes/*",
      "GET /api/v1/telemetry/recent",
      "GET /api/v1/ingestion/status",
      "GET /api/v1/actions/tiers",
      "POST /api/v1/incidents/*/approve",
    ]
    telemetry_producer = [
      "POST /api/v1/events/process",
      "POST /api/v1/alerts/alertmanager",
    ]
    connector_admin = [
      "GET /api/v1/connectors",
      "PUT /api/v1/connectors/selection",
      "PUT /api/v1/connectors/*/credentials",
      "POST /api/v1/connectors/*/test",
      "POST /api/v1/connectors/activate",
      "POST /api/v1/connectors/deactivate",
    ]
    knowledge_base_admin = [
      "GET /api/v1/knowledge/search",
      "GET /api/v1/knowledge/health",
      "POST /api/v1/knowledge/seed",
    ]
    platform_health = [
      "GET /health",
      "GET /api/v1/health/detailed",
      "GET /api/v1/status",
    ]
  }
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${var.name}-vpc"
  }
}

resource "aws_subnet" "private" {
  count                   = 2
  vpc_id                  = aws_vpc.this.id
  availability_zone       = local.availability_zones[count.index]
  cidr_block              = local.private_subnet_cidrs[count.index]
  map_public_ip_on_launch = false

  tags = {
    Name = "${var.name}-private-${count.index + 1}"
    Tier = "private-application"
  }
}

# No internet gateway, NAT gateway or default route: the VPC is air-gapped. AWS services are
# reached only through the VPC endpoints below.
resource "aws_route_table" "private" {
  count  = 2
  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.name}-private-${count.index + 1}"
  }
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

resource "aws_security_group" "api_endpoint" {
  name        = "${var.name}-api-endpoint"
  description = "HTTPS access to the private execute-api VPC endpoint from trusted caller networks."
  vpc_id      = aws_vpc.this.id

  egress = []

  tags = {
    Name = "${var.name}-api-endpoint"
  }
}

resource "aws_vpc_security_group_ingress_rule" "api_endpoint_https" {
  for_each = toset(var.allowed_client_cidrs)

  security_group_id = aws_security_group.api_endpoint.id
  description       = "HTTPS from an explicitly trusted caller CIDR."
  cidr_ipv4         = each.value
  from_port         = 443
  ip_protocol       = "tcp"
  to_port           = 443
}

resource "aws_security_group" "vpc_link" {
  name        = "${var.name}-vpc-link"
  description = "Allows API Gateway VPC Link traffic only to the private ALB."
  vpc_id      = aws_vpc.this.id

  egress = []

  tags = {
    Name = "${var.name}-vpc-link"
  }
}

resource "aws_security_group" "alb" {
  name        = "${var.name}-alb"
  description = "Internal application load balancer; accepts traffic only from the API Gateway VPC Link."
  vpc_id      = aws_vpc.this.id

  egress = []

  tags = {
    Name = "${var.name}-alb"
  }
}

resource "aws_vpc_security_group_egress_rule" "vpc_link_to_alb" {
  security_group_id            = aws_security_group.vpc_link.id
  referenced_security_group_id = aws_security_group.alb.id
  description                  = "Private API integration to the internal ALB."
  from_port                    = 80
  ip_protocol                  = "tcp"
  to_port                      = 80
}

resource "aws_vpc_security_group_ingress_rule" "alb_from_vpc_link" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.vpc_link.id
  description                  = "Only API Gateway VPC Link ENIs can reach the ALB."
  from_port                    = 80
  ip_protocol                  = "tcp"
  to_port                      = 80
}

resource "aws_security_group" "task" {
  name        = "${var.name}-task"
  description = "Private application tasks; ingress only from the internal ALB."
  vpc_id      = aws_vpc.this.id

  egress = []

  tags = {
    Name = "${var.name}-task"
  }
}

resource "aws_security_group" "aws_endpoints" {
  name        = "${var.name}-aws-endpoints"
  description = "Allows application tasks to access private AWS service endpoints over HTTPS."
  vpc_id      = aws_vpc.this.id

  egress = []

  tags = {
    Name = "${var.name}-aws-endpoints"
  }
}

resource "aws_vpc_security_group_ingress_rule" "aws_endpoints_from_task" {
  security_group_id            = aws_security_group.aws_endpoints.id
  referenced_security_group_id = aws_security_group.task.id
  description                  = "HTTPS from application tasks."
  from_port                    = 443
  ip_protocol                  = "tcp"
  to_port                      = 443
}

resource "aws_vpc_security_group_ingress_rule" "aws_endpoints_from_llm" {
  security_group_id            = aws_security_group.aws_endpoints.id
  referenced_security_group_id = aws_security_group.llm.id
  description                  = "HTTPS from the LLM server (ECR image pull)."
  from_port                    = 443
  ip_protocol                  = "tcp"
  to_port                      = 443
}

resource "aws_vpc_security_group_egress_rule" "alb_to_task" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.task.id
  description                  = "ALB health checks and application traffic."
  from_port                    = 8000
  ip_protocol                  = "tcp"
  to_port                      = 8000
}

resource "aws_vpc_security_group_ingress_rule" "task_from_alb" {
  security_group_id            = aws_security_group.task.id
  referenced_security_group_id = aws_security_group.alb.id
  description                  = "Application traffic from the internal ALB only."
  from_port                    = 8000
  ip_protocol                  = "tcp"
  to_port                      = 8000
}

data "aws_prefix_list" "s3" {
  name = "com.amazonaws.${data.aws_region.current.region}.s3"
}

# The task can reach only: AWS endpoints, S3 (ECR image layers), EFS, the LLM server, Postgres,
# and explicitly listed private observability CIDRs. There is no 0.0.0.0/0 rule.
resource "aws_vpc_security_group_egress_rule" "task_to_endpoints" {
  security_group_id            = aws_security_group.task.id
  referenced_security_group_id = aws_security_group.aws_endpoints.id
  description                  = "HTTPS to AWS interface endpoints (ECR, Logs, Secrets Manager)."
  from_port                    = 443
  ip_protocol                  = "tcp"
  to_port                      = 443
}

resource "aws_vpc_security_group_egress_rule" "task_to_s3" {
  security_group_id = aws_security_group.task.id
  description       = "HTTPS to S3 through the gateway endpoint (ECR image layers)."
  prefix_list_id    = data.aws_prefix_list.s3.id
  from_port         = 443
  ip_protocol       = "tcp"
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "task_to_llm" {
  security_group_id            = aws_security_group.task.id
  referenced_security_group_id = aws_security_group.llm.id
  description                  = "Self-hosted LLM inference server."
  from_port                    = local.llm_port
  ip_protocol                  = "tcp"
  to_port                      = local.embed_port
}

resource "aws_vpc_security_group_egress_rule" "task_to_db" {
  security_group_id            = aws_security_group.task.id
  referenced_security_group_id = aws_security_group.db.id
  description                  = "Episodic memory and knowledge base Postgres."
  from_port                    = 5432
  ip_protocol                  = "tcp"
  to_port                      = 5432
}

resource "aws_vpc_security_group_egress_rule" "task_to_observability" {
  for_each = local.egress_targets

  security_group_id = aws_security_group.task.id
  description       = "Private observability backend (Prometheus/Datadog/Splunk via PrivateLink or VPC peering)."
  cidr_ipv4         = each.value.cidr
  from_port         = each.value.port
  ip_protocol       = "tcp"
  to_port           = each.value.port
}

resource "aws_vpc_endpoint" "execute_api" {
  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.execute-api"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.api_endpoint.id]
  private_dns_enabled = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = "*"
      Action    = "execute-api:Invoke"
      Resource  = "arn:aws:execute-api:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:*/*/*/*"
    }]
  })

  tags = {
    Name = "${var.name}-execute-api"
  }
}

resource "aws_vpc_endpoint" "ecr_api" {
  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.ecr.api"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.aws_endpoints.id]
  private_dns_enabled = true
}

resource "aws_vpc_endpoint" "ecr_dkr" {
  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.ecr.dkr"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.aws_endpoints.id]
  private_dns_enabled = true
}

resource "aws_vpc_endpoint" "logs" {
  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.logs"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.aws_endpoints.id]
  private_dns_enabled = true
}

resource "aws_vpc_endpoint" "secrets_manager" {
  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.aws_endpoints.id]
  private_dns_enabled = true
}

resource "aws_vpc_endpoint" "ssm" {
  for_each = toset(["ssm", "ssmmessages", "ec2messages"])

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.aws_endpoints.id]
  private_dns_enabled = true
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = aws_route_table.private[*].id
}

resource "aws_lb" "api" {
  name               = "${var.name}-internal"
  internal           = true
  load_balancer_type = "application"
  subnets            = aws_subnet.private[*].id
  security_groups    = [aws_security_group.alb.id]

  enable_deletion_protection = true

  tags = {
    Name = "${var.name}-internal"
  }
}

resource "aws_lb_target_group" "api" {
  name        = "${var.name}-api"
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.this.id

  health_check {
    enabled             = true
    path                = "/health"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = {
    Name = "${var.name}-api"
  }
}

resource "aws_lb_listener" "api" {
  load_balancer_arn = aws_lb.api.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_apigatewayv2_vpc_link" "api" {
  name               = "${var.name}-private-integration"
  security_group_ids = [aws_security_group.vpc_link.id]
  subnet_ids         = aws_subnet.private[*].id

  tags = {
    Name = "${var.name}-private-integration"
  }
}

resource "aws_api_gateway_rest_api" "private" {
  name        = "${var.name}-private-api"
  description = "Private IAM-authenticated API for the SRE agent."
  endpoint_configuration {
    types            = ["PRIVATE"]
    vpc_endpoint_ids = [aws_vpc_endpoint.execute_api.id]
  }

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = "*"
      Action    = "execute-api:Invoke"
      Resource  = "execute-api:/*"
      Condition = {
        StringEquals = {
          "aws:SourceVpce"       = aws_vpc_endpoint.execute_api.id
          "aws:PrincipalAccount" = data.aws_caller_identity.current.account_id
        }
      }
    }]
  })

  tags = {
    Name = "${var.name}-private-api"
  }
}

resource "aws_api_gateway_resource" "proxy" {
  rest_api_id = aws_api_gateway_rest_api.private.id
  parent_id   = aws_api_gateway_rest_api.private.root_resource_id
  path_part   = "{proxy+}"
}

resource "aws_api_gateway_method" "root" {
  rest_api_id   = aws_api_gateway_rest_api.private.id
  resource_id   = aws_api_gateway_rest_api.private.root_resource_id
  http_method   = "ANY"
  authorization = "AWS_IAM"
}

resource "aws_api_gateway_method" "proxy" {
  rest_api_id   = aws_api_gateway_rest_api.private.id
  resource_id   = aws_api_gateway_resource.proxy.id
  http_method   = "ANY"
  authorization = "AWS_IAM"

  request_parameters = {
    "method.request.path.proxy" = true
  }
}

resource "aws_api_gateway_integration" "root" {
  rest_api_id             = aws_api_gateway_rest_api.private.id
  resource_id             = aws_api_gateway_rest_api.private.root_resource_id
  http_method             = aws_api_gateway_method.root.http_method
  type                    = "HTTP_PROXY"
  integration_http_method = "ANY"
  connection_type         = "VPC_LINK"
  connection_id           = aws_apigatewayv2_vpc_link.api.id
  integration_target      = aws_lb.api.arn
  uri                     = "http://${aws_lb.api.dns_name}/"

  request_parameters = {
    "integration.request.header.X-Authenticated-User" = "context.identity.userArn"
  }
}

resource "aws_api_gateway_integration" "proxy" {
  rest_api_id             = aws_api_gateway_rest_api.private.id
  resource_id             = aws_api_gateway_resource.proxy.id
  http_method             = aws_api_gateway_method.proxy.http_method
  type                    = "HTTP_PROXY"
  integration_http_method = "ANY"
  connection_type         = "VPC_LINK"
  connection_id           = aws_apigatewayv2_vpc_link.api.id
  integration_target      = aws_lb.api.arn
  uri                     = "http://${aws_lb.api.dns_name}/{proxy}"

  request_parameters = {
    "integration.request.path.proxy"                  = "method.request.path.proxy"
    "integration.request.header.X-Authenticated-User" = "context.identity.userArn"
  }
}

resource "aws_api_gateway_deployment" "private" {
  rest_api_id = aws_api_gateway_rest_api.private.id

  triggers = {
    redeployment = filesha1("${path.module}/main.tf")
  }

  depends_on = [
    aws_api_gateway_integration.root,
    aws_api_gateway_integration.proxy,
  ]

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "private" {
  rest_api_id   = aws_api_gateway_rest_api.private.id
  deployment_id = aws_api_gateway_deployment.private.id
  stage_name    = var.api_stage_name

  tags = {
    Name = "${var.name}-${var.api_stage_name}"
  }
}

resource "aws_iam_policy" "caller" {
  for_each = local.caller_route_access

  name        = "${var.name}-${replace(each.key, "_", "-")}-invoke"
  description = "Least-privilege API Gateway invoke permissions for ${replace(each.key, "_", " ")}."
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "execute-api:Invoke"
      Resource = [
        for route in each.value :
        "${aws_api_gateway_rest_api.private.execution_arn}/${var.api_stage_name}/${split(" ", route)[0]}/${trimprefix(split(" ", route)[1], "/")}"
      ]
    }]
  })

  tags = {
    Name = "${var.name}-${replace(each.key, "_", "-")}-invoke"
  }
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${var.name}/api"
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${var.name}-api"
  }
}

resource "aws_ecr_repository" "api" {
  name                 = var.name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = {
    Name = "${var.name}-api"
  }
}

resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Retain the 30 most recently pushed images."
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 30
      }
      action = {
        type = "expire"
      }
    }]
  })
}

resource "aws_iam_role" "execution" {
  name = "${var.name}-task-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "execution" {
  name = "${var.name}-task-execution"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Effect = "Allow"
          Action = [
            "ecr:GetAuthorizationToken",
          ]
          Resource = "*"
        },
        {
          Effect = "Allow"
          Action = [
            "ecr:BatchCheckLayerAvailability",
            "ecr:BatchGetImage",
            "ecr:GetDownloadUrlForLayer",
          ]
          Resource = aws_ecr_repository.api.arn
        },
        {
          Effect = "Allow"
          Action = [
            "logs:CreateLogStream",
            "logs:PutLogEvents",
          ]
          Resource = "${aws_cloudwatch_log_group.api.arn}:*"
        },
      ],
      length(var.secret_arns) > 0 ? [
        {
          Effect   = "Allow"
          Action   = ["secretsmanager:GetSecretValue"]
          Resource = values(var.secret_arns)
        },
      ] : [],
      length(var.kms_key_arns) > 0 ? [
        {
          Effect   = "Allow"
          Action   = ["kms:Decrypt"]
          Resource = var.kms_key_arns
          Condition = {
            StringEquals = {
              "kms:ViaService" = "secretsmanager.${data.aws_region.current.region}.amazonaws.com"
            }
          }
        },
      ] : [],
    )
  })
}

resource "aws_iam_role" "task" {
  name = "${var.name}-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

# ECS Exec (via SSM) lets you open a shell in the task to run DB migrations; no inbound port needed.
resource "aws_iam_role_policy" "task_exec" {
  name = "${var.name}-task-exec"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ssmmessages:CreateControlChannel",
        "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel",
        "ssmmessages:OpenDataChannel",
      ]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "execution_llm_key" {
  name = "${var.name}-task-execution-llm-key"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = var.llm_api_key_secret_arn
    }]
  })
}

resource "aws_iam_role_policy" "task_efs" {
  name = "${var.name}-task-efs"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "elasticfilesystem:ClientMount",
        "elasticfilesystem:ClientWrite",
      ]
      Resource = aws_efs_file_system.app.arn
      Condition = {
        StringEquals = {
          "elasticfilesystem:AccessPointArn" = aws_efs_access_point.app.arn
        }
      }
    }]
  })
}

resource "aws_efs_file_system" "app" {
  encrypted = true

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }

  tags = {
    Name = "${var.name}-app-data"
  }
}

resource "aws_security_group" "efs" {
  name        = "${var.name}-efs"
  description = "NFS ingress only from the ECS application task."
  vpc_id      = aws_vpc.this.id

  egress = []

  tags = {
    Name = "${var.name}-efs"
  }
}

resource "aws_vpc_security_group_ingress_rule" "efs_from_task" {
  security_group_id            = aws_security_group.efs.id
  referenced_security_group_id = aws_security_group.task.id
  description                  = "Encrypted EFS mount from the application task."
  from_port                    = 2049
  ip_protocol                  = "tcp"
  to_port                      = 2049
}

resource "aws_vpc_security_group_egress_rule" "task_to_efs" {
  security_group_id            = aws_security_group.task.id
  referenced_security_group_id = aws_security_group.efs.id
  description                  = "NFS traffic to the application data filesystem."
  from_port                    = 2049
  ip_protocol                  = "tcp"
  to_port                      = 2049
}

resource "aws_efs_mount_target" "app" {
  count           = 2
  file_system_id  = aws_efs_file_system.app.id
  subnet_id       = aws_subnet.private[count.index].id
  security_groups = [aws_security_group.efs.id]
}

resource "aws_efs_access_point" "app" {
  file_system_id = aws_efs_file_system.app.id

  posix_user {
    uid = 1000
    gid = 1000
  }

  root_directory {
    path = "/application-data"

    creation_info {
      owner_uid   = 1000
      owner_gid   = 1000
      permissions = "0750"
    }
  }

  tags = {
    Name = "${var.name}-app-data"
  }
}

resource "aws_ecs_cluster" "api" {
  name = "${var.name}-api"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Name = "${var.name}-api"
  }
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = "${aws_ecr_repository.api.repository_url}:pilot"
      essential = true
      portMappings = [{
        containerPort = 8000
        hostPort      = 8000
        protocol      = "tcp"
      }]
      environment = concat([
        { name = "APP_ENV", value = "production" },
        { name = "API_HOST", value = "0.0.0.0" },
        { name = "API_PORT", value = "8000" },
        { name = "LOG_LEVEL", value = "INFO" },
        { name = "REQUIRE_GATEWAY_AUTH", value = "true" },
        { name = "TRUSTED_IDENTITY_HEADER", value = "X-Authenticated-User" },
        { name = "API_GATEWAY_STAGE_PREFIX", value = "/${var.api_stage_name}" },
        { name = "ENABLE_SYNTHETIC_HEARTBEAT", value = "false" },
        { name = "LLM_BASE_URL", value = "http://${aws_instance.llm.private_ip}:${local.llm_port}/v1" },
        { name = "EMBEDDING_BASE_URL", value = "http://${aws_instance.llm.private_ip}:${local.embed_port}/v1" },
        { name = "EMBEDDING_MODEL", value = var.llm_embed_served_name },
        { name = "DEFAULT_MODEL", value = var.llm_chat_served_name },
        { name = "MONITORING_AGENT_MODEL", value = var.llm_chat_served_name },
        { name = "DIAGNOSIS_AGENT_MODEL", value = var.llm_chat_served_name },
        { name = "ACTION_AGENT_MODEL", value = var.llm_chat_served_name },
        { name = "FEEDBACK_AGENT_MODEL", value = var.llm_chat_served_name },
        { name = "POSTGRES_HOST", value = aws_db_instance.memory.address },
        { name = "POSTGRES_PORT", value = "5432" },
        { name = "POSTGRES_DB", value = "agent_kb" },
        { name = "POSTGRES_USER", value = "postgres" },
        ], [
        for name, value in var.application_environment : { name = name, value = value }
      ])
      secrets = concat(
        [
          {
            name      = "POSTGRES_PASSWORD"
            valueFrom = "${aws_db_instance.memory.master_user_secret[0].secret_arn}:password::"
          },
          {
            name      = "LLM_API_KEY"
            valueFrom = var.llm_api_key_secret_arn
          },
        ],
        [
          for name, arn in var.secret_arns : {
            name      = name
            valueFrom = arn
          }
        ],
      )
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.api.name
          awslogs-region        = data.aws_region.current.region
          awslogs-stream-prefix = "api"
        }
      }
      mountPoints = [{
        sourceVolume  = "application-data"
        containerPath = "/app/data"
        readOnly      = false
      }]
    }
  ])

  volume {
    name = "application-data"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.app.id
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.app.id
        iam             = "ENABLED"
      }
    }
  }

  tags = {
    Name = "${var.name}-api"
  }
}

resource "aws_ecs_service" "api" {
  name            = "${var.name}-api"
  cluster         = aws_ecs_cluster.api.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  enable_execute_command = true

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  depends_on = [
    aws_lb_listener.api,
    aws_iam_role_policy.execution,
    aws_iam_role_policy.task_efs,
    aws_efs_mount_target.app,
    aws_vpc_security_group_ingress_rule.efs_from_task,
    aws_vpc_security_group_egress_rule.task_to_efs,
  ]

  tags = {
    Name = "${var.name}-api"
  }
}

resource "aws_iam_role" "github_actions" {
  name = "${var.name}-github-actions-deploy"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = var.github_oidc_provider_arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repository}:ref:refs/heads/main"
        }
      }
    }]
  })

  tags = {
    Name = "${var.name}-github-actions-deploy"
  }
}

resource "aws_iam_role_policy" "github_actions" {
  name = "${var.name}-github-actions-deploy"
  role = aws_iam_role.github_actions.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:CompleteLayerUpload",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
        ]
        Resource = aws_ecr_repository.api.arn
      },
      {
        Effect = "Allow"
        Action = [
          "ecs:DescribeServices",
        ]
        Resource = "arn:aws:ecs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:service/${aws_ecs_cluster.api.name}/${aws_ecs_service.api.name}"
      },
      {
        Effect   = "Allow"
        Action   = ["ecs:UpdateService"]
        Resource = "arn:aws:ecs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:service/${aws_ecs_cluster.api.name}/${aws_ecs_service.api.name}"
      },
    ]
  })
}
