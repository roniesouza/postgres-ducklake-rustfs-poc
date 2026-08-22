# AGENTS.md

## Objetivo e escopo

Manter uma PoC simples, local e replicável do seguinte pipeline:

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

O projeto deve separar claramente compute, catálogo de metadados e
armazenamento de dados. Não utilizar cloud, API, interface web, orquestrador ou
tecnologias adicionais sem necessidade.

## Tecnologias e estrutura

- Python 3.11 ou superior;
- `uv` para projeto, dependências e execução;
- DuckDB e DuckLake;
- PostgreSQL;
- RustFS;
- Docker Compose;
- Parquet em object storage S3-compatible.

Estrutura esperada:

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
└── src/
    └── main.py
```

## Componentes

### PostgreSQL SOURCE

O serviço `source_postgres` representa o banco operacional:

- database `source` na porta host `5432`;
- volume Docker persistente;
- inicialização automática por `docker/source/init.sql`;
- exatamente cinco tabelas relacionadas: `clientes`, `categorias`, `produtos`,
  `pedidos` e `itens_pedido`;
- poucos dados amostrais coerentes, suficientes para joins e cargas.

### Catálogo DuckLake

O serviço `lake_catalog_postgres` mantém exclusivamente metadados gerenciados
pelo DuckLake:

- database `lake_catalog` na porta host `5433`;
- volume Docker persistente;
- nenhum dado analítico armazenado nesse PostgreSQL.

### RustFS

O serviço `rustfs` armazena os arquivos Parquet:

- imagem oficial com versão pré-release fixada;
- API S3 na porta host `9000`;
- console na porta host `9001`;
- credenciais por variáveis de ambiente;
- volume Docker persistente;
- bucket criado manualmente pelo usuário no console.

Não adicionar AWS CLI, `boto3`, MinIO Client ou dependência semelhante apenas
para administrar o bucket.

## Configuração e segurança

- manter credenciais, portas, região e bucket em variáveis de ambiente;
- manter somente valores locais de desenvolvimento em `.env.example`;
- não versionar `.env`, credenciais, bancos locais ou artefatos de execução;
- não manter senhas fixas no código nem registrá-las nos logs;
- usar secrets temporários no DuckDB;
- não usar tags Docker `latest`;
- fixar versões exatas para imagens pré-release e ao menos a versão principal
  para imagens estáveis, conforme a necessidade de reprodutibilidade da PoC.

## Python

Gerenciar o projeto exclusivamente com `uv`. Não utilizar `pip`, Poetry ou
Conda. A única dependência de runtime necessária é `duckdb`.

Executar com:

```bash
uv run python src/main.py
```

Convenções:

- seguir a [PEP 8](https://peps.python.org/pep-0008/), priorizando legibilidade;
- usar `snake_case` para funções e variáveis;
- usar `PascalCase` para classes;
- usar nomes em maiúsculas para constantes;
- adicionar type hints em novas funções e estruturas;
- manter funções pequenas e com responsabilidade clara;
- priorizar a biblioteca padrão antes de adicionar dependências;
- não adicionar formatadores ou linters sem necessidade explícita.

## Pipeline

O `src/main.py` deve:

1. criar uma conexão DuckDB em memória;
2. instalar e carregar as extensões `postgres`, `httpfs` e `ducklake`;
3. anexar o PostgreSQL SOURCE como somente leitura;
4. configurar um secret S3 temporário para o RustFS;
5. anexar um DuckLake chamado `lake` usando o PostgreSQL de catálogo;
6. definir `DATA_PATH` como `s3://<bucket>/`;
7. usar `DATA_INLINING_ROW_LIMIT 0` para manter os dados em Parquet no RustFS;
8. recriar no DuckLake as cinco tabelas correspondentes ao SOURCE;
9. executar a carga completa em uma transação;
10. validar as quantidades SOURCE × DuckLake antes do `COMMIT`;
11. executar `ROLLBACK` e terminar com erro se a carga ou validação falhar;
12. concluir sem duplicar registros quando executado repetidamente.

