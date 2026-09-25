# Set as the repository variable AWS_ROLE_ARN.
output "role_arn" {
  value = aws_iam_role.ci.arn
}

# Set as the repository variable TF_STATE_BUCKET.
output "bucket" {
  value = aws_s3_bucket.state.bucket
}
