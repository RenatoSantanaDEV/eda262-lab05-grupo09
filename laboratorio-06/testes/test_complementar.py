import importlib.util
import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("complementar", RAIZ / "verificacao" / "complementar.py")
comp = importlib.util.module_from_spec(SPEC)
sys.modules["complementar"] = comp
SPEC.loader.exec_module(comp)

CONTA = "111122223333"
BANCOS = {c: f"eda_a29_grupo09_{c}" for c in ("raw", "trusted", "refined")}
PREF = f"arn:aws:iam::{CONTA}:role/eda-a29-grupo09-prefeitura"
ANA = f"arn:aws:iam::{CONTA}:role/eda-a29-grupo09-analista"


@pytest.mark.parametrize("motivo,existe,esperado", [
    ("Insufficient Lake Formation permission(s) on corridas", True, True),
    ("AccessDeniedException: not authorized", False, True),
    ("TABLE_NOT_FOUND: line 1:22: Table 'awsdatacatalog.x.corridas' does not exist", True, True),
    ("COLUMN_NOT_FOUND: line 1:8: Column 'passageiro_id' cannot be resolved", True, True),
    ("TABLE_NOT_FOUND: line 1:22: Table 'x' does not exist", False, False),
    ("Query timeout", True, False),
    ("SYNTAX_ERROR: line 1:1: mismatched input", True, False),
    ("ThrottlingException: Rate exceeded", True, False),
    ("Query exhausted resources at this scale factor", True, False),
    ("", True, False),
    ("algo inesperado", True, False),
])
def test_negativa_so_aprova_autorizacao(motivo, existe, esperado):
    assert comp.classificar_negativa(motivo, existe)[0] is esperado


@pytest.mark.parametrize("rc,err,esperado", [
    (0, "", "PERMITIDO"),
    (254, "An error occurred (AccessDenied) when calling the GetObject operation", "NEGADO"),
    (254, "An error occurred (403) when calling the HeadObject operation: Forbidden", "NEGADO"),
    (254, "An error occurred (NoSuchKey)", "INEXISTENTE"),
    (255, "Could not connect to the endpoint URL", "ERRO"),
])
def test_classifica_s3(rc, err, esperado):
    assert comp.classificar_s3(rc, err) == esperado


@pytest.mark.parametrize("rc,err,esperado", [
    (0, "", "EXISTE"),
    (254, "An error occurred (404) when calling the HeadBucket operation: Not Found", "AUSENTE"),
    (254, "An error occurred (403) when calling the HeadBucket operation: Forbidden", "SEM_PERMISSAO"),
    (254, "An error occurred (AccessDenied) when calling GetRole", "SEM_PERMISSAO"),
    (255, "Could not connect", "ERRO"),
])
def test_classifica_ausencia_distingue_notfound_de_accessdenied(rc, err, esperado):
    assert comp.classificar_ausencia(rc, err, r"\(404\)|Not Found|NoSuchBucket") == esperado


def grant(principal, recurso, permissoes, grant_option=None):
    return {"Principal": {"DataLakePrincipalIdentifier": principal}, "Resource": recurso,
            "Permissions": permissoes, "PermissionsWithGrantOption": grant_option or []}


def grants_corretos():
    return [
        grant(PREF, {"Database": {"CatalogId": CONTA, "Name": BANCOS["refined"]}}, ["DESCRIBE"]),
        grant(PREF, {"Table": {"CatalogId": CONTA, "DatabaseName": BANCOS["refined"], "Name": "madrugada_bairro"}}, ["SELECT", "DESCRIBE"]),
        grant(ANA, {"Database": {"CatalogId": CONTA, "Name": BANCOS["trusted"]}}, ["DESCRIBE"]),
        grant(ANA, {"Database": {"CatalogId": CONTA, "Name": BANCOS["refined"]}}, ["DESCRIBE"]),
        grant(ANA, {"Table": {"CatalogId": CONTA, "DatabaseName": BANCOS["refined"], "TableWildcard": {}}}, ["SELECT", "DESCRIBE"]),
        grant(ANA, {"TableWithColumns": {"CatalogId": CONTA, "DatabaseName": BANCOS["trusted"], "Name": "corridas",
                                         "ColumnWildcard": {"ExcludedColumnNames": ["passageiro_id", "motorista_id"]}}}, ["SELECT"]),
        grant(f"arn:aws:iam::{CONTA}:role/outro-grupo", {"Database": {"CatalogId": CONTA, "Name": "eda_a29_outro_raw"}}, ["ALL"]),
        grant("IAM_ALLOWED_PRINCIPALS", {"Database": {"CatalogId": CONTA, "Name": "eda_a29_outro_raw"}}, ["ALL"]),
    ]


def avaliar(itens, escopo="tabela"):
    esperados = comp.grants_esperados(BANCOS, escopo, ["motorista_id", "passageiro_id"])
    return comp.avaliar_grants(itens, {"prefeitura": PREF, "analista": ANA}, esperados, BANCOS)


def test_grants_exatos_passam_e_ignoram_outros_grupos():
    problemas = avaliar(grants_corretos())
    assert problemas == {"prefeitura": [], "analista": [], "iam_allowed_principals": []}


def test_prefeitura_com_trusted_reprova():
    itens = grants_corretos() + [grant(PREF, {"Table": {"CatalogId": CONTA, "DatabaseName": BANCOS["trusted"], "Name": "corridas"}}, ["SELECT"])]
    assert any("extra SELECT" in p for p in avaliar(itens)["prefeitura"])


def test_wildcard_quando_escolha_e_tabela_reprova():
    itens = [g for g in grants_corretos() if not (g["Principal"]["DataLakePrincipalIdentifier"] == PREF and "Table" in g["Resource"])]
    itens.append(grant(PREF, {"Table": {"CatalogId": CONTA, "DatabaseName": BANCOS["refined"], "TableWildcard": {}}}, ["SELECT", "DESCRIBE"]))
    problemas = avaliar(itens)["prefeitura"]
    assert any("extra" in p for p in problemas) and any("ausente" in p for p in problemas)


