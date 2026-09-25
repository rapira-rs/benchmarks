# Set as the repository secret AWS_ROLE_ARN.
output "role_arn" {
  value = aws_iam_role.ci.arn
}

# Set as the repository secret TF_STATE_BUCKET.
output "bucket" {
  value = aws_s3_bucket.state.bucket
}
