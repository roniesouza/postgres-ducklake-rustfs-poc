# DuckLake local PoC

PoC de um pipeline totalmente local que lê um PostgreSQL operacional com
DuckDB e grava cinco tabelas em DuckLake. O catálogo do DuckLake fica em um
segundo PostgreSQL e os dados analíticos ficam em arquivos Parquet locais.

```text
PostgreSQL SOURCE (:5432)
          |
        DuckDB
          |
       DuckLake
        /      \
PostgreSQL      data/ducklake/*.parquet
catálogo (:5433)
```

Nenhum serviço de nuvem é utilizado.

## DuckDB e DuckLake

- **[DuckDB](https://duckdb.org/docs/stable/)** é um banco analítico executado
  dentro do próprio processo da aplicação, sem exigir um servidor separado.
  Nesta PoC, ele lê o PostgreSQL SOURCE e executa a carga SQL.
- **[DuckLake](https://ducklake.select/docs/stable/)** é um formato de
  lakehouse que mantém os metadados em um catálogo SQL e os dados em arquivos
  Parquet. Ele acrescenta recursos como transações, snapshots e Time Travel.

Aqui, o DuckDB é o mecanismo de processamento; o DuckLake organiza e versiona
as tabelas, usando PostgreSQL como catálogo e `./data/ducklake` como
armazenamento físico local.

## Pré-requisitos

- Docker com Docker Compose;
- `uv` instalado;
- portas 5432 e 5433 livres.

## Execução

Crie a configuração local. No PowerShell:

```powershell
Copy-Item .env.example .env
```

Em Bash, use `cp .env.example .env`. Altere os valores do `.env` se as portas
ou credenciais locais precisarem ser diferentes. Esse arquivo é ignorado pelo
Git, e o Python o lê sem sobrescrever variáveis já exportadas no ambiente.

Suba os bancos, sincronize o ambiente Python e execute a carga:

```bash
docker compose up -d --wait
uv sync
uv run python src/main.py
```

O primeiro `INSTALL` das extensões `postgres` e `ducklake` feito pelo DuckDB
precisa de acesso à internet. Ao final, os logs mostram as contagens esperadas:

| tabela | registros |
|---|---:|
| `clientes` | 4 |
| `categorias` | 4 |
| `produtos` | 6 |
| `pedidos` | 5 |
| `itens_pedido` | 9 |

### Escopo da validação

A comparação de quantidades é intencionalmente simples. Nesta PoC, ela atua
como um smoke test para detectar cargas vazias, perdas evidentes de registros
e duplicações entre execuções, mantendo o código pequeno e focado na
arquitetura SOURCE → DuckLake.

Contagens iguais, isoladamente, não garantem a qualidade dos dados. Em um
projeto de produção, essa verificação deveria ser complementada por validações
de schema e tipos, unicidade e nulidade de chaves, integridade entre tabelas,
reconciliação de valores e checksums, freshness, regras de domínio, histórico
dos resultados e alertas. Essas camadas não foram adicionadas aqui para manter
o escopo didático e a simplicidade definidos para esta PoC.

A validação é executada antes do `COMMIT`. Se alguma contagem divergir, o
pipeline registra o erro, executa `ROLLBACK` e termina com código de saída `1`,
sem publicar parcialmente o novo snapshot.

O pipeline faz uma carga completa transacional: recria as tabelas no DuckLake
e valida cada contagem contra o SOURCE. Por isso, o mesmo comando pode ser
executado repetidas vezes sem duplicar registros:

```bash
uv run python src/main.py
```

Os Parquet podem ser conferidos com:

```powershell
Get-ChildItem -Recurse data/ducklake -Filter *.parquet
```

Em Bash, use `find data/ducklake -name '*.parquet'`. Arquivos antigos podem
permanecer após novas cargas porque snapshots históricos do DuckLake ainda os
referenciam; isso não representa duplicação nas tabelas atuais.

## O que fica em cada PostgreSQL

O serviço `source_postgres` cria automaticamente, por meio de
`docker/source/init.sql`, as tabelas relacionadas `clientes`, `categorias`,
`produtos`, `pedidos` e `itens_pedido`, com dados amostrais.

O serviço `lake_catalog_postgres` guarda somente as tabelas de metadados
gerenciadas pela extensão DuckLake. Os registros analíticos não são gravados
nesse banco, pois `DATA_INLINING_ROW_LIMIT 0` força a gravação no `DATA_PATH`
absoluto correspondente a `./data/ducklake`.

## Acesso genérico ao DuckLake

Com os contêineres ativos e a carga inicial concluída, qualquer ferramenta
capaz de abrir uma sessão DuckDB pode consultar o lake. O cliente deve usar a
mesma versão do DuckDB registrada no `uv.lock` (nesta PoC, `1.5.5`) e conseguir
carregar as extensões `postgres` e `ducklake`.

Execute o SQL abaixo ao iniciar a sessão. Substitua os valores pelos definidos
no `.env` e informe o caminho absoluto da pasta `data/ducklake`, usando `/`
como separador:

```sql
INSTALL postgres;
LOAD postgres;
INSTALL ducklake;
LOAD ducklake;

CREATE OR REPLACE SECRET catalog_postgres_secret (
    TYPE postgres,
    HOST '<LAKE_CATALOG_POSTGRES_HOST>',
    PORT <LAKE_CATALOG_POSTGRES_PORT>,
    DATABASE '<LAKE_CATALOG_POSTGRES_DB>',
    USER '<LAKE_CATALOG_POSTGRES_USER>',
    PASSWORD '<LAKE_CATALOG_POSTGRES_PASSWORD>'
);

CREATE OR REPLACE SECRET ducklake_access_secret (
    TYPE ducklake,
    METADATA_PATH '',
    DATA_PATH '<CAMINHO_ABSOLUTO_DO_PROJETO>/data/ducklake',
    METADATA_PARAMETERS MAP {
        'TYPE': 'postgres',
        'SECRET': 'catalog_postgres_secret'
    }
);

ATTACH 'ducklake:ducklake_access_secret' AS lake (READ_ONLY);
```

As tabelas estarão no catálogo `lake`, schema `main`, e podem ser consultadas
diretamente:

```sql
SELECT * FROM lake.main.clientes;
SELECT * FROM lake.main.produtos;
SELECT * FROM lake.main.pedidos;
```

A opção `READ_ONLY` mantém esse acesso apenas para consulta. Se a instalação
das extensões ou o `ATTACH` falhar, verifique o acesso à internet, a conexão
com o PostgreSQL de catálogo e a versão do cliente DuckDB. Esse procedimento
pode ser usado por clientes JDBC, CLI, Python ou outras ferramentas que
suportem DuckDB e suas extensões.

## Demonstração de Time Travel

Faça esta demonstração manual somente depois da carga inicial. Abra o Python
do projeto na raiz do repositório:

```bash
uv run python
```

No prompt Python, anexe o mesmo DuckLake e consulte o estado atual:

```python
from src.main import connect, load_settings

con = connect(load_settings())
con.execute("SELECT id, nome, descricao FROM lake.main.categorias WHERE id = 1").fetchall()
snapshot_anterior = con.execute("FROM lake.current_snapshot()").fetchone()[0]
```

Altere um registro no DuckLake e liste os snapshots, do mais novo para o mais
antigo:

```python
con.execute("UPDATE lake.main.categorias SET descricao = 'Descricao alterada no snapshot' WHERE id = 1")
con.execute("SELECT snapshot_id, snapshot_time, changes FROM lake.snapshots() ORDER BY snapshot_id DESC").fetchall()
```

Compare o estado atual com o snapshot capturado antes da alteração:

```python
con.execute("SELECT id, nome, descricao FROM lake.main.categorias WHERE id = 1").fetchall()
con.execute(f"SELECT id, nome, descricao FROM lake.main.categorias AT (VERSION => {snapshot_anterior}) WHERE id = 1").fetchall()
con.close()
```

A primeira consulta mostra a nova descrição; a consulta com `AT (VERSION =>
...)` mostra a descrição anterior, confirmando o versionamento. A alteração é
apenas demonstrativa: execute novamente `uv run python src/main.py` para
restaurar o conteúdo do SOURCE.

## Encerramento e limpeza

Para parar e remover os contêineres, preservando os volumes:

```bash
docker compose down
```

Para remover também os dois bancos/volumes e reiniciar o exemplo do zero:

```bash
docker compose down -v
```

Depois de remover os volumes, apague o conteúdo gerado em `data/ducklake`
(preservando `.gitkeep`) antes de criar um catálogo novo. Catálogo e arquivos
Parquet formam uma única unidade lógica e devem ser reiniciados juntos.

## Licença

Este projeto é distribuído sob a [licença MIT](LICENSE).
