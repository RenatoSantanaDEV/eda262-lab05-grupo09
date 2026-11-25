mock_provider "aws" {
  override_during = plan
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "111122223333"
      arn        = "arn:aws:iam::111122223333:user/executor-grupo09"
    }
  }
  mock_data "aws_iam_session_context" {
    defaults = {
      issuer_arn = "arn:aws:iam::111122223333:user/executor-grupo09"
    }
  }
  mock_data "aws_lakeformation_data_lake_settings" {
    defaults = {
      admins = [
        "arn:aws:iam::111122223333:user/executor-grupo09",
        "arn:aws:iam::111122223333:user/outro-grupo",
        "arn:aws:iam::111122223333:role/administrador",
      ]
    }
  }
  mock_resource "aws_s3_bucket" {
    defaults = {
      arn = "arn:aws:s3:::eda-a29-lake-grupo09"
      id  = "eda-a29-lake-grupo09"
    }
  }
  mock_resource "aws_athena_workgroup" {
    defaults = {
      arn = "arn:aws:athena:us-east-1:111122223333:workgroup/eda-a29-grupo09-wg"
    }
  }
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::111122223333:role/eda-a29-grupo09-mock"
    }
  }
  mock_resource "aws_iam_policy" {
    defaults = {
      arn = "arn:aws:iam::111122223333:policy/eda-a29-grupo09-consumo"
    }
  }
}

variables {
  conta_aws                = "111122223333"
  turma                    = "EDA2026.2"
  sufixo                   = "grupo09"
  escopo_prefeitura        = "tabela"
  colunas_ocultas_analista = ["motorista_id", "passageiro_id"]
  contrato_refined = {
    descricao   = "Corridas e receita em BRL por bairro e dia na madrugada, das 00:00 às 05:59 UTC; sem identificadores de corrida, motorista ou passageiro."
    dono        = "Grupo09 - engenharia de dados"
    atualizacao = "Após o pipeline diário; sobrescreve somente as partições dt recalculadas."
    comentarios = {
      bairro   = "Bairro de início da corrida."
      corridas = "Quantidade de corridas iniciadas entre 00:00 e 05:59 UTC no bairro e dia."
      receita  = "Soma do valor das corridas da madrugada, em BRL, arredondada a 2 casas."
    }
  }
}

run "decisoes_do_grupo09" {
  command = plan

  assert {
    condition     = aws_lakeformation_permissions.prefeitura_refined.table[0].name == "madrugada_bairro"
    error_message = "DECISÃO 01: a Prefeitura deve receber só a madrugada_bairro."
  }
  assert {
    condition     = aws_lakeformation_permissions.prefeitura_refined.table[0].wildcard == false || aws_lakeformation_permissions.prefeitura_refined.table[0].wildcard == null
    error_message = "DECISÃO 01: o escopo tabela não pode usar wildcard."
  }
  assert {
    condition     = toset(aws_lakeformation_permissions.analista_trusted.table_with_columns[0].excluded_column_names) == toset(["motorista_id", "passageiro_id"])
    error_message = "DECISÃO 02: motorista_id e passageiro_id devem ficar ocultos."
  }
  assert {
    condition     = aws_glue_catalog_table.refined.description == var.contrato_refined.descricao && length(aws_glue_catalog_table.refined.description) >= 40
    error_message = "DECISÃO 03: a descrição deve vir do contrato."
  }
  assert {
    condition     = aws_glue_catalog_table.refined.parameters["dono"] == "Grupo09 - engenharia de dados"
    error_message = "DECISÃO 03: dono ausente."
  }
  assert {
    condition     = alltrue([for c in aws_glue_catalog_table.refined.storage_descriptor[0].columns : trimspace(c.comment) != ""])
    error_message = "DECISÃO 03: toda coluna precisa de comentário."
  }
}

run "sem_singleton_global_e_com_guardas" {
  command = plan

  assert {
    condition     = jsondecode(aws_iam_role.perfil["prefeitura"].assume_role_policy).Statement[0].Principal.AWS == "arn:aws:iam::111122223333:user/executor-grupo09"
    error_message = "Os perfis devem ser assumíveis só pelo executor do grupo, não pelo root da conta."
  }
  assert {
    condition     = length([for s in jsondecode(aws_iam_policy.consumo.policy).Statement : s if contains(flatten([s.Action]), "s3:GetBucketLocation") && try(s.Condition, null) == null]) == 1
    error_message = "s3:GetBucketLocation precisa de statement próprio, sem condição de s3:prefix."
  }
  assert {
    condition     = length([for s in jsondecode(aws_iam_policy.consumo.policy).Statement : s if contains(flatten([s.Action]), "s3:ListBucket") && try(s.Condition.StringLike["s3:prefix"], null) != null]) == 1
    error_message = "s3:ListBucket deve continuar restrito ao prefixo athena-results."
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.lake.block_public_acls && aws_s3_bucket_public_access_block.lake.restrict_public_buckets
    error_message = "O bucket precisa bloquear acesso público."
  }
  assert {
    condition     = aws_glue_job.trusted.timeout == 10 && aws_glue_job.refined.timeout == 10
    error_message = "Timeout dos jobs deve ser 10 minutos."
  }
  assert {
    condition     = aws_athena_workgroup.wg.configuration[0].bytes_scanned_cutoff_per_query == 209715200
    error_message = "Teto do workgroup deve ser 200 MiB."
  }
}

run "escopo_banco_tambem_planeja" {
  command = plan
  variables {
    escopo_prefeitura = "banco"
  }
  assert {
    condition     = aws_lakeformation_permissions.prefeitura_refined.table[0].wildcard == true
    error_message = "Escopo banco deve usar wildcard e nenhum nome."
  }
}

run "conta_errada_e_recusada" {
  command = plan
  variables {
    conta_aws = "999999999999"
  }
  expect_failures = [data.aws_caller_identity.eu]
}

run "regiao_errada_e_recusada" {
  command = plan
  variables {
    regiao = "sa-east-1"
  }
  expect_failures = [var.regiao]
}

run "coluna_fora_da_lista_e_recusada" {
  command = plan
  variables {
    colunas_ocultas_analista = ["bairro"]
  }
  expect_failures = [var.colunas_ocultas_analista]
}

run "executor_sem_admin_lf_e_recusado" {
  command = plan
  override_data {
    target = data.aws_lakeformation_data_lake_settings.conta
    values = {
      admins = ["arn:aws:iam::111122223333:role/administrador"]
    }
  }
  expect_failures = [data.aws_lakeformation_data_lake_settings.conta]
}

run "contrato_curto_e_recusado" {
  command = plan
  variables {
    contrato_refined = {
      descricao   = "tabela da refined"
      dono        = "Grupo09"
      atualizacao = "diária"
      comentarios = { bairro = "b", corridas = "c", receita = "r" }
    }
  }
  expect_failures = [var.contrato_refined]
}
