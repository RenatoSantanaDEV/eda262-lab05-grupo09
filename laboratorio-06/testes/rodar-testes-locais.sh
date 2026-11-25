#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.." || exit 2
falhas=0
etapa() {
  printf '\n== %s\n' "$1"
  shift
  if "$@"; then printf 'PASSA  %s\n' "$*"; else printf 'FALHA  %s\n' "$*"; falhas=$((falhas + 1)); fi
}
for script in carregar-raw.sh rodar-jobs.sh como.sh verificacao/verifica.sh verificacao/verifica-complementar.sh terraform/lf/isolar-camadas.sh testes/rodar-testes-locais.sh; do
  etapa "sintaxe bash $script" bash -n "$script"
done
if command -v shellcheck >/dev/null 2>&1; then
  etapa "shellcheck" shellcheck -s bash carregar-raw.sh rodar-jobs.sh como.sh verificacao/verifica-complementar.sh terraform/lf/isolar-camadas.sh testes/rodar-testes-locais.sh
  etapa "shellcheck do verificador oficial (SC2164 herdado)" shellcheck -s bash -e SC2164 verificacao/verifica.sh
else
  printf 'PULADO shellcheck ausente\n'
fi
etapa "compilação Python" python3 -m py_compile app/gerador-eventos.py app/plantar-problemas.py terraform/scripts/pipeline-corridas.py terraform/scripts/refinar-madrugada.py verificacao/complementar.py
if python3 -c 'import pytest' >/dev/null 2>&1; then
  etapa "pytest" python3 -m pytest -q -p no:cacheprovider testes
else
  printf 'PULADO pytest ausente\n'
fi
if command -v terraform >/dev/null 2>&1; then
  etapa "terraform fmt" terraform -chdir=terraform fmt -check -recursive
  etapa "terraform init sem backend" terraform -chdir=terraform init -backend=false -input=false -no-color
  etapa "terraform validate" terraform -chdir=terraform validate -no-color
  etapa "terraform test (mock, sem AWS)" terraform -chdir=terraform test -no-color
else
  printf 'PULADO terraform ausente\n'
fi
printf '\nResultado local: %d falha(s)\n' "$falhas"
[[ "$falhas" -eq 0 ]]
