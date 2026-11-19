
"""
BotWhats — Testes de autenticação e sessão.

Testes simulados, sem conexão real com o Supabase.
"""

from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from backend.session import (
    sessao_ativa,
    validar_sessao_dono,
    encerrar_sessao,
    renovar_sessao_dono,
)


def criar_estado(cliente=None):
    """Cria um estado de sessão para os testes."""
    if cliente is None:
        return {}

    sessao = SimpleNamespace(access_token="token-simulado")
    cliente.auth.get_session.return_value = sessao
    return {
        "supabase_cliente": cliente,
        "supabase_sessao": sessao,
    }


# TESTE 1 — Visitante sem login

def testar_visitante_sem_login():
    with patch("backend.session.st.session_state", new={}):
        assert sessao_ativa() is False
        assert validar_sessao_dono() is False

    print("OK: visitante sem login bloqueado.")


# TESTE 2 — Dono autorizado

def testar_dono_autorizado():
    cliente = MagicMock()

    cliente.auth.get_user.return_value.user.id = "dono-teste"
    cliente.rpc.return_value.execute.return_value.data = True

    with patch(
        "backend.session.st.session_state",
        new=criar_estado(cliente),
    ):
        assert validar_sessao_dono() is True

    print("OK: Dono autorizado.")


# TESTE 3 — Usuário sem permissão

def testar_usuario_sem_permissao():
    cliente = MagicMock()

    cliente.auth.get_user.return_value.user.id = "usuario-teste"
    cliente.rpc.return_value.execute.return_value.data = False

    with patch(
        "backend.session.st.session_state",
        new=criar_estado(cliente),
    ):
        assert validar_sessao_dono() is False

    print("OK: usuário sem permissão bloqueado.")


# TESTE 4 — Token inválido

def testar_token_invalido():
    cliente = MagicMock()

    cliente.auth.get_user.side_effect = Exception(
        "Token inválido"
    )

    with patch(
        "backend.session.st.session_state",
        new=criar_estado(cliente),
    ):
        assert validar_sessao_dono() is False

    print("OK: token inválido bloqueado.")


# TESTE 5 — Falha na autorização

def testar_falha_na_verificacao():
    cliente = MagicMock()

    cliente.auth.get_user.return_value.user.id = "dono-teste"
    cliente.rpc.return_value.execute.side_effect = Exception(
        "Serviço indisponível"
    )

    with patch(
        "backend.session.st.session_state",
        new=criar_estado(cliente),
    ):
        assert validar_sessao_dono() is False

    print("OK: falha de autorização bloqueada.")


# TESTE 6 — Logout normal

def testar_logout():
    cliente = MagicMock()
    estado = criar_estado(cliente)

    with patch(
        "backend.session.st.session_state",
        new=estado,
    ):
        encerrar_sessao()

        assert sessao_ativa() is False
        assert "supabase_cliente" not in estado
        assert "supabase_sessao" not in estado

        cliente.auth.sign_out.assert_called_once_with({"scope": "local"})

    print("OK: logout e limpeza da sessão.")


# TESTE 7 — Logout com falha de conexão

def testar_logout_com_falha():
    cliente = MagicMock()

    cliente.auth.sign_out.side_effect = Exception(
        "Falha na conexão"
    )

    estado = criar_estado(cliente)

    with patch(
        "backend.session.st.session_state",
        new=estado,
    ):
        try:
            encerrar_sessao()
        except Exception:
            pass

        assert sessao_ativa() is False
        assert "supabase_cliente" not in estado
        assert "supabase_sessao" not in estado

    print("OK: sessão limpa mesmo após erro no logout.")


# TESTE 8 — Renovação bem-sucedida

def testar_renovacao_sucesso():
    cliente = MagicMock()
    nova_sessao = SimpleNamespace(access_token="token-renovado")

    cliente.auth.refresh_session.return_value.session = nova_sessao
    cliente.auth.get_user.return_value.user.id = "dono-teste"
    cliente.rpc.return_value.execute.return_value.data = True

    estado = criar_estado(cliente)
    cliente.auth.get_session.return_value = nova_sessao

    with patch(
        "backend.session.st.session_state",
        new=estado,
    ):
        assert renovar_sessao_dono() is True

        assert estado["supabase_sessao"] is nova_sessao

        cliente.auth.refresh_session.assert_called_once()

    print("OK: renovação de sessão autorizada.")


# TESTE 9 — Falha na renovação

def testar_renovacao_com_falha():
    cliente = MagicMock()

    cliente.auth.refresh_session.side_effect = Exception(
        "Refresh token inválido"
    )

    estado = criar_estado(cliente)

    with patch(
        "backend.session.st.session_state",
        new=estado,
    ):
        assert renovar_sessao_dono() is False
        assert sessao_ativa() is False

        cliente.auth.sign_out.assert_called_once_with({"scope": "local"})

    print("OK: falha na renovação bloqueou a sessão.")

def testar_isolamento_entre_sessoes():
    """O logout de um visitante não deve afetar outro."""
    cliente_a = MagicMock()
    cliente_b = MagicMock()

    estado_a = criar_estado(cliente_a)
    estado_b = criar_estado(cliente_b)

    # Visitante A encerra sua sessão.
    with patch(
        "backend.session.st.session_state",
        new=estado_a,
    ):
        encerrar_sessao()
        assert sessao_ativa() is False

    # A sessão armazenada do visitante B permanece.
    with patch(
        "backend.session.st.session_state",
        new=estado_b,
    ):
        assert sessao_ativa() is True
        assert estado_b["supabase_cliente"] is cliente_b

    cliente_a.auth.sign_out.assert_called_once_with({"scope": "local"})
    cliente_b.auth.sign_out.assert_not_called()

    print("OK: isolamento simulado entre sessões.")


# EXECUÇÃO DE TODOS OS TESTES

def load_tests(loader, tests, pattern):
    """Inclui os testes legados na descoberta padrão do unittest."""
    from unittest import FunctionTestCase, TestSuite

    return TestSuite(
        FunctionTestCase(funcao)
        for nome, funcao in sorted(globals().items())
        if nome.startswith("testar_") and callable(funcao)
    )


if __name__ == "__main__":

    testar_visitante_sem_login()
    testar_dono_autorizado()
    testar_usuario_sem_permissao()
    testar_token_invalido()
    testar_falha_na_verificacao()
    testar_logout()
    testar_logout_com_falha()
    testar_renovacao_sucesso()
    testar_renovacao_com_falha()
    testar_isolamento_entre_sessoes()

    print("\nTodos os 10 testes de sessão foram aprovados.")