def test_colunas_ocultas_diferentes_reprovam():
    itens = grants_corretos()
    itens[5] = grant(ANA, {"TableWithColumns": {"CatalogId": CONTA, "DatabaseName": BANCOS["trusted"], "Name": "corridas",
                                                "ColumnWildcard": {"ExcludedColumnNames": ["passageiro_id"]}}}, ["SELECT"])
    assert avaliar(itens)["analista"]


def test_iam_allowed_principals_em_tabela_nossa_reprova():
    itens = grants_corretos() + [grant("IAM_ALLOWED_PRINCIPALS",
                                       {"Table": {"CatalogId": CONTA, "DatabaseName": BANCOS["trusted"], "Name": "corridas"}}, ["ALL"])]
    assert avaliar(itens)["iam_allowed_principals"]


def test_grant_option_reprova():
    itens = grants_corretos()
    itens[0] = grant(PREF, {"Database": {"CatalogId": CONTA, "Name": BANCOS["refined"]}}, ["DESCRIBE"], ["DESCRIBE"])
    assert any("grant option" in p for p in avaliar(itens)["prefeitura"])


def test_sanitiza_conta_e_chave():
    texto = f"arn:aws:iam::{CONTA}:role/x ASIAABCDEFGHIJKLMNOP"
    assert comp.sanitizar(texto, CONTA) == "arn:aws:iam::<CONTA>:role/x <CHAVE>"


class FakePosDestroy:
    def __init__(self, negar=None):
        self.negar = negar
        self.chamadas = []

    def __call__(self, argv, ambiente):
        self.chamadas.append(list(argv))
        if argv[0] == "terraform":
            return 0, "", ""
        servico, acao = argv[1], argv[2]
        if self.negar and self.negar in argv:
            return 254, "", "An error occurred (AccessDenied) when calling the operation: denied"
        respostas = {
            ("s3api", "head-bucket"): (254, "", "An error occurred (404) when calling the HeadBucket operation: Not Found"),
            ("athena", "get-work-group"): (254, "", "An error occurred (InvalidRequestException): WorkGroup eda-a29-grupo09-wg is not found."),
            ("iam", "get-policy"): (254, "", "An error occurred (NoSuchEntity)"),
            ("iam", "get-role"): (254, "", "An error occurred (NoSuchEntity)"),
            ("glue", "get-database"): (254, "", "An error occurred (EntityNotFoundException)"),
            ("glue", "get-job"): (254, "", "An error occurred (EntityNotFoundException)"),
            ("lakeformation", "list-resources"): (0, '{"ResourceInfoList": [{"ResourceArn": "arn:aws:s3:::outro-bucket"}]}', ""),
            ("lakeformation", "list-permissions"): (0, '{"PrincipalResourcePermissions": []}', ""),
            ("logs", "describe-log-groups"): (0, '{"logGroups": []}', ""),
            ("lakeformation", "get-data-lake-settings"): (0, '{"DataLakeSettings": {"DataLakeAdmins": []}}', ""),
        }
        return respostas[(servico, acao)]


def preparar_linha_base(tmp_path, monkeypatch, v):
    monkeypatch.setattr(comp, "SAIDA", tmp_path)
    comp.modo_linha_base(v)


def test_pos_destroy_limpo_passa(tmp_path, monkeypatch):
    v = comp.Verificacao(executor=FakePosDestroy())
    v.conta = CONTA
    preparar_linha_base(tmp_path, monkeypatch, v)
    v.resultados = []
    comp.modo_pos_destroy(v, {"sufixo": "grupo09"})
    assert all(r["status"] == "PASSA" for r in v.resultados), v.resultados


def test_pos_destroy_access_denied_nao_conta_como_ausente(tmp_path, monkeypatch):
    v = comp.Verificacao(executor=FakePosDestroy(negar="eda-a29-grupo09-analista"))
    v.conta = CONTA
    preparar_linha_base(tmp_path, monkeypatch, v)
    v.resultados = []
    comp.modo_pos_destroy(v, {"sufixo": "grupo09"})
    falhas = [r for r in v.resultados if r["status"] == "FALHA"]
    assert [r["item"] for r in falhas] == ["ausente-papel-analista"]
    assert "SEM_PERMISSAO" in falhas[0]["detalhe"]


def test_sem_linha_de_base_reprova(tmp_path, monkeypatch):
    monkeypatch.setattr(comp, "SAIDA", tmp_path)
    v = comp.Verificacao(executor=FakePosDestroy())
    v.conta = CONTA
    comp.modo_pos_destroy(v, {"sufixo": "grupo09"})
    assert any(r["item"] == "lf-global-preservado" and r["status"] == "FALHA" for r in v.resultados)




OPCOES_LIST_RESOURCES = {"--filter-condition-list", "--max-results", "--next-token", "--output"}


def test_pos_destroy_list_resources_sem_catalog_id(tmp_path, monkeypatch):
    fake = FakePosDestroy()
    v = comp.Verificacao(executor=fake)
    v.conta = CONTA
    preparar_linha_base(tmp_path, monkeypatch, v)
    v.resultados = []
    comp.modo_pos_destroy(v, {"sufixo": "grupo09"})
    chamadas = [c for c in fake.chamadas if c[1:3] == ["lakeformation", "list-resources"]]
    assert chamadas, "list-resources não foi chamado"
    for chamada in chamadas:
        assert "--catalog-id" not in chamada
        assert {a for a in chamada[3:] if a.startswith("--")} <= OPCOES_LIST_RESOURCES
    assert any(r["item"] == "registro-lake-formation" and r["status"] == "PASSA" for r in v.resultados)


class FakeRegistroRestante(FakePosDestroy):
    def __call__(self, argv, ambiente):
        if argv[1:3] == ["lakeformation", "list-resources"]:
            self.chamadas.append(list(argv))
            return 0, '{"ResourceInfoList": [{"ResourceArn": "arn:aws:s3:::eda-a29-lake-grupo09/raw"}]}', ""
        return super().__call__(argv, ambiente)


def test_pos_destroy_registro_restante_reprova(tmp_path, monkeypatch):
    v = comp.Verificacao(executor=FakeRegistroRestante())
    v.conta = CONTA
    preparar_linha_base(tmp_path, monkeypatch, v)
    v.resultados = []
    comp.modo_pos_destroy(v, {"sufixo": "grupo09"})
    falhas = [r["item"] for r in v.resultados if r["status"] == "FALHA"]
    assert falhas == ["registro-lake-formation"]




