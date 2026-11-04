# DECISOES.md - Exercício 05 - Grupo 09

Renato Santana (representante), João Augusto Pimentel, Raphael Barros e Lara Pessoa.

Execução real em 04/11/2026, AWS us-east-1.

## DECISÃO 01 - modo de escrita

Escolhemos `sobrescrever`, aceitando reconstruir as partições de evento afetadas em vez de acumular arquivos: o verificador executou novamente o mesmo job e mediu **518.400 → 518.400 linhas** e **11.137 → 11.137 corridas de madrugada**, comprovando idempotência.

## DECISÃO 02 - chave da linha

Escolhemos `corrida_id`, mantendo a primeira chegada e descartando reenvios da mesma corrida: foram removidas **17.280 duplicatas**, restando **518.400 linhas e 518.400 corridas distintas**; aceitamos não tratar um reenvio com o mesmo identificador como nova corrida.

## DECISÃO 03 - janela de atraso

Escolhemos **3 dias**, a menor janela que inclui as **1.591 corridas** atrasadas de 01/11/2026 e mantém **11.137 corridas** de madrugada entre 29/10 e 04/11/2026, com **0 registros fora da janela** nesta carga; aceitamos excluir atrasos maiores da carga diária e recuperá-los por backfill. O script fornecido lê a raw inteira: nesta execução, **30 dias de chegada, 32 arquivos e 535.680 linhas**; a janela filtra o atraso das linhas e não reduz a leitura física para quatro pastas.

## DECISÃO 04 - backfill

Para corrigir um dia de evento, inclusive com chegadas além da janela, usamos `./rodar-job.sh DIAS=2026-11-01` (substituindo a data pelo dia afetado): o teste reescreveu **1 partição**, gravou **17.280 linhas** e manteve **518.400 linhas / 518.400 corridas distintas / 11.137 corridas de madrugada** na tabela; aceitamos o custo de reler a raw para reconstruir esse dia, sem duplicar, porque `--DIAS` dispensa o limite superior de atraso, `corrida_id` deduplica e a sobrescrita dinâmica troca somente a partição selecionada.

## Evidências

As contagens foram medidas no Glue e no Athena. Ver `evidencias/verifica.txt`, `evidencias/glue-primeiro.json`, `evidencias/glue-backfill.json` e `evidencias/athena-*.json`. O teste de backfill usa o dia que já estava completo: comprova a reescrita de uma partição sem duplicação; não simula uma nova chegada com atraso superior a três dias.

O backend remoto S3 e a tabela de lock preexistentes do grupo foram preservados; a chave de estado desta atividade é exclusiva. O verificador confirmou 70/70 nos critérios automáticos e PASSA [5] após o destroy. As quatro decisões compõem os 20% sujeitos à avaliação do professor.
