"""Tools do assistente: única porta de acesso às reservas e aos visitantes.

Garantia 2: nenhuma tool recebe um apartamento como parâmetro. O apartamento vem de
`tool_context.state["apartamento"]`, gravado uma única vez na criação da sessão pela API.
Garantia 1: `reservar_area` (quando há taxa) e `autorizar_visitante` usam
`require_confirmation`; a pendência só é resolvida pela rota de confirmações.
"""
import re
import unicodedata
from datetime import date

from google.adk.tools import ToolContext
from google.adk.tools.function_tool import FunctionTool

from . import db, regulamento

_DATA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _apartamento(tool_context: ToolContext) -> str:
    apt = tool_context.state.get("apartamento")
    if not apt:
        raise PermissionError("sessão sem apartamento")
    return str(apt)


def _data_valida(data: str) -> str | None:
    data = (data or "").strip()
    if not _DATA_RE.match(data):
        return None
    try:
        date.fromisoformat(data)
    except ValueError:
        return None
    return data


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


async def _resolver_area(area: str) -> dict | None:
    """Aceita o id ('salao-de-festas') ou o nome ('Salão de festas')."""
    alvo = _fold(area or "").strip().replace(" ", "-")
    for a in await db.areas():
        if alvo in (a["id"], _fold(a["nome"]).replace(" ", "-")) or alvo.replace("-", "") == a["id"].replace("-", ""):
            return a
    return None


async def _erro_area() -> dict:
    return {"status": "erro", "motivo": "área desconhecida",
            "areas_validas": [{"id": a["id"], "nome": a["nome"]} for a in await db.areas()]}


# ---------------- reservas ----------------

async def listar_minhas_reservas(tool_context: ToolContext) -> dict:
    """Lista as reservas ativas do apartamento do morador desta conversa."""
    return {"status": "ok", "reservas": await db.reservas_do(_apartamento(tool_context))}


async def verificar_disponibilidade(area: str, data: str) -> dict:
    """Informa se uma área comum está livre ou ocupada em uma data (AAAA-MM-DD).

    Só informa livre/ocupada; nunca diz quem reservou.

    Args:
        area: id ou nome da área (salao-de-festas, churrasqueira, quadra).
        data: data no formato AAAA-MM-DD.
    """
    a = await _resolver_area(area)
    if not a:
        return await _erro_area()
    d = _data_valida(data)
    if not d:
        return {"status": "erro", "motivo": "data inválida; use AAAA-MM-DD"}
    return {"status": "ok", "area": a["id"], "data": d,
            "situacao": "ocupada" if await db.data_ocupada(a["id"], d) else "livre"}


async def _exige_confirmacao_reserva(area: str, data: str) -> bool:
    """Pede confirmação quando a reserva gera cobrança (taxa > 0).

    Precisa ser estável: o ADK reavalia esta função ao retomar a execução, então ela
    depende só de fatos imutáveis (taxa da área), nunca da agenda.
    """
    a = await _resolver_area(area)
    return bool(a) and _data_valida(data) is not None and a["taxa"] > 0


async def recusar_se_ocupada(tool, args, tool_context):
    """before_tool_callback: data ocupada é recusada antes de pedir confirmação de cobrança.

    Devolve só 'indisponível', nunca de quem é a reserva. A decisão definitiva é do índice UNIQUE.
    """
    if tool.name != "reservar_area":
        return None
    a = await _resolver_area(args.get("area", ""))
    d = _data_valida(args.get("data", ""))
    if a and d and await db.data_ocupada(a["id"], d):
        return {"status": "recusada", "motivo": "data indisponível para essa área"}
    return None


async def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva uma área comum para o apartamento do morador em uma data.

    Áreas com taxa só são reservadas depois que o morador confirma pelo sistema.

    Args:
        area: id ou nome da área (salao-de-festas, churrasqueira, quadra).
        data: data no formato AAAA-MM-DD.
    """
    apt = _apartamento(tool_context)
    a = await _resolver_area(area)
    if not a:
        return await _erro_area()
    d = _data_valida(data)
    if not d:
        return {"status": "erro", "motivo": "data inválida; use AAAA-MM-DD"}
    codigo = await db.criar_reserva(apt, a["id"], d)
    if codigo is None:
        return {"status": "recusada", "motivo": "data indisponível para essa área"}
    return {"status": "ok", "codigo": codigo, "area": a["id"], "data": d,
            "cobranca": a["taxa"] if a["taxa"] > 0 else 0}


async def cancelar_reserva(area: str, data: str, tool_context: ToolContext) -> dict:
    """Cancela a reserva do próprio apartamento do morador, sem confirmação.

    Args:
        area: id ou nome da área.
        data: data da reserva, AAAA-MM-DD.
    """
    apt = _apartamento(tool_context)
    a = await _resolver_area(area)
    if not a:
        return await _erro_area()
    d = _data_valida(data)
    if not d:
        return {"status": "erro", "motivo": "data inválida; use AAAA-MM-DD"}
    codigo = await db.cancelar_reserva(apt, a["id"], d)
    if codigo is None:
        return {"status": "nao_encontrada", "motivo": "você não tem reserva ativa nessa área e data"}
    return {"status": "ok", "cancelada": codigo, "area": a["id"], "data": d}


# ---------------- visitantes ----------------

async def listar_meus_visitantes(tool_context: ToolContext) -> dict:
    """Lista os visitantes autorizados pelo apartamento do morador desta conversa."""
    return {"status": "ok", "visitantes": await db.visitantes_do(_apartamento(tool_context))}


async def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict:
    """Autoriza a entrada de um visitante no prédio em uma data. Exige confirmação do morador pelo sistema.

    Args:
        nome: nome completo do visitante.
        data: data da visita, AAAA-MM-DD.
    """
    apt = _apartamento(tool_context)
    nome = " ".join((nome or "").split())
    d = _data_valida(data)
    if not nome:
        return {"status": "erro", "motivo": "informe o nome do visitante"}
    if not d:
        return {"status": "erro", "motivo": "data inválida; use AAAA-MM-DD"}
    novo = await db.autorizar_visitante(apt, nome, d)
    return {"status": "ok", "visitante": nome, "data": d, "ja_autorizado": not novo}


# ---------------- regulamento ----------------

def consultar_regulamento(assunto: str) -> dict:
    """Busca no regulamento interno o capítulo que trata do assunto.

    Args:
        assunto: palavras-chave do TEMA da dúvida (ex.: "piscina horário domingo"). Temas do regulamento: disposições
            gerais, direitos e deveres, silêncio, piscina, academia/brinquedoteca/playground, salão de festas/
            churrasqueira/quadra, portaria e segurança, animais de estimação, mudanças, obras, garagem e veículos,
            lixo e reciclagem, infrações e penalidades.
    """
    return regulamento.buscar(assunto)


TOOLS_RESERVAS = [
    FunctionTool(listar_minhas_reservas),
    FunctionTool(verificar_disponibilidade),
    FunctionTool(reservar_area, require_confirmation=_exige_confirmacao_reserva),
    FunctionTool(cancelar_reserva),
]
TOOLS_VISITANTES = [
    FunctionTool(listar_meus_visitantes),
    FunctionTool(autorizar_visitante, require_confirmation=True),
]
TOOLS_REGULAMENTO = [FunctionTool(consultar_regulamento)]