LIMPO = {"prefeitura": [], "analista": [], "iam_allowed_principals": []}
OUTRO = f"arn:aws:iam::{CONTA}:role/outro-grupo"
ALIASES_IAP = ["IAM_ALLOWED_PRINCIPALS", "IAMAllowedPrincipals"]


def esperados_padrao(escopo="tabela"):
    return comp.grants_esperados(BANCOS, escopo, ["motorista_id", "passageiro_id"])


def sem_tabela_da_prefeitura(itens):
    return [g for g in itens if not (g["Principal"]["DataLakePrincipalIdentifier"] == PREF and "Table" in g["Resource"])]


def grants_prefeitura_separados():


    return sem_tabela_da_prefeitura(grants_corretos()) + [
        grant(PREF, {"TableWithColumns": {"CatalogId": CONTA, "DatabaseName": BANCOS["refined"],
                                          "Name": "madrugada_bairro", "ColumnWildcard": {}}}, ["SELECT"]),
        grant(PREF, {"Table": {"CatalogId": CONTA, "DatabaseName": BANCOS["refined"], "Name": "madrugada_bairro"}}, ["DESCRIBE"]),
    ]


def test_select_separado_em_table_with_columns_passa():
    assert avaliar(grants_prefeitura_separados()) == LIMPO


def test_column_wildcard_com_excluidas_vazia_passa():
    itens = grants_prefeitura_separados()
    itens[-2]["Resource"]["TableWithColumns"]["ColumnWildcard"] = {"ExcludedColumnNames": []}
    assert avaliar(itens) == LIMPO


def test_select_separado_sem_describe_em_table_reprova():
    itens = grants_prefeitura_separados()[:-1]
    assert any("ausente DESCRIBE" in p for p in avaliar(itens)["prefeitura"])


@pytest.mark.parametrize("nome", [None, "ALL_TABLES"])
def test_escopo_banco_com_wildcard_passa(nome):
    corpo = {"CatalogId": CONTA, "DatabaseName": BANCOS["refined"], "TableWildcard": {}}
    if nome:
        corpo["Name"] = nome
    itens = sem_tabela_da_prefeitura(grants_corretos()) + [grant(PREF, {"Table": corpo}, ["SELECT", "DESCRIBE"])]
    assert avaliar(itens, escopo="banco") == LIMPO


RECURSOS_DESCONHECIDOS = [
    pytest.param({"Catalog": {}}, id="catalog"),
    pytest.param({"LFTag": {"CatalogId": CONTA, "TagKey": "camada", "TagValues": ["refined"]}}, id="lftag"),
    pytest.param({"LFTagPolicy": {"CatalogId": CONTA, "ResourceType": "TABLE",
                                  "Expression": [{"TagKey": "camada", "TagValues": ["refined"]}]}}, id="lftag-policy"),
    pytest.param({"DataCellsFilter": {"TableCatalogId": CONTA, "DatabaseName": BANCOS["trusted"],
                                      "TableName": "corridas", "Name": "filtro"}}, id="data-cells-filter"),
    pytest.param({"Database": {"CatalogId": CONTA, "Name": BANCOS["refined"]}, "LFTag": {}}, id="conhecido-mais-desconhecido"),
]


@pytest.mark.parametrize("recurso", RECURSOS_DESCONHECIDOS)
@pytest.mark.parametrize("principal,rotulo,outro", [(PREF, "prefeitura", "analista"), (ANA, "analista", "prefeitura")])
def test_recurso_desconhecido_do_perfil_reprova(recurso, principal, rotulo, outro):
    problemas = avaliar(grants_corretos() + [grant(principal, recurso, ["DESCRIBE"])])
    assert any("recurso desconhecido" in p for p in problemas[rotulo])
    assert problemas[outro] == []


def test_recurso_desconhecido_de_outro_principal_e_ignorado():
    itens = grants_corretos() + [grant(OUTRO, p.values[0], ["ALL"]) for p in RECURSOS_DESCONHECIDOS]
    assert avaliar(itens) == LIMPO


def test_data_location_do_perfil_reprova_como_extra():
    itens = grants_corretos() + [grant(ANA, {"DataLocation": {"CatalogId": CONTA, "ResourceArn": "arn:aws:s3:::eda-a29-lake-grupo09"}},
                                       ["DATA_LOCATION_ACCESS"])]
    assert any("extra DATA_LOCATION_ACCESS" in p for p in avaliar(itens)["analista"])


@pytest.mark.parametrize("catalogo,trecho", [
    pytest.param(None, "catálogo ausente", id="ausente"),
    pytest.param("", "catálogo ausente", id="vazio"),
    pytest.param(123, "CatalogId não é texto", id="nao-texto"),
    pytest.param("   ", "catálogo divergente", id="so-espacos"),
    pytest.param("444455556666", "catálogo divergente", id="outra-conta"),
])
@pytest.mark.parametrize("indice,rotulo", [(0, "prefeitura"), (1, "prefeitura"), (2, "analista"),
                                           (3, "analista"), (4, "analista"), (5, "analista")])
def test_catalogo_ausente_ou_errado_reprova(catalogo, trecho, indice, rotulo):
    itens = grants_corretos()
    corpo = next(iter(itens[indice]["Resource"].values()))
    if catalogo is None:
        corpo.pop("CatalogId")
    else:
        corpo["CatalogId"] = catalogo
    assert any(trecho in p for p in avaliar(itens)[rotulo])


def test_conta_do_catalogo_e_derivada_dos_arns():
    outra = "444455556666"
    itens = [json.loads(json.dumps(g).replace(CONTA, outra)) for g in grants_corretos()]
    principais = {"prefeitura": PREF.replace(CONTA, outra), "analista": ANA.replace(CONTA, outra)}
    assert comp.avaliar_grants(itens, principais, esperados_padrao(), BANCOS) == LIMPO


def test_catalogo_da_conta_do_perfil_e_nao_de_outro_grant():
    outra = "444455556666"
    itens = [json.loads(json.dumps(g).replace(f'"CatalogId": "{CONTA}"', f'"CatalogId": "{outra}"')) for g in grants_corretos()]
    problemas = avaliar(itens)
    assert any("catálogo divergente" in p for p in problemas["prefeitura"])
    assert any("catálogo divergente" in p for p in problemas["analista"])


