#!/usr/bin/env bash
set -euo pipefail
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
cd "$(dirname "$0")"
RAIZ="$(pwd -P)"
BUCKET="$(terraform -chdir=terraform output -raw bucket)"
[[ "$BUCKET" =~ ^eda-a29-lake-[a-z0-9]{3,20}$ ]] || { echo "bucket fora do padrão do exercício: $BUCKET" >&2; exit 2; }
SAIDA="$RAIZ/.runtime/dados-gerados"
case "$SAIDA" in
  "$RAIZ"/.runtime/dados-gerados) ;;
  *) echo "caminho de saída inesperado: $SAIDA" >&2; exit 2 ;;
esac
rm -rf -- "$SAIDA"
mkdir -p "$SAIDA"
python3 app/gerador-eventos.py --dias 30 --taxa 12 --seed 42 --saida "$SAIDA" >/dev/null
python3 app/plantar-problemas.py --saida "$SAIDA" --atraso 3 --reentrega 5
echo ">> sincronizando com s3://$BUCKET/raw/"
aws s3 rm "s3://$BUCKET/raw/" --recursive --only-show-errors
aws s3 sync "$SAIDA/raw/" "s3://$BUCKET/raw/" --only-show-errors
aws s3 ls "s3://$BUCKET/raw/corridas/" --recursive --summarize | tail -2
HOJE=$(find "$SAIDA/raw/corridas" -maxdepth 1 -type d -name 'dt=*' | sed -n 's#.*/dt=##p' | sort | tail -1)
echo "hoje=$HOJE  desde=$(python3 -c "import datetime as d; print(d.date.fromisoformat('$HOJE') - d.timedelta(days=6))")"
