import json
import sys

import boto3
from awsglue.context import GlueContext
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import functions as F

args = getResolvedOptions(sys.argv, ["BUCKET", "DIAS"])
BUCKET, DIAS = args["BUCKET"], args["DIAS"]
ORIGEM = f"s3://{BUCKET}/trusted/corridas/"
DESTINO = f"s3://{BUCKET}/refined/madrugada_bairro/"

sc = SparkContext.getOrCreate()
glue = GlueContext(sc)
spark = glue.spark_session
spark.conf.set("spark.sql.session.timeZone", "UTC")
spark.conf.set("spark.sql.sources.partitionColumnTypeInference.enabled", "false")
spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")


trusted = spark.read.parquet(ORIGEM)
if DIAS != "todos":
    trusted = trusted.filter(F.col("dt").isin([d.strip() for d in DIAS.split(",")]))


madrugada = trusted.filter(F.col("faixa_hora").between(0, 5))
df = (madrugada.groupBy("dt", "bairro")
      .agg(F.count(F.lit(1)).alias("corridas"),
           F.round(F.sum("valor"), 2).alias("receita"))
      .select("bairro", "corridas", "receita", "dt"))


df.repartition("dt").write.mode("overwrite").partitionBy("dt").parquet(DESTINO)


tabela = spark.read.parquet(DESTINO)
relatorio = {
    "tabela": "madrugada_bairro", "dias": DIAS,
    "corridas_lidas_da_trusted": trusted.count(),
    "corridas_da_madrugada": madrugada.count(),
    "linhas_gravadas_neste_run": df.count(),
    "dias_gravados_neste_run": df.select("dt").distinct().count(),
    "linhas_na_tabela": tabela.count(),
    "bairros_na_tabela": tabela.select("bairro").distinct().count(),
    "corridas_somadas_na_tabela": tabela.agg(F.sum("corridas")).first()[0],
}
texto = json.dumps(relatorio, indent=2)
print(texto)
boto3.client("s3").put_object(Bucket=BUCKET, Key="relatorios/madrugada_bairro.json",
                              Body=texto.encode("utf-8"), ContentType="application/json")
