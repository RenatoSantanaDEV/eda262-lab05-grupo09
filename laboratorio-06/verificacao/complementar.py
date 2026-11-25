#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

RAIZ = Path(__file__).resolve().parent.parent
TERRAFORM = RAIZ / "terraform"
SAIDA = RAIZ / ".runtime" / "verificacao"
REGIAO = "us-east-1"
CERTA = "11137"
TOTAL = "518400"
COLUNAS_TRUSTED = [
    "corrida_id", "motorista_id", "passageiro_id", "bairro", "inicio", "fim",
    "distancia_km", "duracao_min", "valor", "faixa_hora", "chegada",
]
CAMADAS = ("raw", "trusted", "refined")
TABELA = {"raw": "corridas", "trusted": "corridas", "refined": "madrugada_bairro"}
PADRAO_AUTORIZACAO = re.compile(
    r"insufficient lake formation permission|accessdenied|access denied|not authorized|"
    r"permission denied|does not have permission|is not authorized",
    re.IGNORECASE,
)
PADRAO_OCULTO = re.compile(
    r"table_not_found|schema_not_found|column_not_found|does not exist|cannot be resolved|"
    r"entitynotfound|not found",
    re.IGNORECASE,
)
PADRAO_INFRA = re.compile(
    r"throttl|timeout|timed out|rate exceeded|internal|service unavailable|could not connect|"
    r"expiredtoken|syntax_error|mismatched input|query exhausted|bytes scanned limit",
    re.IGNORECASE,
)

Executor = Callable[[List[str], Optional[Dict[str, str]]], Tuple[int, str, str]]
VERSAO_CANONIZACAO = "listas-como-conjuntos-v1"


def _json_canonico(valor: object) -> str:
    return json.dumps(valor, sort_keys=True, separators=(",", ":"))


def canonizar(valor: object) -> object:
    if isinstance(valor, dict):
        return {chave: canonizar(item) for chave, item in valor.items()}
    if isinstance(valor, list):
        return sorted((canonizar(item) for item in valor), key=_json_canonico)
    return valor


def hash_configuracao(configuracao: object) -> str:
    return hashlib.sha256(_json_canonico(canonizar(configuracao)).encode("utf-8")).hexdigest()


def hash_legado(configuracao: object) -> str:
    return hashlib.sha256(_json_canonico(configuracao).encode("utf-8")).hexdigest()


def executar(argumentos: List[str], ambiente: Optional[Dict[str, str]] = None) -> Tuple[int, str, str]:
    env = dict(os.environ)
    env["AWS_REGION"] = REGIAO
    env["AWS_DEFAULT_REGION"] = REGIAO
    env["AWS_PAGER"] = ""
    if ambiente is not None:
        for chave in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
            env.pop(chave, None)
        env.update(ambiente)
    processo = subprocess.run(argumentos, capture_output=True, text=True, env=env, timeout=300)
    return processo.returncode, processo.stdout, processo.stderr


def sanitizar(texto: str, conta: str) -> str:
    resultado = texto.replace(conta, "<CONTA>") if conta else texto
    resultado = re.sub(r"\b(ASIA|AKIA)[A-Z0-9]{16}\b", "<CHAVE>", resultado)
    return resultado


def classificar_negativa(motivo: str, existe_para_admin: bool) -> Tuple[bool, str]:
    if not motivo.strip():
        return False, "sem motivo registrado"
    if PADRAO_INFRA.search(motivo) and not PADRAO_AUTORIZACAO.search(motivo):
        return False, "falha de infraestrutura ou de SQL, não de autorização"
    if PADRAO_AUTORIZACAO.search(motivo):
        return True, "recusa de autorização"
    if PADRAO_OCULTO.search(motivo):
        if existe_para_admin:
            return True, "objeto existe para o administrador e foi ocultado do perfil"
        return False, "objeto não existe nem para o administrador"
    return False, "motivo não reconhecido como autorização"


def classificar_s3(rc: int, stderr: str) -> str:
    if rc == 0:
        return "PERMITIDO"
    if re.search(r"AccessDenied|\(403\)|Forbidden", stderr):
        return "NEGADO"
    if re.search(r"NoSuchKey|NoSuchBucket|\(404\)|Not Found", stderr):
        return "INEXISTENTE"
    return "ERRO"


def classificar_ausencia(rc: int, stderr: str, padrao_inexistente: str) -> str:
    if rc == 0:
        return "EXISTE"
    if re.search(padrao_inexistente, stderr):
        return "AUSENTE"
    if re.search(r"AccessDenied|\(403\)|Forbidden|not authorized|UnauthorizedOperation", stderr, re.IGNORECASE):
        return "SEM_PERMISSAO"
    return "ERRO"


