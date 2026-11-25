import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
SCRIPT = RAIZ / "terraform" / "lf" / "isolar-camadas.sh"
FAKE = Path(__file__).resolve().parent / "fake_aws.py"
CONTA = "111122223333"
EXECUTOR = f"arn:aws:iam::{CONTA}:user/executor-grupo09"
IAP = "IAM_ALLOWED_PRINCIPALS"


def iap(recurso):
    return {"Principal": {"DataLakePrincipalIdentifier": IAP}, "Resource": recurso,
            "Permissions": ["ALL"], "PermissionsWithGrantOption": []}


def banco(nome):
    return {"Name": nome, "CatalogId": CONTA, "Description": f"camada {nome}",
            "Parameters": {"origem": "terraform"}, "CreateTime": "2026-11-25T00:00:00Z",
            "CreateTableDefaultPermissions": [{"Principal": {"DataLakePrincipalIdentifier": IAP}, "Permissions": ["ALL"]}]}


def estado_inicial():
    nossos = {"raw": "corridas", "trusted": "corridas", "refined": "madrugada_bairro"}
    bancos = {f"eda_a29_grupo09_{c}": banco(f"eda_a29_grupo09_{c}") for c in nossos}
    bancos["eda_a29_outro_raw"] = banco("eda_a29_outro_raw")
    permissoes = []
    for camada, tabela in nossos.items():
        nome = f"eda_a29_grupo09_{camada}"
        permissoes.append(iap({"Database": {"CatalogId": CONTA, "Name": nome}}))
        permissoes.append(iap({"Table": {"CatalogId": CONTA, "DatabaseName": nome, "Name": tabela}}))
    permissoes.append(iap({"Database": {"CatalogId": CONTA, "Name": "eda_a29_outro_raw"}}))
    permissoes.append(iap({"Table": {"CatalogId": CONTA, "DatabaseName": "eda_a29_outro_raw", "Name": "corridas"}}))
    permissoes.append({"Principal": {"DataLakePrincipalIdentifier": f"arn:aws:iam::{CONTA}:role/outro-grupo"},
                       "Resource": {"Table": {"CatalogId": CONTA, "DatabaseName": "eda_a29_grupo09_refined", "Name": "madrugada_bairro"}},
                       "Permissions": ["SELECT"], "PermissionsWithGrantOption": []})
    return {"conta": CONTA, "admins": [EXECUTOR, f"arn:aws:iam::{CONTA}:user/outro"], "bancos": bancos, "permissoes": permissoes}


@pytest.fixture
def ambiente(tmp_path):
    binarios = tmp_path / "bin"
    binarios.mkdir()
    aws = binarios / "aws"
    aws.write_text(f"#!/bin/sh\nexec python3 '{FAKE}' \"$@\"\n", encoding="utf-8")
    aws.chmod(aws.stat().st_mode | stat.S_IEXEC)
    estado = tmp_path / "estado.json"
    estado.write_text(json.dumps(estado_inicial()), encoding="utf-8")
    log = tmp_path / "log.jsonl"
    log.write_text("", encoding="utf-8")
    env = {
        "PATH": f"{binarios}:{os.environ['PATH']}",
        "FAKE_AWS_ESTADO": str(estado),
        "FAKE_AWS_LOG": str(log),
        "LF_CONTA": CONTA,
        "LF_REGIAO": "us-east-1",
        "LF_SUFIXO": "grupo09",
        "LF_EXECUTOR": EXECUTOR,
        "LF_RAW": "eda_a29_grupo09_raw:corridas",
        "LF_TRUSTED": "eda_a29_grupo09_trusted:corridas",
        "LF_REFINED": "eda_a29_grupo09_refined:madrugada_bairro",
    }
    return env, estado, log


def rodar(env, **extra):
    final = dict(env)
    final.update(extra)
    return subprocess.run(["bash", str(SCRIPT)], env=final, capture_output=True, text=True, timeout=120)


def chamadas(log, acao):
    return [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if json.loads(l)[1] == acao]


def mutacoes(log):
    return chamadas(log, "update-database") + chamadas(log, "revoke-permissions")


def test_isola_somente_os_tres_databases_e_tabelas(ambiente):
    env, estado, log = ambiente
    r = rodar(env)
    assert r.returncode == 0, r.stderr
    final = json.loads(estado.read_text(encoding="utf-8"))
    for camada in ("raw", "trusted", "refined"):
        b = final["bancos"][f"eda_a29_grupo09_{camada}"]
        assert b["CreateTableDefaultPermissions"] == []
        assert b["Description"] == f"camada eda_a29_grupo09_{camada}"
        assert b["Parameters"] == {"origem": "terraform"}
    assert final["bancos"]["eda_a29_outro_raw"]["CreateTableDefaultPermissions"] != []
    restantes_iap = [p for p in final["permissoes"] if p["Principal"]["DataLakePrincipalIdentifier"] == IAP]
    assert all("eda_a29_outro_raw" in json.dumps(p["Resource"]) for p in restantes_iap)
    assert len(restantes_iap) == 2
    assert any("outro-grupo" in p["Principal"]["DataLakePrincipalIdentifier"] for p in final["permissoes"])
    for chamada in chamadas(log, "revoke-permissions"):
        assert f"DataLakePrincipalIdentifier={IAP}" in chamada
        assert "eda_a29_grupo09_" in chamada[chamada.index("--resource") + 1]
    assert len(chamadas(log, "revoke-permissions")) == 6
    assert len(chamadas(log, "update-database")) == 3


def test_e_idempotente(ambiente):
    env, _, log = ambiente
    assert rodar(env).returncode == 0
    log.write_text("", encoding="utf-8")
    r = rodar(env)
    assert r.returncode == 0, r.stderr
    assert mutacoes(log) == []


@pytest.mark.parametrize("extra", [
    {"LF_CONTA": "999999999999"},
    {"LF_EXECUTOR": f"arn:aws:iam::{CONTA}:user/nao-admin"},
    {"LF_RAW": "eda_a29_outro_raw:corridas"},
    {"LF_REFINED": "eda_a29_grupo09_refined:outra_tabela"},
    {"LF_REGIAO": "sa-east-1"},
    {"LF_SUFIXO": "grupo-09"},
    {"LF_TRUSTED": ""},
])
def test_guardas_impedem_qualquer_mutacao(ambiente, extra):
    env, estado, log = ambiente
    antes = estado.read_text(encoding="utf-8")
    r = rodar(env, **extra)
    assert r.returncode != 0
    assert mutacoes(log) == []
    assert estado.read_text(encoding="utf-8") == antes


def test_falha_se_revogacao_nao_tiver_efeito(ambiente):
    env, _, _ = ambiente
    r = rodar(env, FAKE_AWS_REVOGA_FALHO="1")
    assert r.returncode != 0
    assert "continua" in r.stderr


def test_falha_se_update_perder_campos(ambiente):
    env, _, _ = ambiente
    r = rodar(env, FAKE_AWS_PERDE_CAMPOS="1")
    assert r.returncode != 0
    assert "perdeu campos" in r.stderr
