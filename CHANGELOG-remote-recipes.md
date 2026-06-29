# thornspkg 0.4.6-remote-recipes

Patch fork de thornspkg 0.4.5 com suporte a **receitas remotas**.

## O que mudou

O thornspkg 0.4.5 original suportava apenas **binários** remotos (pacotes
pré-compilados `.tar.zst`). Receitas remotas (`type: "recipe"` no `index.json`)
eram declaradas mas **não implementadas** — o código apenas exibia um warning
e falhava:

```
aviso: 'libndp': receita remota não implementada, tentando receita local
✗ falha em 'libndp': não foi possível determinar como instalar 'libndp'
```

Esta versão implementa o download e build a partir de receitas remotas.

## Funcionalidades adicionadas

### `repo.download_remote_recipe()`
- Baixa o arquivo `.toml` referenciado em `index.json` (campo `recipe`)
- Verifica SHA256 se declarado no índice (campo `sha256`)
- Cacheia em `<dest_dir>/<arquivo>` para reuso
- Reutiliza cache se SHA256 bater (não re-baixa)

### `commands/common._load_remote_recipe()`
- Chama `download_remote_recipe()` para obter o `.toml`
- Carrega como `Recipe` completa (com `sources`, `build_system`, `steps`, etc.)
- Trata erros graciosamente (warnings em vez de crashes)

### `commands/common.install_one_package()`
- Detecta receitas virtuais (criadas pelo resolver para dep resolution)
- Quando detecta, baixa a receita real via `_load_remote_recipe()`
- Substitui no dict `recipes` para reuso
- Chama `build_one()` com a receita real, que compila normalmente

### `commands/common._fetch_all_repo_recipes()`
- Para pacotes `type="recipe"`, baixa o `.toml` real (não só name+version)
- Usa `depends` + `optional_deps` da receita real para resolver a árvore
- Fallback para `depends` do `index.json` se o download falhar

## Como usar

### 1. Servir um repositório de receitas

Estrutura do diretório no servidor HTTP:

```
/var/www/thorn-repo/
├── index.json
└── recipes/
    ├── libndp-1.9.toml
    ├── libunistring-1.4.2.toml
    ├── libidn2-2.3.8.toml
    └── ... (mais 60 receitas)
```

`index.json` (cada entrada `type: "recipe"` aponta para o `.toml` relativo):

```json
{
  "packages": {
    "libndp": {
      "version": "1.9",
      "type": "recipe",
      "recipe": "recipes/libndp-1.9.toml",
      "sha256": "<sha256 do arquivo .toml>",
      "depends": []
    },
    "libidn2": {
      "version": "2.3.8",
      "type": "recipe",
      "recipe": "recipes/libidn2-2.3.8.toml",
      "sha256": "<sha256 do arquivo .toml>",
      "depends": ["libunistring"]
    }
  }
}
```

### 2. No cliente

```sh
# Adiciona o repositório
sudo thorn repo add meu-repo https://meu-servidor.org/thorn-repo/

# Atualiza o índice (baixa index.json)
sudo thorn repo refresh

# Instala — thorn baixa cada .toml, verifica SHA256, e compila localmente
sudo thorn install networkmanager --atomic
```

As receitas baixadas são cacheadas em `/var/lib/thornspkg/remote-recipes/`.
Para limpar o cache: `sudo rm -rf /var/lib/thornspkg/remote-recipes/`.

## Detalhes técnicos

### Detecção de receita virtual

O resolver `_fetch_all_repo_recipes()` cria "receitas virtuais" com apenas
`name`/`version`/`depends` (sem `sources`/`build_system`) para resolver a
árvore de dependências sem precisar baixar todos os `.toml` imediatamente.

Em `install_one_package()`, detectamos receitas virtuais pela heurística:
```python
is_virtual = (
    recipe is not None
    and not recipe.sources
    and str(recipe.path).startswith("repo:")
)
```

Quando detectada e o pacote está no repo como `type="recipe"`, baixamos a
receita real e substituímos no dict `recipes` para que chamadas subsequentes
do mesmo pacote (em installs não-atômicos) reutilizem.

### Resolução completa de deps

Na versão original, `_fetch_all_repo_recipes()` só via as `depends` declaradas
no `index.json`. Isso significa que `optional_deps` (que o thornspkg usa para
instalar deps recomendadas que já estão no sistema) eram ignoradas.

Agora, para pacotes `type="recipe"`, baixamos o `.toml` real durante a
resolução e usamos `depends + optional_deps` da receita — garantindo que a
árvore esteja completa.

### Comportamento de cache

- **Cache de índice**: `repo refresh` baixa `index.json` para
  `/var/lib/thornspkg/sync/<repo>.json` — inalterado do 0.4.5.
- **Cache de receitas**: receitas `.toml` baixadas ficam em
  `/var/lib/thornspkg/remote-recipes/`. Se o SHA256 bater com o do índice,
  não re-baixa.
- **Cache de sources**: tarballs de source continuam em
  `/var/cache/thornspkg/sources/` — inalterado.

## Compatibilidade

- 100% compatível com repositórios de binários existentes (`type: "binary"`)
- Receitas locais em `/etc/thornspkg/recipes/` continuam funcionando
- `--prefer-source` e `--prefer-binary` continuam funcionando
- Builds com `--atomic` continuam funcionando

## Testes

Inclusos em `/home/z/my-project/scripts/test_remote_recipes.py` — 6 testes
que cobrem:
1. Download básico de receita
2. Carregamento como Recipe completa
3. Resolução de árvore com deps transitivas
4. Rejeição de SHA256 divergente
5. Aceitação de SHA256 correto
6. Funcionamento do cache (não re-baixa)

Todos passam.