def ler_tfvars() -> Dict[str, str]:
    arquivo = TERRAFORM / "terraform.tfvars"
    if not arquivo.exists():
        raise SystemExit("FALHA  terraform/terraform.tfvars ausente")
    valores = {}
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        achado = re.match(r'^\s*(sufixo|conta_aws|escopo_prefeitura)\s*=\s*"([^"]*)"', linha)
        if achado:
            valores[achado.group(1)] = achado.group(2)
        achado = re.match(r"^\s*colunas_ocultas_analista\s*=\s*\[(.*)\]", linha)
        if achado:
            valores["colunas_ocultas_analista"] = ",".join(re.findall(r'"([^"]+)"', achado.group(1)))
    for chave in ("sufixo", "conta_aws"):
        if not valores.get(chave):
            raise SystemExit(f"FALHA  {chave} ausente em terraform/terraform.tfvars")
    if not re.fullmatch(r"[a-z0-9]{3,20}", valores["sufixo"]) or not re.fullmatch(r"[0-9]{12}", valores["conta_aws"]):
        raise SystemExit("FALHA  sufixo ou conta_aws inválidos em terraform.tfvars")
    return valores


class Verificacao:
    def __init__(self, executor: Executor = executar) -> None:
        self.executor = executor
        self.resultados: List[Dict[str, str]] = []
        self.conta = ""

    def registrar(self, item: str, ok: bool, detalhe: str) -> None:
        self.resultados.append({"item": item, "status": "PASSA" if ok else "FALHA", "detalhe": detalhe})
        print(f"{'PASSA' if ok else 'FALHA'}  [{item}]  {sanitizar(detalhe, self.conta)}")

    def aws(self, argumentos: List[str], ambiente: Optional[Dict[str, str]] = None) -> Tuple[int, str, str]:
        return self.executor(["aws"] + argumentos, ambiente)

    def aws_json(self, argumentos: List[str], ambiente: Optional[Dict[str, str]] = None) -> dict:
        rc, out, err = self.aws(argumentos + ["--output", "json"], ambiente)
        if rc != 0:
            raise RuntimeError(f"aws {' '.join(argumentos[:2])} falhou: {err.strip()[:300]}")
        return json.loads(out or "{}")

    def guardar_conta(self, esperada: str) -> None:
        dados = self.aws_json(["sts", "get-caller-identity"])
        self.conta = esperada
        if dados.get("Account") != esperada:
            raise SystemExit("FALHA  as credenciais atuais não são da conta_aws declarada")

    def settings_atuais(self) -> object:
        dados = self.aws_json(["lakeformation", "get-data-lake-settings", "--catalog-id", self.conta])
        return dados.get("DataLakeSettings", {})

    def settings_hash(self) -> str:
        return hash_configuracao(self.settings_atuais())

    def listar_permissoes(self) -> List[dict]:
        itens: List[dict] = []
        token = ""
        vistos = set()
        for _ in range(200):
            argumentos = ["lakeformation", "list-permissions", "--catalog-id", self.conta]
            if token:
                argumentos += ["--next-token", token]
            pagina = self.aws_json(argumentos)
            itens.extend(pagina.get("PrincipalResourcePermissions", []))
            token = pagina.get("NextToken", "")
            if not token:
                return itens
            if token in vistos:
                raise RuntimeError("token de paginação repetido")
            vistos.add(token)
        raise RuntimeError("paginação excedeu 200 páginas")

    def comparar_linha_base(self, rotulo: str) -> None:
        arquivo = SAIDA / "linha-base-lf.json"
        if not arquivo.exists():
            self.registrar(rotulo, False, "sem linha de base: rode --linha-base antes do apply")
            return
        base = json.loads(arquivo.read_text(encoding="utf-8"))
        configuracao = self.settings_atuais()
        atual = hash_configuracao(configuracao)
        if base.get("canonizacao") == VERSAO_CANONIZACAO:
            ok = atual == base.get("sha256")
            self.registrar(rotulo, ok,
                           f"configuração global do Lake Formation {'idêntica' if ok else 'DIFERENTE'} da linha de base ({atual[:12]})")
            return
        ok = hash_legado(configuracao) == base.get("sha256")
        self.registrar(rotulo, ok,
                       "configuração global do Lake Formation idêntica à linha de base em formato antigo" if ok
                       else "linha de base em formato antigo (hash sem canonização de listas) diverge: "
                            "regenere com --linha-base antes do apply e compare de novo")

    def salvar(self, modo: str) -> Path:
        SAIDA.mkdir(parents=True, exist_ok=True)
        carimbo = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destino = SAIDA / f"complementar-{modo}-{carimbo}.json"
        conteudo = {"modo": modo, "gerado_em_utc": carimbo, "resultados": self.resultados,
                    "passou": all(r["status"] == "PASSA" for r in self.resultados)}
        destino.write_text(sanitizar(json.dumps(conteudo, ensure_ascii=False, indent=2), self.conta) + "\n", encoding="utf-8")
        return destino


