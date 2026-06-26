# Resumo das Alterações — thornspkg v0.4.0

Este documento descreve em detalhes todas as alterações realizadas no gerenciador
de pacotes **thornspkg**, seguindo as 6 frentes solicitadas, mantendo total
compatibilidade com bancos e repositórios existentes.

---

## 1. Reorganização da CLI

### Problema
O arquivo `cli.py` tinha 1.412 linhas, misturando parser argparse, lógica de
negócio, helpers de UI e implementação de cada subcomando — difícil de manter e
testar.

### Solução
A CLI foi refatorada em uma estrutura modular:

```
thornspkg/
├── cli.py                 # 348 linhas — apenas argparse + dispatcher
└── commands/              # NOVO pacote
    ├── __init__.py
    ├── common.py          # helpers compartilhados (UI, install, repo)
    ├── install.py         # thorn install (+ --force-overwrite)
    ├── remove.py          # thorn remove, autoremove, recover-tx
    ├── search.py          # thorn search, list, info, why, outdated
    ├── repo.py            # thorn repo (add/remove/list/refresh)
    ├── sync.py            # thorn sync (alias de repo refresh)
    ├── upgrade.py         # thorn upgrade, list-upgrades (NOVO)
    ├── check.py           # thorn check
    ├── orphan.py          # thorn orphan-files
    ├── inspect.py         # thorn deps, tree, fetch, files, owns, log, suggest-deps
    └── cache_cmd.py       # thorn cache stats/clean/list (NOVO)
```

### Benefícios
- **cli.py enxuto**: 348 linhas vs 1.412 (−75%) — apenas definição de argparse.
- **Cada comando isolado**: fácil de localizar, testar e modificar.
- **Helpers reutilizáveis** em `commands/common.py`: `err()`, `warn()`,
  `confirm()`, `build_one()`, `install_binary_one()`, `do_remove_one()`,
  `resolve_install_order()`, `install_one_package()`, etc.
- **Zero quebra de compatibilidade**: todos os comandos existentes continuam
  funcionando com as mesmas flags.

---

## 2. Rastreamento de propriedade de arquivos

### Problema
O thornspkg já mantinha `files` no banco, mas não verificava conflitos antes
de instalar — um pacote podia silenciosamente sobrescrever arquivos de outro.

### Solução
Novo módulo `thornspkg/fileconflict.py` (172 linhas) com:

- **`FileConflictError(Exception)`**: exceção com atributos `package` e
  `conflicts: list[tuple[file_path, owner_package]]`, e mensagem amigável.
- **`build_file_index(db)`**: constrói índice `{caminho: pacote}` para
  consulta O(1).
- **`find_owner(db, rel_path)`**: consulta O(N) sem índice pré-construído.
- **`find_owner_indexed(index, rel_path)`**: consulta O(1) com índice.
- **`check_conflicts(manifest, db, new_package, *, index)`**: retorna lista
  de conflitos (vazia = sem conflito).
- **`assert_no_conflicts(...)`**: idem mas levanta `FileConflictError`.
- **`to_rel_path` / `to_abs_path`**: normalização de caminhos.

### Integração
- `commands/install.py` chama `assert_no_conflicts` após obter o manifest,
  mas **antes** de gravar no DB. Em caso de conflito:
  - Modo normal: reverte arquivos já instalados + retorna erro 1.
  - Modo `--atomic`: inclui no bloco `except` que faz rollback completo.
- `--force-overwrite` (nova flag) desabilita a verificação.
- `db.find_owner_of_file()` e `db.find_owner()` expostos para consulta ad-hoc.

### Comandos CLI
- `thorn owns <caminho>` (pré-existente, agora usando `find_owner_of_file`):
  ```
  $ thorn owns /usr/bin/vim
  /usr/bin/vim  →  vim 9.1
  ```
- `thorn files <pacote>` (pré-existente, agora em `commands/inspect.py`).

---

## 3. Metadados expandidos

### Problema
O banco armazenava apenas `version`, `depends`, `optional_deps`, `provides`,
`reason`, `files`, `installed_at`, `updated_at`, `checked_at`.

### Solução
Adicionados 10 novos campos opcionais em `Recipe` e `db["packages"][name]`:

