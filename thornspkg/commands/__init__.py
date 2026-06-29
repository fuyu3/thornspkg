# * Pacote de subcomandos da CLI — cada arquivo exporta funções cmd_*(args, recipes, pmap, cfg).
# * Esta camada separa a lógica de cada comando da definição do parser (cli.py),
# *   facilitando manutenção e testes.
# * Arquivo: thornspkg/commands/__init__.py

"""Subcomandos da CLI do thornspkg.

Cada módulo aqui contém funções no formato:

    def cmd_xxx(args, recipes, pmap, cfg) -> int:
        ...

que são registradas no parser pelo cli.py. Esta separação mantém o
cli.py enxuto (apenas definição de argparse) e a lógica de cada
comando isolada em seu próprio arquivo.
"""