def chave_recurso(recurso: dict) -> Optional[tuple]:
    if "Database" in recurso:
        return ("database", recurso["Database"].get("Name"))
    if "Table" in recurso:
        corpo = recurso["Table"]
        if "TableWildcard" in corpo:
            return ("todas_tabelas", corpo.get("DatabaseName"))
        return ("tabela", corpo.get("DatabaseName"), corpo.get("Name"))
    if "TableWithColumns" in recurso:
        corpo = recurso["TableWithColumns"]
        if "ColumnNames" in corpo:
            return ("colunas_incluidas", corpo.get("DatabaseName"), corpo.get("Name"), tuple(sorted(corpo["ColumnNames"])))
        excluidas = tuple(sorted(corpo.get("ColumnWildcard", {}).get("ExcludedColumnNames", [])))
        if not excluidas:
            return ("tabela", corpo.get("DatabaseName"), corpo.get("Name"))
        return ("colunas_excluidas", corpo.get("DatabaseName"), corpo.get("Name"), excluidas)
    if "DataLocation" in recurso:
        return ("local", recurso["DataLocation"].get("ResourceArn"))
    return None


def grants_esperados(bancos: Dict[str, str], escopo: str, ocultas: List[str]) -> Dict[str, set]:
    refined = bancos["refined"]
    if escopo == "tabela":
        alvo_prefeitura = ("tabela", refined, "madrugada_bairro")
    else:
        alvo_prefeitura = ("todas_tabelas", refined)
    return {
        "prefeitura": {
            (("database", refined), "DESCRIBE"),
            (alvo_prefeitura, "SELECT"),
            (alvo_prefeitura, "DESCRIBE"),
        },
        "analista": {
            (("database", bancos["trusted"]), "DESCRIBE"),
            (("database", refined), "DESCRIBE"),
            (("todas_tabelas", refined), "SELECT"),
            (("todas_tabelas", refined), "DESCRIBE"),
            (("colunas_excluidas", bancos["trusted"], "corridas", tuple(sorted(ocultas))), "SELECT"),
        },
    }


ALIASES_IAP = ("IAM_ALLOWED_PRINCIPALS", "IAMAllowedPrincipals")
TIPOS_RECURSO = ("Database", "Table", "TableWithColumns", "DataLocation")
TODAS_TABELAS_POR_COLUNAS = "todas_tabelas_por_colunas"
CHAVES_TWC_ALL_TABLES = {"CatalogId", "DatabaseName", "Name", "ColumnWildcard"}
PADRAO_ARN_PAPEL = re.compile(r"arn:aws[a-z-]*:iam::([0-9]{12}):role/\S+")


def _texto(valor: object) -> bool:
    return isinstance(valor, str) and bool(valor.strip())


def _lista_textos(valor: object) -> bool:
    return isinstance(valor, list) and all(_texto(v) for v in valor)