| Campo | Tipo | Default | Descrição |
|-------|------|---------|-----------|
| `build_date` | str\|None | None | Data de build do pacote (ISO 8601) |
| `install_date` | str\|None | None | Alias de `installed_at` |
| `install_size` | int\|None | None | Tamanho instalado em bytes |
| `download_size` | int\|None | None | Tamanho do download em bytes |
| `repository` | str\|None | None | Nome do repositório de origem |
| `architecture` | str\|None | None | `x86_64`, `aarch64`, `any` |
| `license` | str\|None | None | Licença (SPDX ou nome) |
| `description` | str | "" | Descrição curta |
| `homepage` | str\|None | None | URL do projeto |
| `maintainer` | str\|None | None | Mantenedor da receita |

### Migração automática
- `db.migrate_db(db)` é idempotente: percorre todos os pacotes e adiciona
  campos ausentes com defaults seguros. Chamada automaticamente por
  `load_db()`.
- `db.save_db_migrated()` força migração antes de gravar.
- **Compatibilidade**: bancos antigos continuam funcionando. Nenhum dado é
  perdido. `install_date` é populado com `installed_at` quando possível.

### Recipe
- `Recipe` ganhou os mesmos campos como `dataclass` fields com defaults.
- `Recipe.to_metadata_dict()` retorna dict pronto para serialização.
- `recipe.py` `load_recipe()` extrai os novos campos do TOML.

### Comando `thorn info` (expandido)
Agora exibe todos os metadados, da receita e do banco:
```
$ thorn info curl
curl  8.8.0
  Descrição:      Ferramenta e biblioteca de transferência de dados por URL
  Homepage:       https://curl.se/
  Licença:        curl
  Mantenedor:     Daniel Stenberg <daniel@haxx.se>
  Repositório:    core
  Receita:        recipes/curl.toml
  Build system:   autotools
  Source(s):
    https://curl.se/download/curl-8.8.0.tar.gz
  Depends:        zlib>=1.2, openssl>=3.0

  [não instalado]
```

---

## 4. Sistema de atualização de pacotes

### Problema
Não existia comando de upgrade — apenas `outdated` que comparava com a receita.

### Solução
Novo módulo `commands/upgrade.py` (304 linhas) com:

- **`thorn sync`** (também em `commands/sync.py`, 21 linhas): atualiza índices
  dos repositórios. Alias curto para `repo refresh`.
- **`thorn list-upgrades`**: lista pacotes desatualizados sem instalar.
- **`thorn upgrade [pkg...]`**: atualiza todos (ou apenas os especificados).
  - Usa `version.py` para comparar versões instaladas vs. disponíveis
    (receita local ou repositório).
  - Resolve dependências transitivas via `resolve_install_order()`.
  - Suporta `--dry-run`, `--atomic` (com rollback), `--reinstall`,
    `--prefer-binary`, `--prefer-source`, `--force-overwrite`.

### Fluxo interno
1. `find_upgrades(db, recipes, cfg)`: compara versões, retorna
   `[(name, installed_ver, available_ver, source)]`.
2. `cmd_list_upgrades`: exibe tabela formatada.
3. `cmd_upgrade`: resolve ordem, filtra pacotes desatualizados, executa
   transação (`_upgrade_normal` ou `_upgrade_atomic`).
4. Em caso de erro com `--atomic`: rollback via `TransactionJournal.rollback()`.

### Exemplos
```sh
sudo thorn sync                  # baixa índices atualizados
thorn list-upgrades              # mostra o que está desatualizado
sudo thorn upgrade               # atualiza tudo
sudo thorn upgrade curl          # atualiza apenas curl (e deps)
sudo thorn upgrade --atomic      # com rollback em caso de erro
thorn upgrade --dry-run          # simula
```

---

## 5. Dependências com versões

### Problema
`depends = ["openssl", "python"]` — só nomes, sem constraints de versão.

### Solução
Novo módulo `thornspkg/version.py` (404 linhas) com:

- **`Version` (dataclass)**: versão parseada, comparável, hasheável.
  - Suporta: parte numérica, epoch (`1:5.0`), sufixos de release (`-1`),
    pré-releases (`rc1`, `beta2`, `alpha1`, `pre3`), sufixos debian-like
    (`+r1`).
  - Comparação robusta: `alpha < beta < pre < rc < release < +r1 < -1`.
