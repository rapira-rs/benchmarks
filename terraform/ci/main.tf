provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project = "rapira-bench"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  bucket = "rapira-bench-tfstate-${data.aws_caller_identity.current.account_id}"
  # The subject claim of a job on the main branch without an environment.
  # https://docs.github.com/en/actions/reference/security/oidc#filtering-for-a-specific-branch
  subject = "${var.github_sub_prefix}:ref:refs/heads/main"
}

# IAM checks the provider TLS certificate against its trusted root CAs, so no
# thumbprint is set.
# https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_create_oidc_verify-thumbprint.html
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.subject]
    }
  }
}

resource "aws_iam_role" "ci" {
  name               = "rapira-bench-ci"
  assume_role_policy = data.aws_iam_policy_document.trust.json
  # The bench job runs up to 110 minutes and destroys the rig at the end.
  max_session_duration = 7200
}

data "aws_iam_policy_document" "ci" {
  statement {
    sid = "Describe"
    actions = [
      "ec2:Describe*",
      "servicequotas:GetServiceQuota",
    ]
    resources = ["*"]
  }

  statement {
    sid = "Rig"
    actions = [
      "ec2:RunInstances",
      "ec2:TerminateInstances",
      "ec2:ModifyInstanceAttribute",
      "ec2:CreateTags",
      "ec2:DeleteTags",
      "ec2:CreateSecurityGroup",
      "ec2:DeleteSecurityGroup",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupIngress",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:CreatePlacementGroup",
      "ec2:DeletePlacementGroup",
      "ec2:ImportKeyPair",
      "ec2:DeleteKeyPair",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.region]
    }
  }

  statement {
    sid       = "StateList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }

  # The state object and its lockfile.
  statement {
    sid       = "StateObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/rig/*"]
  }
}

resource "aws_iam_role_policy" "ci" {
  name   = "rapira-bench-ci"
  role   = aws_iam_role.ci.id
  policy = data.aws_iam_policy_document.ci.json
}

resource "aws_s3_bucket" "state" {
  bucket = local.bucket
}

# Versions keep earlier states when a write goes wrong.
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
