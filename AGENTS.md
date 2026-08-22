# AGENTS.md

## Objetivo

Criar um projeto simples demonstrando um pipeline local:

```text
PostgreSQL SOURCE (Docker)
        ↓
      DuckDB                 compute
        ↓
     DuckLake                lakehouse
      ↙     ↘
PostgreSQL   RustFS          object storage S3-compatible
metadados    (Docker)
                 ↓
              Parquet
```

Não utilizar serviços de nuvem.

## Tecnologias

- Python
- `uv` para gerenciamento do projeto e dependências
- DuckDB
- DuckLake
- PostgreSQL
- RustFS
- Docker Compose
- Parquet em object storage S3-compatible

## Estrutura esperada

```text
.
├── AGENTS.md
├── README.md
├── pyproject.toml
├── uv.lock
├── .env.example
├── docker-compose.yml
├── docker/
│   └── source/
│       └── init.sql
├── src/
│   └── main.py
```

## PostgreSQL

Criar dois serviços no `docker-compose.yml`:

### `source_postgres`

Banco operacional de origem.

- database: `source`
- porta host: `5432`
- executar automaticamente `docker/source/init.sql`

Criar e popular aproximadamente 5 tabelas relacionadas:

- `clientes`
- `categorias`
- `produtos`
- `pedidos`
- `itens_pedido`

Adicionar poucos dados amostrais coerentes, suficientes para testar joins e cargas.

### `lake_catalog_postgres`

Catálogo de metadados do DuckLake.

- database: `lake_catalog`
- porta host: `5433`
- não armazenar os dados analíticos neste banco

Usar volumes Docker persistentes para ambos os serviços.

## RustFS

Criar um serviço `rustfs` no `docker-compose.yml`:

- imagem Docker oficial com versão fixada;
- API S3 na porta host `9000`;
- console na porta host `9001`;
- credenciais configuradas por variáveis de ambiente;
- volume Docker persistente para os objetos;
- bucket criado manualmente pelo usuário no console.

O RustFS deve armazenar os arquivos Parquet. Não adicionar AWS CLI, `boto3`,
MinIO Client ou outra dependência apenas para administrar o bucket.

## Python

Inicializar e gerenciar exclusivamente com `uv`.

Dependência principal:

```bash
uv add duckdb
```

Executar o projeto com:

```bash
uv run python src/main.py
```

Não utilizar `pip`, Poetry ou Conda.

## Pipeline

O `src/main.py` deve:

1. Criar uma conexão DuckDB local ou em memória.
2. Instalar/carregar as extensões `postgres`, `httpfs` e `ducklake`.
3. Anexar o PostgreSQL SOURCE como somente leitura.
4. Anexar um DuckLake chamado `lake`.
5. Usar o PostgreSQL `lake_catalog_postgres` como catálogo do DuckLake.
6. Configurar um secret S3 temporário apontando para o RustFS.
7. Definir o `DATA_PATH` como `s3://<bucket>/`.
8. Configurar `DATA_INLINING_ROW_LIMIT 0` para manter os dados em Parquet no RustFS.
9. Criar no DuckLake uma tabela correspondente para cada tabela do SOURCE.
10. Fazer uma carga completa dos dados amostrais.
11. Consultar as tabelas carregadas e imprimir uma validação simples com quantidade de registros.
12. A execução deve ser idempotente: executar `uv run python src/main.py` várias vezes não deve gerar erro nem duplicar dados.

Fluxo esperado:

```text
source.clientes
source.categorias
source.produtos
source.pedidos
source.itens_pedido
        ↓
      DuckDB
        ↓
      DuckLake
        ↓
RustFS / s3://<bucket>/.../*.parquet
```

O PostgreSQL `lake_catalog` deve conter apenas os metadados gerenciados pelo DuckLake.

## Logs

Exibir no console o progresso da execução utilizando o módulo `logging`
da biblioteca padrão do Python.

Os logs devem indicar, no mínimo:

- início do pipeline;
- conexão com SOURCE;
- conexão com DuckLake;
- conexão com RustFS;
- tabela sendo carregada;
- quantidade de registros carregados;
- validação SOURCE × DuckLake;
- conclusão do pipeline;
- erros, quando ocorrerem.

Manter os logs simples e legíveis. Não adicionar bibliotecas externas
de logging ou observabilidade.

## Configuração

Credenciais, portas, região e bucket devem ficar em variáveis de ambiente.

Criar `.env.example` com valores locais de desenvolvimento.

O código não deve conter senhas fixas.

## Demonstração de Snapshot / Time Travel

O pipeline principal deve terminar após a carga e validação dos dados.

Não criar automaticamente alterações adicionais apenas para demonstrar snapshots.

O `README.md` deve conter uma seção curta chamada **Demonstração de Time Travel**, para ser executada manualmente pelo usuário após a carga inicial.

A demonstração deve orientar o usuário a:

1. consultar o estado atual de uma tabela do DuckLake;
2. alterar ou inserir um registro no DuckLake;
3. listar os snapshots existentes;
4. consultar novamente o estado atual;
5. consultar o snapshot anterior usando Time Travel;
6. comparar os resultados e confirmar o versionamento.

Usar comandos compatíveis com a versão do DuckLake instalada no projeto.

A finalidade dessa etapa é demonstrar que o projeto utiliza recursos do DuckLake além da simples gravação de arquivos Parquet.

## Idempotência

O projeto deve poder ser executado várias vezes com:

```bash
uv run python src/main.py
```

sem:

- duplicar registros;
- falhar porque tabelas já existem;
- exigir limpeza manual entre execuções.

Para esta PoC, priorizar uma solução simples e determinística. É aceitável recriar as tabelas do DuckLake antes da carga completa.

A demonstração manual de Time Travel descrita no README é independente dessa característica e pode alterar o estado do Lake após a carga inicial.

## Docker

O projeto deve funcionar com:

```bash
docker compose up -d
uv sync
uv run python src/main.py
```

O bucket configurado deve ser criado manualmente no console RustFS após subir
os serviços e antes da primeira execução do pipeline.

Também documentar no `README.md` como encerrar o ambiente:

```bash
docker compose down
```

e como apagar completamente bancos/volumes para reiniciar o exemplo:

```bash
docker compose down -v
```

## Critérios de conclusão

O projeto está concluído quando:

- o progresso da execução é exibido de forma clara no terminal;
- os dois PostgreSQL e o RustFS sobem via Docker Compose;
- o SOURCE é criado automaticamente com dados amostrais;
- o Python conecta ao SOURCE;
- o DuckLake utiliza o segundo PostgreSQL como catálogo;
- arquivos Parquet aparecem no bucket RustFS;
- as 5 tabelas podem ser consultadas através do DuckLake;
- a quantidade de registros SOURCE × DuckLake é validada;
- executar o pipeline novamente não duplica dados nem gera erros;
- o `README.md` contém a demonstração manual de Snapshot / Time Travel;
- todo o projeto pode ser executado apenas seguindo o `README.md`.

Priorizar simplicidade, código legível e poucas abstrações. Não implementar cloud, API, interface web, orquestrador ou recursos desnecessários.
