# Como o thornspkg se auto-atualiza via GitHub

O thornspkg usa o **GitHub Releases API** como única fonte de atualização
(removemos o PyPI). Este documento explica exatamente:

1. Como o thornspkg descobre novas versões
2. Quais arquivos você precisa colocar no repositório GitHub
3. Como publicar um release correto
4. Como os clientes recebem a atualização

---

## Visão geral do fluxo

```
[mantenedor]                                  [clientes]

  1. Faz push do código + tag v0.4.3             |
  2. Cria release no GitHub (com tag v0.4.3)     |
  3. Faz upload do asset thornspkg-0.4.3.tar.gz  |
     (GitHub calcula SHA256 automaticamente)     |
                                                 |
                                             4. thorn version --check
                                                → consulta api.github.com
                                                → vê que 0.4.3 > 0.4.2
                                             5. sudo thorn self-update
                                                → baixa o asset
                                                → verifica SHA256
                                                → pip install
                                             6. thorn version → confirma 0.4.3
```

---

## O que o thornspkg consulta no GitHub

Quando você roda `thorn self-update` (ou `thorn version --check`), o thornspkg
faz uma requisição HTTP para:

```
GET https://api.github.com/repos/<OWNER>/<REPO>/releases/latest
```

Por padrão, `<OWNER>/<REPO>` é `fuyu3/thornspkg` (constante
`DEFAULT_GITHUB_REPO` em `selfupdate.py`). Você pode mudar isso via:

- **Variável de ambiente** `THORN_SELFUPDATE_REPO=seu-usuario/thornspkg`
- **Flag CLI** `thorn self-update --repo seu-usuario/thornspkg`

A resposta do GitHub é um JSON com esta estrutura (campos que o thornspkg
usa destacados com `←`):

```json
{
  "tag_name": "v0.4.3",                          ← versão (sem 'v')
  "name": "thornspkg 0.4.3",                     ← título do release
  "body": "Release notes em markdown...",        ← mostradas no dry-run
  "assets": [
    {
      "name": "thornspkg-0.4.3.tar.gz",          ← nome do arquivo
      "browser_download_url": "https://github.com/.../thornspkg-0.4.3.tar.gz",
      "size": 82432,                             ← tamanho em bytes
      "digest": "sha256:79be59bde885332d..."     ← SHA256 (GitHub calcula)
    }
  ],
  ...
}
```

### O que o thornspkg faz com cada campo

| Campo | Uso |
|-------|-----|
| `tag_name` | Versão (remove prefixo `v`). Compara com versão instalada. |
| `name` | Mostrado como "Título" no output |
| `body` | Mostrado como "Notas do release" (5 primeiras linhas) |
| `assets[].name` | Seleciona o asset `.tar.gz` (prefere `thornspkg-*.tar.gz`) |
| `assets[].browser_download_url` | URL de download do sdist |
| `assets[].size` | Mostrado como "Tamanho" |
| `assets[].digest` | SHA256 para verificação (formato `sha256:hex`) |

---

## O que você precisa colocar no repositório GitHub

### 1. O código fonte (repo normal)

Estrutura típica do repo (igual à que você já tem):

```
thornspkg/
├── README.md
├── pyproject.toml                    ← importante: define name + version
├── thornspkg/
│   ├── __init__.py                   ← __version__ = "0.4.3"
│   ├── cli.py, db.py, ...
│   └── commands/
├── tests/
└── recipes/
```

### 2. Tags no formato `v<X.Y.Z>`

Cada release precisa de uma tag git no formato `v0.4.3` (com prefixo `v`):

```sh
git tag -a v0.4.3 -m "Release 0.4.3"
git push origin v0.4.3
```

O thornspkg remove o prefixo `v` automaticamente para obter a versão
(`v0.4.3` → `0.4.3`).

### 3. GitHub Releases com asset `.tar.gz`

Para cada release, você precisa fazer upload de um **asset** chamado
`thornspkg-<versão>.tar.gz`. Esse é o sdist (source distribution) que
será baixado e instalado via `pip install`.

> **Importante**: o GitHub **calcula automaticamente** o SHA256 de cada
> asset quando você faz upload via API. É esse SHA256 que o thornspkg
> usa para verificação. Você **não precisa** criar um arquivo
> `checksums.txt` separado.

### 4. Release "latest" marcado

O thornspkg consulta o endpoint `/releases/latest`, que retorna o release
mais recente **não-pré-lançamento**. Ao criar o release no GitHub:

