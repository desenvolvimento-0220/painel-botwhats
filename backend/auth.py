
"""
BotWhats SaaS — Autenticação administrativa.
"""

from backend.supabase_client import criar_cliente_supabase
from backend.auth_diagnostics import registrar


def validar_cliente_dono(cliente, renovar=False):
    """Valida no servidor; get_session renova o token quando necessário."""
    sessao = (cliente.auth.refresh_session().session if renovar
              else cliente.auth.get_session())
    if sessao is None:
        raise PermissionError("Sessão indisponível.")
    resposta = cliente.auth.get_user(jwt=sessao.access_token)
    if resposta is None or resposta.user is None:
        raise PermissionError("Identidade inválida.")
    if cliente.rpc("sou_dono").execute().data is not True:
        raise PermissionError("Usuário não autorizado.")
    return sessao


def autenticar_dono(email: str, senha: str):
    """
    Autentica o usuário e verifica se é Dono.

    Retorna o cliente e a sessão autenticada.
    """
    if not email.strip() or not senha:
        registrar("validacao", "negado", "campos_obrigatorios")
        raise ValueError("Informe e-mail e senha.")

    registrar("validacao", "aprovado")
    registrar("configuracao", "iniciado")
    try:
        cliente = criar_cliente_supabase()
    except Exception:
        registrar("configuracao", "falha", "configuracao_indisponivel")
        raise
    registrar("configuracao", "aprovado")

    try:
        registrar("autenticacao", "iniciado")
        try:
            resposta = cliente.auth.sign_in_with_password({
                "email": email.strip(),
                "password": senha,
            })
        except Exception:
            registrar("autenticacao", "falha", "autenticacao_falhou")
            raise

        if not resposta.user or not resposta.session:
            registrar("autenticacao", "negado", "resposta_incompleta")
            raise PermissionError("Falha na autenticação.")
        registrar("autenticacao", "aprovado")

        registrar("permissao", "iniciado")
        try:
            resultado = cliente.rpc("sou_dono").execute()
        except Exception:
            registrar("permissao", "falha", "permissao_falhou")
            raise

        if resultado.data is not True:
            registrar("permissao", "negado", "sem_permissao")
            raise PermissionError("Usuário não autorizado.")
        registrar("permissao", "aprovado")

        return cliente, resposta.session

    except Exception:
        try:
            cliente.auth.sign_out({"scope": "local"})
        except Exception:
            pass
        raise
