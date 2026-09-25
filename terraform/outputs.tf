output "server_public_ip" {
  value = aws_instance.server.public_ip
}

output "server_private_ip" {
  value = aws_instance.server.private_ip
}

output "loader_public_ips" {
  value = aws_instance.loader[*].public_ip
}

output "loader_private_ips" {
  value = aws_instance.loader[*].private_ip
}

# The AMI the boxes actually run, not the data source's newest image.
output "ami_id" {
  value = aws_instance.server.ami
}

output "server_instance_type" {
  value = var.server_instance_type
}

output "loader_instance_type" {
  value = var.loader_instance_type
}

output "loader_count" {
  value = var.loader_count
}

output "key_file" {
  value = local_sensitive_file.key.filename
}

output "placement_group" {
  value = aws_placement_group.rig.name
}
