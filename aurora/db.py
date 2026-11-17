"""Armazenamento do condomínio (SQLite).

Garantia 5: a exclusividade de uma reserva é decidida pelo próprio banco, por um
índice UNIQUE parcial em (area, data) para reservas ativas. A conferência prévia
de agenda é só cortesia; quem decide é o INSERT.
"""
import asyncio
import json
import sqlite3
import uuid
from contextlib import closing

from .config import DADOS, DB_CONDOMINIO

SCHEMA = """
CREATE TABLE IF NOT EXISTS apartamentos (numero TEXT PRIMARY KEY, morador TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS areas (id TEXT PRIMARY KEY, nome TEXT NOT NULL, taxa REAL NOT NULL);
CREATE TABLE IF NOT EXISTS reservas (
    codigo TEXT PRIMARY KEY,            -- nunca reaproveitado: cancelar não apaga a linha
    apartamento TEXT NOT NULL,
    area TEXT NOT NULL REFERENCES areas(id),
    data TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ativa' CHECK (status IN ('ativa', 'cancelada'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_reserva_ativa
    ON reservas (area, data) WHERE status = 'ativa';
CREATE TABLE IF NOT EXISTS visitantes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    apartamento TEXT NOT NULL,
    nome TEXT NOT NULL,
    data TEXT NOT NULL,
    UNIQUE (apartamento, nome, data)
);
"""


def _conectar() -> sqlite3.Connection:
    con = sqlite3.connect(DB_CONDOMINIO, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _ler(nome: str):
    return json.loads((DADOS / nome).read_text(encoding="utf-8"))


def inicializar(restaurar: bool = False) -> None:
    """Cria o schema e carrega dados/ na primeira execução (ou ao restaurar)."""
    with closing(_conectar()) as con:
        if restaurar:
            for t in ("visitantes", "reservas", "areas", "apartamentos"):
                con.execute(f"DROP TABLE IF EXISTS {t}")
        con.executescript(SCHEMA)
        if con.execute("SELECT COUNT(*) FROM areas").fetchone()[0] == 0:
            con.executemany("INSERT INTO apartamentos VALUES (:numero, :morador)", _ler("apartamentos.json"))
            con.executemany("INSERT INTO areas VALUES (:id, :nome, :taxa)", _ler("areas.json"))
            con.executemany(
                "INSERT INTO reservas (codigo, apartamento, area, data) VALUES (:codigo, :apartamento, :area, :data)",
                _ler("reservas.json"),
            )
            con.executemany(
                "INSERT INTO visitantes (apartamento, nome, data) VALUES (:apartamento, :nome, :data)",
                _ler("visitantes.json"),
            )
        con.commit()


# ---- operações (síncronas; chamadas via asyncio.to_thread) ----

def _area(area_id: str):
    with closing(_conectar()) as con:
        r = con.execute("SELECT id, nome, taxa FROM areas WHERE id = ?", (area_id,)).fetchone()
        return dict(r) if r else None


def _areas():
    with closing(_conectar()) as con:
        return [dict(r) for r in con.execute("SELECT id, nome, taxa FROM areas ORDER BY id")]


def _data_ocupada(area_id: str, data: str) -> bool:
    with closing(_conectar()) as con:
        return con.execute(
            "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND status = 'ativa'", (area_id, data)
        ).fetchone() is not None


def _reservas_do(apartamento: str):
    with closing(_conectar()) as con:
        rows = con.execute(
            "SELECT codigo, area, data FROM reservas WHERE apartamento = ? AND status = 'ativa' ORDER BY data, codigo",
            (apartamento,),
        )
        return [dict(r) for r in rows]


def _criar_reserva(apartamento: str, area_id: str, data: str):
    """Devolve o código, ou None se a data já estiver ocupada (decidido pelo índice UNIQUE)."""
    for _ in range(10):
        codigo = f"RSV-{uuid.uuid4().hex[:8].upper()}"
        with closing(_conectar()) as con:
            try:
                con.execute(
                    "INSERT INTO reservas (codigo, apartamento, area, data) VALUES (?, ?, ?, ?)",
                    (codigo, apartamento, area_id, data),
                )
                con.commit()
                return codigo
            except sqlite3.IntegrityError:
                con.rollback()
                if con.execute("SELECT 1 FROM reservas WHERE codigo = ?", (codigo,)).fetchone():
                    continue  # colisão de código: sorteia outro
                return None  # conflito de (area, data)
    raise RuntimeError("não foi possível gerar um código de reserva único")


def _cancelar_reserva(apartamento: str, area_id: str, data: str):
    """Cancela só reserva ativa DO apartamento informado. Devolve o código ou None."""
    with closing(_conectar()) as con:
        r = con.execute(
            "SELECT codigo FROM reservas WHERE apartamento = ? AND area = ? AND data = ? AND status = 'ativa'",
            (apartamento, area_id, data),
        ).fetchone()
        if not r:
            return None
        con.execute("UPDATE reservas SET status = 'cancelada' WHERE codigo = ?", (r["codigo"],))
        con.commit()
        return r["codigo"]


def _autorizar_visitante(apartamento: str, nome: str, data: str) -> bool:
    with closing(_conectar()) as con:
        try:
            con.execute("INSERT INTO visitantes (apartamento, nome, data) VALUES (?, ?, ?)", (apartamento, nome, data))
            con.commit()
            return True
        except sqlite3.IntegrityError:
            return False  # já autorizado (idempotente)


def _visitantes_do(apartamento: str):
    with closing(_conectar()) as con:
        rows = con.execute(
            "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY data, nome", (apartamento,)
        )
        return [dict(r) for r in rows]


def _wrap(fn):
    async def inner(*a):
        return await asyncio.to_thread(fn, *a)
    return inner


area = _wrap(_area)
areas = _wrap(_areas)
data_ocupada = _wrap(_data_ocupada)
reservas_do = _wrap(_reservas_do)
criar_reserva = _wrap(_criar_reserva)
cancelar_reserva = _wrap(_cancelar_reserva)
autorizar_visitante = _wrap(_autorizar_visitante)
visitantes_do = _wrap(_visitantes_do)
