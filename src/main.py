"""Carga local PostgreSQL SOURCE -> DuckDB -> DuckLake."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb


LOGGER = logging.getLogger("ducklake-pipeline")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
TABLES = ("clientes", "categorias", "produtos", "pedidos", "itens_pedido")


@dataclass(frozen=True)
class PostgresConfig:
    host: str
    port: int
    database: str
    user: str
    password: str


@dataclass(frozen=True)
class ObjectStorageConfig:
    host: str
    port: int
    access_key: str
    secret_key: str
    region: str
    bucket: str

    @property
    def endpoint(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def data_path(self) -> str:
        return f"s3://{self.bucket}/"


@dataclass(frozen=True)
class Settings:
    source: PostgresConfig
    catalog: PostgresConfig
    object_storage: ObjectStorageConfig


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def load_env_file(path: Path) -> None:
    """Carrega um .env simples sem sobrescrever variaveis ja exportadas."""
    if not path.exists():
        return

    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Linha invalida em {path.name}:{line_number}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ValueError(f"Variavel de ambiente obrigatoria ausente: {name}")
    return value


def required_port(name: str) -> int:
    try:
        return int(required_env(name))
    except ValueError as exc:
        if "obrigatoria ausente" in str(exc):
            raise
        raise ValueError(f"{name} deve ser uma porta numerica") from exc


def postgres_config(prefix: str) -> PostgresConfig:
    port_name = f"{prefix}_PORT"

    return PostgresConfig(
        host=required_env(f"{prefix}_HOST"),
        port=required_port(port_name),
        database=required_env(f"{prefix}_DB"),
        user=required_env(f"{prefix}_USER"),
        password=required_env(f"{prefix}_PASSWORD"),
    )


def load_settings() -> Settings:
    load_env_file(PROJECT_ROOT / ".env")
    return Settings(
        source=postgres_config("SOURCE_POSTGRES"),
        catalog=postgres_config("LAKE_CATALOG_POSTGRES"),
        object_storage=ObjectStorageConfig(
            host=required_env("RUSTFS_HOST"),
            port=required_port("RUSTFS_API_PORT"),
            access_key=required_env("RUSTFS_ACCESS_KEY"),
            secret_key=required_env("RUSTFS_SECRET_KEY"),
            region=required_env("RUSTFS_REGION"),
            bucket=required_env("RUSTFS_BUCKET"),
        ),
    )


def sql_string(value: str | Path) -> str:
    """Escapa um literal SQL.

    Usado somente com configuracao local confiavel.
    """
    return "'" + str(value).replace("'", "''") + "'"


def create_postgres_secret(
    connection: duckdb.DuckDBPyConnection,
    name: str,
    config: PostgresConfig,
) -> None:
    connection.execute(
        f"""
        CREATE TEMPORARY SECRET {name} (
            TYPE postgres,
            HOST {sql_string(config.host)},
            PORT {config.port},
            DATABASE {sql_string(config.database)},
            USER {sql_string(config.user)},
            PASSWORD {sql_string(config.password)}
        )
        """
    )


def create_s3_secret(
    connection: duckdb.DuckDBPyConnection,
    config: ObjectStorageConfig,
) -> None:
    connection.execute(
        f"""
        CREATE TEMPORARY SECRET rustfs_s3_secret (
            TYPE s3,
            PROVIDER config,
            KEY_ID {sql_string(config.access_key)},
            SECRET {sql_string(config.secret_key)},
            REGION {sql_string(config.region)},
            ENDPOINT {sql_string(config.endpoint)},
            URL_STYLE 'path',
            USE_SSL false,
            SCOPE {sql_string(config.data_path)}
        )
        """
    )


def connect(settings: Settings) -> duckdb.DuckDBPyConnection:
    """Cria o DuckDB em memoria e anexa SOURCE e DuckLake."""
    connection = duckdb.connect(":memory:")
    try:
        for extension in ("postgres", "httpfs", "ducklake"):
            connection.execute(f"INSTALL {extension}")
            connection.execute(f"LOAD {extension}")

        create_postgres_secret(
            connection,
            "source_postgres_secret",
            settings.source,
        )
        create_postgres_secret(
            connection,
            "lake_catalog_postgres_secret",
            settings.catalog,
        )
        create_s3_secret(connection, settings.object_storage)
        connection.execute(
            f"""
            CREATE TEMPORARY SECRET lake_ducklake_secret (
                TYPE ducklake,
                METADATA_PATH '',
                DATA_PATH {sql_string(settings.object_storage.data_path)},
                METADATA_PARAMETERS MAP {{
                    'TYPE': 'postgres',
                    'SECRET': 'lake_catalog_postgres_secret'
                }}
            )
            """
        )

        LOGGER.info(
            "Conectando ao PostgreSQL SOURCE em %s:%s",
            settings.source.host,
            settings.source.port,
        )
        connection.execute(
            """
            ATTACH '' AS source (
                TYPE postgres,
                SECRET source_postgres_secret,
                READ_ONLY
            )
            """
        )

        LOGGER.info(
            "Conectando ao RustFS em %s; bucket=%s",
            settings.object_storage.endpoint,
            settings.object_storage.bucket,
        )
        LOGGER.info(
            "Conectando ao catalogo DuckLake em %s:%s",
            settings.catalog.host,
            settings.catalog.port,
        )
        connection.execute(
            "ATTACH 'ducklake:lake_ducklake_secret' AS lake "
            "(DATA_INLINING_ROW_LIMIT 0)"
        )
        LOGGER.info(
            "DuckLake anexado; DATA_PATH=%s",
            settings.object_storage.data_path,
        )
        return connection
    except Exception:
        connection.close()
        raise


def refresh_tables(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute("BEGIN TRANSACTION")
    try:
        for table in TABLES:
            LOGGER.info("Carregando tabela: %s", table)
            connection.execute(f'DROP TABLE IF EXISTS lake.main."{table}"')
            connection.execute(
                f'CREATE TABLE lake.main."{table}" AS '
                f'SELECT * FROM source.public."{table}"'
            )
            count = connection.execute(
                f'SELECT count(*) FROM lake.main."{table}"'
            ).fetchone()[0]
            LOGGER.info("Tabela %s carregada: %s registros", table, count)
        validate_counts(connection)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


def validate_counts(connection: duckdb.DuckDBPyConnection) -> None:
    LOGGER.info("Validando contagens SOURCE x DuckLake")
    mismatches: list[str] = []
    for table in TABLES:
        source_count = connection.execute(
            f'SELECT count(*) FROM source.public."{table}"'
        ).fetchone()[0]
        lake_count = connection.execute(
            f'SELECT count(*) FROM lake.main."{table}"'
        ).fetchone()[0]
        LOGGER.info(
            "Validacao %-13s SOURCE=%s DuckLake=%s",
            table,
            source_count,
            lake_count,
        )
        if source_count != lake_count:
            mismatches.append(
                f"{table}: SOURCE={source_count}, DuckLake={lake_count}"
            )

    if mismatches:
        raise RuntimeError("Contagens divergentes: " + "; ".join(mismatches))


def run_pipeline() -> None:
    LOGGER.info("Iniciando pipeline local SOURCE -> DuckLake")
    settings = load_settings()
    connection = connect(settings)
    try:
        refresh_tables(connection)
    finally:
        connection.close()
    LOGGER.info("Pipeline concluido com sucesso")


def main() -> int:
    configure_logging()
    try:
        run_pipeline()
    except Exception as exc:
        LOGGER.error("Pipeline encerrado com erro: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
