#!/usr/bin/env python3
import json
import os
import sys


def carregar():
    with open(os.environ["FAKE_AWS_ESTADO"], encoding="utf-8") as fh:
        return json.load(fh)


def salvar(estado):
    with open(os.environ["FAKE_AWS_ESTADO"], "w", encoding="utf-8") as fh:
        json.dump(estado, fh)


def registrar(argv):
    with open(os.environ["FAKE_AWS_LOG"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps(argv) + "\n")


def opcao(argv, nome, padrao=None):
    if nome in argv:
        return argv[argv.index(nome) + 1]
    return padrao


def lista(argv, nome):
    if nome not in argv:
        return []
    valores = []
    for item in argv[argv.index(nome) + 1:]:
        if item.startswith("--"):
            break
        valores.append(item)
    return valores


def mesmo_recurso(a, b):
    tipo = next(iter(b))
    if tipo not in a:
        return False
    x, y = a[tipo], b[tipo]
    if tipo == "Database":
        return x.get("Name") == y.get("Name")
    return x.get("DatabaseName") == y.get("DatabaseName") and x.get("Name") == y.get("Name")


def main():
    argv = sys.argv[1:]
    registrar(argv)
    estado = carregar()
    servico, acao = argv[0], argv[1]
    if servico == "sts" and acao == "get-caller-identity":
        print(estado["conta"])
        return 0
    if servico == "lakeformation" and acao == "get-data-lake-settings":
        admins = [{"DataLakePrincipalIdentifier": a} for a in estado["admins"]]
        print(json.dumps({"DataLakeSettings": {"DataLakeAdmins": admins}}))
        return 0
    if servico == "glue" and acao == "get-database":
        nome = opcao(argv, "--name")
        if nome not in estado["bancos"]:
            sys.stderr.write("An error occurred (EntityNotFoundException)\n")
            return 254
        print(json.dumps({"Database": estado["bancos"][nome]}))
        return 0
    if servico == "glue" and acao == "update-database":
        nome = opcao(argv, "--name")
        caminho = opcao(argv, "--database-input")[len("file://"):]
        with open(caminho, encoding="utf-8") as fh:
            entrada = json.load(fh)
        banco = {"Name": entrada["Name"], "CatalogId": estado["conta"], "CreateTime": "2026-11-25T00:00:00Z"}
        for campo, valor in entrada.items():
            banco[campo] = valor
        if os.environ.get("FAKE_AWS_PERDE_CAMPOS") == "1":
            banco.pop("Parameters", None)
        estado["bancos"][nome] = banco
        salvar(estado)
        print("{}")
        return 0
    if servico == "lakeformation" and acao == "list-permissions":
        recurso = json.loads(opcao(argv, "--resource"))
        achados = [p for p in estado["permissoes"] if mesmo_recurso(p["Resource"], recurso)]
        inicio = int(opcao(argv, "--next-token", "0"))
        pagina = achados[inicio:inicio + 1]
        resposta = {"PrincipalResourcePermissions": pagina}
        if inicio + 1 < len(achados):
            resposta["NextToken"] = str(inicio + 1)
        print(json.dumps(resposta))
        return 0
    if servico == "lakeformation" and acao == "revoke-permissions":
        if os.environ.get("FAKE_AWS_REVOGA_FALHO") == "1":
            print("{}")
            return 0
        principal = opcao(argv, "--principal").split("=", 1)[1]
        recurso = json.loads(opcao(argv, "--resource"))
        permissoes = set(lista(argv, "--permissions"))
        restantes = []
        for p in estado["permissoes"]:
            if p["Principal"]["DataLakePrincipalIdentifier"] == principal and mesmo_recurso(p["Resource"], recurso):
                p["Permissions"] = [x for x in p["Permissions"] if x not in permissoes]
                p["PermissionsWithGrantOption"] = [x for x in p.get("PermissionsWithGrantOption", [])
                                                   if x not in set(lista(argv, "--permissions-with-grant-option"))]
                if not p["Permissions"]:
                    continue
            restantes.append(p)
        estado["permissoes"] = restantes
        salvar(estado)
        print("{}")
        return 0
    sys.stderr.write(f"fake_aws: comando não suportado {argv}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