def _serializar(valor: object) -> str:
    try:
        return json.dumps(valor, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(valor)


def analisar_recurso(recurso: object) -> Tuple[Optional[tuple], Optional[object], str]:





    if not isinstance(recurso, dict) or not recurso:
        return None, None, "payload malformado: Resource ausente ou não é objeto"
    desconhecidos = sorted(str(k) for k in recurso if k not in TIPOS_RECURSO)
    if desconhecidos:
        return None, None, f"recurso desconhecido {desconhecidos}"
    if len(recurso) != 1:
        return None, None, f"payload malformado: Resource com mais de um tipo {sorted(recurso)}"
    tipo = next(iter(recurso))
    corpo = recurso[tipo]
    if not isinstance(corpo, dict):
        return None, None, f"payload malformado: {tipo} não é objeto"
    catalogo = corpo.get("CatalogId")
    if tipo == "Database":
        if not _texto(corpo.get("Name")):
            return None, catalogo, "payload malformado: Database sem Name"
        return ("database", corpo["Name"]), catalogo, ""
    if tipo == "DataLocation":
        if not _texto(corpo.get("ResourceArn")):
            return None, catalogo, "payload malformado: DataLocation sem ResourceArn"
        return ("local", corpo["ResourceArn"]), catalogo, ""
    banco = corpo.get("DatabaseName")
    if not _texto(banco):
        return None, catalogo, f"payload malformado: {tipo} sem DatabaseName"
    nome = corpo.get("Name")
    if tipo == "Table":
        if "TableWildcard" in corpo:
            if corpo["TableWildcard"] != {}:
                return None, catalogo, "payload malformado: TableWildcard não é objeto vazio"
            if nome is not None and nome != "ALL_TABLES":
                return None, catalogo, f"payload malformado: Table com Name '{nome}' e TableWildcard"
            return ("todas_tabelas", banco), catalogo, ""
        if not _texto(nome):
            return None, catalogo, "payload malformado: Table sem Name nem TableWildcard"
        return ("tabela", banco, nome), catalogo, ""
    if not _texto(nome):
        return None, catalogo, "payload malformado: TableWithColumns sem Name"
    if ("ColumnNames" in corpo) == ("ColumnWildcard" in corpo):
        return None, catalogo, "payload malformado: TableWithColumns exige exatamente um entre ColumnNames e ColumnWildcard"
    if nome == "ALL_TABLES":
        if set(corpo) - CHAVES_TWC_ALL_TABLES or corpo.get("ColumnWildcard") != {}:
            return None, catalogo, "payload malformado: TableWithColumns ALL_TABLES só é aceito com ColumnWildcard vazio"
        return (TODAS_TABELAS_POR_COLUNAS, banco), catalogo, ""
    if "ColumnNames" in corpo:
        colunas = corpo["ColumnNames"]
        if not _lista_textos(colunas) or not colunas:
            return None, catalogo, "payload malformado: ColumnNames inválido"
        return ("colunas_incluidas", banco, nome, tuple(sorted(colunas))), catalogo, ""
    curinga = corpo["ColumnWildcard"]
    if not isinstance(curinga, dict) or set(curinga) - {"ExcludedColumnNames"}:
        return None, catalogo, "payload malformado: ColumnWildcard inválido"
    excluidas = curinga.get("ExcludedColumnNames", [])
    if not _lista_textos(excluidas):
        return None, catalogo, "payload malformado: ExcludedColumnNames inválido"
    if not excluidas:

        return ("tabela", banco, nome), catalogo, ""
    return ("colunas_excluidas", banco, nome, tuple(sorted(excluidas))), catalogo, ""


def avaliar_grants(itens: List[dict], principais: Dict[str, str], esperados: Dict[str, set],
                   bancos: Dict[str, str]) -> Dict[str, List[str]]:
    nossos = set(bancos.values())
    problemas: Dict[str, List[str]] = {"prefeitura": [], "analista": [], "iam_allowed_principals": []}
    reais: Dict[str, set] = {"prefeitura": set(), "analista": set()}
    por_arn = {arn: rotulo for rotulo, arn in principais.items() if rotulo in reais and _texto(arn)}

    contas: Dict[str, str] = {}
    for rotulo in ("prefeitura", "analista"):
        arn = principais.get(rotulo)
        achado = PADRAO_ARN_PAPEL.fullmatch(arn) if isinstance(arn, str) else None
        if achado is None:
            problemas[rotulo].append("ARN do perfil inválido: impossível derivar a conta do catálogo")
        else:
            contas[rotulo] = achado.group(1)
    conta = None
    if len(set(contas.values())) == 1:
        conta = next(iter(contas.values()))
    elif len(set(contas.values())) > 1:
        for rotulo in contas:
            problemas[rotulo].append("ARNs dos perfis em contas diferentes: catálogo esperado indefinido")

    def cita_banco_nosso(valor: object) -> bool:
        texto = _serializar(valor)
        return any(banco in texto for banco in nossos)

    for item in itens:
        principal_bruto = item.get("Principal") if isinstance(item, dict) else None
        principal = principal_bruto.get("DataLakePrincipalIdentifier") if isinstance(principal_bruto, dict) else None
        if not _texto(principal):

            texto = _serializar(item)
            for arn, rotulo in por_arn.items():
                if arn in texto:
                    problemas[rotulo].append("payload malformado: Principal ilegível em item que cita o perfil")
            if any(alias in texto for alias in ALIASES_IAP) and cita_banco_nosso(item):
                problemas["iam_allowed_principals"].append("payload malformado: Principal ilegível citando banco do projeto")
            continue
        recurso = item.get("Resource")
        chave, catalogo, erro = analisar_recurso(recurso)

        if principal in ALIASES_IAP:
            if chave is not None:
                if chave[0] != "local" and chave[1] in nossos:
                    problemas["iam_allowed_principals"].append(
                        f"{principal} em {chave} {item.get('Permissions')} / grant option {item.get('PermissionsWithGrantOption')}")
            elif cita_banco_nosso(recurso):
                problemas["iam_allowed_principals"].append(f"{principal}: {erro} citando banco do projeto")
            continue

        rotulo = por_arn.get(principal)
        if rotulo is None:
            continue
        if erro:
            problemas[rotulo].append(f"{erro}: {_serializar(recurso)[:200]}")
            continue
        if catalogo is None or catalogo == "":
            problemas[rotulo].append(f"catálogo ausente em {chave}")
            continue
        if not isinstance(catalogo, str):
            problemas[rotulo].append(f"payload malformado: CatalogId não é texto em {chave}")
            continue
        if conta is None or catalogo != conta:
            problemas[rotulo].append(f"catálogo divergente ({catalogo}) em {chave}")
            continue
        permissoes = item.get("Permissions")
        opcoes = item.get("PermissionsWithGrantOption", [])
        if not _lista_textos(permissoes) or not permissoes:
            problemas[rotulo].append(f"payload malformado: Permissions inválido em {chave}")
            continue
        if not _lista_textos(opcoes):
            problemas[rotulo].append(f"payload malformado: PermissionsWithGrantOption inválido em {chave}")
            continue
        if opcoes:
            problemas[rotulo].append(f"grant option em {chave}")
        if chave[0] == TODAS_TABELAS_POR_COLUNAS:
            if permissoes != ["SELECT"]:
                problemas[rotulo].append(f"TableWithColumns ALL_TABLES aceita só SELECT, recebeu {permissoes} em {chave[1]}")
                continue
            chave = ("todas_tabelas", chave[1])
        for permissao in permissoes:
            reais[rotulo].add((chave, permissao))
    for rotulo in ("prefeitura", "analista"):
        for extra in sorted(reais[rotulo] - esperados[rotulo], key=str):
            problemas[rotulo].append(f"extra {extra[1]} em {extra[0]}")
        for falta in sorted(esperados[rotulo] - reais[rotulo], key=str):
            problemas[rotulo].append(f"ausente {falta[1]} em {falta[0]}")
    return problemas


class Atena:
    def __init__(self, verif: Verificacao, workgroup: str, espera_max: int = 180) -> None:
        self.v = verif
        self.wg = workgroup
        self.espera_max = espera_max

    def consultar(self, sql: str, ambiente: Dict[str, str]) -> Dict[str, object]:
        rc, out, err = self.v.aws(["athena", "start-query-execution", "--work-group", self.wg,
                                   "--query-string", sql, "--query", "QueryExecutionId", "--output", "text"], ambiente)
        if rc != 0:
            return {"estado": "NAO_INICIOU", "motivo": err.strip()}
        consulta = out.strip()
        inicio = time.time()
        while True:
            dados = self.v.aws_json(["athena", "get-query-execution", "--query-execution-id", consulta], ambiente)
            status = dados["QueryExecution"]["Status"]
            estado = status["State"]
            if estado == "SUCCEEDED":
                break
            if estado in ("FAILED", "CANCELLED"):
                motivo = status.get("StateChangeReason", "")
                erro = status.get("AthenaError", {})
                return {"estado": estado, "motivo": f"{motivo} {erro.get('ErrorMessage', '')}".strip(), "id": consulta}
            if time.time() - inicio > self.espera_max:
                return {"estado": "TEMPO_ESGOTADO", "motivo": "consulta não terminou no prazo", "id": consulta}
            time.sleep(2)
        resultado = self.v.aws_json(["athena", "get-query-results", "--query-execution-id", consulta], ambiente)
        linhas = resultado["ResultSet"]["Rows"]
        cabecalho = [c.get("VarCharValue", "") for c in linhas[0]["Data"]] if linhas else []
        primeira = [c.get("VarCharValue", "") for c in linhas[1]["Data"]] if len(linhas) > 1 else []
        return {"estado": "SUCCEEDED", "cabecalho": cabecalho, "primeira": primeira, "id": consulta}


def assumir(verif: Verificacao, arn: str, perfil: str) -> Dict[str, str]:
    dados = verif.aws_json(["sts", "assume-role", "--role-arn", arn, "--role-session-name",
                            f"complementar-{perfil}", "--duration-seconds", "900"])
    cred = dados["Credentials"]
    return {"AWS_ACCESS_KEY_ID": cred["AccessKeyId"], "AWS_SECRET_ACCESS_KEY": cred["SecretAccessKey"],
            "AWS_SESSION_TOKEN": cred["SessionToken"]}


def outputs_terraform(executor: Executor) -> Dict[str, object]:
    rc, out, err = executor(["terraform", f"-chdir={TERRAFORM}", "output", "-json"], None)
    if rc != 0:
        raise SystemExit(f"FALHA  terraform output falhou: {err.strip()[:200]}")
    dados = json.loads(out or "{}")
    if not dados:
        raise SystemExit("FALHA  sem outputs: rode terraform apply antes")
    return {k: v["value"] for k, v in dados.items()}


def objeto_real(verif: Verificacao, bucket: str, camada: str) -> Optional[str]:
    dados = verif.aws_json(["s3api", "list-objects-v2", "--bucket", bucket, "--prefix", f"{camada}/", "--max-items", "20"])
    for item in dados.get("Contents", []):
        if not item["Key"].endswith("/") and item.get("Size", 0) > 0:
            return item["Key"]
    return None


def modo_principal(v: Verificacao, tfvars: Dict[str, str]) -> None:
    saidas = outputs_terraform(v.executor)
    bancos = {c: saidas[f"database_{c}"] for c in CAMADAS}
    for camada, nome in bancos.items():
        if nome != f"eda_a29_{tfvars['sufixo']}_{camada}":
            raise SystemExit(f"FALHA  database fora do escopo: {nome}")
    bucket = saidas["bucket"]
    workgroup = saidas["workgroup"]
    principais = {"prefeitura": saidas["papel_prefeitura"], "analista": saidas["papel_analista"]}
    decisoes = saidas.get("decisoes", {})
    escopo = decisoes.get("escopo_prefeitura", tfvars.get("escopo_prefeitura", ""))
    ocultas = list(decisoes.get("colunas_ocultas_analista", []))
    visiveis = [c for c in COLUNAS_TRUSTED if c not in ocultas]

    for camada, nome in bancos.items():
        db = v.aws_json(["glue", "get-database", "--name", nome])["Database"]
        padrao = db.get("CreateTableDefaultPermissions", [])
        v.registrar(f"isolamento-{camada}", padrao == [],
                    f"{nome}: permissões padrão para tabelas novas = {padrao}")

    itens = v.listar_permissoes()
    problemas = avaliar_grants(itens, principais, grants_esperados(bancos, escopo, ocultas), bancos)
    v.registrar("iam-allowed-principals", not problemas["iam_allowed_principals"],
                "nenhum IAM_ALLOWED_PRINCIPALS nos 3 databases e 3 tabelas" if not problemas["iam_allowed_principals"]
                else "; ".join(problemas["iam_allowed_principals"]))
    for perfil in ("prefeitura", "analista"):
        v.registrar(f"grants-{perfil}", not problemas[perfil],
                    "grants exatamente iguais ao desenho" if not problemas[perfil] else "; ".join(problemas[perfil]))

    hoje_rc, hoje_out, _ = v.aws(["s3", "ls", f"s3://{bucket}/raw/corridas/"])
    dias = sorted(re.findall(r"dt=(\d{4}-\d{2}-\d{2})/", hoje_out)) if hoje_rc == 0 else []
    if not dias:
        raise SystemExit("FALHA  raw vazia: rode ./carregar-raw.sh")
    hoje = dias[-1]
    desde = (dt.date.fromisoformat(hoje) - dt.timedelta(days=6)).isoformat()

    tabela_trusted = v.aws_json(["glue", "get-table", "--database-name", bancos["trusted"], "--name", "corridas"])["Table"]
    colunas_admin = {c["Name"] for c in tabela_trusted["StorageDescriptor"]["Columns"]}
    atena = Atena(v, workgroup)
    ambientes = {p: assumir(v, arn, p) for p, arn in principais.items()}

    r = atena.consultar(f"SELECT sum(corridas) FROM {bancos['refined']}.madrugada_bairro WHERE dt >= '{desde}'", ambientes["prefeitura"])
    valor = r.get("primeira", [""])[0] if r["estado"] == "SUCCEEDED" else r["estado"]
    v.registrar("prefeitura-refined", valor == CERTA, f"soma da madrugada desde {desde} = {valor} (esperado {CERTA})")

    def negar(item: str, perfil: str, sql: str, existe: bool) -> None:
        resposta = atena.consultar(sql, ambientes[perfil])
        if resposta["estado"] == "SUCCEEDED":
            v.registrar(item, False, f"o perfil {perfil} conseguiu ler: {resposta.get('primeira')}")
            return
        ok, razao = classificar_negativa(str(resposta.get("motivo", "")), existe)
        v.registrar(item, ok, f"{resposta['estado']}: {razao} — {str(resposta.get('motivo', ''))[:160]}")

    for camada in ("raw", "trusted"):
        existe = v.aws(["glue", "get-table", "--database-name", bancos[camada], "--name", "corridas"])[0] == 0
        negar(f"prefeitura-{camada}-negada", "prefeitura",
              f"SELECT count(*) FROM {bancos[camada]}.corridas WHERE dt = '{hoje}'", existe)
    existe_raw = v.aws(["glue", "get-table", "--database-name", bancos["raw"], "--name", "corridas"])[0] == 0
    negar("analista-raw-negada", "analista", f"SELECT count(*) FROM {bancos['raw']}.corridas WHERE dt = '{hoje}'", existe_raw)

    r = atena.consultar(f"SELECT count(bairro) FROM {bancos['trusted']}.corridas", ambientes["analista"])
    valor = r.get("primeira", [""])[0] if r["estado"] == "SUCCEEDED" else r["estado"]
    v.registrar("analista-count-bairro", valor == TOTAL, f"count(bairro) = {valor} (esperado {TOTAL})")

    for coluna in ocultas:
        negar(f"analista-{coluna}-negada", "analista",
              f"SELECT {coluna} FROM {bancos['trusted']}.corridas WHERE dt = '{hoje}' LIMIT 1", coluna in colunas_admin)
    if "corrida_id" not in ocultas:
        r = atena.consultar(f"SELECT corrida_id FROM {bancos['trusted']}.corridas WHERE dt = '{hoje}' LIMIT 1", ambientes["analista"])
        ok = r["estado"] == "SUCCEEDED" and bool(r.get("primeira")) and r["primeira"][0] != ""
        v.registrar("analista-corrida-id-visivel", ok, f"SELECT corrida_id: {r['estado']}")
    r = atena.consultar(f"SELECT * FROM {bancos['trusted']}.corridas WHERE dt = '{hoje}' LIMIT 1", ambientes["analista"])
    cabecalho = [c for c in r.get("cabecalho", []) if c != "dt"] if r["estado"] == "SUCCEEDED" else []
    v.registrar("analista-colunas-visiveis", sorted(cabecalho) == sorted(visiveis),
                f"{len(cabecalho)} de {len(COLUNAS_TRUSTED)} colunas de dados visíveis: {','.join(cabecalho) or r['estado']}")

    for perfil, ambiente in ambientes.items():
        for camada in CAMADAS:
            rc, _, err = v.aws(["s3api", "list-objects-v2", "--bucket", bucket, "--prefix", f"{camada}/", "--max-items", "1"], ambiente)
            estado = classificar_s3(rc, err)
            v.registrar(f"s3-list-{perfil}-{camada}", estado == "NEGADO", f"ListBucket em {camada}/ como {perfil}: {estado}")
            chave = objeto_real(v, bucket, camada)
            if chave is None:
                v.registrar(f"s3-get-{perfil}-{camada}", False, f"sem objeto real em {camada}/ para testar")
                continue
            rc, _, err = v.aws(["s3api", "get-object", "--bucket", bucket, "--key", chave, os.devnull], ambiente)
            estado = classificar_s3(rc, err)
            v.registrar(f"s3-get-{perfil}-{camada}", estado == "NEGADO", f"GetObject em {chave} como {perfil}: {estado}")

    tabela = v.aws_json(["glue", "get-table", "--database-name", bancos["refined"], "--name", "madrugada_bairro"])["Table"]
    parametros = tabela.get("Parameters", {})
    colunas = tabela["StorageDescriptor"]["Columns"] + tabela.get("PartitionKeys", [])
    sem_comentario = [c["Name"] for c in colunas if not (c.get("Comment") or "").strip()]
    descricao = (tabela.get("Description") or "").strip()
    ok = len(descricao) >= 40 and all((parametros.get(k) or "").strip() for k in ("dono", "atualizacao", "grao"))\
        and not sem_comentario and parametros.get("classification") == "parquet"
    v.registrar("contrato-refined", ok, f"descrição {len(descricao)} caracteres; dono '{parametros.get('dono', '')}'; "
                f"atualização '{parametros.get('atualizacao', '')}'; colunas sem comentário: {','.join(sem_comentario) or '-'}")
    v.comparar_linha_base("lf-global-preservado")


def modo_pos_destroy(v: Verificacao, tfvars: Dict[str, str]) -> None:
    sufixo = tfvars["sufixo"]
    nome = f"eda-a29-{sufixo}"
    rc, out, err = v.executor(["terraform", f"-chdir={TERRAFORM}", "state", "list"], None)
    v.registrar("estado-terraform", rc == 0 and not out.strip(),
                "estado do projeto vazio" if rc == 0 and not out.strip()
                else f"rc={rc} recursos={len(out.split())} {err.strip()[:120]}")
    verificacoes = [
        ("bucket", ["s3api", "head-bucket", "--bucket", f"eda-a29-lake-{sufixo}"], r"\(404\)|Not Found|NoSuchBucket"),
        ("workgroup", ["athena", "get-work-group", "--work-group", f"{nome}-wg"], r"is not found|WorkGroup .* not found|InvalidRequestException"),
        ("politica-consumo", ["iam", "get-policy", "--policy-arn", f"arn:aws:iam::{v.conta}:policy/{nome}-consumo"], r"NoSuchEntity"),
    ]
    for camada in CAMADAS:
        verificacoes.append((f"database-{camada}", ["glue", "get-database", "--name", f"eda_a29_{sufixo}_{camada}"], r"EntityNotFoundException"))
    for job in ("trusted", "refined"):
        verificacoes.append((f"job-{job}", ["glue", "get-job", "--job-name", f"{nome}-{job}"], r"EntityNotFoundException"))
    for papel in ("glue", "lf", "prefeitura", "analista"):
        verificacoes.append((f"papel-{papel}", ["iam", "get-role", "--role-name", f"{nome}-{papel}"], r"NoSuchEntity"))
    for rotulo, argumentos, padrao in verificacoes:
        rc, _, err = v.aws(argumentos)
        estado = classificar_ausencia(rc, err, padrao)
        v.registrar(f"ausente-{rotulo}", estado == "AUSENTE", f"{rotulo}: {estado}")

    try:
        dados = v.aws_json(["lakeformation", "list-resources"])
        sobra = [r["ResourceArn"] for r in dados.get("ResourceInfoList", []) if f"eda-a29-lake-{sufixo}" in r.get("ResourceArn", "")]
        v.registrar("registro-lake-formation", not sobra, f"localizações restantes: {sobra or 'nenhuma'}")
    except RuntimeError as erro:
        v.registrar("registro-lake-formation", False, str(erro))
    try:
        bancos = {f"eda_a29_{sufixo}_{c}" for c in CAMADAS}
        restos = []
        for item in v.listar_permissoes():
            chave = chave_recurso(item.get("Resource", {}))
            principal = item.get("Principal", {}).get("DataLakePrincipalIdentifier", "")
            if (chave and len(chave) > 1 and chave[1] in bancos) or f":role/{nome}-" in principal:
                restos.append(str(chave))
        v.registrar("grants-lake-formation", not restos, f"grants restantes do exercício: {restos or 'nenhum'}")
    except RuntimeError as erro:
        v.registrar("grants-lake-formation", False, str(erro))
    try:
        dados = v.aws_json(["logs", "describe-log-groups", "--log-group-name-pattern", nome])
        grupos = [g["logGroupName"] for g in dados.get("logGroups", [])]
        v.registrar("log-groups", not grupos, f"log groups com {nome}: {grupos or 'nenhum'} (os /aws-glue/jobs/* são compartilhados da conta)")
    except RuntimeError as erro:
        v.registrar("log-groups", False, str(erro))
    v.comparar_linha_base("lf-global-preservado")


def modo_linha_base(v: Verificacao) -> None:
    SAIDA.mkdir(parents=True, exist_ok=True)
    valor = v.settings_hash()
    destino = SAIDA / "linha-base-lf.json"
    destino.write_text(json.dumps({"sha256": valor, "canonizacao": VERSAO_CANONIZACAO,
                                   "gerado_em_utc": dt.datetime.now(dt.timezone.utc).isoformat()}) + "\n",
                       encoding="utf-8")
    v.registrar("linha-base", True, f"hash da configuração global do Lake Formation registrado: {valor[:12]}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Verificação complementar fail-closed do Exercício 06.")
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument("--pos-destroy", action="store_true")
    grupo.add_argument("--linha-base", action="store_true")
    args = parser.parse_args(argv)
    tfvars = ler_tfvars()
    v = Verificacao()
    v.guardar_conta(tfvars["conta_aws"])
    modo = "pos-destroy" if args.pos_destroy else "linha-base" if args.linha_base else "principal"
    try:
        if args.linha_base:
            modo_linha_base(v)
        elif args.pos_destroy:
            modo_pos_destroy(v, tfvars)
        else:
            modo_principal(v, tfvars)
    except (RuntimeError, KeyError, ValueError, subprocess.TimeoutExpired) as erro:
        v.registrar("execucao", False, f"interrompida: {erro}")
    destino = v.salvar(modo)
    passou = bool(v.resultados) and all(r["status"] == "PASSA" for r in v.resultados)
    total = len(v.resultados)
    aprovados = sum(r["status"] == "PASSA" for r in v.resultados)
    print(f"\nResultado complementar ({modo}): {aprovados} PASSA, {total - aprovados} FALHA · evidência em {destino.relative_to(RAIZ)}")
    return 0 if passou else 1


if __name__ == "__main__":
    sys.exit(main())
