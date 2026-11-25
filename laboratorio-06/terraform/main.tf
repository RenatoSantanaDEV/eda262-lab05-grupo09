locals {
  nome   = "eda-a29-${var.sufixo}"
  bucket = "eda-a29-lake-${var.sufixo}"
  banco  = "eda_a29_${var.sufixo}"
  projecao = {
    "projection.enabled"          = "true"
    "projection.dt.type"          = "date"
    "projection.dt.format"        = "yyyy-MM-dd"
    "projection.dt.range"         = "NOW-60DAYS,NOW+1DAYS"
    "projection.dt.interval"      = "1"
    "projection.dt.interval.unit" = "DAYS"
  }
  colunas_raw = [
    ["corrida_id", "string"], ["motorista_id", "string"], ["passageiro_id", "string"], ["bairro", "string"],
    ["data_corrida", "string"], ["fim", "string"], ["distancia_km", "double"], ["duracao_min", "double"],
    ["valor", "string"],
  ]
  colunas_trusted = [
    ["corrida_id", "string"], ["motorista_id", "string"], ["passageiro_id", "string"], ["bairro", "string"],
    ["inicio", "timestamp"], ["fim", "timestamp"], ["distancia_km", "double"], ["duracao_min", "double"],
    ["valor", "double"], ["faixa_hora", "int"], ["chegada", "string"],
  ]
  parquet = {
    input  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
    serde  = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
  }
  executor = data.aws_iam_session_context.eu.issuer_arn
  tabelas_isoladas = {
    raw     = aws_glue_catalog_table.raw.name
    trusted = aws_glue_catalog_table.trusted.name
    refined = aws_glue_catalog_table.refined.name
  }
}

data "aws_caller_identity" "eu" {
  lifecycle {
    postcondition {
      condition     = self.account_id == var.conta_aws
      error_message = "A conta das credenciais não é a conta_aws declarada em terraform.tfvars."
    }
  }
}

data "aws_iam_session_context" "eu" {
  arn = data.aws_caller_identity.eu.arn
}

data "aws_lakeformation_data_lake_settings" "conta" {
  catalog_id = data.aws_caller_identity.eu.account_id
  lifecycle {
    postcondition {
      condition     = contains(self.admins, data.aws_iam_session_context.eu.issuer_arn)
      error_message = "Quem executa o Terraform precisa já ser administrador do Lake Formation; este projeto não altera as configurações globais da conta."
    }
  }
}

resource "aws_s3_bucket" "lake" {
  bucket        = local.bucket
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_glue_catalog_database" "camada" {
  for_each   = toset(["raw", "trusted", "refined"])
  name       = "${local.banco}_${each.key}"
  depends_on = [data.aws_lakeformation_data_lake_settings.conta]
}

resource "aws_glue_catalog_table" "raw" {
  name          = "corridas"
  database_name = aws_glue_catalog_database.camada["raw"].name
  description   = "Corridas como o export mandou. dt = dia em que o arquivo chegou."
  table_type    = "EXTERNAL_TABLE"
  parameters = merge(local.projecao, {
    "classification"            = "json"
    "storage.location.template" = "s3://${local.bucket}/raw/corridas/dt=$${dt}/"
  })
  partition_keys {
    name = "dt"
    type = "string"
  }
  storage_descriptor {
    location      = "s3://${local.bucket}/raw/corridas/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"
    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
    }
    dynamic "columns" {
      for_each = local.colunas_raw
      content {
        name = columns.value[0]
        type = columns.value[1]
      }
    }
  }
}

resource "aws_glue_catalog_table" "trusted" {
  name          = "corridas"
  database_name = aws_glue_catalog_database.camada["trusted"].name
  description   = "Uma linha por corrida, limpa e tipada. dt = dia em que a corrida aconteceu."
  table_type    = "EXTERNAL_TABLE"
  parameters = merge(local.projecao, {
    "classification"            = "parquet"
    "storage.location.template" = "s3://${local.bucket}/trusted/corridas/dt=$${dt}/"
  })
  partition_keys {
    name = "dt"
    type = "string"
  }
  storage_descriptor {
    location      = "s3://${local.bucket}/trusted/corridas/"
    input_format  = local.parquet.input
    output_format = local.parquet.output
    ser_de_info {
      serialization_library = local.parquet.serde
    }
    dynamic "columns" {
      for_each = local.colunas_trusted
      content {
        name = columns.value[0]
        type = columns.value[1]
      }
    }
  }
}

