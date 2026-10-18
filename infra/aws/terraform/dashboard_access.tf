locals {
  dashboard_roles = length(var.dashboard_principal_arns) > 0 ? {
    sre        = "sre_responder"
    ai_product = "ai_product_viewer"
  } : {}
}

# The dashboard server assumes one of these roles per signed-in user, using the user's identity as
# the session name. API Gateway then reports the assumed-role session ARN as X-Authenticated-User,
# so approvals and audit entries carry the real person rather than a shared service identity.
resource "aws_iam_role" "dashboard_caller" {
  for_each = local.dashboard_roles

  name                 = "${var.name}-dashboard-${replace(each.key, "_", "-")}"
  description          = "API caller identity for ${replace(each.key, "_", " ")} users of the dashboard."
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = var.dashboard_principal_arns }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    Name = "${var.name}-dashboard-${replace(each.key, "_", "-")}"
  }
}

resource "aws_iam_role_policy_attachment" "dashboard_caller" {
  for_each = local.dashboard_roles

  role       = aws_iam_role.dashboard_caller[each.key].name
  policy_arn = aws_iam_policy.caller[each.value].arn
}
