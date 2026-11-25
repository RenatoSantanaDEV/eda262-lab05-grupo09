#!/usr/bin/env bash
set -euo pipefail

falha() {
  printf 'isolar-camadas: FALHA: %s\n' "$1" >&2
  exit 1
}

for comando in aws python3; do
  command -v "$comando" >/dev/null 2>&1 || falha "comando ausente: $comando"
done

for variavel in LF_CONTA LF_REGIAO LF_SUFIXO LF_EXECUTOR LF_RAW LF_TRUSTED LF_REFINED; do
  [[ -n "${!variavel:-}" ]] || falha "variável ausente: $variavel"
done

[[ "$LF_REGIAO" == "us-east-1" ]] || falha "região inesperada: $LF_REGIAO"
[[ "$LF_SUFIXO" =~ ^[a-z0-9]{3,20}$ ]] || falha "sufixo inválido: $LF_SUFIXO"
[[ "$LF_CONTA" =~ ^[0-9]{12}$ ]] || falha "conta inválida"
export AWS_REGION="$LF_REGIAO" AWS_DEFAULT_REGION="$LF_REGIAO" AWS_PAGER=""

trabalho="$(mktemp -d)"
limpar() {
  find "$trabalho" -type f -delete
  rmdir "$trabalho"
}
trap limpar EXIT

conta_atual="$(aws sts get-caller-identity --query Account --output text)" || falha "sts get-caller-identity falhou"
[[ "$conta_atual" == "$LF_CONTA" ]] || falha "as credenciais atuais são de outra conta"

aws lakeformation get-data-lake-settings --catalog-id "$LF_CONTA" --output json >"$trabalho/settings.json" ||
  falha "não foi possível ler as configurações do Lake Formation"
python3 - "$trabalho/settings.json" "$LF_EXECUTOR" <<'PY' || falha "o executor não é administrador do Lake Formation"
import json, sys
dados = json.load(open(sys.argv[1], encoding="utf-8"))
admins = {a.get("DataLakePrincipalIdentifier") for a in dados.get("DataLakeSettings", {}).get("DataLakeAdmins", [])}
sys.exit(0 if sys.argv[2] in admins else 1)
PY

alvo() {
  local camada="$1" valor="$2" tabela_esperada="$3"
  local banco="${valor%%:*}" tabela="${valor#*:}"
  [[ "$banco" == "eda_a29_${LF_SUFIXO}_${camada}" ]] || falha "database fora do escopo: $banco"
  [[ "$tabela" == "$tabela_esperada" ]] || falha "tabela fora do escopo: $banco.$tabela"
  printf '%s %s\n' "$banco" "$tabela"
}

{
  alvo raw "$LF_RAW" corridas
  alvo trusted "$LF_TRUSTED" corridas
  alvo refined "$LF_REFINED" madrugada_bairro
} >"$trabalho/alvos.txt"

isolar_database() {
  local banco="$1"
  aws glue get-database --catalog-id "$LF_CONTA" --name "$banco" --output json >"$trabalho/db.json" ||
    falha "get-database falhou para $banco"
  python3 - "$trabalho/db.json" "$banco" "$trabalho/db-input.json" "$trabalho/estado.txt" <<'PY' || falha "não foi possível preparar o update de $banco"
import json, sys
db = json.load(open(sys.argv[1], encoding="utf-8"))["Database"]
if db.get("Name") != sys.argv[2]:
    sys.exit("nome divergente")
entrada = {"Name": db["Name"], "CreateTableDefaultPermissions": []}
for campo in ("Description", "LocationUri", "Parameters", "TargetDatabase", "FederatedDatabase"):
    if campo in db:
        entrada[campo] = db[campo]
json.dump(entrada, open(sys.argv[3], "w", encoding="utf-8"))
open(sys.argv[4], "w", encoding="utf-8").write("ja_isolado" if db.get("CreateTableDefaultPermissions", []) == [] else "alterar")
PY
}

confirmar_database() {
  local banco="$1"
  aws glue get-database --catalog-id "$LF_CONTA" --name "$banco" --output json >"$trabalho/db-depois.json" ||
    falha "releitura falhou para $banco"
  python3 - "$trabalho/db.json" "$trabalho/db-depois.json" <<'PY' || falha "database não ficou isolado ou perdeu campos"
import json, sys
antes = json.load(open(sys.argv[1], encoding="utf-8"))["Database"]
depois = json.load(open(sys.argv[2], encoding="utf-8"))["Database"]
if depois.get("CreateTableDefaultPermissions", []) != []:
    sys.exit(1)
for campo in ("Name", "Description", "LocationUri", "Parameters", "TargetDatabase", "FederatedDatabase"):
    if antes.get(campo) != depois.get(campo):
        sys.exit(1)
PY
}

