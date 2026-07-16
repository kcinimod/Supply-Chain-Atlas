# MLflow tracking server + model registry, backed by Postgres.
# The official image ships no Postgres driver, so we add one.
FROM ghcr.io/mlflow/mlflow:v3.13.0

RUN pip install --no-cache-dir psycopg2-binary
