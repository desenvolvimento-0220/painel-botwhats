"""Fluxo completo de login e diagnósticos seguros, sem chamadas remotas."""

from html.parser import HTMLParser
import re
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient

from backend.auth import autenticar_dono
from backend.auth_diagnostics import registrar, fluxo_diagnostico
from backend.gateway import ConfigGateway, criar_gateway, POLITICA_REFERENCIA
from backend.session_store import SessoesServidor


class FormularioHTML(HTMLParser):
    def __init__(self, texto):
        super().__init__()
        self.form = None
        self.meta = None
        self.csrf = None
        self.feed(texto)

    def handle_starttag(self, tag, atributos):
        attrs = dict(atributos)
        if tag == "form":
            self.form = attrs
        if tag == "meta" and attrs.get("name") == "referrer":
            self.meta = attrs.get("content")
        if tag == "input" and attrs.get("name") == "csrf":
            self.csrf = attrs.get("value")


class TestLogin403(unittest.TestCase):
    def setUp(self):
        self.config = ConfigGateway("https://bot.test")
        self.store = SessoesServidor()
        self.cliente = MagicMock()
        self.sessao = SimpleNamespace(access_token="ACCESS_SENTINELA", refresh_token="REFRESH_SENTINELA")
        self.cliente.auth.sign_in_with_password.return_value = SimpleNamespace(user=object(), session=self.sessao)
        self.cliente.auth.get_session.return_value = self.sessao
        self.cliente.auth.get_user.return_value.user.id = "identidade-simulada"
        self.cliente.rpc.return_value.execute.return_value.data = True
        p = patch("backend.auth.criar_cliente_supabase", return_value=self.cliente)
        self.fabrica = p.start()
        self.addCleanup(p.stop)
        p_criar = patch.object(self.store, "criar", wraps=self.store.criar)
        self.criar = p_criar.start()
        self.addCleanup(p_criar.stop)

        async def painel(request):
            return Response("painel")
        app = criar_gateway(self.config, self.store, autenticar_dono,
                            Starlette(routes=[Route("/", painel)]))
        self.http = TestClient(app, base_url=self.config.origem, follow_redirects=False)
        self.http.__enter__()
        self.addCleanup(self.http.__exit__, None, None, None)
        self.csrf = FormularioHTML(self.http.get("/auth/login").text).csrf

    def post(self, **kwargs):
        dados = {"email": "EMAIL_SENTINELA@teste.invalid", "senha": "SENHA_SENTINELA", "csrf": self.csrf}
        dados.update(kwargs.pop("dados", {}))
        headers = kwargs.pop("headers", {"origin": self.config.origem})
        with self.assertLogs("botwhats.auth", level="INFO") as captura:
            resposta = self.http.post("/auth/login", data=dados, headers=headers, **kwargs)
        self.logs = [record.getMessage() for record in captura.records]
        for record in captura.records:
            self.assertIsNone(record.exc_info)
            self.assertIsNone(record.stack_info)
        return resposta

    def evento(self, etapa, resultado, motivo="ok"):
        self.assertTrue(any(f"etapa={etapa} resultado={resultado} motivo={motivo}" in l for l in self.logs))

    def test_get_html_e_politica_alinhados(self):
        with self.assertLogs("botwhats.auth", level="INFO") as captura:
            resposta = self.http.get("/auth/login")
        self.assertEqual(resposta.status_code, 200)
        formulario = FormularioHTML(resposta.text)
        self.assertEqual(resposta.headers["referrer-policy"], POLITICA_REFERENCIA)
        self.assertEqual(formulario.meta, POLITICA_REFERENCIA)
        self.assertEqual(formulario.form["method"], "post")
        self.assertEqual(formulario.form["action"], "/auth/login")
        self.assertEqual(formulario.form["target"], "_self")
        self.assertTrue(formulario.csrf)
        self.assertIn("form-action 'self'", resposta.headers["content-security-policy"])
        self.assertEqual(resposta.headers["cache-control"], "no-store")
        self.assertTrue(any("motivo=renderizado" in l for l in captura.output))
        self.fabrica.assert_not_called()

    def test_post_legitimo_todas_etapas_e_sessao(self):
        resposta = self.post()
        self.assertEqual(resposta.status_code, 303)
        self.assertEqual(resposta.headers["location"], "/")
        for etapa in ("origem", "formulario", "csrf", "validacao", "configuracao", "autenticacao", "permissao"):
            self.evento(etapa, "aprovado")
        self.evento("sessao", "aprovado", "criada")
        self.evento("resposta", "aprovado", "redirecionamento_303")
        self.cliente.rpc.assert_called_once_with("sou_dono")
        self.criar.assert_called_once_with(self.cliente)
        self.assertTrue(self.store.existe(resposta.cookies.get(self.config.cookie)))
        self.assertEqual(self.http.get("/").status_code, 200)

    def test_origem_nula_csrf_correto_continua_rejeitada(self):
        resposta = self.post(headers={"origin": "null", "referer": self.config.origem + "/auth/login",
                                     "sec-fetch-site": "same-origin"})
        self.assertEqual(resposta.status_code, 403)
        self.evento("origem", "negado", "origem_nula")
        self.fabrica.assert_not_called()
        self.criar.assert_not_called()
        self.assertFalse(any("etapa=csrf" in l for l in self.logs))

    def test_origem_ausente_nao_usa_referer(self):
        self.assertEqual(self.post(headers={"referer": self.config.origem + "/auth/login"}).status_code, 403)
        self.evento("origem", "negado", "origem_ausente")
        self.fabrica.assert_not_called()

    def test_origens_invalidas(self):
        for origem in ("https://malicioso.test", "https://bot.test.malicioso.test", "http://bot.test", "https://bot.test:9999"):
            with self.subTest(caso="origem_divergente"):
                self.assertEqual(self.post(headers={"origin": origem}).status_code, 403)
                self.evento("origem", "negado", "origem_divergente")
        self.fabrica.assert_not_called()

    def test_origin_duplicado_rejeitado(self):
        self.assertEqual(self.post(headers=[("origin", self.config.origem), ("origin", "null")]).status_code, 403)
        self.evento("origem", "negado", "origem_duplicada")
        self.fabrica.assert_not_called()

    def test_host_invalido(self):
        self.assertEqual(self.post(headers={"origin": self.config.origem, "host": "invalido.test"}).status_code, 403)
        self.evento("origem", "negado", "host_divergente")
        self.fabrica.assert_not_called()

    def test_csrf_ausente(self):
        self.assertEqual(self.post(dados={"csrf": ""}).status_code, 403)
        self.evento("csrf", "negado", "csrf_ausente")
        self.fabrica.assert_not_called()

    def test_cookie_csrf_ausente(self):
        self.http.cookies.clear()
        self.assertEqual(self.post().status_code, 403)
        self.evento("csrf", "negado", "cookie_csrf_ausente")
        self.fabrica.assert_not_called()

    def test_csrf_adulterado(self):
        self.assertEqual(self.post(dados={"csrf": "CSRF_SENTINELA"}).status_code, 403)
        self.evento("csrf", "negado", "csrf_divergente")
        self.fabrica.assert_not_called()

    def test_csrf_expirado(self):
        with patch("backend.gateway.time.time", return_value=100):
            self.csrf = FormularioHTML(self.http.get("/auth/login").text).csrf
        with patch("backend.gateway.time.time", return_value=701):
            self.assertEqual(self.post(headers={"origin": self.config.origem,
                "cookie": self.config.cookie_csrf + "=" + self.csrf}).status_code, 403)
        self.evento("csrf", "negado", "csrf_expirado")
        self.fabrica.assert_not_called()

    def test_configuracao_falhou(self):
        self.fabrica.side_effect = RuntimeError("CONFIG_SENTINELA")
        self.assertEqual(self.post().status_code, 401)
        self.evento("configuracao", "falha", "configuracao_indisponivel")
        self.criar.assert_not_called()

    def test_autenticacao_falhou(self):
        self.cliente.auth.sign_in_with_password.side_effect = RuntimeError("SENHA_SENTINELA ACCESS_SENTINELA")
        self.assertEqual(self.post().status_code, 401)
        self.evento("autenticacao", "falha", "autenticacao_falhou")
        self.cliente.rpc.assert_not_called()
        self.criar.assert_not_called()

    def test_autenticacao_sem_sessao(self):
        self.cliente.auth.sign_in_with_password.return_value.session = None
        self.assertEqual(self.post().status_code, 401)
        self.evento("autenticacao", "negado", "resposta_incompleta")
        self.criar.assert_not_called()

    def test_permissao_dono_negada(self):
        self.cliente.rpc.return_value.execute.return_value.data = False
        self.assertEqual(self.post().status_code, 401)
        self.evento("autenticacao", "aprovado")
        self.evento("permissao", "negado", "sem_permissao")
        self.criar.assert_not_called()
        self.cliente.auth.sign_out.assert_called_once_with({"scope": "local"})

    def test_rpc_indisponivel(self):
        self.cliente.rpc.return_value.execute.side_effect = RuntimeError("REFRESH_SENTINELA")
        self.assertEqual(self.post().status_code, 401)
        self.evento("permissao", "falha", "permissao_falhou")
        self.criar.assert_not_called()

    def test_criacao_de_sessao_falhou(self):
        self.criar.side_effect = RuntimeError("SESSAO_SENTINELA")
        resposta = self.post()
        self.assertEqual(resposta.status_code, 503)
        self.evento("permissao", "aprovado")
        self.evento("sessao", "falha", "criacao_falhou")
        self.cliente.auth.sign_out.assert_called_once_with({"scope": "local"})
        self.assertNotIn(self.config.cookie, resposta.cookies)

    def test_campos_obrigatorios(self):
        self.assertEqual(self.post(dados={"senha": ""}).status_code, 401)
        self.evento("validacao", "negado", "campos_obrigatorios")
        self.fabrica.assert_not_called()

    def test_campos_duplicados_e_tipo_conteudo_invalidos(self):
        with self.assertLogs("botwhats.auth", level="INFO") as captura:
            resposta = self.http.post("/auth/login", content="csrf=a&csrf=b",
                                      headers={"origin": self.config.origem, "content-type": "application/x-www-form-urlencoded"})
        self.assertEqual(resposta.status_code, 400)
        self.assertTrue(any("motivo=campos_duplicados" in l for l in captura.output))
        with self.assertLogs("botwhats.auth", level="INFO") as captura:
            resposta = self.http.post("/auth/login", json={}, headers={"origin": self.config.origem})
        self.assertEqual(resposta.status_code, 400)
        self.assertTrue(any("motivo=tipo_conteudo_invalido" in l for l in captura.output))
        self.fabrica.assert_not_called()

    def test_logs_sem_segredos_ou_dados_pessoais(self):
        self.post(headers={"origin": self.config.origem, "referer": "https://bot.test/?email=PII_SENTINELA",
                           "user-agent": "UA_SENTINELA", "x-request-id": "ID_SENTINELA"})
        fluxo = set()
        for linha in self.logs:
            self.assertNotIn("SENTINELA", linha)
            self.assertNotIn("bot.test", linha)
            self.assertRegex(linha, r"^auth fluxo=\d+ etapa=[a-z]+ resultado=[a-z]+ motivo=[a-z_0-9]+$")
            fluxo.add(re.search(r"fluxo=(\d+)", linha).group(1))
        self.assertEqual(len(fluxo), 1)
        self.assertTrue(all("cookie=" not in l and "csrf=" not in l for l in self.logs))

    def test_logger_recusa_texto_dinamico(self):
        with fluxo_diagnostico(), self.assertRaises(ValueError):
            registrar("autenticacao", "falha", "DADO_SENTINELA")


if __name__ == "__main__":
    unittest.main()