- **`Constraint` (dataclass)**: `name`, `op`, `version`.
- **`parse_constraint(spec)`**: faz parse de `"openssl>=3.0"` etc.
- **`satisfies(provided_name, provided_version, constraint)`**: verificação.
- **`compare(a, b)`**: retorna -1/0/1.
- **`dep_name(spec)`**: extrai apenas o nome (`"openssl>=3.0"` → `"openssl"`).

### Operadores suportados
`>`, `>=`, `<`, `<=`, `=`, `!=`, `==` (alias de `=`).

### Integração com `depgraph.py`
- `resolve_order()` ganhou parâmetro `installed_versions: dict[str, str]`.
- Quando uma constraint tem `op` e o pacote está instalado, verifica
  `satisfies()`. Se falhar, levanta `VersionConflictError`.
- `dep_name()` é usado para extrair o nome base das depends (ignorando
  operadores) em `reverse_deps()`, `dep_tree_lines()`, `find_dependents()`,
  `find_all_orphans()`.

### Exemplos
```toml
depends = [
    "openssl>=3.0",
    "python<3.15",
    "glibc>=2.40",
    "curl=8.9.1",
    "bash!=5.0",
    "zlib",  # sem operador = qualquer versão
]
```

```python
satisfies("openssl", "3.5", "openssl>=3.0")  # True
satisfies("python", "3.15", "python<3.15")   # False
satisfies("curl", "8.9.1", "curl=8.9.1")     # True
compare("3.12.4", "3.12.10")                  # -1 (older)
compare("1:5.0", "5.0")                       # 1 (epoch wins)
compare("1.2.3rc1", "1.2.3beta1")             # 1 (rc > beta)
```

---

## 6. Cache local de fontes e downloads

### Problema
Downloads eram feitos diretamente para `sources_dir` sem verificação de
checksum no cache — re-instalações rebaixavam arquivos.

### Solução
Novo módulo `thornspkg/cache.py` (326 linhas) com cache persistente:

```
/var/cache/thornspkg/
├── sources/    — tarballs de código-fonte
├── packages/   — pacotes binários baixados
└── indexes/    — índices de repositórios
```

### Funcionamento
- `get_cached_source(url, cfg, expected_sha256)`: retorna `CacheResult` com
  `path`, `from_cache`, `downloaded`. Verifica checksum antes de reaproveitar.
- `get_cached_package(url, cfg, expected_sha256)`: idem para binários.
- `put_in_cache(local_path, cache_dir, filename)`: copia arquivo local para cache.
- `_verify_checksum(path, expected)`: True se arquivo existe e checksum bate.

### Estatísticas e limpeza
- `cache_stats(cfg) → CacheStats`: conta arquivos e bytes por categoria.
- `cache_clean(cfg, *, sources=True, packages=True, indexes=False)`: remove
  conteúdo. Por padrão não limpa `indexes/` (são baratos de manter).
- `cache_list(cfg)`: lista arquivos por categoria.

### Integração
- `commands/common.py` `install_binary_one()` usa `get_cached_package()`
  em vez de baixar diretamente.
- `builder.fetch_source()` continua usando `sources_dir` como cache (já
  fazia cache simples, agora é parte da arquitetura documentada).

### Comando `thorn cache`
```sh
$ thorn cache stats
Cache do thornspkg:
  sources/       12 arquivo(s)   145.3 MB
  packages/       8 arquivo(s)   98.7 MB
  indexes/        3 arquivo(s)   12.4 KB
  ──────────────────────────────────────────────────
  total          23 arquivo(s)   244.0 MB

$ thorn cache list
$ thorn cache clean              # limpa sources/ e packages/
$ thorn cache clean --indexes    # limpa também indexes/
$ thorn cache clean --no-sources # não limpa sources/
```

---

## Requisitos de qualidade

### Bugs corrigidos
- **Regex de `parse_constraint`**: o quantificador `*?` (non-greedy) fazia
  `parse_constraint("python")` retornar `name="p", version="ython"`. Corrigido
  para `*` (greedy), que para no primeiro operador.
- **Hash de `Version`**: `parse_version("1.0")` e `parse_version("1.0.0")`
  eram iguais sob `__eq__` mas tinham hashes diferentes. Adicionado
  `_comparison_key()` que normaliza removendo zeros à direita.

### Type hints
- Todos os novos módulos (`version.py`, `fileconflict.py`, `cache.py`) têm
  type hints completos.
