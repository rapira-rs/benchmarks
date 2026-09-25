variable "region" {
  description = "The region of the rig stack and the state bucket."
  type        = string
  default     = "eu-central-1"
}

# The repository was created after 2026-07-15 and uses the immutable subject
# format with the owner and repository ids.
# https://docs.github.com/en/actions/reference/security/oidc#immutable-subject-claims
# Read it with: gh api repos/rapira-rs/benchmarks/actions/oidc/customization/sub --jq .sub_claim_prefix
variable "github_sub_prefix" {
  description = "The OIDC subject prefix of the benchmarks repository."
  type        = string
  default     = "repo:rapira-rs@293146261/benchmarks@1314214977"
}