def test_arns_dos_perfis_em_contas_diferentes_reprovam():
    ana_outra = ANA.replace(CONTA, "444455556666")
    itens = [json.loads(json.dumps(g).replace(ANA, ana_outra)) for g in grants_corretos()]
    problemas = comp.avaliar_grants(itens, {"prefeitura": PREF, "analista": ana_outra}, esperados_padrao(), BANCOS)
    assert any("contas diferentes" in p for p in problemas["prefeitura"])
    assert any("contas diferentes" in p for p in problemas["analista"])


def test_arn_de_perfil_invalido_reprova():
    problemas = comp.avaliar_grants(grants_corretos(), {"prefeitura": "eda-a29-grupo09-prefeitura", "analista": ANA},
                                    esperados_padrao(), BANCOS)
    assert any("ARN do perfil inválido" in p for p in problemas["prefeitura"])
    assert problemas["analista"] == []


def _twc(**corpo):
    return {"TableWithColumns": {"CatalogId": CONTA, "DatabaseName": BANCOS["trusted"], "Name": "corridas", **corpo}}


DB_TRUSTED = {"Database": {"CatalogId": CONTA, "Name": BANCOS["trusted"]}}
DB_REFINED = {"Database": {"CatalogId": CONTA, "Name": BANCOS["refined"]}}

ITENS_MALFORMADOS = [
    pytest.param("analista", {"Principal": {"DataLakePrincipalIdentifier": ANA}, "Resource": DB_TRUSTED,
                              "Permissions": "DESCRIBE"}, id="permissions-texto"),
    pytest.param("analista", grant(ANA, DB_TRUSTED, []), id="permissions-vazio"),
    pytest.param("analista", {"Principal": {"DataLakePrincipalIdentifier": ANA}, "Resource": DB_TRUSTED}, id="permissions-ausente"),
    pytest.param("prefeitura", grant(PREF, DB_REFINED, [None]), id="permissions-nao-texto"),
    pytest.param("prefeitura", {**grant(PREF, DB_REFINED, ["DESCRIBE"]), "PermissionsWithGrantOption": "DESCRIBE"},
                 id="grant-option-nao-lista"),
    pytest.param("prefeitura", {**grant(PREF, DB_REFINED, ["DESCRIBE"]), "PermissionsWithGrantOption": None},
                 id="grant-option-nulo"),
    pytest.param("analista", {"Principal": {"DataLakePrincipalIdentifier": ANA}, "Permissions": ["SELECT"]}, id="resource-ausente"),
    pytest.param("analista", grant(ANA, [], ["SELECT"]), id="resource-lista"),
    pytest.param("analista", grant(ANA, {}, ["SELECT"]), id="resource-vazio"),
    pytest.param("analista", grant(ANA, {**DB_TRUSTED, "Table": {"CatalogId": CONTA, "DatabaseName": BANCOS["trusted"],
                                                                 "Name": "corridas"}}, ["DESCRIBE"]), id="dois-tipos"),
    pytest.param("analista", grant(ANA, {"Database": BANCOS["trusted"]}, ["DESCRIBE"]), id="corpo-nao-objeto"),
    pytest.param("prefeitura", grant(PREF, {"Database": {"CatalogId": CONTA}}, ["DESCRIBE"]), id="database-sem-name"),
    pytest.param("prefeitura", grant(PREF, {"Table": {"CatalogId": CONTA, "Name": "madrugada_bairro"}}, ["SELECT"]),
                 id="table-sem-banco"),
    pytest.param("prefeitura", grant(PREF, {"Table": {"CatalogId": CONTA, "DatabaseName": BANCOS["refined"]}}, ["SELECT"]),
                 id="table-sem-name-nem-wildcard"),
    pytest.param("prefeitura", grant(PREF, {"Table": {"CatalogId": CONTA, "DatabaseName": BANCOS["refined"],
                                                      "Name": "madrugada_bairro", "TableWildcard": {}}}, ["SELECT"]),
                 id="table-name-e-wildcard"),
    pytest.param("prefeitura", grant(PREF, {"Table": {"CatalogId": CONTA, "DatabaseName": BANCOS["refined"],
                                                      "TableWildcard": {"x": 1}}}, ["SELECT"]), id="tablewildcard-nao-vazio"),
    pytest.param("prefeitura", grant(PREF, {"Table": {"CatalogId": CONTA, "DatabaseName": BANCOS["refined"],
                                                      "TableWildcard": None}}, ["SELECT"]), id="tablewildcard-nulo"),
    pytest.param("analista", grant(ANA, _twc(ColumnNames=["bairro"], ColumnWildcard={}), ["SELECT"]), id="twc-nomes-e-curinga"),
    pytest.param("analista", grant(ANA, _twc(), ["SELECT"]), id="twc-sem-colunas"),
    pytest.param("analista", grant(ANA, {"TableWithColumns": {"CatalogId": CONTA, "DatabaseName": BANCOS["trusted"],
                                                              "ColumnWildcard": {}}}, ["SELECT"]), id="twc-sem-name"),
    pytest.param("analista", grant(ANA, _twc(ColumnNames=[]), ["SELECT"]), id="column-names-vazio"),
    pytest.param("analista", grant(ANA, _twc(ColumnNames="bairro"), ["SELECT"]), id="column-names-texto"),
    pytest.param("analista", grant(ANA, _twc(ColumnWildcard={"ExcludedColumnNames": [], "Outro": 1}), ["SELECT"]),
                 id="curinga-chave-extra"),
    pytest.param("analista", grant(ANA, _twc(ColumnWildcard={"ExcludedColumnNames": "passageiro_id"}), ["SELECT"]),
                 id="excluidas-texto"),
    pytest.param("analista", grant(ANA, _twc(ColumnWildcard=None), ["SELECT"]), id="curinga-nulo"),
    pytest.param("analista", grant(ANA, {"DataLocation": {"CatalogId": CONTA}}, ["DATA_LOCATION_ACCESS"]), id="datalocation-sem-arn"),
]


@pytest.mark.parametrize("rotulo,item", ITENS_MALFORMADOS)
def test_payload_malformado_do_perfil_reprova(rotulo, item):
    problemas = avaliar(grants_corretos() + [item])
    assert any("payload malformado" in p for p in problemas[rotulo]), problemas


