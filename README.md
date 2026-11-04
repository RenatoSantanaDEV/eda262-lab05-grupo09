# Laboratório 05 - O pipeline que pode rodar de novo

Engenharia de Dados 2026.2, CESAR School. Grupo 09: Renato Santana
(representante), João Augusto Pimentel, Raphael Barros e Lara Pessoa.

## Resultado em 04/11/2026

- Execução real na AWS, região `us-east-1`, com backend remoto S3 e lock preexistentes.
- Verificador original: **70 / 70** nos critérios automáticos.
- Reexecução: **518.400 → 518.400 linhas** e **11.137 → 11.137 corridas de madrugada**.
- Deduplicação: **17.280** duplicatas removidas; **518.400** corridas distintas.
- Backfill de 01/11/2026: **1** partição reescrita; os totais permaneceram iguais.
- Limpeza: **10 recursos removidos**, `PASSA [5]`; backend e lock preservados.
- As quatro decisões em `DECISOES.md` correspondem aos 20% de avaliação manual.

## Arquivos

- `terraform/`: infraestrutura, decisões em `terraform.tfvars` e lock do provedor.
- `terraform/scripts/pipeline-corridas.py`: script original do professor.
- `app/`, `carregar-raw.sh`, `rodar-job.sh`, `verificacao/`: scripts fornecidos.
- `DECISOES.md`: escolhas e números medidos.
- `evidencias/`: saídas reais do Terraform, Glue, Athena e verificadores.
- `Relatorio-Lab05-Grupo09.pdf`: documento consolidado.

## Reproduzir

Use a conta AWS do grupo, na região `us-east-1`, com AWS CLI e Terraform >= 1.6.
O limite definido para a atividade é US$ 1,00 por grupo. As credenciais devem
permanecer fora do repositório. A configuração está no sufixo `grupo09`.

1. Copie `terraform/backend.hcl.example` para `terraform/backend.hcl` e informe
   o bucket e a tabela de lock remotos da disciplina, usando uma chave exclusiva.
2. Execute, na raiz:

```bash
terraform -chdir=terraform init -backend-config=backend.hcl
terraform -chdir=terraform plan
terraform -chdir=terraform apply
./carregar-raw.sh
./rodar-job.sh
./verificacao/verifica.sh
```

3. Teste o backfill de um dia de evento (a data abaixo pertence à execução registrada):

```bash
./rodar-job.sh DIAS=2026-11-01
```

4. Salve as evidências, remova os recursos deste laboratório e verifique a limpeza:

```bash
terraform -chdir=terraform destroy
./verificacao/verifica.sh --pos-destroy
```

Os dados usam a data da geração; atualize as datas das consultas e do backfill
se reproduzir em outro dia. A janela de três dias limita o atraso aceito,
mas o script lê toda a raw. Não é um filtro físico de quatro pastas.

O backfill registrado refez um dia já completo: prova a ausência de duplicação
e a reescrita de uma partição, sem simular uma chegada nova acima da janela.

## Origem

Projeto-base e rubrica fornecidos pelo Prof. Carlos Diego Cavalcanti Pereira
na [atividade do Classroom](https://classroom.google.com/u/5/c/ODcwNzE4ODE1ODMw/a/ODk3NDcxNTc1Nzg4/details).
As alterações funcionais são as três ligações de argumentos no `main.tf` e
as escolhas no `terraform.tfvars`; os scripts de dados e verificação foram preservados.
