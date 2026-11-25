variable "regiao" {
  type    = string
  default = "us-east-1"
  validation {
    condition     = var.regiao == "us-east-1"
    error_message = "O exercício roda somente em us-east-1."
  }
}

variable "conta_aws" {
  type        = string
  description = "ID de 12 dígitos da conta onde o grupo pode aplicar este exercício."
  validation {
    condition     = can(regex("^[0-9]{12}$", var.conta_aws))
    error_message = "conta_aws precisa ter exatamente 12 dígitos."
  }
}

variable "turma" {
  type        = string
  description = "Turma, na tag Turma."
}

variable "sufixo" {
  type        = string
  description = "Login do grupo: só minúsculas e números (o Glue recusa hífen no database)."
  validation {
    condition     = can(regex("^[a-z0-9]{3,20}$", var.sufixo))
    error_message = "O sufixo tem de ter de 3 a 20 letras minúsculas ou números, sem hífen."
  }
}

variable "timeout_job_min" {
  type        = number
  default     = 10
  description = "Timeout de cada execução Glue, em minutos."
  validation {
    condition     = var.timeout_job_min >= 5 && var.timeout_job_min <= 15
    error_message = "Use um timeout entre 5 e 15 minutos."
  }
}

variable "limite_bytes_athena" {
  type        = number
  default     = 209715200
  description = "Teto de bytes lidos por consulta no workgroup."
  validation {
    condition     = var.limite_bytes_athena >= 10485760 && var.limite_bytes_athena <= 1073741824
    error_message = "Use um teto entre 10 MiB e 1 GiB."
  }
}

variable "escopo_prefeitura" {
  type        = string
  description = "DECISÃO 01: banco (todas as tabelas da refined, inclusive futuras) ou tabela (só a madrugada_bairro)."
  validation {
    condition     = contains(["banco", "tabela"], var.escopo_prefeitura)
    error_message = "DECISÃO 01: escolha banco ou tabela."
  }
}

variable "colunas_ocultas_analista" {
  type        = list(string)
  description = "DECISÃO 02: subconjunto de corrida_id, motorista_id, passageiro_id."
  validation {
    condition = length(var.colunas_ocultas_analista) > 0 && alltrue([
      for c in var.colunas_ocultas_analista : contains(["corrida_id", "motorista_id", "passageiro_id"], c)
    ])
    error_message = "DECISÃO 02: liste ao menos uma coluna, só entre corrida_id, motorista_id e passageiro_id."
  }
}

variable "contrato_refined" {
  type = object({
    descricao   = string
    dono        = string
    atualizacao = string
    comentarios = object({ bairro = string, corridas = string, receita = string })
  })
  description = "DECISÃO 03: descrição (ao menos 40 caracteres), dono, atualização e o comentário de cada coluna da madrugada_bairro."
  validation {
    condition = (length(trimspace(var.contrato_refined.descricao)) >= 40
      && trimspace(var.contrato_refined.dono) != ""
      && trimspace(var.contrato_refined.atualizacao) != ""
    && alltrue([for c in values(var.contrato_refined.comentarios) : trimspace(c) != ""]))
    error_message = "DECISÃO 03: descrição com ao menos 40 caracteres; dono, atualização e os três comentários preenchidos."
  }
}