A validação por contagem é intencionalmente simples para esta PoC. O README
deve explicar seu objetivo e as validações adicionais esperadas em produção.

## Logs

Usar somente o módulo `logging` da biblioteca padrão. Os logs devem ser simples
e indicar, no mínimo:

- início e conclusão do pipeline;
- conexão com SOURCE, DuckLake e RustFS;
- tabela sendo carregada e quantidade de registros;
- validação SOURCE × DuckLake;
- erros.

Não adicionar bibliotecas externas de logging ou observabilidade.

## Snapshot e Time Travel

O pipeline principal deve terminar após a carga e validação, sem criar
alterações extras apenas para demonstrar snapshots.

O README deve conter uma demonstração manual, compatível com a versão instalada
do DuckLake, que permita:

1. consultar o estado atual de uma tabela;
2. alterar ou inserir um registro;
3. listar os snapshots;
4. consultar novamente o estado atual;
5. consultar o snapshot anterior com Time Travel;
6. comparar os resultados e confirmar o versionamento.

A demonstração pode alterar o lake. Uma nova execução do pipeline deve
restaurar deterministicamente o conteúdo do SOURCE.

## Documentação

O README deve permitir executar todo o projeto a partir da raiz e documentar:

- preparação do `.env`;
- inicialização e encerramento dos containers;
- criação manual do bucket pelo console RustFS;
- instalação das dependências e execução do pipeline;
- acesso genérico somente leitura ao DuckLake;
- validação, idempotência, Time Travel e limitações da PoC;
- limpeza completa com `docker compose down -v`.

Alterações em arquitetura, ambiente, portas, dependências ou comandos devem
atualizar, quando aplicável, `README.md`, `.env.example`, `docker-compose.yml`,
`pyproject.toml`, `uv.lock` e este arquivo.

## Commits

Todos os commits devem seguir
[Conventional Commits](https://www.conventionalcommits.org/):

```text
<tipo>(<escopo opcional>): <descrição>
```

Usar tipos como `feat`, `fix`, `docs`, `refactor`, `test` e `chore`. Manter a
descrição curta, objetiva e coerente com a alteração.

Escrever a descrição em português. Os tipos definidos pelo Conventional Commits
devem permanecer em inglês.

## Validação proporcional

Aplicar somente as validações relacionadas à alteração:

- sempre executar `git diff --check`;
- executar `docker compose config --quiet` ao alterar Compose ou ambiente;
- executar `uv sync --locked` ao alterar dependências ou lockfile;
- executar `uv run python src/main.py` ao alterar código, configuração ou
  arquitetura do pipeline;
- repetir o pipeline ao alterar carga ou idempotência;
- confirmar a saúde dos containers em validações funcionais.

Alterações somente documentais não exigem executar o pipeline.

## Execução

Fluxo principal:

```bash
docker compose up -d --wait
# criar manualmente o bucket no console RustFS
uv sync
uv run python src/main.py
```

Encerrar preservando os volumes:

```bash
docker compose down
```

Reiniciar todo o exemplo, removendo bancos e objetos:

```bash
docker compose down -v
```

Após remover os volumes, o bucket deve ser criado novamente antes da carga.

## Definição de pronto

Uma alteração está concluída quando as validações proporcionais passam e os
requisitos afetados permanecem atendidos. Para a PoC completa, isso significa:

- os dois PostgreSQL e o RustFS sobem saudáveis pelo Docker Compose;
- o SOURCE é inicializado automaticamente com as cinco tabelas amostrais;
- o DuckLake usa o segundo PostgreSQL somente como catálogo;
- os dados ficam em arquivos Parquet no bucket RustFS;
- as cinco tabelas podem ser consultadas pelo DuckLake;
- as contagens SOURCE × DuckLake coincidem;
- uma segunda carga não falha nem duplica registros;
- falhas durante a carga provocam rollback;
- o README permite reproduzir o projeto e demonstra Time Travel.

Priorizar sempre simplicidade, legibilidade e poucas abstrações.