- Módulos refatorados (`db.py`, `depgraph.py`, `commands/*.py`) receberam
  type hints nas assinaturas públicas.

### Tratamento de exceções
- Novas exceções: `FileConflictError`, `VersionError`, `VersionConflictError`,
  `CacheError`.
- `commands/install.py` e `commands/upgrade.py` capturam explicitamente
  `FileConflictError` para fazer rollback apropriado.
- `commands/common.py` `resolve_install_order()` captura
  `VersionConflictError` e tenta fallback via repositórios antes de desistir.

### Compatibilidade com LFS
- **Zero novas dependências externas**. Tudo usa stdlib (`re`, `hashlib`,
  `json`, `pathlib`, `dataclasses`, `tempfile`).
- `tomli` continua sendo fallback opcional para Python < 3.11 (não muda).
- `requires-python = ">=3.9"` mantido.

### SOLID
- **Single Responsibility**: cada módulo tem uma responsabilidade clara
  (`version.py` = parsing, `fileconflict.py` = ownership, `cache.py` = cache).
- **Open/Closed**: `Recipe` é extensível via `extra_metadata` em
  `record_install` sem precisar mudar a assinatura.
- **Dependency Inversion**: `commands/common.py` depende de abstrações
  (módulos `db`, `builder`, `repo`), não de implementações concretas.

### Testes
- **74 testes unitários** em `tests/`, todos passando:
  - `test_version.py` (22 testes): parsing, comparação, constraints, satisfies.
  - `test_fileconflict.py` (9 testes): build_file_index, find_owner,
    check_conflicts, assert_no_conflicts, FileConflictError.
  - `test_db.py` (10 testes): migração, record_install, find_dependents,
    orphans, find_owner_of_file, get_metadata, TransactionJournal.
  - `test_depgraph.py` (8 testes): resolve_order com constraints,
    VersionConflictError, ciclos, missing deps, reverse_deps, dep_tree_lines.
  - `test_cache.py` (12 testes): cache_stats, cache_clean, cache_list,
    CacheStats.human_size.
  - `test_commands_common.py` (1 teste): build_installed_versions.
- Script `scripts/demo_v04.py` demonstra todos os novos recursos em ação.

---

## Árvore final do projeto

```
thornspkg/
├── README.md                          (atualizado com novos recursos)
├── pyproject.toml                     (versão bumped para 0.4.0)
├── recipes/                           (curl.toml e openssl.toml atualizados)
│   ├── bash.toml
│   ├── bzip2.toml
│   ├── curl.toml                      ← metadados + depends versionadas
│   ├── git.toml
│   ├── libffi.toml
│   ├── ncurses.toml
│   ├── openssl.toml                   ← metadados + depends versionadas
│   ├── python.toml
│   ├── readline.toml
│   ├── sqlite.toml
│   ├── xz.toml
│   ├── zlib.toml
│   └── algo.txt
├── scripts/
│   └── demo_v04.py                    (NOVO — demonstração dos recursos)
├── tests/                             (NOVO — 74 testes unitários)
│   ├── __init__.py
│   ├── test_cache.py
│   ├── test_commands_common.py
│   ├── test_db.py
│   ├── test_depgraph.py
│   ├── test_fileconflict.py
│   └── test_version.py
└── thornspkg/
    ├── __init__.py                    (versão bumped para 0.4.0)
    ├── __main__.py
    ├── builder.py                     (adicionado compute_install_size, download_to_temp)
    ├── cache.py                       (NOVO — 326 linhas)
    ├── cli.py                         (refatorado: 1412 → 348 linhas)
    ├── colors.py
    ├── commands/                      (NOVO pacote)
    │   ├── __init__.py
    │   ├── cache_cmd.py
    │   ├── check.py
    │   ├── common.py                  (392 linhas — helpers compartilhados)
    │   ├── inspect.py
    │   ├── install.py
    │   ├── orphan.py
    │   ├── remove.py
    │   ├── repo.py
    │   ├── search.py
    │   ├── sync.py
    │   └── upgrade.py
    ├── config.py
    ├── db.py                          (migrate_db, get_metadata, find_owner_of_file)
    ├── depgraph.py                    (versionamento integrado)
    ├── downloader.py
    ├── fileconflict.py                (NOVO — 172 linhas)
    ├── hooks.py
    ├── lock.py
    ├── orphan.py
    ├── recipe.py                      (metadados expandidos)
    ├── repo.py
    ├── signature.py
    ├── suggest.py
    └── version.py                     (NOVO — 404 linhas)
```

