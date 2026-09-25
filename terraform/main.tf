provider "aws" {
  region = var.region

  # Every resource, root volumes included, carries this tag so `make nuke`
  # can find the rig without terraform state.
  default_tags {
    tags = {
      Project = "rapira-bench"
    }
  }
}

data "aws_ami" "fedora" {
  most_recent = true
  owners      = ["125523088429"] # Fedora infra account

  filter {
    name   = "name"
    values = ["Fedora-Cloud-Base-AmazonEC2.x86_64-44-*"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }

  filter {
    name   = "state"
    values = ["available"]
  }
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnet" "az" {
  vpc_id            = data.aws_vpc.default.id
  availability_zone = var.az
  default_for_az    = true
}

locals {
  ami_id = coalesce(var.ami_id, data.aws_ami.fedora.id)
  # Static on purpose: a user_data change stops and starts the instances, and
  # the bootstrap can never run again anyway. The real TTL is set by the
  # provision scripts over ssh.
  user_data = file("${path.module}/cloud-init/bootstrap.sh")
}

resource "tls_private_key" "rig" {
  algorithm = "ED25519"
}

resource "aws_key_pair" "rig" {
  key_name = "rapira-bench"
  # trimspace: the tls provider appends a newline that EC2 rejects as invalid format.
  public_key = trimspace(tls_private_key.rig.public_key_openssh)
}

# OpenSSH-format key, not the PKCS#8 PEM: LibreSSL-linked ssh clients cannot
# read ed25519 PKCS#8. The key also sits in tfstate; both stay gitignored.
resource "local_sensitive_file" "key" {
  content         = tls_private_key.rig.private_key_openssh
  filename        = "${path.module}/rig-key.pem"
  file_permission = "0600"
}

resource "aws_placement_group" "rig" {
  name     = "rapira-bench"
  strategy = "cluster"
}

resource "aws_security_group" "rig" {
  name        = "rapira-bench"
  description = "rapira bench rig: ssh from the operator, bench traffic SG-internal"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "ssh from the operator IP"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_cidr]
  }

  ingress {
    description = "bench traffic between the rig boxes"
    from_port   = 0
    to_port     = 65535
    protocol    = "tcp"
    self        = true
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "server" {
  ami                                  = local.ami_id
  instance_type                        = var.server_instance_type
  subnet_id                            = data.aws_subnet.az.id
  vpc_security_group_ids               = [aws_security_group.rig.id]
  key_name                             = aws_key_pair.rig.key_name
  placement_group                      = aws_placement_group.rig.name
  associate_public_ip_address          = true
  user_data                            = local.user_data
  instance_initiated_shutdown_behavior = "terminate"

  root_block_device {
    # The Fedora cloud AMI root is 5 GiB; two release builds plus debuginfo need more.
    volume_size = 40
    volume_type = "gp3"
  }

  metadata_options {
    http_tokens = "required"
  }

  tags = {
    Name = "rapira-bench-server"
    Role = "server"
  }

  # Dependents are destroyed first, so this edge makes destroy terminate the
  # instances before it deletes the local key file: a partially failed
  # destroy must keep the ssh path to boxes that still bill.
  depends_on = [local_sensitive_file.key]

  lifecycle {
    # Fedora rebuilds the AMI daily and ami is ForceNew; a re-apply must not
    # replace a provisioned rig.
    ignore_changes = [ami]
  }
}

resource "aws_instance" "loader" {
  count = var.loader_count

  ami                                  = local.ami_id
  instance_type                        = var.loader_instance_type
  subnet_id                            = data.aws_subnet.az.id
  vpc_security_group_ids               = [aws_security_group.rig.id]
  key_name                             = aws_key_pair.rig.key_name
  placement_group                      = aws_placement_group.rig.name
  associate_public_ip_address          = true
  user_data                            = local.user_data
  instance_initiated_shutdown_behavior = "terminate"

  root_block_device {
    volume_size = 40
    volume_type = "gp3"
  }

  metadata_options {
    http_tokens = "required"
  }

  tags = {
    Name = "rapira-bench-loader-${count.index + 1}"
    Role = "loader"
  }

  depends_on = [local_sensitive_file.key]

  lifecycle {
    ignore_changes = [ami]
  }
}
