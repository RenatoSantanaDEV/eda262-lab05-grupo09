import json
import sys

import boto3
from awsglue.context import GlueContext
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import Window
from pyspark.sql import functions as F
from pyspark.sql import types as T

args = getResolvedOptions(sys.argv, ["BUCKET", "TABELA", "MODO", "JANELA", "DIAS", "CHAVE"])
BUCKET, TABELA, MODO = args["BUCKET"], args["TABELA"], args["MODO"]
JANELA, DIAS, CHAVE = int(args["JANELA"]), args["DIAS"], args["CHAVE"]
if MODO not in ("append", "sobrescrever"):
    raise ValueError("--MODO precisa ser append ou sobrescrever")
ORIGEM = f"s3://{BUCKET}/raw/corridas/"
DESTINO = f"s3://{BUCKET}/trusted/{TABELA}/"

sc = SparkContext.getOrCreate()
glue = GlueContext(sc)
spark = glue.spark_session
spark.conf.set("spark.sql.session.timeZone", "UTC")
spark.conf.set("spark.sql.sources.partitionColumnTypeInference.enabled", "false")

spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")


SCHEMA_RAW = T.StructType([
    T.StructField("corrida_id", T.StringType()),
    T.StructField("motorista_id", T.StringType()),
    T.StructField("passageiro_id", T.StringType()),
    T.StructField("bairro", T.StringType()),
    T.StructField("data_corrida", T.StringType()),
    T.StructField("fim", T.StringType()),
    T.StructField("distancia_km", T.DoubleType()),
    T.StructField("duracao_min", T.DoubleType()),
    T.StructField("valor", T.StringType()),
])
raw = spark.read.schema(SCHEMA_RAW).json(ORIGEM)


inicio = F.to_timestamp(F.col("data_corrida"))
df = raw.select(
    "corrida_id", "motorista_id", "passageiro_id", "bairro",
    inicio.alias("inicio"),
    F.to_timestamp(F.col("fim")).alias("fim"),
    "distancia_km", "duracao_min",
    F.regexp_replace(F.col("valor"), ",", ".").cast("double").alias("valor"),
    F.hour(inicio).alias("faixa_hora"),
    F.col("dt").alias("chegada"),
    F.date_format(inicio, "yyyy-MM-dd").alias("dt"),
)
lidas = df.count()


atraso = F.datediff(F.to_date("chegada"), F.to_date("dt"))
if DIAS == "todos":
    fora = df.filter(atraso > JANELA).count()
    df = df.filter((atraso >= 0) & (atraso <= JANELA))
else:
    df = df.filter(F.col("dt").isin([d.strip() for d in DIAS.split(",")]))
    fora = 0
    df = df.filter(atraso >= 0)


antes = df.count()
if CHAVE != "nenhuma":
    w = Window.partitionBy(CHAVE).orderBy("chegada")
    df = df.withColumn("_n", F.row_number().over(w)).filter("_n = 1").drop("_n")
depois = df.count()


modo = "append" if MODO == "append" else "overwrite"
df.repartition("dt").write.mode(modo).partitionBy("dt").parquet(DESTINO)


tabela = spark.read.parquet(DESTINO)
relatorio = {
    "tabela": TABELA, "modo": MODO, "janela": JANELA, "dias": DIAS, "chave": CHAVE,
    "linhas_lidas_da_raw": lidas,
    "fora_da_janela": fora,
    "duplicatas_removidas": antes - depois,
    "linhas_gravadas_neste_run": depois,
    "dias_gravados_neste_run": df.select("dt").distinct().count(),
    "linhas_na_tabela": tabela.count(),
    "corridas_distintas_na_tabela": tabela.select("corrida_id").distinct().count(),
}
texto = json.dumps(relatorio, indent=2)
print(texto)
boto3.client("s3").put_object(Bucket=BUCKET, Key=f"relatorios/{TABELA}.json",
                              Body=texto.encode("utf-8"), ContentType="application/json")
