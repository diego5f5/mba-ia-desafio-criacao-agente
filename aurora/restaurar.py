"""Restaura reservas e visitantes ao estado de dados/*.json e apaga as sessões.

Uso (com a API parada):  uv run python -m aurora.restaurar
"""
from . import db
from .config import DB_CONDOMINIO, DB_SESSOES


def main() -> None:
    for base in (DB_CONDOMINIO, DB_SESSOES):
        for sufixo in ("", "-wal", "-shm"):
            p = base.with_name(base.name + sufixo)
            if p.exists():
                p.unlink()
    db.inicializar(restaurar=True)
    print("Dados restaurados (reservas, visitantes e sessões).")


if __name__ == "__main__":
    main()
