output "server_public_ip" {
  value = aws_instance.rig["server"].public_ip
}

output "server_private_ip" {
  value = aws_instance.rig["server"].private_ip
}

output "loader_public_ip" {
  value = aws_instance.rig["loader"].public_ip
}

# The AMI the boxes actually run, not the data source's newest image.
output "ami_id" {
  value = aws_instance.rig["server"].ami
}

# The run directories and run-meta.json are named by the measured box.
output "instance_type" {
  value = var.server_instance_type
}

output "loader_instance_type" {
  value = var.loader_instance_type
}

output "key_file" {
  value = local_sensitive_file.key.filename
}

output "ssh_server" {
  value = "ssh -i ${local_sensitive_file.key.filename} fedora@${aws_instance.rig["server"].public_ip}"
}

output "ssh_loader" {
  value = "ssh -i ${local_sensitive_file.key.filename} fedora@${aws_instance.rig["loader"].public_ip}"
}
