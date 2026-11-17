# Assistente virtual do Residencial Aurora

API em Python (FastAPI) com um assistente construído em Google ADK 2.9.1. O modelo conduz a conversa; as regras críticas ficam no código.

## Arquitetura

```
POST /sessoes/{id}/mensagens ─▶ Runner (App, resumável, SqliteSessionService)
                                   │
                          aurora (agente principal, só roteia)
              ┌────────────────────┼────────────────────┐
   especialista_reservas   especialista_visitantes   especialista_regulamento
   (tools de reservas)     (tools de visitantes)     (consultar_regulamento)
              └──────── tools ──▶ aurora/db.py (SQLite condominio.db)
```

| Agente | Responsabilidade | Como é acionado | Por quê |
|---|---|---|---|
| `aurora` (`aurora/agents.py`) | Entende o pedido e transfere. Não tem tools de negócio nem o regulamento. | Raiz do `App`. | Instrução curta e barata; um único ponto de entrada. |
| `especialista_reservas` | Reservar, ver disponibilidade, listar e cancelar. | `sub_agents` (transferência). | Só ele enxerga as tools de reserva, e é quem pede a confirmação de cobrança. |
| `especialista_visitantes` | Autorizar e listar visitantes. | `sub_agents`. | Isola a tool que libera acesso. |
| `especialista_regulamento` | Responde dúvidas via `consultar_regulamento`. | `sub_agents`. | O texto do regulamento só entra no contexto quando necessário. |

Outros arquivos: `aurora/tools.py` (tools), `aurora/db.py` (armazenamento), `aurora/regulamento.py` (busca por capítulo), `aurora/api.py` (rotas), `aurora/restaurar.py` (restauração).

Armazenamento: `condominio.db` (reservas e visitantes) e `sessoes.db` (sessões/eventos do ADK), ambos SQLite e fora do Git. Os arquivos de `dados/` são lidos só para semear/restaurar.

## Garantias

1. **Cobrança ou acesso só com confirmação.** Em `aurora/tools.py`, `FunctionTool(reservar_area, require_confirmation=_exige_confirmacao_reserva)` (só quando a taxa é > 0) e `FunctionTool(autorizar_visitante, require_confirmation=True)`. O ADK emite `adk_request_confirmation` e a tool só executa quando chega um `FunctionResponse` com esse id. Em `aurora/api.py`: `_pendentes()` calcula as pendências a partir dos eventos gravados; `responder_confirmacao` devolve 409 se o id não estiver pendente (inclusive já respondido) e, se estiver, envia o `FunctionResponse` ao Runner. O `App` usa `ResumabilityConfig(is_resumable=True)`, necessário para o Runner devolver a resposta ao especialista que pediu a confirmação. Texto como "já confirmei" nunca vira `FunctionResponse`, então não executa nada.
2. **Sessão pertence a um apartamento.** `criar_sessao` (`aurora/api.py`) grava `state={"apartamento": ...}` uma única vez. Nenhuma tool tem parâmetro de apartamento: `_apartamento(tool_context)` em `aurora/tools.py` lê do state e todas as consultas em `aurora/db.py` filtram por ele. `cancelar_reserva` só enxerga reservas do próprio apartamento; `verificar_disponibilidade` só devolve "livre/ocupada". O prompt reforça, mas a barreira é o código.
3. **Nada se perde no reinício.** `SqliteSessionService(DB_SESSOES)` em `aurora/api.py` persiste sessões e eventos; reservas e visitantes ficam em `condominio.db`. O código de reserva é `RSV-<8 hex>` com `PRIMARY KEY`, e cancelar só muda `status`, então o código nunca se repete.
4. **Regulamento consultado, não carregado.** Nenhum agente tem o texto nas instruções. `consultar_regulamento` (`aurora/tools.py`) chama `regulamento.buscar` (`aurora/regulamento.py`), que devolve apenas o capítulo mais relevante; a resposta da tool é o único trecho que entra nos eventos.
5. **Dois moradores, uma reserva.** Em `aurora/db.py`, o índice `ux_reserva_ativa` (`UNIQUE (area, data) WHERE status='ativa'`) e `_criar_reserva`: o `INSERT` é quem decide e a violação vira "indisponível" (resposta normal 200). A checagem prévia em `recusar_se_ocupada` (`before_tool_callback`) é só para evitar pedir cobrança de data ocupada. A função de confirmação depende só da taxa, porque o ADK a reavalia na retomada.

## Como rodar

Pré-requisitos: Python 3.12+, [uv](https://docs.astral.sh/uv/) e uma chave do Google AI Studio.

```bash
cp .env.example .env     # preencha GOOGLE_API_KEY
uv sync
uv run python -m aurora.restaurar                 # restaura reservas/visitantes (e apaga sessões)
uv run uvicorn aurora.api:app --port 8000         # sobe a API em http://localhost:8000
```

Variáveis do `.env`: `GOOGLE_API_KEY` (obrigatória) e `AURORA_MODELO` (opcional, padrão `gemini-3.5-flash`; para gastar menos use `gemini-3.1-flash-lite`). Rode a restauração com a API parada. Para reiniciar sem perder dados, basta repetir apenas o comando de subida.
