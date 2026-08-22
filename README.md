# PostgreSQL + DuckLake + RustFS PoC

PoC de um pipeline totalmente local que lê um PostgreSQL operacional com
DuckDB e grava cinco tabelas em DuckLake. O catálogo do DuckLake fica em um
segundo PostgreSQL e os arquivos Parquet ficam no object storage RustFS.

```text
PostgreSQL SOURCE (:5432)
          |
        DuckDB                 compute
          |
       DuckLake               lakehouse
        /      \
PostgreSQL      RustFS         storage S3-compatible
catálogo (:5433)  |
                Parquet
```

Nenhum serviço de nuvem é utilizado.

## Motivação

Esta PoC nasceu do interesse em explorar stacks open source modernas para
engenharia de dados, combinando componentes simples, interoperáveis e com
responsabilidades bem definidas.

DuckDB, DuckLake, PostgreSQL e RustFS formam uma arquitetura local e replicável
que separa processamento, catálogo de metadados e armazenamento de objetos.
Essa combinação apresenta alto potencial para projetos que buscam independência
de fornecedores, compatibilidade com o ecossistema S3 e evolução gradual para
ambientes mais robustos.

O objetivo não é propor uma arquitetura pronta para produção, mas oferecer uma
base prática para experimentar as tecnologias, compreender suas integrações e
avaliar possibilidades com ferramentas abertas.

## DuckDB, DuckLake e RustFS

