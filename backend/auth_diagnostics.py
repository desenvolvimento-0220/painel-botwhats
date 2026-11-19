"""Diagnósticos com vocabulário fechado; nenhum dado da requisição é aceito."""

from contextlib import contextmanager
from contextvars import ContextVar
from itertools import count
import logging


LOGGER = logging.getLogger("botwhats.auth")
_fluxo = ContextVar("fluxo_diagnostico_auth", default=None)
_sequencia = count(1)
ETAPAS = frozenset({"requisicao", "origem", "formulario", "csrf", "validacao",
                    "configuracao", "autenticacao", "permissao", "login", "sessao", "resposta"})
RESULTADOS = frozenset({"iniciado", "aprovado", "negado", "falha"})
MOTIVOS = frozenset({
    "ok", "login_get", "login_post", "logout_get", "logout_post", "outro_metodo",
    "host_ausente", "host_duplicado", "host_divergente", "origem_ausente",
    "origem_duplicada", "origem_nula", "origem_divergente", "renderizado",
    "tipo_conteudo_invalido", "corpo_excedido", "corpo_invalido", "campos_duplicados",
    "cookie_csrf_ausente", "csrf_ausente", "csrf_formato_invalido", "csrf_divergente",
    "csrf_assinatura_invalida", "csrf_expirado", "campos_obrigatorios",
    "configuracao_indisponivel", "autenticacao_falhou", "resposta_incompleta",
    "permissao_falhou", "sem_permissao", "criacao_falhou", "criada", "revogada",
    "redirecionamento_303",
})


def configurar_diagnosticos():
    """Saída de console própria, sem formatter de requisição ou dados externos."""
    if not LOGGER.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False


@contextmanager
def fluxo_diagnostico():
    # Contador efêmero, sem vínculo com usuário, cookie ou sessão autenticada.
    contexto = _fluxo.set(next(_sequencia))
    try:
        yield
    finally:
        _fluxo.reset(contexto)


def registrar(etapa, resultado, motivo="ok"):
    if etapa not in ETAPAS or resultado not in RESULTADOS or motivo not in MOTIVOS:
        raise ValueError("Diagnóstico fora do vocabulário permitido.")
    fluxo = _fluxo.get()
    if fluxo is not None:
        LOGGER.info("auth fluxo=%d etapa=%s resultado=%s motivo=%s", fluxo, etapa, resultado, motivo)
