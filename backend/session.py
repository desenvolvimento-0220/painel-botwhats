"""Proteção do Streamlit; a sessão persistente pertence ao gateway HTTP."""

import hashlib

import streamlit as st


def consultar_sessao_persistente(operacao="validate"):
    """Restaura pelo identificador opaco injetado pelo middleware do servidor."""
    from backend import session_runtime
    from backend.session_store import chave_sessao

    sessoes = session_runtime.sessoes
    sid = st.context.headers.get("x-botwhats-session", "")
    if sessoes is None or chave_sessao(sid) is None:
        st.session_state.clear()
        return False
    fingerprint = hashlib.sha256(sid.encode("ascii")).hexdigest()
    if st.session_state.get("_visitante") != fingerprint:
        st.session_state.clear()
    if operacao == "logout":
        sessoes.revogar(sid)
        autorizado = False
    elif operacao in ("validate", "refresh"):
        autorizado = sessoes.validar(sid, renovar=operacao == "refresh")
    else:
        autorizado = False
    if autorizado:
        st.session_state["_visitante"] = fingerprint
    else:
        st.session_state.clear()
    return autorizado


def proteger_painel():
    """F5 restaura pelo cookie do gateway, sem confiar no session_state."""
    if not consultar_sessao_persistente():
        st.title("🤖 BotWhats")
        st.subheader("Acesso administrativo")
        from backend import session_runtime
        if session_runtime.sessoes is None:
            st.error("Inicie o painel com iniciar.py para habilitar o login seguro.")
        else:
            st.info("Entre novamente para confirmar seu acesso.")
            st.link_button("Entrar", "/auth/login")
        st.stop()
    # Navegação de página inteira: o endpoint HTTP pode remover o cookie HttpOnly.
    st.sidebar.link_button("Sair", "/auth/logout")


# Funções de sessão em memória mantidas para compatibilidade e testes antigos.
# Não são o mecanismo de autenticação usado pelo app.py.


def salvar_sessao(cliente, sessao):
    """Guarda o cliente individual e a sessão somente na memória do visitante."""
    st.session_state["supabase_cliente"] = cliente
    st.session_state["supabase_sessao"] = sessao


def obter_cliente():
    return st.session_state.get("supabase_cliente")


def sessao_ativa():
    """Indica presença local; não substitui validar_sessao_dono."""
    return (
        obter_cliente() is not None
        and st.session_state.get("supabase_sessao") is not None
    )


def encerrar_sessao():
    """Encerra apenas este login e limpa inclusive os formulários do painel."""
    cliente = obter_cliente()
    try:
        if cliente is not None:
            cliente.auth.sign_out({"scope": "local"})
    except Exception:
        # A indisponibilidade remota nunca pode impedir a saída local.
        pass
    finally:
        st.session_state.clear()


def validar_sessao_dono():
    """Renova se necessário e confirma identidade e permissão a cada execução.

    get_session mantém os tokens sincronizados com o SDK e renova perto da
    expiração. A autorização depende de get_user e da RPC, nunca dos dados
    locais da sessão. Qualquer falha fecha o acesso e limpa o estado.
    """
    if not sessao_ativa():
        encerrar_sessao()
        return False

    cliente = obter_cliente()
    try:
        sessao = cliente.auth.get_session()
        if sessao is None:
            raise PermissionError("Sessão indisponível.")

        resposta = cliente.auth.get_user(jwt=sessao.access_token)
        if resposta is None or resposta.user is None:
            raise PermissionError("Identidade inválida.")

        if cliente.rpc("sou_dono").execute().data is not True:
            raise PermissionError("Usuário não autorizado.")

        salvar_sessao(cliente, sessao)
        return True
    except Exception:
        encerrar_sessao()
        return False


def renovar_sessao_dono():
    """Força a renovação e revalida a identidade e a permissão do Dono."""
    if not sessao_ativa():
        encerrar_sessao()
        return False

    cliente = obter_cliente()
    try:
        resposta = cliente.auth.refresh_session()
        if resposta.session is None:
            raise PermissionError("Não foi possível renovar a sessão.")
        salvar_sessao(cliente, resposta.session)
    except Exception:
        encerrar_sessao()
        return False

    return validar_sessao_dono()
