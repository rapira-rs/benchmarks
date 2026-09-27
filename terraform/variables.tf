variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "az" {
  description = "Every instance shares this AZ. Cross-AZ traffic is billed; same-AZ private IPv4 is free."
  type        = string
  default     = "eu-central-1a"
}

variable "server_instance_type" {
  description = "The measured box. c7a has no SMT: one vCPU is one physical core. A c7a.2xlarge has 8 vCPUs: the rate stage runs 8 rapira processes, and the capacity stage runs the process count of the suite."
  type        = string
  default     = "c7a.2xlarge"
}

variable "loader_instance_type" {
  description = "One load generator. A c7a.xlarge has 4 vCPUs and runs one wrk2 or h2load thread per vCPU. The ci suite rates keep each loader below 70% busy CPU."
  type        = string
  default     = "c7a.xlarge"
}

variable "loader_count" {
  description = "The number of loaders. The rate and the connection count of a stage are split evenly over them."
  type        = number
  default     = 2

  validation {
    condition     = var.loader_count >= 1
    error_message = "loader_count must be 1 or more."
  }
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