resource "aws_glue_catalog_table" "refined" {
  name          = "madrugada_bairro"
  database_name = aws_glue_catalog_database.camada["refined"].name
  description   = var.contrato_refined.descricao
  table_type    = "EXTERNAL_TABLE"
  parameters = merge(local.projecao, {
    "classification"            = "parquet"
    "storage.location.template" = "s3://${local.bucket}/refined/madrugada_bairro/dt=$${dt}/"
    "dono"                      = var.contrato_refined.dono
    "grao"                      = "uma linha por bairro e dia"
    "atualizacao"               = var.contrato_refined.atualizacao
  })
  partition_keys {
    name    = "dt"
    type    = "string"
    comment = "dia em que as corridas aconteceram (AAAA-MM-DD)"
  }
  storage_descriptor {
    location      = "s3://${local.bucket}/refined/madrugada_bairro/"
    input_format  = local.parquet.input
    output_format = local.parquet.output
    ser_de_info {
      serialization_library = local.parquet.serde
    }
    columns {
      name    = "bairro"
      type    = "string"
      comment = var.contrato_refined.comentarios.bairro
    }
    columns {
      name    = "corridas"
      type    = "bigint"
      comment = var.contrato_refined.comentarios.corridas
    }
    columns {
      name    = "receita"
      type    = "double"
      comment = var.contrato_refined.comentarios.receita
    }
  }
}

resource "aws_iam_role" "job" {
  name = "${local.nome}-glue"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "glue.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "bucket" {
  name = "job-runtime"
  role = aws_iam_role.job.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "LakeBucket"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [aws_s3_bucket.lake.arn, "${aws_s3_bucket.lake.arn}/*"]
      },
      {
        Sid    = "GlueLogs"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = [
          "arn:aws:logs:${var.regiao}:${data.aws_caller_identity.eu.account_id}:log-group:/aws-glue/*",
          "arn:aws:logs:${var.regiao}:${data.aws_caller_identity.eu.account_id}:log-group:/aws-glue/*:*",
        ]
      },
      {
        Sid      = "GlueLogsDescribe"
        Effect   = "Allow"
        Action   = ["logs:DescribeLogGroups", "logs:DescribeLogStreams"]
        Resource = "*"
      },
      {
        Sid       = "GlueMetrics"
        Effect    = "Allow"
        Action    = ["cloudwatch:PutMetricData"]
        Resource  = "*"
        Condition = { StringEquals = { "cloudwatch:namespace" = "Glue" } }
      },
    ]
  })
}

resource "aws_s3_object" "script" {
  for_each = toset(["pipeline-corridas.py", "refinar-madrugada.py"])
  bucket   = aws_s3_bucket.lake.id
  key      = "scripts/${each.key}"
  source   = "${path.module}/scripts/${each.key}"
  etag     = filemd5("${path.module}/scripts/${each.key}")
}

resource "aws_glue_job" "trusted" {
  name              = "${local.nome}-trusted"
  role_arn          = aws_iam_role.job.arn
  glue_version      = "4.0"
  worker_type       = "G.1X"
  number_of_workers = 2
  timeout           = var.timeout_job_min
  max_retries       = 0
  execution_property {
    max_concurrent_runs = 1
  }
  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "s3://${aws_s3_bucket.lake.id}/${aws_s3_object.script["pipeline-corridas.py"].key}"
  }
  default_arguments = {
    "--BUCKET"              = aws_s3_bucket.lake.id
    "--TABELA"              = "corridas"
    "--MODO"                = "sobrescrever"
    "--CHAVE"               = "corrida_id"
    "--JANELA"              = "3"
    "--DIAS"                = "todos"
    "--job-language"        = "python"
    "--job-bookmark-option" = "job-bookmark-disable"
  }
  depends_on = [aws_iam_role_policy.bucket]
}

resource "aws_glue_job" "refined" {
  name              = "${local.nome}-refined"
  role_arn          = aws_iam_role.job.arn
  glue_version      = "4.0"
  worker_type       = "G.1X"
  number_of_workers = 2
  timeout           = var.timeout_job_min
  max_retries       = 0
  execution_property {
    max_concurrent_runs = 1
  }
  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "s3://${aws_s3_bucket.lake.id}/${aws_s3_object.script["refinar-madrugada.py"].key}"
  }
  default_arguments = {
    "--BUCKET"              = aws_s3_bucket.lake.id
    "--DIAS"                = "todos"
    "--job-language"        = "python"
    "--job-bookmark-option" = "job-bookmark-disable"
  }
  depends_on = [aws_iam_role_policy.bucket]
}

resource "aws_athena_workgroup" "wg" {
  name          = "${local.nome}-wg"
  force_destroy = true
  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = false
    bytes_scanned_cutoff_per_query     = var.limite_bytes_athena
    result_configuration {
      output_location = "s3://${aws_s3_bucket.lake.id}/athena-results/"
      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
  depends_on = [aws_s3_bucket_public_access_block.lake]
}

resource "aws_iam_role" "lf" {
  name = "${local.nome}-lf"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lakeformation.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "lf" {
  name = "camadas-rw"
  role = aws_iam_role.lf.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      Resource = [for c in ["raw", "trusted", "refined"] : "${aws_s3_bucket.lake.arn}/${c}/*"] },
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = [aws_s3_bucket.lake.arn] },
    ]
  })
}

