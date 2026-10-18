# Self-hosted LLM inference server (GPU EC2) and Postgres (episodic memory + RAG), both private.

resource "aws_security_group" "llm" {
  name        = "${var.name}-llm"
  description = "LLM inference server: ingress only from the agent task."
  vpc_id      = aws_vpc.this.id

  egress = []

  tags = {
    Name = "${var.name}-llm"
  }
}

resource "aws_vpc_security_group_ingress_rule" "llm_from_task" {
  security_group_id            = aws_security_group.llm.id
  referenced_security_group_id = aws_security_group.task.id
  description                  = "OpenAI-compatible API from the agent task."
  from_port                    = local.llm_port
  ip_protocol                  = "tcp"
  to_port                      = local.embed_port
}

resource "aws_vpc_security_group_egress_rule" "llm_to_endpoints" {
  security_group_id            = aws_security_group.llm.id
  referenced_security_group_id = aws_security_group.aws_endpoints.id
  description                  = "HTTPS to AWS interface endpoints (ECR image pull, Logs)."
  from_port                    = 443
  ip_protocol                  = "tcp"
  to_port                      = 443
}

resource "aws_vpc_security_group_egress_rule" "llm_to_s3" {
  security_group_id = aws_security_group.llm.id
  description       = "Model weights and ECR layers from S3 through the gateway endpoint."
  prefix_list_id    = data.aws_prefix_list.s3.id
  from_port         = 443
  ip_protocol       = "tcp"
  to_port           = 443
}

resource "aws_iam_role" "llm" {
  name = "${var.name}-llm"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "llm" {
  name = "${var.name}-llm"
  role = aws_iam_role.llm.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Effect   = "Allow"
          Action   = ["ecr:GetAuthorizationToken"]
          Resource = "*"
        },
        {
          Effect   = "Allow"
          Action   = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
          Resource = aws_ecr_repository.llm.arn
        },
        {
          Effect   = "Allow"
          Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
          Resource = "${aws_cloudwatch_log_group.llm.arn}:*"
        },
      ],
      [{
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = var.llm_api_key_secret_arn
      }],
      var.llm_models_bucket != "" ? [
        {
          Effect   = "Allow"
          Action   = ["s3:GetObject", "s3:ListBucket"]
          Resource = ["arn:aws:s3:::${var.llm_models_bucket}", "arn:aws:s3:::${var.llm_models_bucket}/*"]
        },
      ] : [],
    )
  })
}

resource "aws_iam_role_policy_attachment" "llm_ssm" {
  role       = aws_iam_role.llm.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "llm" {
  name = "${var.name}-llm"
  role = aws_iam_role.llm.name
}

resource "aws_ecr_repository" "llm" {
  name                 = "${var.name}-llm"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name = "${var.name}-llm"
  }
}

resource "aws_cloudwatch_log_group" "llm" {
  name              = "/ec2/${var.name}/llm"
  retention_in_days = var.log_retention_days
}

resource "aws_instance" "llm" {
  ami                    = var.llm_ami_id
  instance_type          = var.llm_instance_type
  subnet_id              = aws_subnet.private[0].id
  vpc_security_group_ids = [aws_security_group.llm.id]
  iam_instance_profile   = aws_iam_instance_profile.llm.name

  associate_public_ip_address = false

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_size = var.llm_root_volume_gb
    encrypted   = true
  }

  # Two OpenAI-compatible vLLM servers from the private ECR image, weights from the private S3 bucket,
  # no Hugging Face access (offline mode). Both require the API key stored in Secrets Manager.
  user_data = <<-EOT
    #!/bin/bash
    set -euo pipefail
    REGION=${data.aws_region.current.region}
    IMAGE=${aws_ecr_repository.llm.repository_url}:${var.llm_image_tag}
    aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin ${aws_ecr_repository.llm.repository_url}
    mkdir -p /models
    aws s3 sync s3://${var.llm_models_bucket} /models
    API_KEY=$(aws secretsmanager get-secret-value --region $REGION --secret-id ${var.llm_api_key_secret_arn} --query SecretString --output text)
    LOGOPTS="--log-driver=awslogs --log-opt awslogs-region=$REGION --log-opt awslogs-group=${aws_cloudwatch_log_group.llm.name}"
    COMMON="--restart unless-stopped -v /models:/models:ro -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e VLLM_API_KEY=$API_KEY"
    docker run -d --name vllm-chat $COMMON $LOGOPTS --gpus '"device=${var.llm_chat_gpu_devices}"' -p ${local.llm_port}:8000 $IMAGE \
      --model /models/${var.llm_chat_model_dir} --served-model-name ${var.llm_chat_served_name} \
      --tensor-parallel-size ${var.llm_chat_tensor_parallel} --max-model-len ${var.llm_chat_max_len} \
      --enable-auto-tool-choice --tool-call-parser ${var.llm_tool_call_parser}
    docker run -d --name vllm-embed $COMMON $LOGOPTS --gpus '"device=${var.llm_embed_gpu_device}"' -p ${local.embed_port}:8000 $IMAGE \
      --model /models/${var.llm_embed_model_dir} --served-model-name ${var.llm_embed_served_name} --gpu-memory-utilization 0.5
  EOT

  tags = {
    Name = "${var.name}-llm"
  }
}

resource "aws_security_group" "db" {
  name        = "${var.name}-db"
  description = "Postgres: ingress only from the agent task."
  vpc_id      = aws_vpc.this.id

  egress = []

  tags = {
    Name = "${var.name}-db"
  }
}

resource "aws_vpc_security_group_ingress_rule" "db_from_task" {
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = aws_security_group.task.id
  description                  = "Postgres from the agent task."
  from_port                    = 5432
  ip_protocol                  = "tcp"
  to_port                      = 5432
}

resource "aws_db_subnet_group" "this" {
  name       = "${var.name}-db"
  subnet_ids = aws_subnet.private[*].id
}

# pgvector ships with RDS Postgres 16; run knowledge_base/migrations/*.sql once after creation.
resource "aws_db_instance" "memory" {
  identifier                  = "${var.name}-memory"
  engine                      = "postgres"
  engine_version              = var.db_engine_version
  instance_class              = var.db_instance_class
  allocated_storage           = var.db_storage_gb
  db_name                     = "agent_kb"
  username                    = "postgres"
  manage_master_user_password = true
  storage_encrypted           = true
  publicly_accessible         = false
  multi_az                    = var.db_multi_az
  db_subnet_group_name        = aws_db_subnet_group.this.name
  vpc_security_group_ids      = [aws_security_group.db.id]
  backup_retention_period     = 7
  deletion_protection         = true
  skip_final_snapshot         = false
  final_snapshot_identifier   = "${var.name}-memory-final"

  tags = {
    Name = "${var.name}-memory"
  }
}

resource "aws_iam_role_policy" "execution_db_secret" {
  name = "${var.name}-task-execution-db-secret"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = aws_db_instance.memory.master_user_secret[0].secret_arn
    }]
  })
}