@pytest.mark.parametrize("rotulo,item", [
    pytest.param("analista", f"texto solto {ANA}", id="item-texto"),
    pytest.param("analista", {"Principal": ANA, "Resource": DB_TRUSTED, "Permissions": ["ALL"]}, id="principal-texto"),
    pytest.param("prefeitura", {"Principal": {"DataLakePrincipalIdentifier": None, "Outro": PREF}, "Resource": DB_REFINED,
                                "Permissions": ["ALL"]}, id="identificador-nulo"),
    pytest.param("prefeitura", {"Resource": {"Table": {"CatalogId": CONTA, "DatabaseName": BANCOS["trusted"], "Name": "corridas"}},
                                "Permissions": ["SELECT"], "Nota": PREF}, id="principal-ausente"),
])
def test_principal_ilegivel_que_cita_perfil_reprova(rotulo, item):
    problemas = avaliar(grants_corretos() + [item])
    assert any("Principal ilegível" in p for p in problemas[rotulo])


def test_payload_malformado_de_outro_principal_e_ignorado():
    itens = grants_corretos() + [
        grant(OUTRO, {}, []),
        grant(OUTRO, {"Table": {"DatabaseName": "eda_a29_outro_trusted"}}, "ALL"),
        "lixo",
        {"Principal": None},
        None,
    ]
    assert avaliar(itens) == LIMPO


@pytest.mark.parametrize("indice,rotulo", [(1, "prefeitura"), (4, "analista"), (5, "analista")])
def test_grant_option_em_tabela_e_colunas_reprova(indice, rotulo):
    itens = grants_corretos()
    itens[indice]["PermissionsWithGrantOption"] = list(itens[indice]["Permissions"])
    assert any("grant option" in p for p in avaliar(itens)[rotulo])


def test_grant_option_sem_permissions_reprova():
    itens = grants_corretos() + [grant(ANA, {"Database": {"CatalogId": CONTA, "Name": BANCOS["raw"]}}, [], ["DESCRIBE"])]
    assert avaliar(itens)["analista"]


def recursos_do_projeto():
    return [
        {"Database": {"CatalogId": CONTA, "Name": BANCOS["raw"]}},
        {"Table": {"CatalogId": CONTA, "DatabaseName": BANCOS["trusted"], "Name": "corridas"}},
        {"Table": {"CatalogId": CONTA, "DatabaseName": BANCOS["refined"], "TableWildcard": {}}},
        {"TableWithColumns": {"CatalogId": CONTA, "DatabaseName": BANCOS["trusted"], "Name": "corridas", "ColumnWildcard": {}}},
    ]


@pytest.mark.parametrize("alias", ALIASES_IAP)
@pytest.mark.parametrize("indice", range(4))
def test_iap_em_banco_do_projeto_reprova_com_ambos_aliases(alias, indice):
    problemas = avaliar(grants_corretos() + [grant(alias, recursos_do_projeto()[indice], ["ALL"])])
    assert problemas["iam_allowed_principals"]
    assert problemas["prefeitura"] == [] and problemas["analista"] == []


@pytest.mark.parametrize("alias", ALIASES_IAP)
def test_iap_so_com_grant_option_em_banco_do_projeto_reprova(alias):
    itens = grants_corretos() + [grant(alias, recursos_do_projeto()[0], [], ["ALL"])]
    assert avaliar(itens)["iam_allowed_principals"]


@pytest.mark.parametrize("alias", ALIASES_IAP)
def test_iap_malformado_citando_banco_do_projeto_reprova(alias):
    itens = grants_corretos() + [grant(alias, {"Table": {"CatalogId": CONTA, "DatabaseName": BANCOS["trusted"]}}, ["ALL"])]
    assert avaliar(itens)["iam_allowed_principals"]


@pytest.mark.parametrize("alias", ALIASES_IAP)
def test_iap_fora_dos_bancos_do_projeto_e_ignorado(alias):
    itens = grants_corretos() + [
        grant(alias, {"Database": {"CatalogId": CONTA, "Name": "eda_a29_outro_trusted"}}, ["ALL"]),
        grant(alias, {"Table": {"CatalogId": CONTA, "DatabaseName": "default", "TableWildcard": {}}}, ["ALL"]),
        grant(alias, {"DataLocation": {"CatalogId": CONTA, "ResourceArn": "arn:aws:s3:::eda-a29-lake-grupo09"}},
              ["DATA_LOCATION_ACCESS"]),
        grant(alias, {"LFTag": {"CatalogId": CONTA, "TagKey": "camada", "TagValues": ["raw"]}}, ["DESCRIBE"]),
    ]
    assert avaliar(itens) == LIMPO


def twc_all_tables(**alteracoes):
    corpo = {"CatalogId": CONTA, "DatabaseName": BANCOS["refined"], "Name": "ALL_TABLES", "ColumnWildcard": {}}
    corpo.update(alteracoes)
    return {"TableWithColumns": corpo}


def wildcard_refined(nome):
    corpo = {"CatalogId": CONTA, "DatabaseName": BANCOS["refined"], "TableWildcard": {}}
    if nome:
        corpo["Name"] = nome
    return {"Table": corpo}


def grants_analista_forma_aws(nome="ALL_TABLES"):
    itens = grants_corretos()
    itens[4] = grant(ANA, wildcard_refined(nome), ["DESCRIBE"])
    return itens + [grant(ANA, twc_all_tables(), ["SELECT"])]


@pytest.mark.parametrize("nome", [None, "ALL_TABLES"])
def test_forma_aws_all_tables_da_analista_passa(nome):
    assert avaliar(grants_analista_forma_aws(nome)) == LIMPO


def test_forma_aws_all_tables_nao_vira_tabela_chamada_all_tables():
    problemas = avaliar(grants_analista_forma_aws())["analista"]
    assert not any("ALL_TABLES" in p for p in problemas)
    assert not any("ausente SELECT em ('todas_tabelas'" in p for p in problemas)


def test_forma_aws_all_tables_sem_describe_no_wildcard_reprova():
    itens = [g for i, g in enumerate(grants_analista_forma_aws()) if i != 4]
    assert any("ausente DESCRIBE em ('todas_tabelas'" in p for p in avaliar(itens)["analista"])