resource "aws_lakeformation_resource" "camada" {
  for_each   = toset(["raw", "trusted", "refined"])
  arn        = "${aws_s3_bucket.lake.arn}/${each.key}"
  role_arn   = aws_iam_role.lf.arn
  depends_on = [aws_iam_role_policy.lf, data.aws_lakeformation_data_lake_settings.conta]
}

resource "terraform_data" "isolamento_lf" {
  triggers_replace = {
    bancos  = [for c in ["raw", "trusted", "refined"] : aws_glue_catalog_database.camada[c].id]
    tabelas = [aws_glue_catalog_table.raw.id, aws_glue_catalog_table.trusted.id, aws_glue_catalog_table.refined.id]
    script  = filesha256("${path.module}/lf/isolar-camadas.sh")
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = "bash '${path.module}/lf/isolar-camadas.sh'"
    environment = {
      LF_CONTA    = data.aws_caller_identity.eu.account_id
      LF_REGIAO   = var.regiao
      LF_SUFIXO   = var.sufixo
      LF_EXECUTOR = local.executor
      LF_RAW      = "${aws_glue_catalog_database.camada["raw"].name}:${local.tabelas_isoladas.raw}"
      LF_TRUSTED  = "${aws_glue_catalog_database.camada["trusted"].name}:${local.tabelas_isoladas.trusted}"
      LF_REFINED  = "${aws_glue_catalog_database.camada["refined"].name}:${local.tabelas_isoladas.refined}"
      AWS_REGION  = var.regiao
      AWS_PAGER   = ""
    }
  }
}

resource "aws_iam_policy" "consumo" {
  name = "${local.nome}-consumo"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults", "athena:StopQueryExecution", "athena:GetWorkGroup"],
      Resource = [aws_athena_workgroup.wg.arn] },
      { Effect = "Allow", Action = ["glue:GetDatabase", "glue:GetDatabases", "glue:GetTable", "glue:GetTables", "glue:GetPartition", "glue:GetPartitions"],
      Resource = ["*"] },
      { Effect = "Allow", Action = ["lakeformation:GetDataAccess"], Resource = ["*"] },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"],
      Resource = ["${aws_s3_bucket.lake.arn}/athena-results/*"] },
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = [aws_s3_bucket.lake.arn],
      Condition = { StringLike = { "s3:prefix" = ["athena-results/*", "athena-results"] } } },
      { Effect = "Allow", Action = ["s3:GetBucketLocation"], Resource = [aws_s3_bucket.lake.arn] },
    ]
  })
}

resource "aws_iam_role" "perfil" {
  for_each = toset(["prefeitura", "analista"])
  name     = "${local.nome}-${each.key}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { AWS = local.executor },
    Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "perfil" {
  for_each   = aws_iam_role.perfil
  role       = each.value.name
  policy_arn = aws_iam_policy.consumo.arn
}

resource "aws_lakeformation_permissions" "ver_banco" {
  for_each = {
    "prefeitura-refined" = ["prefeitura", "refined"]
    "analista-trusted"   = ["analista", "trusted"]
    "analista-refined"   = ["analista", "refined"]
  }
  principal   = aws_iam_role.perfil[each.value[0]].arn
  permissions = ["DESCRIBE"]
  database {
    name = aws_glue_catalog_database.camada[each.value[1]].name
  }
  depends_on = [terraform_data.isolamento_lf, aws_lakeformation_resource.camada]
}

resource "aws_lakeformation_permissions" "prefeitura_refined" {
  principal   = aws_iam_role.perfil["prefeitura"].arn
  permissions = ["SELECT", "DESCRIBE"]
  table {
    database_name = aws_glue_catalog_database.camada["refined"].name
    name          = var.escopo_prefeitura == "tabela" ? aws_glue_catalog_table.refined.name : null
    wildcard      = var.escopo_prefeitura == "banco" ? true : null
  }
  depends_on = [terraform_data.isolamento_lf, aws_lakeformation_resource.camada]
}

resource "aws_lakeformation_permissions" "analista_refined" {
  principal   = aws_iam_role.perfil["analista"].arn
  permissions = ["SELECT", "DESCRIBE"]
  table {
    database_name = aws_glue_catalog_database.camada["refined"].name
    wildcard      = true
  }
  depends_on = [terraform_data.isolamento_lf, aws_lakeformation_resource.camada]
}

resource "aws_lakeformation_permissions" "analista_trusted" {
  principal   = aws_iam_role.perfil["analista"].arn
  permissions = ["SELECT"]
  table_with_columns {
    database_name         = aws_glue_catalog_database.camada["trusted"].name
    name                  = aws_glue_catalog_table.trusted.name
    wildcard              = true
    excluded_column_names = var.colunas_ocultas_analista
  }
  depends_on = [terraform_data.isolamento_lf, aws_lakeformation_resource.camada]
}
