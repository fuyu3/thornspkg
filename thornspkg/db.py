# * Banco de dados de pacotes instalados — JSON em disco com operações atômicas.
# * Estrutura: <db_dir>/installed.json (índice) + checksums/<nome>.json (sha256 por arquivo).
# * Suporta transações atômicas com journal (transaction.json): em caso de falha,
# *   o banco não é modificado e `thorn recover-tx` pode desfazer arquivos instalados.
# * Razões de instalação: "explicit" (usuário pediu) vs "dependency" (automático).
# * Detecção de órfãos: find_all_orphans_transitively() calcula o fecho transitivo.
# * Metadados expandidos (v0.4+): build_date, install_date, install_size, download_size,
# *   repository, architecture, license, description, homepage, maintainer.
# * Migração automática: load_db() preenche campos ausentes com defaults seguros.
# * Funções principais: load_db(), save_db(), record_install(), remove_record(),
# *   find_dependents(), find_all_orphans_transitively(), TransactionJournal,
# *   migrate_db() — adiciona campos novos mantendo compatibilidade.
# * Arquivo: thornspkg/db.py

"""Banco de dados de pacotes instalados.

Estrutura em disco:
  <db_dir>/
    installed.json           — índice principal (leve)
    checksums/<nome>.json    — sha256 por arquivo, por pacote
    transaction.json         — journal de transação atômica em progresso
                               (apagado no commit ou rollback)

Razões de instalação
--------------------
  "explicit"    — o usuário pediu diretamente
  "dependency"  — instalado automaticamente como dep de outro pacote

Entradas sem o campo "reason" (banco antigo) são tratadas como "explicit"
para nunca remover algo sem consentimento.

Metadados (v0.4+)
-----------------
Cada entrada de pacote pode conter os seguintes campos adicionais,
todos opcionais (ausentes em bancos antigos):
  build_date      — data de build do pacote (ISO 8601)
  install_date    — data de instalação no sistema (alias de installed_at)
  install_size    — tamanho instalado em bytes
  download_size   — tamanho do download em bytes
  repository      — nome do repositório de origem
  architecture    — arquitetura (x86_64, aarch64, any, …)
  license         — licença do software
  description     — descrição curta
  homepage        — URL do projeto
  maintainer      — mantenedor da receita/pacote

A função migrate_db() preenche campos ausentes com defaults seguros,
preservando todos os dados existentes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .recipe import Recipe

_INDEX        = "installed.json"
_CHECKSUMS    = "checksums"
_TX_FILE      = "transaction.json"

REASON_EXPLICIT    = "explicit"
REASON_DEPENDENCY  = "dependency"


# ---------------------------------------------------------------------------
# helpers internos
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _chk_path(db_dir: Path, name: str) -> Path:
    return db_dir / _CHECKSUMS / f"{name}.json"


# ---------------------------------------------------------------------------
# índice principal
# ---------------------------------------------------------------------------

def load_db(db_dir: Path) -> dict:
    db_dir.mkdir(parents=True, exist_ok=True)
    path = db_dir / _INDEX
    if not path.exists():
        return {"packages": {}}
    with open(path) as f:
        db = json.load(f)
    # Migração automática in-place: preenche campos novos ausentes com defaults.
    migrate_db(db)
    return db


# ---------------------------------------------------------------------------
# migração
# ---------------------------------------------------------------------------

# Lista de campos de metadados (v0.4+) com seus defaults.
# Campos obrigatórios (version, depends, etc.) não estão aqui porque já
# são tratados pelas funções de acesso com .get(chave, default).
_METADATA_FIELDS: dict[str, object] = {
    "build_date":     None,
    "install_size":   None,
    "download_size":  None,
    "repository":     None,
    "architecture":   None,
    "license":        None,
    "description":    "",
    "homepage":       None,
    "maintainer":     None,
    # Alias: install_date espelha installed_at quando ausente
    "install_date":   None,
}


def migrate_db(db: dict) -> bool:
    """Adiciona campos novos a entradas antigas, preservando dados existentes.

    Esta função é idempotente: chamar várias vezes produz o mesmo resultado.
    Retorna True se algum campo foi adicionado (DB precisa ser salvo).
    """
    changed = False
    for name, info in db.get("packages", {}).items():
        if not isinstance(info, dict):
            continue
        for field, default in _METADATA_FIELDS.items():
            if field not in info:
                # install_date: alias para installed_at quando possível
                if field == "install_date":
                    info[field] = info.get("installed_at")
                else:
                    info[field] = default
                changed = True
        # Garante que os campos estruturais básicos também existem
        info.setdefault("optional_deps", [])
        info.setdefault("provides", [])
        info.setdefault("files", [])
        info.setdefault("reason", REASON_EXPLICIT)
        info.setdefault("depends", [])
    return changed


def save_db_migrated(db_dir: Path, db: dict) -> None:
    """Equivalente a save_db(), mas força migração antes de gravar."""
    migrate_db(db)
    save_db(db_dir, db)


def save_db(db_dir: Path, db: dict) -> None:
    db_dir.mkdir(parents=True, exist_ok=True)
    path = db_dir / _INDEX
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(db, f, indent=2, sort_keys=True)
    tmp.rename(path)   # rename() é atômico — sem DB corrompido em caso de crash


def is_installed(db: dict, name: str) -> bool:
    return name in db["packages"]


def reason_of(db: dict, name: str) -> str:
    """Retorna 'explicit' ou 'dependency'. Entradas antigas → 'explicit'."""
    return db["packages"].get(name, {}).get("reason", REASON_EXPLICIT)


def record_install(
    db: dict,
    recipe: Recipe,
    manifest: list[str],
    *,
    reason: str = REASON_EXPLICIT,
    update_check_ts: bool = False,
    extra_metadata: dict | None = None,
) -> None:
    """Registra a instalação de um pacote no banco.

    Args:
        db:               banco de dados (modificado in-place)
        recipe:           receita do pacote
        manifest:         lista de caminhos relativos instalados
        reason:           REASON_EXPLICIT ou REASON_DEPENDENCY
        update_check_ts:  se True, atualiza o timestamp de verificação
        extra_metadata:   metadados adicionais a mesclar (sobrescreve recipe)
    """
    entry = db["packages"].get(recipe.name, {})

    # Se o pacote JÁ estava instalado explicitamente, mantém explicit
    # mesmo que desta vez esteja sendo instalado como dep (ex: reinstall).
    # Para pacotes novos (entry vazio), usa o reason passado sem alteração.
    if entry and entry.get("reason", REASON_EXPLICIT) == REASON_EXPLICIT \
            and reason == REASON_DEPENDENCY:
        reason = REASON_EXPLICIT

    now = _now()
    new_entry = {
        "version":       recipe.version,
        "depends":       list(recipe.depends),
        "optional_deps": list(recipe.optional_deps),
        "provides":      list(recipe.provides),
        "reason":        reason,
        "files":         manifest,
        "installed_at":  entry.get("installed_at") or now,
        "install_date":  entry.get("install_date") or entry.get("installed_at") or now,
        "updated_at":    now,
        "checked_at":    entry.get("checked_at"),
        # Metadados expandidos (v0.4+)
        "description":   recipe.description or entry.get("description", ""),
        "homepage":      recipe.homepage or entry.get("homepage"),
        "license":       recipe.license or entry.get("license"),
        "maintainer":    recipe.maintainer or entry.get("maintainer"),
        "repository":    recipe.repository or entry.get("repository"),
        "architecture":  recipe.architecture or entry.get("architecture"),
        "build_date":    recipe.build_date or entry.get("build_date"),
        "install_size":  recipe.install_size if recipe.install_size is not None
                         else entry.get("install_size"),
        "download_size": recipe.download_size if recipe.download_size is not None
                         else entry.get("download_size"),
    }

    # Mescla metadados extras (de pacotes binários via repositório, etc.)
    if extra_metadata:
        for k, v in extra_metadata.items():
            if v is not None:
                new_entry[k] = v

    if update_check_ts:
        new_entry["checked_at"] = now

    db["packages"][recipe.name] = new_entry


def update_check_ts(db: dict, name: str) -> None:
    if name in db["packages"]:
        db["packages"][name]["checked_at"] = _now()


def remove_record(db: dict, name: str) -> None:
    del db["packages"][name]


def find_dependents(db: dict, name: str) -> list[str]:
    """Pacotes instalados cujo depends ou optional_deps inclui `name`.

    Suporta depends com operadores de versão: "openssl>=3.0" ainda conta
    como dependente de "openssl".
    """
    from .version import dep_name
    return [
        pkg for pkg, info in db["packages"].items()
        if pkg != name and (
            name in [dep_name(d) for d in info.get("depends", []) + info.get("optional_deps", [])]
        )
    ]


def get_metadata(db: dict, name: str) -> dict:
    """Retorna todos os metadados conhecidos de um pacote instalado.

    Garante que todos os campos (v0.4+) estejam presentes, mesmo que
    seja com valor None. Útil para exibição no comando `thorn info`.
    """
    info = db["packages"].get(name, {})
    result = dict(_METADATA_FIELDS)  # defaults
    result.update(info)
    return result


def find_owner_of_file(db: dict, rel_path: str) -> str | None:
    """Retorna o nome do pacote que possui o arquivo `rel_path`.

    `rel_path` é relativo ao root, sem / inicial (ex: "usr/bin/vim").
    """
    for pkg_name, info in db["packages"].items():
        if rel_path in info.get("files", []):
            return pkg_name
    return None


# ---------------------------------------------------------------------------
# detecção de órfãos
# ---------------------------------------------------------------------------

def find_all_orphans(packages: dict) -> list[str]:
    """Pacotes com reason='dependency' não requeridos por mais ninguém.

    `packages` é db["packages"] ou uma cópia de trabalho.

    Suporta depends com operadores de versão: "openssl>=3.0" ainda conta
    como dependente de "openssl".
    """
    from .version import dep_name

    # Pré-computa os nomes canônicos de dependências de cada pacote
    dep_name_sets: dict[str, set[str]] = {}
    for pkg, other in packages.items():
        names = set()
        for d in other.get("depends", []) + other.get("optional_deps", []):
            try:
                names.add(dep_name(d))
            except Exception:
                # dependência malformada — ignora silenciosamente
                names.add(d)
        dep_name_sets[pkg] = names

    orphans = []
    for name, info in packages.items():
        if info.get("reason", REASON_EXPLICIT) != REASON_DEPENDENCY:
            continue
        needed = any(
            name in dep_name_sets.get(pkg, set())
            for pkg in packages
            if pkg != name
        )
        if not needed:
            orphans.append(name)
    return sorted(orphans)


def find_all_orphans_transitively(packages: dict) -> list[str]:
    """Fecho transitivo de órfãos: inclui pacotes que ficariam órfãos após
    a remoção de outros órfãos (ex: A dep B dep C, todos deps, todos órfãos).

    Retorna a lista na ordem em que se tornam órfãos — os mais altos na
    hierarquia aparecem primeiro (ordem correta de remoção).
    """
    working = dict(packages)
    all_orphans: list[str] = []

    while True:
        batch = find_all_orphans(working)
        if not batch:
            break
        for name in batch:
            del working[name]
        all_orphans.extend(batch)

    return all_orphans   # já em ordem de remoção segura


# ---------------------------------------------------------------------------
# checksums
# ---------------------------------------------------------------------------

def save_checksums(db_dir: Path, name: str, checksums: dict[str, str]) -> None:
    cdir = db_dir / _CHECKSUMS
    cdir.mkdir(parents=True, exist_ok=True)
    path = _chk_path(db_dir, name)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(checksums, f, indent=2, sort_keys=True)
    tmp.rename(path)


def load_checksums(db_dir: Path, name: str) -> dict[str, str]:
    path = _chk_path(db_dir, name)
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def delete_checksums(db_dir: Path, name: str) -> None:
    path = _chk_path(db_dir, name)
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------------------
# journal de transação
# ---------------------------------------------------------------------------

class TransactionJournal:
    """Journal em disco para transações atômicas.

    Enquanto --atomic está em progresso, `transaction.json` existe no
    db_dir listando tudo que foi instalado até aqui. Se o processo cair,
    o arquivo permanece e pode ser usado para rollback manual.

    Uso normal:
        journal = TransactionJournal(cfg.db_dir)
        journal.record(name, version, reason, files)  # após cada pacote
        ...
        journal.commit(db, checksums_map)   # grava DB e apaga journal
        # ou
        journal.rollback(root_dir)          # remove arquivos e apaga journal
    """

    def __init__(self, db_dir: Path) -> None:
        self._path = db_dir / _TX_FILE
        self._db_dir = db_dir
        self._entries: list[dict] = []
        self._started_at = _now()

    # --- escrita incremental ---

    def record(
        self,
        name: str,
        version: str,
        reason: str,
        files: list[str],
        checksums: dict[str, str],
    ) -> None:
        self._entries.append({
            "name":        name,
            "version":     version,
            "reason":      reason,
            "files":       files,
            "checksums":   checksums,
            "recorded_at": _now(),
        })
        self._flush()

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(
                {"started_at": self._started_at, "packages": self._entries},
                f, indent=2,
            )
        tmp.rename(self._path)

    # --- commit: grava DB e apaga journal ---

    def commit(self, db: dict, recipes_map: dict) -> None:
        """Grava todos os registros no DB e apaga o journal."""
        from .recipe import Recipe  # evitar import circular no topo

        for e in self._entries:
            # O recipe pode não estar disponível (ex: reinstall de pacote removido)
            # Usamos os dados salvos no journal
            files     = e["files"]
            checksums = e["checksums"]

            # Atualiza DB manualmente (sem Recipe completo)
            existing = db["packages"].get(e["name"], {})
            old_reason = existing.get("reason", REASON_EXPLICIT)
            reason = e["reason"]
            if old_reason == REASON_EXPLICIT and reason == REASON_DEPENDENCY:
                reason = REASON_EXPLICIT

            db["packages"][e["name"]] = {
                "version":       e["version"],
                "depends":       existing.get("depends", []),
                "optional_deps": existing.get("optional_deps", []),
                "provides":      existing.get("provides", []),
                "reason":        reason,
                "files":         files,
                "installed_at":  existing.get("installed_at") or _now(),
                "updated_at":    _now(),
                "checked_at":    existing.get("checked_at"),
            }
            save_checksums(self._db_dir, e["name"], checksums)

        save_db(self._db_dir, db)
        self.clear()

    # --- rollback: remove arquivos do root e apaga journal ---

    def rollback(self, root_dir: Path) -> list[str]:
        """Remove os arquivos instalados nesta transação. Retorna nomes removidos."""
        from .builder import remove_installed_files

        removed_names = []
        # Rollback em ordem inversa (último instalado primeiro)
        for e in reversed(self._entries):
            remove_installed_files(root_dir, e["files"])
            removed_names.append(e["name"])

        self.clear()
        return removed_names

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def package_names(self) -> list[str]:
        return [e["name"] for e in self._entries]

    # --- recuperação de crash ---

    @classmethod
    def is_pending(cls, db_dir: Path) -> bool:
        return (db_dir / _TX_FILE).exists()

    @classmethod
    def load_from_disk(cls, db_dir: Path) -> "TransactionJournal":
        """Carrega um journal existente (ex: após crash) para rollback."""
        obj = cls(db_dir)
        path = db_dir / _TX_FILE
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            obj._entries = data.get("packages", [])
            obj._started_at = data.get("started_at", _now())
        return obj
