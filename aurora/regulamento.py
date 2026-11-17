"""Consulta ao regulamento (Garantia 4).

O texto de dados/regulamento.md nunca vai para as instruções de nenhum agente nem
para o histórico por inteiro: a tool `consultar_regulamento` devolve só o capítulo
mais relevante para o assunto pedido.
"""
import re
import unicodedata

from .config import DADOS

_STOP = {
    "a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "no", "na", "nos", "nas", "um", "uma",
    "para", "por", "com", "que", "qual", "quais", "como", "ate", "se", "ao", "aos", "eh", "sao", "ser", "pode",
    "posso", "tem", "ha", "quando", "horas", "hora", "funciona", "meu", "minha",
}


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def _tokens(s: str) -> set[str]:
    out = set()
    for t in re.findall(r"[a-z0-9]+", _fold(s)):
        if t in _STOP or len(t) < 3:
            continue
        out.add(t[:-1] if t.endswith("s") and len(t) > 4 else t)
    return out


def _carregar() -> list[dict]:
    texto = (DADOS / "regulamento.md").read_text(encoding="utf-8")
    capitulos = []
    for bloco in re.split(r"(?m)^## ", texto)[1:]:
        titulo, _, corpo = bloco.partition("\n")
        capitulos.append({"titulo": titulo.strip(), "texto": f"## {titulo.strip()}\n{corpo.strip()}",
                          "t_titulo": _tokens(titulo), "t_corpo": _tokens(corpo)})
    return capitulos


_CAPITULOS = _carregar()


def buscar(assunto: str) -> dict:
    """Devolve o capítulo mais relevante para o assunto (ou a lista de títulos se nada casar)."""
    q = _tokens(assunto)
    melhor, pontos = None, 0.0
    for c in _CAPITULOS:
        p = 5 * len(q & c["t_titulo"]) + sum(1 for t in q if t in c["t_corpo"]) * 1.0
        if p > pontos:
            melhor, pontos = c, p
    if melhor is None:
        return {"encontrado": False, "capitulos_disponiveis": [c["titulo"] for c in _CAPITULOS]}
    return {"encontrado": True, "capitulo": melhor["titulo"], "texto": melhor["texto"]}
