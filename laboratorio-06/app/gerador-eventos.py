#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sys
from pathlib import Path


BAIRROS: list[tuple[str, float]] = [
    ("Boa Viagem", 18.0),
    ("Pina", 7.5),
    ("Recife Antigo", 6.0),
    ("Ilha do Leite", 5.5),
    ("Espinheiro", 5.0),
    ("Graças", 5.0),
    ("Madalena", 4.8),
    ("Casa Forte", 4.5),
    ("Torre", 4.0),
    ("Boa Vista", 4.0),
    ("Derby", 3.6),
    ("Aflitos", 3.4),
    ("Santo Amaro", 3.2),
    ("Encruzilhada", 3.0),
    ("Imbiribeira", 3.0),
    ("Cordeiro", 2.8),
    ("Tamarineira", 2.6),
    ("Caxangá", 2.4),
    ("Várzea", 2.4),
    ("Bongi", 2.0),
    ("Setúbal", 4.0),
    ("Jaqueira", 3.3),
]


CURVA_HORA: list[float] = [
    0.55, 0.42, 0.34, 0.30, 0.32, 0.48,
    0.95, 1.70, 2.05, 1.60, 1.15, 1.10,
    1.25, 1.20, 1.05, 1.05, 1.30, 1.95,
    2.00, 1.55, 1.20, 1.05, 0.90, 0.72,
]


FRACAO_VALOR_TEXTO = 0.015


def pesos_normalizados(curva: list[float]) -> list[float]:

    media = sum(curva) / len(curva)
    return [c / media for c in curva]


def reparte(total: int, pesos: list[float]) -> list[int]:

    soma = sum(pesos)
    exatos = [total * p / soma for p in pesos]
    base = [int(v) for v in exatos]
    falta = total - sum(base)
    ordem = sorted(range(len(pesos)), key=lambda i: (-(exatos[i] - base[i]), i))
    for i in ordem[:falta]:
        base[i] += 1
    return base


def escolhe_bairro(rnd: random.Random, acumulado: list[float], nomes: list[str]) -> str:
    alvo = rnd.random() * acumulado[-1]
    lo, hi = 0, len(acumulado) - 1
    while lo < hi:
        meio = (lo + hi) // 2
        if acumulado[meio] < alvo:
            lo = meio + 1
        else:
            hi = meio
    return nomes[lo]


def gera(dias: int, taxa: int, seed: int, saida: Path) -> dict:
    rnd = random.Random(seed)
    nomes = [b for b, _ in BAIRROS]
    acumulado: list[float] = []
    corrente = 0.0
    for _, peso in BAIRROS:
        corrente += peso
        acumulado.append(corrente)

    curva_hora = pesos_normalizados(CURVA_HORA)
    hoje = dt.date.today()
    primeiro_dia = hoje - dt.timedelta(days=dias - 1)

    destino = saida / "raw" / "corridas"
    destino.mkdir(parents=True, exist_ok=True)

    corrida = 0
    total_bytes = 0
    total_eventos = 0
    valores_texto = 0
    particoes: list[tuple[str, int, int]] = []

    for offset in range(dias):
        dia = primeiro_dia + dt.timedelta(days=offset)


        eventos_do_dia = taxa * 1440
        por_hora = reparte(eventos_do_dia, curva_hora)

        pasta = destino / f"dt={dia.isoformat()}"
        pasta.mkdir(parents=True, exist_ok=True)
        arquivo = pasta / "parte-0001.json"

        bytes_do_dia = 0
        with arquivo.open("w", encoding="utf-8", newline="\n") as fh:
            for hora, quantidade in enumerate(por_hora):
                for _ in range(quantidade):
                    corrida += 1
                    minuto = rnd.randrange(60)
                    segundo = rnd.randrange(60)
                    inicio = dt.datetime(
                        dia.year, dia.month, dia.day, hora, minuto, segundo,
                        tzinfo=dt.timezone.utc,
                    )
                    distancia = round(rnd.lognormvariate(1.05, 0.62), 2)
                    distancia = min(distancia, 48.0)

                    velocidade = 14.0 + 20.0 * (1.0 - min(curva_hora[hora] / 2.05, 1.0))
                    duracao = round(max(3.0, distancia / velocidade * 60.0), 1)
                    fim = inicio + dt.timedelta(minutes=duracao)
                    valor = round(5.20 + distancia * 2.35 + duracao * 0.42, 2)

                    if rnd.random() < FRACAO_VALOR_TEXTO:
                        campo_valor: object = f"{valor:.2f}".replace(".", ",")
                        valores_texto += 1
                    else:
                        campo_valor = valor

                    evento = {
                        "corrida_id": f"c-{corrida:08d}",
                        "motorista_id": f"m-{rnd.randrange(1, 4200):05d}",
                        "passageiro_id": f"p-{rnd.randrange(1, 99999):05d}",
                        "bairro": escolhe_bairro(rnd, acumulado, nomes),
                        "data_corrida": inicio.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "fim": fim.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "distancia_km": distancia,
                        "duracao_min": duracao,
                        "valor": campo_valor,
                    }
                    linha = json.dumps(evento, ensure_ascii=False,
                                       separators=(", ", ":")) + "\n"
                    bytes_do_dia += len(linha.encode("utf-8"))
                    fh.write(linha)

        total_bytes += bytes_do_dia
        total_eventos += eventos_do_dia
        particoes.append((dia.isoformat(), eventos_do_dia, bytes_do_dia))

    return {
        "hoje": hoje.isoformat(),
        "particoes": particoes,
        "objetos": dias,
        "eventos": total_eventos,
        "bytes": total_bytes,
        "valores_texto": valores_texto,
        "destino": destino,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Gera eventos de corrida em JSON, particionados por dia.",
    )
    p.add_argument("--dias", type=int, default=30,
                   help="quantos dias de histórico gerar (padrão: 30)")
    p.add_argument("--taxa", type=int, default=12,
                   help="média de eventos por minuto, não o pico (padrão: 12)")
    p.add_argument("--seed", type=int, required=True,
                   help="semente do gerador; sem ela a demonstração não se repete")
    p.add_argument("--saida", type=Path, default=Path("./saida"),
                   help="diretório de saída (padrão: ./saida)")
    args = p.parse_args(argv)

    if args.dias < 1:
        p.error("--dias precisa ser 1 ou mais")
    if args.taxa < 1:
        p.error("--taxa precisa ser 1 ou mais")

    r = gera(args.dias, args.taxa, args.seed, args.saida)

    maior = max(r["particoes"], key=lambda t: t[2])
    hoje_part = [x for x in r["particoes"] if x[0] == r["hoje"]][0]

    print()
    print(f"destino ............. {r['destino']}")
    print(f"objetos ............. {r['objetos']}  (um por partição dt=)")
    print(f"eventos ............. {r['eventos']:,}".replace(",", "."))
    print(f"bytes ............... {r['bytes']:,}".replace(",", "."))
    print(f"maior partição ...... dt={maior[0]}  {maior[2]:,} bytes".replace(",", "."))
    print(f"partição de hoje .... dt={hoje_part[0]}  {hoje_part[2]:,} bytes".replace(",", "."))
    pct = 100.0 * r["valores_texto"] / r["eventos"]
    print(f"valor como texto .... {r['valores_texto']:,} eventos ({pct:.2f}%)".replace(",", "."))
    print()
    print("a linha WHERE do movimento 4, pronta para copiar:")
    print(f"    WHERE dt = '{r['hoje']}'")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
