"""Caminhos e configuração do projeto."""
import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

DADOS = RAIZ / "dados"
DB_CONDOMINIO = Path(os.getenv("AURORA_DB_CONDOMINIO", RAIZ / "condominio.db"))
DB_SESSOES = Path(os.getenv("AURORA_DB_SESSOES", RAIZ / "sessoes.db"))

APP_NAME = "residencial_aurora"
MODELO = os.getenv("AURORA_MODELO") or "gemini-3.5-flash"