- **[DuckDB](https://duckdb.org/docs/stable/)** é um banco analítico executado
  dentro do próprio processo da aplicação, sem exigir um servidor separado.
  Nesta PoC, ele lê o PostgreSQL SOURCE e executa a carga SQL.
- **[DuckLake](https://ducklake.select/docs/stable/)** é um formato de
  lakehouse que mantém os metadados em um catálogo SQL e os dados em arquivos
  Parquet. Ele acrescenta recursos como transações, snapshots e Time Travel.
- **[RustFS](https://docs.rustfs.com/)** é um object storage open source e
  compatível com a API S3. Nesta PoC, ele armazena fisicamente os Parquet.

Aqui, o DuckDB é o mecanismo de processamento; o DuckLake organiza e versiona
as tabelas; o PostgreSQL mantém os metadados; e o RustFS mantém os dados. Essa
separação permite substituir o storage S3-compatible sem redesenhar o pipeline.

## Pré-requisitos

- Docker com Docker Compose;
- `uv` instalado;
- portas 5432, 5433, 9000 e 9001 livres.

## Execução

Crie a configuração local. No PowerShell:

```powershell
Copy-Item .env.example .env
```

Em Bash, use `cp .env.example .env`. Altere os valores do `.env` se as portas
ou credenciais locais precisarem ser diferentes. Esse arquivo é ignorado pelo
Git, e o Python o lê sem sobrescrever variáveis já exportadas no ambiente.

Se você executou uma versão anterior desta PoC, baseada em
`./data/ducklake`, reinicie os volumes uma vez antes de continuar:

```bash
docker compose down -v
```

Suba os três serviços:

```bash
docker compose up -d --wait
```

Abra o console RustFS em <http://localhost:9001>, entre com
`RUSTFS_ACCESS_KEY` e `RUSTFS_SECRET_KEY` e crie manualmente um bucket com o
nome definido em `RUSTFS_BUCKET` — por padrão, `ducklake`. O pipeline não cria
o bucket automaticamente.

Essa etapa manual é intencional: mantém o provisionamento do storage separado
da carga e evita adicionar SDKs ou ferramentas administrativas ao projeto. Em
uma automação real, o bucket seria criado previamente pela infraestrutura; o
pipeline continuaria apenas consumindo um bucket existente.

Depois, sincronize o ambiente Python e execute a carga:

```bash
uv sync
uv run python src/main.py
```

O primeiro `INSTALL` das extensões `postgres`, `httpfs` e `ducklake` feito pelo
DuckDB precisa de acesso à internet. Ao final, os logs mostram as contagens
esperadas:

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

Os objetos Parquet podem ser conferidos no console RustFS, dentro do bucket
configurado. Objetos antigos podem permanecer após novas cargas porque
snapshots históricos do DuckLake ainda os referenciam; isso não representa
duplicação nas tabelas atuais.

## Responsabilidades dos componentes

O serviço `source_postgres` cria automaticamente, por meio de
`docker/source/init.sql`, as tabelas relacionadas `clientes`, `categorias`,
`produtos`, `pedidos` e `itens_pedido`, com dados amostrais.

O serviço `lake_catalog_postgres` guarda somente as tabelas de metadados
gerenciadas pela extensão DuckLake. Os registros analíticos não são gravados
nesse banco, pois `DATA_INLINING_ROW_LIMIT 0` força a gravação dos dados no
`DATA_PATH` S3.

O serviço `rustfs` mantém os objetos Parquet em um volume Docker persistente.
Ele expõe a API S3 em `http://localhost:9000` e o console em
`http://localhost:9001`. O `DATA_PATH` usado pelo DuckLake é
`s3://<RUSTFS_BUCKET>/`.

## Acesso genérico ao DuckLake

Com os contêineres ativos e a carga inicial concluída, qualquer ferramenta
capaz de abrir uma sessão DuckDB pode consultar o lake. O cliente deve usar a
mesma versão do DuckDB registrada no `uv.lock` (nesta PoC, `1.5.5`), alcançar o
PostgreSQL e o endpoint RustFS e carregar `postgres`, `httpfs` e `ducklake`.

Execute o SQL abaixo ao iniciar a sessão e substitua os placeholders pelos
valores definidos no `.env`:

```sql
INSTALL postgres;
LOAD postgres;
INSTALL httpfs;
LOAD httpfs;
INSTALL ducklake;
LOAD ducklake;

CREATE TEMPORARY SECRET rustfs_s3_secret (
    TYPE s3,
    PROVIDER config,
    KEY_ID '<RUSTFS_ACCESS_KEY>',
    SECRET '<RUSTFS_SECRET_KEY>',
    REGION '<RUSTFS_REGION>',
    ENDPOINT '<RUSTFS_HOST>:<RUSTFS_API_PORT>',
    URL_STYLE 'path',
    USE_SSL false,
    SCOPE 's3://<RUSTFS_BUCKET>/'
);

CREATE TEMPORARY SECRET catalog_postgres_secret (
    TYPE postgres,
    HOST '<LAKE_CATALOG_POSTGRES_HOST>',
    PORT <LAKE_CATALOG_POSTGRES_PORT>,
    DATABASE '<LAKE_CATALOG_POSTGRES_DB>',
    USER '<LAKE_CATALOG_POSTGRES_USER>',
    PASSWORD '<LAKE_CATALOG_POSTGRES_PASSWORD>'
);

CREATE TEMPORARY SECRET ducklake_access_secret (
    TYPE ducklake,
    METADATA_PATH '',
    DATA_PATH 's3://<RUSTFS_BUCKET>/',
    METADATA_PARAMETERS MAP {
        'TYPE': 'postgres',
        'SECRET': 'catalog_postgres_secret'
    }
);

ATTACH 'ducklake:ducklake_access_secret' AS lake (
    AUTOMATIC_MIGRATION TRUE,
    DATA_INLINING_ROW_LIMIT 0,
    READ_ONLY
);
```

As tabelas estarão no catálogo `lake`, schema `main`, e podem ser consultadas
diretamente:

```sql
SELECT * FROM lake.main.clientes;
SELECT * FROM lake.main.categorias;
SELECT * FROM lake.main.produtos;
SELECT * FROM lake.main.pedidos;
SELECT * FROM lake.main.itens_pedido;
```

Os secrets existem somente durante essa sessão DuckDB. `READ_ONLY` mantém o
acesso apenas para consulta,
`DATA_INLINING_ROW_LIMIT 0` preserva a regra de manter dados fora do catálogo e
`AUTOMATIC_MIGRATION TRUE` permite atualizar o formato interno do catálogo se
a extensão usada pelo cliente exigir uma versão mais nova. Se a instalação das
extensões ou o `ATTACH` falhar, verifique o acesso à internet, a conexão com o
PostgreSQL de catálogo e a versão do cliente DuckDB. Esse procedimento pode ser
usado por clientes JDBC, CLI, Python ou outras ferramentas que suportem DuckDB
e suas extensões.

## Demonstração de Time Travel

Faça esta demonstração manual somente depois da carga inicial. Abra o Python
do projeto na raiz do repositório. O Python é usado porque esta demonstração
precisa alterar uma tabela para criar um novo snapshot, enquanto o acesso
genérico documentado anteriormente é intencionalmente somente leitura:

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
con.execute("SELECT snapshot_id, CAST(snapshot_time AS VARCHAR), changes FROM lake.snapshots() ORDER BY snapshot_id DESC").fetchall()
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

Para remover todos os bancos, objetos e volumes e reiniciar o exemplo do zero:

```bash
docker compose down -v
```

Esse comando remove os bancos e os objetos persistidos pelo RustFS. Na próxima
execução, recrie manualmente o bucket antes de executar o pipeline. Catálogo e
object storage formam uma única unidade lógica e devem ser reiniciados juntos.

## Limitações da PoC

O RustFS roda em um único container, com um único volume Docker. O volume
preserva os objetos durante reinícios e recriações normais do container, mas
não é backup nem oferece tolerância à perda do disco ou da máquina. Também não
há outro nó para assumir o serviço durante uma indisponibilidade.

O acesso usa HTTP, mas as portas estão vinculadas a `127.0.0.1`, limitando a
exposição à máquina local. Isso é suficiente para esta PoC. Em homologação ou
produção, a evolução natural seria habilitar
[TLS](https://docs.rustfs.com/en/integration/tls-configured), usar múltiplos
discos ou um cluster distribuído conforme os
[modos de implantação do RustFS](https://docs.rustfs.com/en/installation), além
de proteger e tornar resiliente o PostgreSQL de catálogo.

A imagem foi fixada em `rustfs/rustfs:1.0.0-beta.12` para tornar a demonstração
replicável enquanto o projeto RustFS ainda publica pré-releases.

## Licença

Este projeto é distribuído sob a [licença MIT](LICENSE).
