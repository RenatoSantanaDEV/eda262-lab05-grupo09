# DECISOES — Laboratório 06 · Grupo 09

Execução real em 25/11/2026, AWS `us-east-1`. As três decisões são configuradas em `terraform.tfvars.example` e verificadas nos registros em `evidencias/`.

## 1. Alcance da Prefeitura na refined

Escolhemos `escopo_prefeitura = "tabela"`: a Prefeitura lê apenas `madrugada_bairro`, mantendo tabelas futuras da refined invisíveis até uma concessão explícita, e medimos **11.137 corridas** nos últimos sete dias, com **raw, trusted e S3 direto recusados**.

A soma usa `dt >= '2026-11-19'`, sobre dados até 25/11/2026. Os testes extras confirmaram `AccessDenied` no acesso S3 a objetos reais das três camadas e recusa de permissão no Athena para raw/trusted. A tabela publicada contém somente bairro, quantidade de corridas, receita e data; nenhum identificador individual.

## 2. Colunas ocultas da analista

Ocultamos `motorista_id` e `passageiro_id`, mantendo `corrida_id` para investigar uma corrida reclamada, e medimos **518.400 linhas**, **9 de 11 colunas de dados visíveis**, leitura de `corrida_id` permitida e consultas aos dois identificadores pessoais recusadas.

As colunas visíveis são `corrida_id`, `bairro`, `inicio`, `fim`, `distancia_km`, `duracao_min`, `valor`, `faixa_hora` e `chegada`; `dt` é a partição. A analista também recebe leitura da refined, conforme o template, mas não da raw nem do S3 direto. O catálogo completo foi consultado como administrador para confirmar que as colunas recusadas existem.

## 3. Contrato da refined

Definimos a refined como corridas e receita em BRL por bairro e dia, entre **00:00 e 05:59 UTC**, sem identificadores individuais e com atualização após o pipeline diário, e medimos no catálogo uma descrição de **137 caracteres**, dono **Grupo09 - engenharia de dados** e comentário em todas as colunas, incluindo a partição.

O processamento gerou **660 agregações**, cobrindo **22 bairros e 30 dias**. A atualização sobrescreve apenas as partições `dt` recalculadas. O pipeline removeu **17.280 duplicatas** das **535.680 linhas** da raw, preservando **518.400 corridas únicas** na trusted.

## Isolamento na conta compartilhada

O Terraform do laboratório lê, mas não gerencia, as configurações globais do Lake Formation. O acesso administrativo do grupo havia sido removido por outra operação da conta; sua restauração foi autorizada separadamente, preservando o administrador então existente e as demais configurações. A linha de base foi registrada depois dessa restauração e antes do apply.

O isolamento revoga `IAM_ALLOWED_PRINCIPALS` apenas nos três databases e três tabelas deste exercício e zera as permissões padrão de tabelas novas somente nesses databases. O plano criou **32 recursos**, com **0 alterações e 0 remoções** de recursos existentes. Uma segunda comparação do Terraform retornou **No changes**.

A comparação das configurações globais ignora somente a ordem das listas devolvidas pela API. A correção do hash usou o snapshot completo salvo antes do apply, conferido contra seu hash original, sem substituir a referência pelo estado posterior.

A verificação complementar aprovou **29 de 29 verificações**: grants exatos dos dois perfis, isolamento das três camadas, consultas e recusas previstas, S3 direto bloqueado, contrato e configuração global preservada. Ver [`evidencias/verifica-complementar.txt`](evidencias/verifica-complementar.txt).

## Saída do verificador oficial

```text
OK     [0]  (elimina)  backend remoto s3, três databases (um por camada) e a refined em Parquet
PASSA  [1]  (20%)  a Prefeitura lê a refined: 11137 corridas na madrugada dos últimos 7 dias
PASSA  [2]  (25%)  a Prefeitura não lê a trusted, nem a raw, nem o S3 direto
PASSA  [3]  (15%)  a analista lê a trusted (518400 corridas) e não vê passageiro_id
PASSA  [4]  (10%)  o contrato da refined está no catálogo: descrição (137 caracteres), dono 'Grupo09 - engenharia de dados', comentário em toda coluna

Critérios automáticos: 70 / 70 pontos-percentuais
Falta o que o script não mede: o critério 5 (rode com --pos-destroy depois do destroy) e o DECISOES.md (20%).

evidência: sufixo=grupo09 hoje=2026-11-25 prefeitura_refined=11137 prefeitura_trusted=RECUSADO prefeitura_raw=RECUSADO prefeitura_s3=RECUSADO analista_trusted=518400 analista_passageiro=RECUSADO {"colunas_ocultas_analista":["motorista_id","passageiro_id"],"escopo_prefeitura":"tabela"}
```

## Remoção limpa

```text
PASSA  [5]  (10%)  destroy limpo: nenhum recurso eda-a29-grupo09 sobrou
```

O Terraform removeu exatamente os 32 recursos criados. As 17 verificações complementares pós-destroy passaram: estado sem recursos, bucket/roles/policy/jobs/databases/workgroup ausentes, nenhum registro ou grant remanescente do exercício no Lake Formation e configurações globais preservadas. O bucket de backend e o lock DynamoDB continuam disponíveis.

Os critérios automáticos totalizam 80 pontos-percentuais (70 antes do destroy e 10 após); os 20% deste documento dependem da avaliação manual do professor.
