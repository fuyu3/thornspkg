# * Ponto de entrada para execução via `python -m thornspkg`.
# * Apenas delega para cli.main() e encerra com o código de retorno correto.
# * Arquivo: thornspkg/__main__.py

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