- **Não** marque "Set as a pre-release" (a não ser que seja beta/rc)
- **Sim** marque "Set as the latest release" (ou deixe o GitHub decidir
  automaticamente — por padrão, o último release published vira latest)

---

## Como gerar o sdist `.tar.gz`

### Opção A: Via `python -m build` (recomendado)

```sh
# Instala build (uma vez)
pip install build

# Gera sdist em dist/
python -m build --sdist

# Resultado: dist/thornspkg-0.4.3.tar.gz
```

### Opção B: Via `python setup.py sdist` (legacy)

```sh
python setup.py sdist
# Resultado: dist/thornspkg-0.4.3.tar.gz
```

### Opção C: Via `git archive` (sem build system)

```sh
git archive --format=tar.gz --prefix=thornspkg-0.4.3/ v0.4.3 \
    -o thornspkg-0.4.3.tar.gz
```

> **Atenção**: a Opção C não inclui `pyproject.toml` processado, então
> `pip install` pode falhar. Prefira A ou B.

### Verificar o sdist antes de publicar

```sh
# Testa instalar em ambiente limpo
python -m venv /tmp/test-venv
/tmp/test-venv/bin/pip install dist/thornspkg-0.4.3.tar.gz
/tmp/test-venv/bin/thorn version
# Deve mostrar: thornspkg 0.4.3
```

---

## Como publicar um release no GitHub

### Opção A: Via interface web (mais simples)

1. Vá para `https://github.com/seu-usuario/thornspkg/releases/new`
2. Em "Choose a tag", selecione ou crie `v0.4.3`
3. Em "Release title", digite `thornspkg 0.4.3`
4. Em "Describe this release", cole as release notes (markdown)
5. Em "Attach binaries by dropping them here", faça upload do arquivo
   `dist/thornspkg-0.4.3.tar.gz`
6. Clique em "Publish release"

> **Importante**: faça upload via interface web ou via API. **Não** use
> `git push` para o asset — ele precisa ser um asset de release, não um
> arquivo no repo.

### Opção B: Via `gh` CLI (automatizável)

```sh
# Cria release e faz upload do asset numa linha:
gh release create v0.4.3 \
    dist/thornspkg-0.4.3.tar.gz \
    --title "thornspkg 0.4.3" \
    --notes "Release notes aqui"
```

### Opção C: Via GitHub API (CI/CD)

```sh
# 1. Cria o release
RELEASE_RESPONSE=$(curl -s -X POST \
    -H "Authorization: token $GITHUB_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    https://api.github.com/repos/seu-usuario/thornspkg/releases \
    -d '{"tag_name":"v0.4.3","name":"thornspkg 0.4.3","body":"Release notes"}')

UPLOAD_URL=$(echo "$RELEASE_RESPONSE" | python -c "import json,sys; print(json.load(sys.stdin)['upload_url'].split('{')[0])")

# 2. Faz upload do asset (GitHub calcula SHA256 automaticamente)
curl -s -X POST \
    -H "Authorization: token $GITHUB_TOKEN" \
    -H "Content-Type: application/gzip" \
    --data-binary @dist/thornspkg-0.4.3.tar.gz \
    "${UPLOAD_URL}?name=thornspkg-0.4.3.tar.gz"
```

---

## Workflow completo de release (exemplo)

```sh
# 1. Atualizar versão no código
sed -i 's/__version__ = "0.4.2"/__version__ = "0.4.3"/' thornspkg/__init__.py
sed -i 's/version = "0.4.2"/version = "0.4.3"/' pyproject.toml

# 2. Commit + tag
git add thornspkg/__init__.py pyproject.toml
git commit -m "bump version to 0.4.3"
git tag -a v0.4.3 -m "Release 0.4.3"
git push origin main v0.4.3

# 3. Gerar sdist
python -m build --sdist
# Resultado: dist/thornspkg-0.4.3.tar.gz

# 4. Testar instalação local antes de publicar
python -m venv /tmp/test-venv
/tmp/test-venv/bin/pip install dist/thornspkg-0.4.3.tar.gz
/tmp/test-venv/bin/thorn version    # deve mostrar 0.4.3

# 5. Criar release no GitHub + fazer upload do asset
gh release create v0.4.3 \
    dist/thornspkg-0.4.3.tar.gz \
    --title "thornspkg 0.4.3" \
    --notes "$(cat CHANGELOG.md | head -50)"

# 6. Testar self-update a partir de um cliente
sudo thorn version --check
# Deve mostrar: versão remota 0.4.3
sudo thorn self-update --yes
thorn version
# Deve mostrar: thornspkg 0.4.3
```

