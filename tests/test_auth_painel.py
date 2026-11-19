"""Regressões locais: unittest + Streamlit AppTest, sem Supabase remoto."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from supabase_auth import SyncGoTrueClient
from backend.auth import autenticar_dono
from backend.session import validar_sessao_dono, renovar_sessao_dono


APP = Path(__file__).resolve().parents[1] / "app.py"


def cliente_dono():
    cliente = MagicMock()
    sessao = SimpleNamespace(access_token="token-teste")
    cliente.auth.get_session.return_value = sessao
    cliente.auth.get_user.return_value = SimpleNamespace(user=SimpleNamespace(id="dono"))
    cliente.auth.sign_in_with_password.return_value = SimpleNamespace(
        user=SimpleNamespace(id="dono"), session=sessao,
    )
    cliente.auth.refresh_session.return_value = SimpleNamespace(session=sessao)
    cliente.rpc.return_value.execute.return_value.data = True
    return cliente, sessao


class TestAutenticacao(unittest.TestCase):
    def setUp(self):
        self.cliente, self.sessao = cliente_dono()
        self.fabrica = patch("backend.auth.criar_cliente_supabase", return_value=self.cliente)
        self.criar = self.fabrica.start()
        self.addCleanup(self.fabrica.stop)

    def test_campos_obrigatorios(self):
        for email, senha in [("  ", "senha"), ("dono@teste", "")]:
            with self.subTest(email=email), self.assertRaises(ValueError):
                autenticar_dono(email, senha)
        self.criar.assert_not_called()

    def test_login_e_rpc(self):
        self.assertEqual(autenticar_dono(" dono@teste ", " senha "), (self.cliente, self.sessao))
        self.cliente.auth.sign_in_with_password.assert_called_once_with(
            {"email": "dono@teste", "password": " senha "},
        )
        self.cliente.rpc.assert_called_once_with("sou_dono")

    def test_negacao_exige_booleano_true(self):
        for data in [False, None, "true", 1, [True]]:
            with self.subTest(data=data):
                self.cliente.rpc.return_value.execute.return_value.data = data
                with self.assertRaises(PermissionError):
                    autenticar_dono("dono@teste", "senha")
        self.cliente.auth.sign_out.assert_called_with({"scope": "local"})

    def test_credenciais_invalidas(self):
        self.cliente.auth.sign_in_with_password.side_effect = RuntimeError("segredo-remoto")
        with self.assertRaises(RuntimeError):
            autenticar_dono("dono@teste", "senha")
        self.cliente.rpc.assert_not_called()
        self.cliente.auth.sign_out.assert_called_once_with({"scope": "local"})

    def test_resposta_incompleta(self):
        for campo in ["user", "session"]:
            with self.subTest(campo=campo):
                resposta = SimpleNamespace(user=object(), session=self.sessao)
                setattr(resposta, campo, None)
                self.cliente.auth.sign_in_with_password.return_value = resposta
                with self.assertRaises(PermissionError):
                    autenticar_dono("dono@teste", "senha")
        self.cliente.rpc.assert_not_called()

    def test_falha_rpc_e_logout_preserva_erro(self):
        self.cliente.rpc.return_value.execute.side_effect = RuntimeError("rpc indisponível")
        self.cliente.auth.sign_out.side_effect = OSError("offline")
        with self.assertRaisesRegex(RuntimeError, "rpc indisponível"):
            autenticar_dono("dono@teste", "senha")


class TestSessao(unittest.TestCase):
    def setUp(self):
        self.cliente, self.sessao = cliente_dono()
        self.estado = {"supabase_cliente": self.cliente, "supabase_sessao": self.sessao,
                       "rascunho": "conteúdo privado"}
        self.mock_estado = patch("backend.session.st.session_state", new=self.estado)
        self.mock_estado.start()
        self.addCleanup(self.mock_estado.stop)

    def test_sincroniza_token_renovado_pelo_sdk(self):
        nova = SimpleNamespace(access_token="novo-token")
        self.cliente.auth.get_session.return_value = nova
        self.assertTrue(validar_sessao_dono())
        self.assertIs(self.estado["supabase_sessao"], nova)
        self.cliente.auth.get_user.assert_called_once_with(jwt="novo-token")
        self.cliente.auth.refresh_session.assert_not_called()

    def test_sem_sessao_sdk(self):
        self.cliente.auth.get_session.return_value = None
        self.assertFalse(validar_sessao_dono())
        self.assertEqual(self.estado, {})
        self.cliente.rpc.assert_not_called()

    def test_sdk_renova_token_expirado(self):
        auth = self.cliente.auth
        auth._load_session.return_value = SimpleNamespace(
            expires_at=1, refresh_token="refresh-teste",
        )
        nova = SimpleNamespace(access_token="token-renovado")
        auth._call_refresh_token.return_value = nova
        # Exercita o get_session real da versão instalada, com transporte simulado.
        auth.get_session.side_effect = lambda: SyncGoTrueClient.get_session(auth)
        self.assertTrue(validar_sessao_dono())
        auth._call_refresh_token.assert_called_once_with("refresh-teste")
        auth.get_user.assert_called_once_with(jwt="token-renovado")
        self.assertIs(self.estado["supabase_sessao"], nova)

    def test_sdk_nao_renova_token_valido(self):
        auth = self.cliente.auth
        sessao = SimpleNamespace(expires_at=10**12, access_token="token-valido")
        auth._load_session.return_value = sessao
        auth.get_session.side_effect = lambda: SyncGoTrueClient.get_session(auth)
        self.assertTrue(validar_sessao_dono())
        auth._call_refresh_token.assert_not_called()
        auth.get_user.assert_called_once_with(jwt="token-valido")

    def test_usuario_ausente(self):
        for resposta in [None, SimpleNamespace(user=None)]:
            with self.subTest(resposta=resposta):
                self.estado.update(supabase_cliente=self.cliente, supabase_sessao=self.sessao)
                self.cliente.auth.get_user.return_value = resposta
                self.assertFalse(validar_sessao_dono())
                self.assertEqual(self.estado, {})
        self.cliente.rpc.assert_not_called()

    def test_permissao_revogada(self):
        for data in [False, None, "true", 1]:
            with self.subTest(data=data):
                self.estado.update(supabase_cliente=self.cliente, supabase_sessao=self.sessao)
                self.cliente.rpc.return_value.execute.return_value.data = data
                self.assertFalse(validar_sessao_dono())
                self.assertEqual(self.estado, {})

    def test_indisponibilidade_fecha_acesso(self):
        self.cliente.auth.get_session.side_effect = RuntimeError("offline")
        self.cliente.auth.sign_out.side_effect = RuntimeError("offline")
        self.assertFalse(validar_sessao_dono())
        self.assertEqual(self.estado, {})

    def test_estado_incompleto(self):
        del self.estado["supabase_sessao"]
        self.assertFalse(validar_sessao_dono())
        self.assertEqual(self.estado, {})

    def test_renovacao_sem_resposta(self):
        self.cliente.auth.refresh_session.return_value.session = None
        self.assertFalse(renovar_sessao_dono())
        self.assertEqual(self.estado, {})

    def test_renovacao_nao_concede_permissao(self):
        self.cliente.rpc.return_value.execute.return_value.data = False
        self.assertFalse(renovar_sessao_dono())
        self.assertEqual(self.estado, {})


if __name__ == "__main__":
    unittest.main()
