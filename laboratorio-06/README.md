# Laboratório 06 — O lake em camadas, e quem lê cada uma

EDA 2026.2 · Grupo 09 · execução real em 25/11/2026, `us-east-1`.

Esta pasta acrescenta o Laboratório 06 ao repositório do Lab 05. Usa branch, nomes de recursos e chave de estado próprios; o PR do Lab 05 permanece separado e inalterado.

A execução foi concluída e os recursos de laboratório foram removidos após os testes, conforme o enunciado. O backend remoto permanece disponível.

## Resultado

| Conferência | Resultado medido |
|---|---|
| Verificador oficial, critérios 0 a 4 | 70/70; requisito eliminatório aprovado |
| Raw | 32 objetos JSON, 535.680 registros |
| Trusted | 518.400 corridas únicas; 17.280 duplicatas removidas |
| Refined | 660 agregações, 22 bairros, 30 dias, Parquet |
| Prefeitura | 11.137 corridas na madrugada dos últimos sete dias; raw, trusted e S3 direto recusados |
| Analista | 518.400 linhas; 9/11 colunas de dados visíveis; motorista e passageiro ocultos |
| Contrato no catálogo | Descrição de 137 caracteres, dono, atualização, grão e comentários em todas as colunas |
| Terraform | 32 criados; plano seguinte sem alterações |
| Verificação complementar | 29/29 aprovações na AWS |
| Remoção limpa | 32 recursos removidos; critério 5 aprovado (10%); 17/17 verificações complementares finais |

Os logs e relatórios estão em [`evidencias/`](evidencias/). Os 20% de avaliação manual do [`DECISOES.md`](DECISOES.md) dependem do professor; os resultados automáticos não representam uma nota final atribuída.

## Desenho e decisões

```text
JSON raw/corridas
        │ Glue: deduplicação por corrida_id e atraso de 3 dias
        ▼
Parquet trusted/corridas ──► analista: sem motorista_id e passageiro_id
        │ Glue: madrugada 00:00–05:59 UTC, bairro × dia
        ▼
Parquet refined/madrugada_bairro ──► prefeitura: somente esta tabela
```

O acesso de consumo passa pelo Athena e pelo Lake Formation. Os dois perfis não têm leitura direta das camadas no S3. A analista também pode consultar a refined, conforme o template do professor.

1. `escopo_prefeitura = "tabela"`: tabelas futuras não ganham acesso automaticamente.
2. `colunas_ocultas_analista = ["motorista_id", "passageiro_id"]`: `corrida_id` permanece disponível para investigar uma corrida, sem identificar pessoas.
3. Contrato da refined: quantidades e receita em BRL, bairro × dia, madrugada UTC, sem identificadores individuais, atualizada após o pipeline diário.

## Conta compartilhada

O singleton `aws_lakeformation_data_lake_settings` do template foi substituído por uma leitura das configurações existentes. O plano recusa conta incorreta ou executor que não seja administrador do Lake Formation. O laboratório não gerencia a lista global de administradores nem os defaults globais.

O script `terraform/lf/isolar-camadas.sh` atua exclusivamente nos três databases `eda_a29_grupo09_{raw,trusted,refined}` e suas três tabelas. Ele remove `IAM_ALLOWED_PRINCIPALS`, zera a permissão padrão de tabelas novas nesses databases e valida o resultado antes dos grants. Preserva os outros campos dos databases. As guardas verificam conta, região, executor e nomes exatos.

A restauração autorizada do administrador do grupo aconteceu separadamente e antes da linha de base do laboratório. A configuração global é comparada com o snapshot pré-apply, ignorando somente a ordem de listas devolvidas pela AWS. Nenhuma configuração global é apagada no destroy.

Os papéis de consumo podem ser assumidos somente pelo executor do grupo. O bucket bloqueia acesso público, e os resultados do Athena usam SSE-S3. `GetBucketLocation` tem permissão separada da condição de prefixo de `ListBucket`. Os jobs usam uma policy de execução restrita ao bucket e aos logs/métricas necessários.

## Reproduzir