---

## Autenticação e rate limits

### Rate limits do GitHub API

- **Sem autenticação**: 60 requisições/hora por IP (compartilhado entre
  todos os usuários atrás do mesmo NAT)
- **Com `GITHUB_TOKEN`**: 5000 requisições/hora

Para a maioria dos casos de uso (1-2 checks por dia), 60 req/hora é
suficiente. Mas se você tem muitos servidores, defina `GITHUB_TOKEN`:

```sh
# Crie um Personal Access Token em:
# https://github.com/settings/tokens
# Scope: público (não precisa de nenhuma permissão para repos públicos)

export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
sudo -E thorn self-update   # -E preserva o env
```

### Repositórios privados

Se o thornspkg está em um repo privado, **obrigatoriamente** precisa
definir `GITHUB_TOKEN` com um token que tenha acesso ao repo:

```sh
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
export THORN_SELFUPDATE_REPO=empresa/thornspkg-privado
sudo -E thorn self-update
```

---

## Configuração do cliente

### Variáveis de ambiente

| Variável | Default | Descrição |
|----------|---------|-----------|
| `THORN_SELFUPDATE_REPO` | `thornspkg/thornspkg` | Repo GitHub no formato `owner/repo` |
| `GITHUB_TOKEN` | (vazio) | Token de autenticação (opcional, aumenta rate limit) |

### Flags CLI

```sh
thorn version                           # mostra versão instalada
thorn version --check                   # verifica se há versão mais recente
thorn version --check --repo user/repo  # usa repo diferente

sudo thorn self-update                  # atualiza para latest
sudo thorn self-update --tag v0.4.3     # instala versão específica
sudo thorn self-update --repo user/repo # usa repo diferente
sudo thorn self-update --dry-run        # só simula
sudo thorn self-update --force          # reinstala mesma versão
sudo thorn self-update --yes            # sem confirmação
sudo thorn self-update --url URL        # URL customizada (sobrescreve --repo/--tag)
```

---

## Troubleshooting

### "release não encontrado no repositório 'X/Y'"

1. Verifique que o repositório existe: `https://github.com/X/Y`
2. Verifique que há pelo menos um release published
3. Verifique que o release NÃO está marcado como "pre-release"
4. Verifique que o release tem a tag no formato `v<X.Y.Z>`

### "release 'v0.4.3' não tem assets"

Você criou o release mas não fez upload do sdist. Use:

```sh
gh release upload v0.4.3 dist/thornspkg-0.4.3.tar.gz
```

### "GitHub API rate limit excedido"

Você fez mais de 60 requisições em 1 hora. Solução:

```sh
export GITHUB_TOKEN=ghp_xxx    # aumenta para 5000/hora
sudo -E thorn self-update
```

### "SHA256 diverge"

O asset no GitHub foi alterado após o upload (improvável, mas possível
se você reescreveu o arquivo). Re-faça o upload do asset:

```sh
gh release upload v0.4.3 dist/thornspkg-0.4.3.tar.gz --clobber
```

### "sem SHA256 para verificar"

Isso acontece se o GitHub não calculou o digest do asset. Provavelmente
o upload foi feito de forma não-padrão. Re-faça o upload via `gh release
upload` ou via interface web.

### "não foi possível consultar GitHub API"

- Verifique conexão com a internet
- Verifique que `api.github.com` está acessível: `curl -I https://api.github.com`
- Se está atrás de proxy, defina `HTTPS_PROXY`

---

## Checklist de release

Antes de publicar uma nova versão:

- [ ] Atualizei `__version__` em `thornspkg/__init__.py`
- [ ] Atualizei `version` em `pyproject.toml`
- [ ] Atualizei `CHANGELOG.md` (se existir)
- [ ] Commitei as mudanças
- [ ] Criei a tag `v<X.Y.Z>` com `git tag -a`
- [ ] Fiz push da tag: `git push origin v<X.Y.Z>`
- [ ] Gerei o sdist: `python -m build --sdist`
- [ ] Testei instalação em venv limpo
- [ ] Criei o release no GitHub (via web, `gh`, ou API)
- [ ] Fiz upload do asset `thornspkg-<versão>.tar.gz`
- [ ] Testei `thorn version --check` de um cliente
- [ ] Testei `sudo thorn self-update --dry-run` de um cliente
- [ ] Testei `sudo thorn self-update --yes` em um servidor de teste