---

## Arquitetura adotada

### Camadas
1. **Núcleo** (`db.py`, `recipe.py`, `config.py`, `depgraph.py`, `version.py`,
   `fileconflict.py`, `cache.py`, `builder.py`, `downloader.py`, `repo.py`,
   `hooks.py`, `lock.py`, `colors.py`, `orphan.py`, `suggest.py`,
   `signature.py`): módulos de domínio, sem dependência da CLI.
2. **Commands** (`commands/*.py`): implementação de cada subcomando, usando
   o núcleo. `commands/common.py` centraliza helpers compartilhados.
3. **CLI** (`cli.py`): apenas argparse + dispatcher. Carrega receitas, builda
   `pmap`, acquire lock, chama `args.func(args, recipes, pmap, cfg)`.

### Fluxo de uma instalação
```
cli.main()
  → parser.parse_args(argv)
  → build_config(args)
  → PackageLock(cfg.db_dir).acquire()    [se cmd de escrita]
  → load_all_recipes(cfg.recipes_dir)
  → build_provides_map(recipes)
  → args.func(args, recipes, pmap, cfg)  [dispatch para commands/install.py]
      → resolve_install_order(...)
          → depgraph.resolve_order(..., installed_versions=...)
              → version.parse_constraint() / version.satisfies()
              → version.VersionConflictError se não satisfaz
      → for pkg in todo:
          → commands/common.install_one_package(...)
              → fileconflict.assert_no_conflicts(manifest, db, pkg)
                  → fileconflict.FileConflictError se conflito
              → cache.get_cached_package() / builder.fetch_source()
              → builder.build_and_install() / builder.install_binary_package()
          → db.record_install(db, recipe, manifest, extra_metadata=...)
          → db.save_db()
  → lock.release()
```

### Migrações realizadas
1. **DB `installed.json`**: `migrate_db()` adiciona campos v0.4+ com defaults
   seguros. Idempotente. Chamada automaticamente por `load_db()`.
2. **Receitas TOML**: campos novos são opcionais. Receitas antigas continuam
   funcionando. `load_recipe()` usa `data.get("homepage")` etc.
3. **CLI**: `cli.py` reduziu de 1412 para 348 linhas. Lógica movida para
   `commands/`. Funções públicas (`main()`, `build_config()`) mantêm
   assinatura para compatibilidade com `thorn = "thornspkg.cli:main"`.

---

## Arquivos modificados

### Novos (15)
- `thornspkg/version.py` — parser e comparador de versões
- `thornspkg/fileconflict.py` — detecção de conflito de arquivos
- `thornspkg/cache.py` — cache persistente de downloads
- `thornspkg/commands/__init__.py`
- `thornspkg/commands/common.py` — helpers compartilhados
- `thornspkg/commands/install.py`
- `thornspkg/commands/remove.py`
- `thornspkg/commands/search.py`
- `thornspkg/commands/repo.py`
- `thornspkg/commands/sync.py`
- `thornspkg/commands/upgrade.py`
- `thornspkg/commands/check.py`
- `thornspkg/commands/orphan.py`
- `thornspkg/commands/inspect.py`
- `thornspkg/commands/cache_cmd.py`
- `tests/__init__.py`
- `tests/test_version.py`
- `tests/test_fileconflict.py`
- `tests/test_db.py`
- `tests/test_depgraph.py`
- `tests/test_cache.py`
- `tests/test_commands_common.py`
- `scripts/demo_v04.py`

### Modificados (7)
- `thornspkg/__init__.py` — bump versão para 0.4.0, docstring atualizada
- `thornspkg/cli.py` — refatorado de 1412 para 348 linhas, adiciona sync/
  upgrade/list-upgrades/cache commands, --force-overwrite flag
- `thornspkg/db.py` — adiciona migrate_db(), get_metadata(), find_owner_of_file(),
  expande record_install() com extra_metadata, find_dependents/find_all_orphans
  suportam depends versionadas
- `thornspkg/depgraph.py` — resolve_order() aceita installed_versions e
  levanta VersionConflictError, reverse_deps()/dep_tree_lines() suportam
  constraints de versão
- `thornspkg/recipe.py` — Recipe ganha 10 novos campos de metadados,
  to_metadata_dict(), load_recipe() extrai novos campos
