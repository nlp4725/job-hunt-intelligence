# RDS keeps the master (table owner) password in Secrets Manager itself
# (manage_master_user_password): no password in Terraform state, no Lambda.

resource "aws_db_subnet_group" "main" {
  name       = "jhi"
  subnet_ids = aws_subnet.data[*].id
}

resource "aws_db_parameter_group" "main" {
  name   = "jhi-postgres18"
  family = "postgres18"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "main" {
  identifier                  = "jhi"
  engine                      = "postgres"
  engine_version              = "18.3"
  instance_class              = "db.t4g.micro"
  allocated_storage           = 20
  max_allocated_storage       = 100
  storage_type                = "gp3"
  storage_encrypted           = true
  db_name                     = "jhi"
  username                    = "jhi_owner"
  manage_master_user_password = true
  db_subnet_group_name        = aws_db_subnet_group.main.name
  vpc_security_group_ids      = [aws_security_group.db.id]
  parameter_group_name        = aws_db_parameter_group.main.name
  publicly_accessible         = false
  multi_az                    = false
  backup_retention_period     = 7
  copy_tags_to_snapshot       = true
  deletion_protection         = true
  skip_final_snapshot         = false
  final_snapshot_identifier   = "jhi-final"
  auto_minor_version_upgrade  = true
  lifecycle {
    prevent_destroy = true
  }
}
