variable "profile" {
  description = "AWS CLI profile (SSO). Run `aws sso login --profile <this>` before apply."
  type        = string
  default     = "Rustatian"
}

variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "az" {
  description = "Both instances share this AZ. Cross-AZ traffic is billed; same-AZ private IPv4 is free."
  type        = string
  default     = "eu-central-1a"
}

variable "server_instance_type" {
  description = "The measured box. c7a has no SMT: one vCPU is one physical core; the 8xlarge network is a fixed 12.5 Gbps, no burst credits."
  type        = string
  default     = "c7a.8xlarge"
}

variable "loader_instance_type" {
  description = "The load generator. Sized to saturate the server: ~0.62 loader cores and ~4.4 Gbps sustained per saturated 32-core server."
  type        = string
  default     = "c7a.4xlarge"
}

variable "ami_id" {
  description = "Pin an AMI for the life of a baseline set. Null selects the newest Fedora 44 cloud image, which Fedora rebuilds daily."
  type        = string
  default     = null
}

variable "ssh_cidr" {
  description = "CIDR allowed to ssh. The Makefile injects the operator IP; the default allows nothing real so destroy never prompts."
  type        = string
  default     = "127.0.0.1/32"
}