def test_forma_aws_all_tables_sem_select_reprova():
    itens = grants_analista_forma_aws()[:-1]
    assert any("ausente SELECT em ('todas_tabelas'" in p for p in avaliar(itens)["analista"])


def test_forma_aws_all_tables_da_prefeitura_no_escopo_banco_passa():
    itens = sem_tabela_da_prefeitura(grants_corretos()) + [
        grant(PREF, wildcard_refined("ALL_TABLES"), ["DESCRIBE"]),
        grant(PREF, twc_all_tables(), ["SELECT"]),
    ]
    assert avaliar(itens, escopo="banco") == LIMPO


def test_forma_aws_all_tables_da_prefeitura_no_escopo_tabela_reprova():
    itens = grants_corretos() + [grant(PREF, twc_all_tables(), ["SELECT"])]
    assert any("extra SELECT em ('todas_tabelas'" in p for p in avaliar(itens)["prefeitura"])


TWC_ALL_TABLES_COM_COLUNAS = {"TableWithColumns": {"CatalogId": CONTA, "DatabaseName": BANCOS["refined"],
                                                   "Name": "ALL_TABLES", "ColumnNames": ["bairro"]}}

VARIANTES_ALL_TABLES_EXCESSIVAS = [
    pytest.param(grant(ANA, twc_all_tables(ColumnWildcard={"ExcludedColumnNames": []}), ["SELECT"]),
                 "payload malformado", id="excluidas-vazia-explicita"),
    pytest.param(grant(ANA, twc_all_tables(ColumnWildcard={"ExcludedColumnNames": ["passageiro_id"]}), ["SELECT"]),
                 "payload malformado", id="colunas-excluidas"),
    pytest.param(grant(ANA, twc_all_tables(ColumnWildcard={"ExcludedColumnNames": "passageiro_id"}), ["SELECT"]),
                 "payload malformado", id="excluidas-texto"),
    pytest.param(grant(ANA, twc_all_tables(ColumnWildcard={"Outro": 1}), ["SELECT"]),
                 "payload malformado", id="curinga-chave-extra"),
    pytest.param(grant(ANA, twc_all_tables(ColumnWildcard=None), ["SELECT"]),
                 "payload malformado", id="curinga-nulo"),
    pytest.param(grant(ANA, TWC_ALL_TABLES_COM_COLUNAS, ["SELECT"]), "payload malformado", id="column-names"),
    pytest.param(grant(ANA, twc_all_tables(ColumnNames=["bairro"]), ["SELECT"]), "payload malformado",
                 id="column-names-e-curinga"),
    pytest.param(grant(ANA, twc_all_tables(TableWildcard={}), ["SELECT"]), "payload malformado", id="corpo-chave-extra"),
    pytest.param(grant(ANA, twc_all_tables(DatabaseName=None), ["SELECT"]), "payload malformado", id="sem-banco"),
    pytest.param(grant(ANA, twc_all_tables(), ["SELECT", "DESCRIBE"]), "aceita só SELECT", id="select-e-describe"),
    pytest.param(grant(ANA, twc_all_tables(), ["ALL"]), "aceita só SELECT", id="all"),
    pytest.param(grant(ANA, twc_all_tables(), ["DESCRIBE"]), "aceita só SELECT", id="describe"),
    pytest.param(grant(ANA, twc_all_tables(), ["SELECT", "SELECT"]), "aceita só SELECT", id="select-duplicado"),
    pytest.param(grant(ANA, twc_all_tables(), ["SELECT"], ["SELECT"]), "grant option", id="grant-option"),
    pytest.param(grant(ANA, twc_all_tables(), ["SELECT"], ["ALL"]), "grant option", id="grant-option-all"),
    pytest.param({**grant(ANA, twc_all_tables(), ["SELECT"]), "PermissionsWithGrantOption": None},
                 "payload malformado", id="grant-option-nulo"),
    pytest.param(grant(ANA, twc_all_tables(CatalogId=None), ["SELECT"]), "catálogo ausente", id="catalogo-nulo"),
    pytest.param(grant(ANA, twc_all_tables(CatalogId=""), ["SELECT"]), "catálogo ausente", id="catalogo-vazio"),
    pytest.param(grant(ANA, twc_all_tables(CatalogId="444455556666"), ["SELECT"]), "catálogo divergente",
                 id="catalogo-outra-conta"),
    pytest.param(grant(ANA, twc_all_tables(CatalogId=123), ["SELECT"]), "CatalogId não é texto", id="catalogo-nao-texto"),
    pytest.param(grant(ANA, twc_all_tables(DatabaseName=BANCOS["trusted"]), ["SELECT"]),
                 f"extra SELECT em ('todas_tabelas', '{BANCOS['trusted']}')", id="banco-trusted"),
    pytest.param(grant(ANA, twc_all_tables(DatabaseName=BANCOS["raw"]), ["SELECT"]),
                 f"extra SELECT em ('todas_tabelas', '{BANCOS['raw']}')", id="banco-raw"),
    pytest.param(grant(ANA, twc_all_tables(Name="all_tables"), ["SELECT"]), "ausente SELECT em ('todas_tabelas'",
                 id="nome-minusculo"),
]


@pytest.mark.parametrize("item,trecho", VARIANTES_ALL_TABLES_EXCESSIVAS)
def test_variante_excessiva_de_all_tables_reprova(item, trecho):
    problemas = avaliar(grants_analista_forma_aws()[:-1] + [item])
    assert any(trecho in p for p in problemas["analista"]), problemas
    assert problemas["prefeitura"] == []


@pytest.mark.parametrize("item,trecho", VARIANTES_ALL_TABLES_EXCESSIVAS)
def test_variante_excessiva_de_all_tables_somada_a_forma_legitima_reprova(item, trecho):
    problemas = avaliar(grants_analista_forma_aws() + [item])
    assert problemas["analista"], problemas