listar_iap() {
  local recurso="$1" saida="$2"
  local token="" pagina=0
  : >"$saida"
  while :; do
    pagina=$((pagina + 1))
    ((pagina <= 50)) || falha "paginação excedeu 50 páginas"
    if [[ -n "$token" ]]; then
      aws lakeformation list-permissions --catalog-id "$LF_CONTA" --resource "$recurso" \
        --next-token "$token" --output json >"$trabalho/pagina.json" || falha "list-permissions falhou"
    else
      aws lakeformation list-permissions --catalog-id "$LF_CONTA" --resource "$recurso" \
        --output json >"$trabalho/pagina.json" || falha "list-permissions falhou"
    fi
    token="$(python3 - "$trabalho/pagina.json" "$saida" "$recurso" <<'PY'
import json, sys
pagina = json.load(open(sys.argv[1], encoding="utf-8"))
alvo = json.loads(sys.argv[3])
tipo = next(iter(alvo))
with open(sys.argv[2], "a", encoding="utf-8") as fh:
    for item in pagina.get("PrincipalResourcePermissions", []):
        if item.get("Principal", {}).get("DataLakePrincipalIdentifier") != "IAM_ALLOWED_PRINCIPALS":
            continue
        recurso = item.get("Resource", {})
        if tipo not in recurso:
            continue
        corpo = recurso[tipo]
        esperado = alvo[tipo]
        if tipo == "Database" and corpo.get("Name") != esperado["Name"]:
            continue
        if tipo == "Table" and (corpo.get("DatabaseName") != esperado["DatabaseName"] or corpo.get("Name") != esperado["Name"]):
            continue
        fh.write(json.dumps({"Permissions": item.get("Permissions", []),
                             "PermissionsWithGrantOption": item.get("PermissionsWithGrantOption", [])}) + "\n")
print(pagina.get("NextToken", ""))
PY
)" || falha "não foi possível interpretar list-permissions"
    [[ -n "$token" ]] || break
  done
}

revogar_iap() {
  local recurso="$1" rotulo="$2"
  listar_iap "$recurso" "$trabalho/iap.jsonl"
  if [[ ! -s "$trabalho/iap.jsonl" ]]; then
    printf 'isolar-camadas: %s sem IAM_ALLOWED_PRINCIPALS\n' "$rotulo"
    return 0
  fi
  python3 - "$trabalho/iap.jsonl" "$trabalho/perms.txt" <<'PY' || falha "não foi possível consolidar permissões"
import json, sys
perms, grant = set(), set()
for linha in open(sys.argv[1], encoding="utf-8"):
    item = json.loads(linha)
    perms.update(item["Permissions"])
    grant.update(item["PermissionsWithGrantOption"])
open(sys.argv[2], "w", encoding="utf-8").write(json.dumps({"p": sorted(perms), "g": sorted(grant)}))
PY
  local permissoes grant
  permissoes="$(python3 -c 'import json,sys;print(" ".join(json.load(open(sys.argv[1]))["p"]))' "$trabalho/perms.txt")" || falha "permissões ilegíveis"
  grant="$(python3 -c 'import json,sys;print(" ".join(json.load(open(sys.argv[1]))["g"]))' "$trabalho/perms.txt")" || falha "grant options ilegíveis"
  if [[ -n "$permissoes" ]]; then
    local argumentos=(lakeformation revoke-permissions --catalog-id "$LF_CONTA"
      --principal DataLakePrincipalIdentifier=IAM_ALLOWED_PRINCIPALS --resource "$recurso" --permissions)
    local lista=()
    read -r -a lista <<<"$permissoes"
    argumentos+=("${lista[@]}")
    if [[ -n "$grant" ]]; then
      read -r -a lista <<<"$grant"
      argumentos+=(--permissions-with-grant-option "${lista[@]}")
    fi
    aws "${argumentos[@]}" >/dev/null || falha "revoke-permissions falhou em $rotulo"
  fi
  listar_iap "$recurso" "$trabalho/iap.jsonl"
  [[ ! -s "$trabalho/iap.jsonl" ]] || falha "IAM_ALLOWED_PRINCIPALS continua em $rotulo"
  printf 'isolar-camadas: %s IAM_ALLOWED_PRINCIPALS revogado (%s)\n' "$rotulo" "$permissoes"
}

while read -r banco tabela <&3; do
  isolar_database "$banco"
  estado="$(cat "$trabalho/estado.txt")"
  if [[ "$estado" == "alterar" ]]; then
    aws glue update-database --catalog-id "$LF_CONTA" --name "$banco" \
      --database-input "file://$trabalho/db-input.json" >/dev/null || falha "update-database falhou para $banco"
    printf 'isolar-camadas: %s sem permissões padrão para tabelas novas\n' "$banco"
  else
    printf 'isolar-camadas: %s já estava sem permissões padrão\n' "$banco"
  fi
  confirmar_database "$banco"
  revogar_iap "{\"Database\":{\"CatalogId\":\"$LF_CONTA\",\"Name\":\"$banco\"}}" "database $banco"
  revogar_iap "{\"Table\":{\"CatalogId\":\"$LF_CONTA\",\"DatabaseName\":\"$banco\",\"Name\":\"$tabela\"}}" "tabela $banco.$tabela"
done 3<"$trabalho/alvos.txt"

printf 'isolar-camadas: PASSA: 3 databases e 3 tabelas de eda_a29_%s isolados\n' "$LF_SUFIXO"
