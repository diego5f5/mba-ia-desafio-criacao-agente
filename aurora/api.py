"""API HTTP (FastAPI) em cima do Runner do ADK.

Subir:  uv run uvicorn aurora.api:app --port 8000
"""
import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from google.adk.apps import App
from google.adk.apps.app import ResumabilityConfig
from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types
from pydantic import BaseModel

from . import db
from .agents import root_agent
from .config import APP_NAME, DB_SESSOES

CONFIRMACAO_FN = "adk_request_confirmation"
USER_ID = "morador"  # a identidade que importa é state["apartamento"]


class NovaSessao(BaseModel):
    apartamento: str


class Mensagem(BaseModel):
    texto: str


class RespostaConfirmacao(BaseModel):
    id: str
    confirmado: bool


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.inicializar()
    session_service = SqliteSessionService(str(DB_SESSOES))
    adk_app = App(
        name=APP_NAME,
        root_agent=root_agent,
        # Sem resumabilidade o Runner não devolve a resposta de confirmação ao agente que a pediu.
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
    app.state.sessions = session_service
    app.state.runner = Runner(app=adk_app, session_service=session_service)
    app.state.locks = {}
    yield


app = FastAPI(title="Assistente do Residencial Aurora", lifespan=lifespan)


def _lock(session_id: str) -> asyncio.Lock:
    return app.state.locks.setdefault(session_id, asyncio.Lock())


async def _sessao(session_id: str):
    s = await app.state.sessions.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
    if s is None:
        raise HTTPException(404, "sessão não encontrada")
    return s


def _pendentes(session) -> list[dict]:
    """Confirmações pedidas e ainda sem resposta, calculadas a partir dos eventos gravados."""
    pedidas: dict[str, dict] = {}
    respondidas: set[str] = set()
    for ev in session.events:
        for fc in ev.get_function_calls():
            if fc.name == CONFIRMACAO_FN and fc.id:
                original = (fc.args or {}).get("originalFunctionCall") or {}
                pedidas[fc.id] = {
                    "id": fc.id,
                    "acao": original.get("name", ""),
                    "detalhes": original.get("args") or {},
                }
        for fr in ev.get_function_responses():
            if fr.name == CONFIRMACAO_FN and fr.id:
                respondidas.add(fr.id)
    return [p for i, p in pedidas.items() if i not in respondidas]


async def _executar(session_id: str, content: types.Content) -> dict:
    textos: list[str] = []
    async for ev in app.state.runner.run_async(user_id=USER_ID, session_id=session_id, new_message=content):
        if ev.author != "user" and ev.is_final_response() and ev.content and ev.content.parts:
            t = "".join(p.text for p in ev.content.parts if p.text and not p.thought)
            if t.strip():
                textos.append(t.strip())
    sessao = await app.state.sessions.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
    return {"resposta": "\n".join(textos), "confirmacoes_pendentes": _pendentes(sessao)}


@app.post("/sessoes", status_code=201)
async def criar_sessao(corpo: NovaSessao):
    session_id = str(uuid.uuid4())
    apt = corpo.apartamento.strip()
    # Garantia 2: o apartamento é gravado aqui, uma única vez, no state da sessão.
    await app.state.sessions.create_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session_id, state={"apartamento": apt}
    )
    return {"session_id": session_id}


@app.post("/sessoes/{session_id}/mensagens")
async def enviar_mensagem(session_id: str, corpo: Mensagem):
    await _sessao(session_id)
    async with _lock(session_id):
        content = types.Content(role="user", parts=[types.Part(text=corpo.texto)])
        return await _executar(session_id, content)


@app.post("/sessoes/{session_id}/confirmacoes")
async def responder_confirmacao(session_id: str, corpo: RespostaConfirmacao):
    async with _lock(session_id):
        sessao = await _sessao(session_id)
        if corpo.id not in {p["id"] for p in _pendentes(sessao)}:
            raise HTTPException(409, "não existe confirmação pendente com esse id nesta sessão")
        resposta = types.Part(
            function_response=types.FunctionResponse(
                id=corpo.id, name=CONFIRMACAO_FN, response={"confirmed": corpo.confirmado}
            )
        )
        return await _executar(session_id, types.Content(role="user", parts=[resposta]))


@app.get("/sessoes/{session_id}/eventos")
async def listar_eventos(session_id: str):
    sessao = await _sessao(session_id)
    return [e.model_dump(mode="json", exclude_none=True) for e in sessao.events]


@app.get("/apartamentos/{numero}/reservas")
async def reservas_do_apartamento(numero: str):
    return await db.reservas_do(numero)


@app.get("/apartamentos/{numero}/visitantes")
async def visitantes_do_apartamento(numero: str):
    return await db.visitantes_do(numero)