- `thornspkg/builder.py` — adiciona compute_install_size(), compute_download_size(),
  download_to_temp() helper
- `recipes/openssl.toml` — exemplo com metadados completos + depends versionada
- `recipes/curl.toml` — exemplo com metadados + depends versionadas
- `pyproject.toml` — bump versão para 0.4.0, adiciona [tool.pytest.ini_options]
- `README.md` — documentação completa dos novos recursos

### Não modificados (mantidos como estavam)
- `thornspkg/__main__.py`
- `thornspkg/colors.py`
- `thornspkg/config.py`
- `thornspkg/downloader.py`
- `thornspkg/hooks.py`
- `thornspkg/lock.py`
- `thornspkg/orphan.py`
- `thornspkg/repo.py`
- `thornspkg/signature.py`
- `thornspkg/suggest.py`
- `recipes/algo.txt`, `bash.toml`, `bzip2.toml`, `git.toml`, `libffi.toml`,
  `ncurses.toml`, `python.toml`, `readline.toml`, `sqlite.toml`, `xz.toml`,
  `zlib.toml`

---

## Exemplos de uso dos novos comandos

### Dependências com versão
```toml
# recipes/myapp.toml
depends = ["openssl>=3.0", "python<3.15", "glibc>=2.40", "curl=8.9.1"]
```

### Verificar ordem com constraints
```sh
$ thorn deps myapp
Ordem de build/instalação:
    1.  zlib                     1.3.1        [pendente]
    2.  openssl                  3.3.1        [pendente]
    3.  curl                     8.8.0        [pendente]
    4.  myapp                    1.0          [pendente]
```

### Árvore com constraints
```sh
$ thorn tree curl
curl 8.8.0
├── zlib 1.3.1  >=1.2
└── openssl 3.3.1  >=3.0
    └── zlib 1.3.1  >=1.2 [já listado]
```

### Conflito de arquivos
```sh
$ sudo thorn install my-vim
erro: conflito de arquivos ao instalar 'my-vim':
  /usr/bin/vim  →  já pertence a 'vim'
  Use --force-overwrite para sobrescrever (não recomendado).

$ sudo thorn install my-vim --force-overwrite  # força
```

### Consulta de ownership
```sh
$ thorn owns /usr/bin/vim
/usr/bin/vim  →  vim 9.1

$ thorn files vim
vim: 156 arquivo(s)
  /usr/bin/vim
  /usr/bin/ex
  ...
```

### Info com metadados completos
```sh
$ thorn info openssl
openssl  3.3.1
  Descrição:      Toolkit de criptografia e protocolo TLS/SSL
  Homepage:       https://www.openssl.org/
  Licença:        Apache-2.0
  Repositório:    core
  Arquitetura:    x86_64
  Receita:        recipes/openssl.toml
  Build system:   custom
  Source(s):
    https://www.openssl.org/source/openssl-3.3.1.tar.gz
  Depends:        zlib>=1.2
  Post-install:   ldconfig

  [não instalado]
```

### Sistema de atualização
```sh
$ sudo thorn sync                     # atualiza índices
$ thorn list-upgrades                 # mostra desatualizados
Pacotes desatualizados (1):
  Pacote          Instalado     → Disponível    Origem
  zlib            1.2.0         → 1.3.1         recipe

$ sudo thorn upgrade                  # atualiza tudo
$ sudo thorn upgrade curl             # apenas curl
$ sudo thorn upgrade --atomic         # com rollback
$ thorn upgrade --dry-run             # simula
```

### Cache
```sh
$ thorn cache stats
Cache do thornspkg:
  sources/       12 arquivo(s)   145.3 MB
  packages/       8 arquivo(s)   98.7 MB
  indexes/        3 arquivo(s)   12.4 KB
  total          23 arquivo(s)   244.0 MB

$ thorn cache list
$ thorn cache clean                   # limpa sources + packages
$ thorn cache clean --indexes         # limpa também indexes
```

---

## Verificação final

- **74 testes unitários passando** (`python -m unittest discover tests/`)
- **Demo script executado com sucesso** (`python scripts/demo_v04.py`)
- **Todos os comandos CLI testados manualmente** contra um banco de testes
- **Compatibilidade com Python 3.9+** mantida
- **Zero novas dependências externas**
