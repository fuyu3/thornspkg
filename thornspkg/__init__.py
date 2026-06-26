# * Módulo principal do thornspkg — define a versão e a docstring do pacote.
# * Este é o ponto de entrada quando alguém faz `import thornspkg`.
# * A única informação útil aqui é __version__, usada para identificação.
# * Arquivo: thornspkg/__init__.py

"""thornspkg — source-based package manager for LFS/BLFS systems.

Suporta:
  - Pacotes source (compilação local)
  - Pacotes binários (download direto)
  - Pacotes baseados em receita de repositórios remotos
  - Repositórios remotos com cache de índices
  - Extração segura contra path traversal
  - Lock contra instâncias simultâneas
  - Download centralizado (curl/urllib)
  - Interface preparada para assinaturas GPG futuras
  - Dependências com operadores de versão (>=, <, =, !=, …) — v0.4+
  - Conflito de arquivos com FileConflictError — v0.4+
  - Metadados expandidos (build_date, install_size, license, …) — v0.4+
  - Cache persistente de sources, packages e indexes — v0.4+
  - Comandos sync, upgrade, list-upgrades, cache — v0.4+
"""

__version__ = "0.4.4"