Pré-requisitos: Terraform ≥ 1.11 para todos os testes, AWS CLI v2, Python 3, credenciais locais e executor administrador do Lake Formation. Para testes locais, também pytest e shellcheck. O backend remoto da disciplina precisa existir; ele não é criado nem removido por este laboratório.

```bash
cd laboratorio-06/terraform
cp backend.hcl.example backend.hcl
cp terraform.tfvars.example terraform.tfvars
```

Preencha `conta_aws` em `terraform.tfvars`, confira as decisões e mantenha a chave exclusiva `aula-29/grupo09/terraform.tfstate`. Nunca envie credenciais, estados ou os dois arquivos locais de configuração ao GitHub.

```bash
terraform init -backend-config=backend.hcl
cd ..
./verificacao/verifica-complementar.sh --linha-base
terraform -chdir=terraform plan -out=lab06.tfplan
terraform -chdir=terraform apply lab06.tfplan
./carregar-raw.sh
./rodar-jobs.sh
./verificacao/verifica.sh
./verificacao/verifica-complementar.sh
terraform -chdir=terraform plan
terraform -chdir=terraform plan -destroy -out=lab06-destroy.tfplan
terraform -chdir=terraform apply lab06-destroy.tfplan
./verificacao/verifica.sh --pos-destroy
./verificacao/verifica-complementar.sh --pos-destroy
```

Confira os planos antes de aplicar. Os scripts produzem dados sintéticos em `.runtime/dados-gerados/`. O backend S3 e o lock DynamoDB ficam preservados após o destroy. A linha de base deve ser registrada antes do apply; não a substitua para esconder uma divergência posterior.

## Validação

O código foi implementado no aplicativo do Claude e os verificadores foram revisados com Claude Opus 5.5 em esforço alto. Os testes e a execução real foram conferidos separadamente.

- 235 testes Python e 8 testes Terraform com provider simulado aprovados; sintaxe dos sete scripts shell e compilação Python aprovadas.
- A geração foi comparada byte a byte com a original; a AST dos quatro scripts Python do professor foi preservada, removendo apenas comentários/docstrings.
- Os `comment` do catálogo Glue foram mantidos porque são requisitos do exercício.
- O verificador complementar distingue recusa de autorização de falhas de SQL, timeout ou serviço; compara grants, catálogo e grant options; verifica S3 com objetos reais.
- No pós-destroy, diferencia recurso ausente de acesso negado e confere estado, bucket, roles, policy, jobs, databases, workgroup, registros e grants do LF.

```bash
bash testes/rodar-testes-locais.sh
```

O teste opcional de equivalência com o gerador original só roda quando `testes/original_app/` contém os arquivos originais fornecidos pelo professor; essa cópia não é entregue no PR. Na execução registrada, ele foi realizado e passou. `shellcheck` não estava instalado no Mac e não foi contabilizado como validação executada. O script informa etapas puladas quando uma ferramenta está ausente.

## Custo e limites

Os dois jobs concluídos reportaram 405 DPU-segundos: **US$ 0,0495 estimados para Glue** a US$ 0,44/DPU-hora, conforme [preços do AWS Glue](https://aws.amazon.com/glue/pricing/). Isso é uma estimativa, não uma fatura, e exclui S3, Athena e logs. Cada job tem 2 workers G.1X, timeout de 10 minutos, sem retries automáticos; cada consulta Athena tem teto de 200 MiB. Não há agendamento de execução.

Os logs `/aws-glue/jobs/*` são compartilhados pela conta e não são apagados. Os perfis mantêm a leitura de metadados `glue:Get*` herdada do template, mas não têm S3 direto nas camadas. A garantia de acesso aos dados é comprovada pelos testes reais.

## Conteúdo da entrega

`terraform/`, scripts de geração e processamento, verificadores, testes, `DECISOES.md` e evidências. O PR contém a saída integral do verificador oficial e do pós-destroy. Não inclui credenciais, `backend.hcl`, `terraform.tfvars`, `.terraform/`, estados, planos, dados gerados ou o material de aula.