@pytest.mark.parametrize("alias", ALIASES_IAP)
@pytest.mark.parametrize("recurso", [
    pytest.param(twc_all_tables(), id="forma-legitima"),
    pytest.param(twc_all_tables(ColumnWildcard={"ExcludedColumnNames": ["passageiro_id"]}), id="malformado"),
])
def test_iap_em_all_tables_do_projeto_reprova(alias, recurso):
    problemas = avaliar(grants_corretos() + [grant(alias, recurso, ["SELECT"])])
    assert problemas["iam_allowed_principals"]
    assert problemas["prefeitura"] == [] and problemas["analista"] == []


def test_all_tables_de_outro_principal_e_ignorado():
    itens = grants_corretos() + [grant(OUTRO, twc_all_tables(), ["ALL"], ["ALL"]),
                                 grant(OUTRO, twc_all_tables(ColumnWildcard=None), ["SELECT"])]
    assert avaliar(itens) == LIMPO


ADMIN_A = f"arn:aws:iam::{CONTA}:role/Admin"
ADMIN_B = f"arn:aws:iam::{CONTA}:role/eda-a29-grupo09-lf"
ADMIN_C = f"arn:aws:iam::{CONTA}:user/terraform"


def configuracao_lf():
    return {
        "DataLakeAdmins": [{"DataLakePrincipalIdentifier": ADMIN_A},
                           {"DataLakePrincipalIdentifier": ADMIN_B},
                           {"DataLakePrincipalIdentifier": ADMIN_C}],
        "ReadOnlyAdmins": [],
        "CreateDatabaseDefaultPermissions": [
            {"Principal": {"DataLakePrincipalIdentifier": "IAM_ALLOWED_PRINCIPALS"}, "Permissions": ["ALL"]},
            {"Principal": {"DataLakePrincipalIdentifier": ADMIN_A}, "Permissions": ["ALTER", "DESCRIBE", "DROP"]},
        ],
        "CreateTableDefaultPermissions": [
            {"Principal": {"DataLakePrincipalIdentifier": ADMIN_A}, "Permissions": ["SELECT", "DESCRIBE", "INSERT"]},
        ],
        "Parameters": {"CROSS_ACCOUNT_VERSION": "4", "SET_CONTEXT": "TRUE"},
        "TrustedResourceOwners": [],
        "AllowExternalDataFiltering": False,
        "AllowFullTableExternalDataAccess": False,
        "ExternalDataFilteringAllowList": [],
        "AuthorizedSessionTagValueList": [],
    }


def reordenada():
    cfg = configuracao_lf()
    cfg["DataLakeAdmins"].reverse()
    cfg["CreateDatabaseDefaultPermissions"].reverse()
    cfg["CreateDatabaseDefaultPermissions"][0]["Permissions"] = ["DROP", "ALTER", "DESCRIBE"]
    cfg["CreateTableDefaultPermissions"][0]["Permissions"] = ["INSERT", "DESCRIBE", "SELECT"]
    cfg["CreateTableDefaultPermissions"][0] = {"Permissions": cfg["CreateTableDefaultPermissions"][0]["Permissions"],
                                               "Principal": cfg["CreateTableDefaultPermissions"][0]["Principal"]}
    return cfg


def test_ordem_de_admins_nao_muda_hash():
    cfg = configuracao_lf()
    outra = configuracao_lf()
    outra["DataLakeAdmins"] = [outra["DataLakeAdmins"][i] for i in (2, 0, 1)]
    assert comp.hash_legado(cfg) != comp.hash_legado(outra)
    assert comp.hash_configuracao(cfg) == comp.hash_configuracao(outra)


def test_ordem_de_permissoes_nao_muda_hash():
    cfg = configuracao_lf()
    outra = configuracao_lf()
    outra["CreateDatabaseDefaultPermissions"][1]["Permissions"] = ["DROP", "DESCRIBE", "ALTER"]
    outra["CreateTableDefaultPermissions"][0]["Permissions"] = ["INSERT", "SELECT", "DESCRIBE"]
    outra["CreateDatabaseDefaultPermissions"].reverse()
    assert comp.hash_configuracao(cfg) == comp.hash_configuracao(outra)


def test_reordenacao_recursiva_completa_nao_muda_hash():
    assert comp.hash_configuracao(configuracao_lf()) == comp.hash_configuracao(reordenada())


def test_canonizacao_nao_altera_a_entrada():
    cfg = configuracao_lf()
    copia = json.loads(json.dumps(cfg))
    comp.hash_configuracao(cfg)
    assert cfg == copia


def _incluir_admin(cfg):
    cfg["DataLakeAdmins"].append({"DataLakePrincipalIdentifier": f"arn:aws:iam::{CONTA}:role/intruso"})


def _remover_admin(cfg):
    cfg["DataLakeAdmins"].pop(1)


def _trocar_admin(cfg):
    cfg["DataLakeAdmins"][1] = {"DataLakePrincipalIdentifier": f"arn:aws:iam::{CONTA}:role/eda-a29-grupo10-lf"}


def _duplicar_admin(cfg):
    cfg["DataLakeAdmins"].append(dict(cfg["DataLakeAdmins"][0]))


def _grant_padrao(cfg, lista, principal):
    return next(g for g in cfg[lista] if g["Principal"]["DataLakePrincipalIdentifier"] == principal)


def _incluir_permissao(cfg):
    _grant_padrao(cfg, "CreateTableDefaultPermissions", ADMIN_A)["Permissions"].append("DELETE")


def _remover_permissao(cfg):
    _grant_padrao(cfg, "CreateDatabaseDefaultPermissions", ADMIN_A)["Permissions"].remove("DROP")


def _mudar_permissao(cfg):
    _grant_padrao(cfg, "CreateDatabaseDefaultPermissions", "IAM_ALLOWED_PRINCIPALS")["Permissions"] = ["DESCRIBE"]


def _incluir_grant_padrao(cfg):
    cfg["CreateTableDefaultPermissions"].append(
        {"Principal": {"DataLakePrincipalIdentifier": "IAM_ALLOWED_PRINCIPALS"}, "Permissions": ["ALL"]})


def _remover_grant_padrao(cfg):
    cfg["CreateDatabaseDefaultPermissions"].remove(
        _grant_padrao(cfg, "CreateDatabaseDefaultPermissions", "IAM_ALLOWED_PRINCIPALS"))


def _mudar_principal_do_grant(cfg):
    _grant_padrao(cfg, "CreateTableDefaultPermissions", ADMIN_A)["Principal"]["DataLakePrincipalIdentifier"] = ADMIN_B


