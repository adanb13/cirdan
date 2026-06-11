provider "aws" {
  region = "us-west-2"
}

resource "aws_db_instance" "postgres_prod" {
  engine         = "postgres"
  instance_class = "db.m6g.large"
}

resource "aws_sqs_queue" "payments_jobs" {
  name = "payments-jobs"
}

resource "aws_eks_cluster" "prod" {
  name = "prod-eks"
}
