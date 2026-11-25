import datetime as dt
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def gerar(destino: Path, app: Path) -> None:
    subprocess.run([sys.executable, str(app / "gerador-eventos.py"), "--dias", "30", "--taxa", "12", "--seed", "42",
                    "--saida", str(destino)], check=True, capture_output=True)
    subprocess.run([sys.executable, str(app / "plantar-problemas.py"), "--saida", str(destino), "--atraso", "3",
                    "--reentrega", "5"], check=True, capture_output=True)


def simular(destino: Path) -> dict:
    raiz = destino / "raw" / "corridas"
    linhas = []
    objetos = 0
    for pasta in sorted(raiz.glob("dt=*")):
        chegada = dt.date.fromisoformat(pasta.name[3:])
        for arquivo in sorted(pasta.glob("*.json")):
            objetos += 1
            for texto in arquivo.read_text(encoding="utf-8").splitlines():
                ev = json.loads(texto)
                inicio = dt.datetime.strptime(ev["data_corrida"], "%Y-%m-%dT%H:%M:%SZ")
                valor = ev["valor"]
                valor = float(valor.replace(",", ".")) if isinstance(valor, str) else float(valor)
                linhas.append((ev["corrida_id"], chegada, inicio.date(), inicio.hour, ev["bairro"], valor))
    aceitas = [l for l in linhas if 0 <= (l[1] - l[2]).days <= 3]
    primeiras = {}
    for linha in sorted(aceitas, key=lambda l: l[1]):
        primeiras.setdefault(linha[0], linha)
    trusted = list(primeiras.values())
    grupos = defaultdict(lambda: [0, 0.0])
    for _, _, dia, hora, bairro, valor in trusted:
        if 0 <= hora <= 5:
            grupos[(dia, bairro)][0] += 1
            grupos[(dia, bairro)][1] += valor
    hoje = max(dt.date.fromisoformat(p.name[3:]) for p in raiz.glob("dt=*"))
    desde = hoje - dt.timedelta(days=6)
    return {
        "objetos_raw": objetos,
        "linhas_trusted": len(trusted),
        "linhas_refined": len(grupos),
        "madrugada_7_dias": sum(v[0] for (dia, _), v in grupos.items() if dia >= desde),
    }


def digest(destino: Path) -> str:
    h = hashlib.sha256()
    for arquivo in sorted((destino / "raw").rglob("*.json")):
        h.update(str(arquivo.relative_to(destino)).encode())
        h.update(arquivo.read_bytes())
    return h.hexdigest()


@pytest.fixture(scope="module")
def gerado(tmp_path_factory):
    destino = tmp_path_factory.mktemp("dados")
    gerar(destino, RAIZ / "app")
    return destino


def test_numeros_do_enunciado(gerado):
    r = simular(gerado)
    assert r == {"objetos_raw": 32, "linhas_trusted": 518400, "linhas_refined": 660, "madrugada_7_dias": 11137}


def test_remover_comentarios_nao_mudou_os_dados(gerado, tmp_path):
    original = Path(__file__).resolve().parent / "original_app"
    if not original.exists():
        pytest.skip("cópia do app original ausente")
    destino = tmp_path / "original"
    gerar(destino, original)
    assert digest(destino) == digest(gerado)