def _mover_permissao_entre_principais(cfg):
    _grant_padrao(cfg, "CreateDatabaseDefaultPermissions", "IAM_ALLOWED_PRINCIPALS")["Permissions"] = ["ALL", "DROP"]
    _grant_padrao(cfg, "CreateDatabaseDefaultPermissions", ADMIN_A)["Permissions"] = ["ALTER", "DESCRIBE"]


def _incluir_read_only_admin(cfg):
    cfg["ReadOnlyAdmins"].append({"DataLakePrincipalIdentifier": ADMIN_C})


def _mudar_booleano(cfg):
    cfg["AllowExternalDataFiltering"] = True


def _mudar_parametro(cfg):
    cfg["Parameters"]["CROSS_ACCOUNT_VERSION"] = "3"


def _remover_chave(cfg):
    cfg.pop("TrustedResourceOwners")


@pytest.mark.parametrize("alterar", [
    pytest.param(_incluir_admin, id="inclui-admin"),
    pytest.param(_remover_admin, id="remove-admin"),
    pytest.param(_trocar_admin, id="troca-admin"),
    pytest.param(_duplicar_admin, id="duplica-admin"),
    pytest.param(_incluir_permissao, id="inclui-permissao"),
    pytest.param(_remover_permissao, id="remove-permissao"),
    pytest.param(_mudar_permissao, id="muda-permissao"),
    pytest.param(_incluir_grant_padrao, id="inclui-grant-padrao"),
    pytest.param(_remover_grant_padrao, id="remove-grant-padrao"),
    pytest.param(_mudar_principal_do_grant, id="muda-principal-do-grant"),
    pytest.param(_mover_permissao_entre_principais, id="move-permissao-entre-principais"),
    pytest.param(_incluir_read_only_admin, id="inclui-read-only-admin"),
    pytest.param(_mudar_booleano, id="muda-booleano"),
    pytest.param(_mudar_parametro, id="muda-parametro"),
    pytest.param(_remover_chave, id="remove-chave"),
])
def test_alteracao_real_muda_hash(alterar):
    original = configuracao_lf()
    alterada = configuracao_lf()
    alterar(alterada)
    assert comp.hash_configuracao(original) != comp.hash_configuracao(alterada)
    reordenada_e_alterada = reordenada()
    alterar(reordenada_e_alterada)
    assert comp.hash_configuracao(original) != comp.hash_configuracao(reordenada_e_alterada)


@pytest.mark.parametrize("a,b", [
    pytest.param([1], [True], id="inteiro-vs-booleano"),
    pytest.param(["1"], [1], id="texto-vs-inteiro"),
    pytest.param([None], [], id="nulo-vs-vazio"),
    pytest.param([[1, 2], [3]], [[1], [2, 3]], id="particao-de-listas-aninhadas"),
])
def test_canonizacao_distingue_valores(a, b):
    assert comp.hash_configuracao({"x": a}) != comp.hash_configuracao({"x": b})


class FakeSettings:
    def __init__(self, configuracao):
        self.configuracao = configuracao

    def __call__(self, argv, ambiente):
        assert argv[1:3] == ["lakeformation", "get-data-lake-settings"]
        return 0, json.dumps({"DataLakeSettings": self.configuracao}), ""


def _linha_base_com(tmp_path, monkeypatch, configuracao):
    monkeypatch.setattr(comp, "SAIDA", tmp_path)
    fake = FakeSettings(configuracao)
    v = comp.Verificacao(executor=fake)
    v.conta = CONTA
    comp.modo_linha_base(v)
    v.resultados = []
    return v, fake


def test_linha_base_registra_versao_da_canonizacao(tmp_path, monkeypatch):
    _linha_base_com(tmp_path, monkeypatch, configuracao_lf())
    base = json.loads((tmp_path / "linha-base-lf.json").read_text(encoding="utf-8"))
    assert base["canonizacao"] == comp.VERSAO_CANONIZACAO
    assert base["sha256"] == comp.hash_configuracao(configuracao_lf())


def test_linha_base_com_admins_em_outra_ordem_passa(tmp_path, monkeypatch):
    v, fake = _linha_base_com(tmp_path, monkeypatch, configuracao_lf())
    fake.configuracao = reordenada()
    v.comparar_linha_base("lf-global-preservado")
    assert [r["status"] for r in v.resultados] == ["PASSA"]


@pytest.mark.parametrize("alterar", [_incluir_admin, _remover_admin, _mudar_permissao, _incluir_permissao])
def test_linha_base_com_alteracao_real_reprova(tmp_path, monkeypatch, alterar):
    v, fake = _linha_base_com(tmp_path, monkeypatch, configuracao_lf())
    alterada = reordenada()
    alterar(alterada)
    fake.configuracao = alterada
    v.comparar_linha_base("lf-global-preservado")
    assert [r["status"] for r in v.resultados] == ["FALHA"]
    assert "DIFERENTE" in v.resultados[0]["detalhe"]


def _linha_base_antiga(tmp_path, monkeypatch, configuracao):
    monkeypatch.setattr(comp, "SAIDA", tmp_path)
    (tmp_path / "linha-base-lf.json").write_text(
        json.dumps({"sha256": comp.hash_legado(configuracao), "gerado_em_utc": "2026-11-01T00:00:00+00:00"}) + "\n",
        encoding="utf-8")


def test_linha_base_antiga_identica_passa(tmp_path, monkeypatch):
    _linha_base_antiga(tmp_path, monkeypatch, configuracao_lf())
    v = comp.Verificacao(executor=FakeSettings(configuracao_lf()))
    v.conta = CONTA
    v.comparar_linha_base("lf-global-preservado")
    assert [r["status"] for r in v.resultados] == ["PASSA"]


def test_linha_base_antiga_divergente_reprova_e_pede_regenerar(tmp_path, monkeypatch):
    _linha_base_antiga(tmp_path, monkeypatch, configuracao_lf())
    v = comp.Verificacao(executor=FakeSettings(reordenada()))
    v.conta = CONTA
    v.comparar_linha_base("lf-global-preservado")
    assert [r["status"] for r in v.resultados] == ["FALHA"]
    assert "regenere com --linha-base" in v.resultados[0]["detalhe"]
