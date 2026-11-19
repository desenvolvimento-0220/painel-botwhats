"""Testes locais de cookies, CSRF, revogação, F5 e isolamento; sem Supabase real."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import logging
import os
from pathlib import Path
import re
import threading
import time
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from streamlit.testing.v1 import AppTest

from backend.gateway import ConfigGateway, criar_gateway
from backend.session_store import SessoesServidor, chave_sessao
from backend import session_runtime


def cliente_dono():
    cliente = MagicMock()
    sessao = SimpleNamespace(access_token="access-SIMULADO", refresh_token="refresh-SIMULADO")
    cliente.auth.get_session.return_value = sessao
    cliente.auth.refresh_session.return_value.session = sessao
    cliente.auth.get_user.return_value.user.id = "dono-simulado"
    cliente.rpc.return_value.execute.return_value.data = True
    return cliente


class TestRegistroServidor(unittest.TestCase):
    def setUp(self):
        self.agora = [100.0]
        self.store = SessoesServidor(duracao=100, inatividade=20, relogio=lambda: self.agora[0])
        self.cliente = cliente_dono()
        self.sid = self.store.criar(self.cliente)

    def test_cookie_opaco_e_hash_no_registro(self):
        self.assertEqual(len(self.sid), 43)
        self.assertIn(hashlib.sha256(self.sid.encode()).digest(), self.store._registros)
        self.assertNotIn(self.sid, self.store._registros)

    def test_identificador_invalido(self):
        for sid in (None, "", "alterado", "../" * 200, {}, 123):
            with self.subTest(tipo=type(sid).__name__):
                self.assertIsNone(chave_sessao(sid))
                self.assertFalse(self.store.validar(sid))

    def test_f5_nao_precisa_novo_login(self):
        self.assertTrue(self.store.validar(self.sid))
        self.assertTrue(self.store.validar(self.sid))
        self.cliente.auth.sign_in_with_password.assert_not_called()
        self.assertEqual(self.cliente.rpc.call_count, 2)

    def test_renovacao_forcada_valida_dono(self):
        self.assertTrue(self.store.validar(self.sid, renovar=True))
        self.cliente.auth.refresh_session.assert_called_once()
        self.cliente.rpc.assert_called_once_with("sou_dono")

    def test_revogacao_de_permissao(self):
        for valor in (False, None, 1, "true"):
            with self.subTest(valor=valor):
                sid = self.store.criar(self.cliente)
                self.cliente.rpc.return_value.execute.return_value.data = valor
                self.assertFalse(self.store.validar(sid))
                self.assertFalse(self.store.existe(sid))

    def test_falha_renovacao_revoga(self):
        self.cliente.auth.get_session.side_effect = RuntimeError("falha simulada")
        self.assertFalse(self.store.validar(self.sid))
        self.assertFalse(self.store.existe(self.sid))

    def test_usuario_invalido(self):
        self.cliente.auth.get_user.return_value = None
        self.assertFalse(self.store.validar(self.sid))
        self.cliente.rpc.assert_not_called()

    def test_expiracao_por_inatividade(self):
        self.agora[0] += 20
        self.assertFalse(self.store.validar(self.sid))

    def test_expiracao_absoluta_mesmo_com_atividade(self):
        for _ in range(9):
            self.agora[0] += 10
            self.assertTrue(self.store.validar(self.sid))
        self.agora[0] += 10
        self.assertFalse(self.store.validar(self.sid))

    def test_logout_offline_nao_ressuscita(self):
        self.cliente.auth.sign_out.side_effect = RuntimeError("offline")
        self.store.revogar(self.sid)
        self.assertFalse(self.store.validar(self.sid))
        self.cliente.auth.sign_out.assert_called_once_with({"scope": "local"})

    def test_logout_isola_outro_visitante(self):
        outro = cliente_dono()
        sid_b = self.store.criar(outro)
        self.store.revogar(self.sid)
        self.assertTrue(self.store.validar(sid_b))
        outro.auth.sign_out.assert_not_called()

    def test_reinicio_exige_login(self):
        self.assertFalse(SessoesServidor().validar(self.sid))

    def test_capacidade_limitada_e_limpeza(self):
        store = SessoesServidor(capacidade=1, inatividade=5, relogio=lambda: self.agora[0])
        store.criar(self.cliente)
        with self.assertRaises(RuntimeError):
            store.criar(cliente_dono())
        self.agora[0] += 5
        self.assertTrue(store.existe(store.criar(cliente_dono())))

    def test_multiplas_abas_serializam_refresh(self):
        concorrentes = [0, 0]
        lock = threading.Lock()

        def sessao():
            with lock:
                concorrentes[0] += 1
                concorrentes[1] = max(concorrentes)
            time.sleep(0.01)
            with lock:
                concorrentes[0] -= 1
            return SimpleNamespace(access_token="simulado")

        self.cliente.auth.get_session.side_effect = sessao
        with ThreadPoolExecutor(max_workers=4) as pool:
            resultados = list(pool.map(lambda _: self.store.validar(self.sid), range(8)))
        self.assertTrue(all(resultados))
        self.assertEqual(concorrentes[1], 1)


class TestHTTP(unittest.TestCase):
    def setUp(self):
        self.config = ConfigGateway("https://bot.test")
        self.store = SessoesServidor()
        self.clientes = []
        self.recebidos = []

        def login(email, senha):
            cliente = cliente_dono()
            self.clientes.append(cliente)
            return cliente, cliente.auth.get_session.return_value

        async def pagina(request):
            self.recebidos.append(dict(request.headers))
            return JSONResponse({"painel": True})

        async def ws(websocket):
            self.recebidos.append(dict(websocket.headers))
            await websocket.accept(subprotocol="streamlit")
            try:
                while True:
                    await websocket.send_text(await websocket.receive_text())
            except WebSocketDisconnect:
                pass

        self.login = MagicMock(side_effect=login)
        self.stub = Starlette(routes=[Route("/{path:path}", pagina), WebSocketRoute("/{path:path}", ws)])
        app = criar_gateway(self.config, self.store, self.login, self.stub)
        self.http = TestClient(app, base_url=self.config.origem, follow_redirects=False)
        self.http.__enter__()
        self.addCleanup(self.http.__exit__, None, None, None)

    def csrf(self, caminho="/auth/login"):
        resposta = self.http.get(caminho)
        self.assertEqual(resposta.status_code, 200)
        return re.search(r'name="csrf" value="([A-Za-z0-9_.-]+)"', resposta.text).group(1)

    def entrar(self):
        csrf = self.csrf()
        resposta = self.http.post("/auth/login", data={"email": "dono@teste.invalid", "senha": "simulada", "csrf": csrf},
                                  headers={"origin": self.config.origem})
        self.assertEqual(resposta.status_code, 303)
        return resposta

    def sair(self):
        csrf = self.csrf("/auth/logout")
        return self.http.post("/auth/logout", data={"csrf": csrf}, headers={"origin": self.config.origem})

    def test_flags_cookie_seguro(self):
        resposta = self.entrar()
        cookie = next(c for c in resposta.headers.get_list("set-cookie") if c.startswith(self.config.cookie + "="))
        for atributo in ("HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=28800"):
            self.assertIn(atributo, cookie)
        self.assertNotIn("Domain=", cookie)
        self.assertNotIn("access-SIMULADO", cookie)
        self.assertNotIn("refresh-SIMULADO", cookie)

    def test_f5_duas_requisicoes_mesmo_cookie(self):
        self.entrar()
        for _ in range(2):
            self.assertEqual(self.http.get("/").status_code, 200)
        self.assertEqual(self.login.call_count, 1)
        self.assertEqual(self.clientes[0].rpc.call_count, 2)

    def test_anonimo_e_cookie_adulterado_bloqueados(self):
        self.assertEqual(self.http.get("/").status_code, 303)
        self.http.cookies.set(self.config.cookie, "a" * 43)
        self.assertEqual(self.http.get("/").status_code, 303)

    def test_csrf_ausente_ou_invalido(self):
        for csrf in ("", "invalido", "não-ascii"):
            resposta = self.http.post("/auth/login", data={"csrf": csrf}, headers={"origin": self.config.origem})
            self.assertEqual(resposta.status_code, 403)
        self.login.assert_not_called()

    def test_csrf_expirado(self):
        with patch("backend.gateway.time.time", return_value=100):
            csrf = self.csrf()
        with patch("backend.gateway.time.time", return_value=701):
            resposta = self.http.post("/auth/login", data={"csrf": csrf}, headers={"origin": self.config.origem})
        self.assertEqual(resposta.status_code, 403)
        self.login.assert_not_called()

    def test_csrf_assinatura_falsa(self):
        csrf = "100." + "n" * 43 + "." + "0" * 64
        self.http.cookies.set(self.config.cookie_csrf, csrf)
        resposta = self.http.post("/auth/login", data={"csrf": csrf}, headers={"origin": self.config.origem})
        self.assertEqual(resposta.status_code, 403)

    def test_origin_e_host(self):
        for headers in ({"origin": "https://malicioso.test"}, {"host": "malicioso.test"}, {}):
            resposta = self.http.post("/auth/login", data={}, headers=headers)
            self.assertEqual(resposta.status_code, 403)
        self.login.assert_not_called()

    def test_falha_login_nao_expoe_excecao(self):
        self.login.side_effect = RuntimeError("senha-SENTINELA access-SENTINELA refresh-SENTINELA")
        csrf = self.csrf()
        resposta = self.http.post("/auth/login", data={"csrf": csrf}, headers={"origin": self.config.origem})
        self.assertEqual(resposta.status_code, 401)
        self.assertNotIn("SENTINELA", resposta.text)
        self.assertNotIn(self.config.cookie, resposta.cookies)

    def test_logout_revoga_e_apaga_cookie(self):
        self.entrar()
        antigo = self.http.cookies.get(self.config.cookie)
        resposta = self.sair()
        self.assertEqual(resposta.status_code, 303)
        self.assertFalse(self.store.existe(antigo))
        cookie = next(c for c in resposta.headers.get_list("set-cookie") if c.startswith(self.config.cookie + "="))
        self.assertIn("Max-Age=0", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertNotIn(self.config.cookie, self.http.cookies)
        self.http.cookies.set(self.config.cookie, antigo)
        self.assertEqual(self.http.get("/").status_code, 303)

    def test_logout_offline(self):
        self.entrar()
        self.clientes[0].auth.sign_out.side_effect = RuntimeError("offline")
        self.assertEqual(self.sair().status_code, 303)
        self.assertEqual(self.http.get("/").status_code, 303)

    def test_logout_get_nao_muda_estado_e_post_exige_csrf(self):
        self.entrar()
        self.http.get("/auth/logout")
        self.assertEqual(self.http.get("/").status_code, 200)
        self.assertEqual(self.http.post("/auth/logout", data={}, headers={"origin": self.config.origem,
            "content-type": "application/x-www-form-urlencoded"}).status_code, 403)
        self.assertEqual(self.http.get("/").status_code, 200)

    def test_novo_login_rotaciona_identificador(self):
        self.entrar()
        antigo = self.http.cookies.get(self.config.cookie)
        self.entrar()
        novo = self.http.cookies.get(self.config.cookie)
        self.assertTrue(antigo != novo)
        self.assertFalse(self.store.existe(antigo))
        self.assertTrue(self.store.existe(novo))

    def test_revogacao_permissao_bloqueia_media(self):
        self.entrar()
        self.clientes[0].rpc.return_value.execute.return_value.data = False
        self.assertEqual(self.http.get("/media/arquivo").status_code, 303)

    def test_header_visitante_nao_sobrescreve_cookie(self):
        self.entrar()
        self.http.get("/", headers={"x-botwhats-session": "forjado"})
        self.assertTrue(self.recebidos[-1]["x-botwhats-session"] != "forjado")
        resposta = self.http.get("/")
        self.assertNotIn(self.http.cookies.get(self.config.cookie), resposta.text)

    def test_websocket_reconecta_apos_f5(self):
        self.entrar()
        for _ in range(2):
            with self.http.websocket_connect("wss://bot.test/_stcore/stream", headers={"origin": self.config.origem},
                                             subprotocols=["streamlit", "xsrf", "sessao-antiga"]) as ws:
                ws.send_text("teste")
                self.assertEqual(ws.receive_text(), "teste")
        self.assertNotIn("sessao-antiga", self.recebidos[-1]["sec-websocket-protocol"])
        self.assertEqual(self.login.call_count, 1)

    def test_websocket_sem_cookie_ou_cross_origin(self):
        with self.assertRaises(WebSocketDisconnect):
            with self.http.websocket_connect("wss://bot.test/_stcore/stream", headers={"origin": self.config.origem}):
                pass
        self.entrar()
        with self.assertRaises(WebSocketDisconnect):
            with self.http.websocket_connect("wss://bot.test/_stcore/stream", headers={"origin": "https://malicioso.test"}):
                pass

    def test_websocket_aberto_nao_sobrevive_ao_logout(self):
        self.entrar()
        with self.http.websocket_connect("wss://bot.test/_stcore/stream", headers={"origin": self.config.origem},
                                         subprotocols=["streamlit"]) as ws:
            self.sair()
            ws.send_text("teste")
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_text()

    def test_headers_e_sem_tokens_em_urls(self):
        resposta = self.entrar()
        self.assertEqual(resposta.headers["location"], "/")
        for nome in ("cache-control", "referrer-policy", "strict-transport-security"):
            self.assertIn(nome, resposta.headers)
        pagina = self.http.get("/auth/login")
        for valor in ("access-SIMULADO", "refresh-SIMULADO", "localStorage", "sessionStorage"):
            self.assertNotIn(valor, pagina.text)
        self.assertIn("form-action 'self'", pagina.headers["content-security-policy"])

    def test_sem_segredos_em_logs(self):
        registros = []
        class Captura(logging.Handler):
            def emit(self, record):
                registros.append(record.getMessage())
        handler = Captura()
        logging.getLogger().addHandler(handler)
        try:
            self.login.side_effect = RuntimeError("senha-SENTINELA access-SENTINELA refresh-SENTINELA")
            csrf = self.csrf()
            self.http.post("/auth/login", data={"csrf": csrf}, headers={"origin": self.config.origem})
        finally:
            logging.getLogger().removeHandler(handler)
        self.assertFalse(any("SENTINELA" in mensagem for mensagem in registros))


class TestConfig(unittest.TestCase):
    def test_https_case_insensitive_continua_secure(self):
        self.assertTrue(ConfigGateway("HTTPS://bot.test").seguro)

    def test_recusa_http_fora_loopback(self):
        for origem in ("http://bot.test", "http://192.168.1.3"):
            with self.assertRaises(ValueError):
                ConfigGateway(origem, permitir_http_local=True)

    def test_http_local_exige_opcao(self):
        with self.assertRaises(ValueError):
            ConfigGateway("http://127.0.0.1:8501")
        config = ConfigGateway("http://127.0.0.1:8501", permitir_http_local=True)
        self.assertFalse(config.seguro)
        self.assertFalse(config.cookie.startswith("__Host-"))

    def test_cookie_http_local_preserva_httponly_e_samesite(self):
        config = ConfigGateway("http://127.0.0.1", permitir_http_local=True)
        async def pagina(request):
            return JSONResponse({"ok": True})
        cliente = cliente_dono()
        app = criar_gateway(config, autenticar=lambda e, s: (cliente, object()),
                            app_streamlit=Starlette(routes=[Route("/", pagina)]))
        with TestClient(app, base_url=config.origem, follow_redirects=False) as http:
            csrf = re.search(r'name="csrf" value="([A-Za-z0-9_.-]+)"', http.get("/auth/login").text).group(1)
            resposta = http.post("/auth/login", data={"csrf": csrf}, headers={"origin": config.origem})
            cookie = next(c for c in resposta.headers.get_list("set-cookie") if c.startswith(config.cookie + "="))
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=strict", cookie)
            self.assertNotIn("Secure", cookie)

    def test_origens_invalidas(self):
        for origem in ("https://user:pass@host", "https://host/rota", "https://host?query", "https://host/"):
            with self.assertRaises(ValueError):
                ConfigGateway(origem)


class TestInicializacao(unittest.TestCase):
    def executar(self, argumentos):
        import iniciar
        with patch.object(sys, "argv", ["iniciar.py"] + argumentos), \
             patch("iniciar.criar_gateway", return_value=object()), \
             patch("iniciar.uvicorn.run") as servidor, patch.dict(os.environ):
            iniciar.main()
            return servidor

    def test_http_local_explicito_loopback_sem_access_log(self):
        servidor = self.executar(["--origin", "http://127.0.0.1:8501", "--allow-http-local"])
        self.assertEqual(servidor.call_args.kwargs["host"], "127.0.0.1")
        self.assertIs(servidor.call_args.kwargs["access_log"], False)
        self.assertIs(servidor.call_args.kwargs["proxy_headers"], False)

    def test_https_sem_tls_recusado(self):
        with patch("sys.stderr"), self.assertRaises(SystemExit):
            self.executar(["--origin", "https://localhost:8501"])

    def test_proxy_tls_exige_opcao(self):
        servidor = self.executar(["--origin", "https://localhost:8501", "--behind-tls-proxy"])
        self.assertEqual(servidor.call_args.kwargs["host"], "127.0.0.1")

    def test_http_rede_recusado(self):
        with patch("sys.stderr"), self.assertRaises(SystemExit):
            self.executar(["--origin", "http://192.168.1.2:8501", "--allow-http-local"])


class TestIntegracaoASGI(unittest.TestCase):
    def test_st_app_real_reconecta(self):
        raiz = Path(__file__).resolve().parents[1]
        resultado = subprocess.run([sys.executable, str(raiz / "tests/smoke_asgi.py")],
                                   cwd=raiz, env=dict(os.environ, PYTHONPATH=str(raiz), PYTHONUTF8="1"),
                                   capture_output=True, text=True, encoding="utf-8", timeout=40)
        self.assertEqual(resultado.returncode, 0, "Integração ASGI falhou: " + resultado.stderr[-3000:])


class TestPainelPersistente(unittest.TestCase):
    def setUp(self):
        self.store = SessoesServidor()
        self.cliente = cliente_dono()
        self.sid = self.store.criar(self.cliente)
        self.contexto = SimpleNamespace(headers={"x-botwhats-session": self.sid})
        self.patches = [patch("backend.session.st.context", self.contexto),
                        patch("backend.session_runtime.sessoes", self.store),
                        patch("backend.auth.criar_cliente_supabase", side_effect=AssertionError("Remoto proibido"))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.app = self.nova_pagina()

    def nova_pagina(self):
        return AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=10)

    def test_f5_sem_session_state(self):
        self.app.run()
        self.assertEqual(len(self.app.radio), 1)
        nova = self.nova_pagina().run()
        self.assertFalse(nova.exception)
        self.assertEqual(len(nova.radio), 1)
        self.assertNotIn("supabase_sessao", nova.session_state)
        self.assertNotIn("supabase_cliente", nova.session_state)

    def test_quatro_paginas(self):
        self.app.run()
        opcoes = list(self.app.radio[0].options)
        self.assertEqual(len(opcoes), 4)
        titulos = ["Conexão do WhatsApp", "Regras de Resposta", "Envio de Mensagens em Massa",
                   "Configurações Visuais (White Label)"]
        for pagina, titulo in zip(opcoes, titulos):
            self.app.radio[0].set_value(pagina).run()
            self.assertFalse(self.app.exception)
            self.assertEqual(self.app.title[0].value, titulo)

    def test_sem_cookie_nao_confia_no_estado_antigo(self):
        self.app.session_state["supabase_cliente"] = self.cliente
        self.app.session_state["supabase_sessao"] = object()
        self.contexto.headers.clear()
        self.app.run()
        self.assertFalse(self.app.exception)
        self.assertEqual(len(self.app.radio), 0)

    def test_revogacao_dono_limpa_rascunhos(self):
        self.app.run()
        self.app.session_state["rascunho"] = "privado"
        self.cliente.rpc.return_value.execute.return_value.data = False
        self.app.run()
        self.assertFalse(self.app.exception)
        self.assertEqual(len(self.app.radio), 0)
        self.assertNotIn("rascunho", self.app.session_state)

    def test_logout_nao_restaura_com_f5(self):
        self.app.run()
        self.store.revogar(self.sid)
        self.assertEqual(len(self.nova_pagina().run().radio), 0)

    def test_troca_visitante_nao_herda_estado(self):
        self.app.run()
        self.app.session_state["rascunho"] = "privado"
        self.contexto.headers["x-botwhats-session"] = self.store.criar(cliente_dono())
        self.app.run()
        self.assertFalse(self.app.exception)
        self.assertEqual(len(self.app.radio), 1)
        self.assertNotIn("rascunho", self.app.session_state)

    def test_sem_gateway_falha_fechado(self):
        with patch("backend.session_runtime.sessoes", None):
            self.app.run()
        self.assertFalse(self.app.exception)
        self.assertEqual(len(self.app.radio), 0)
        self.assertIn("iniciar.py", self.app.error[0].value)

    def test_sair_aponta_para_endpoint_http(self):
        self.app.run()
        links = self.app.get("link_button")
        self.assertTrue(any(link.proto.label == "Sair" and link.proto.url == "/auth/logout" for link in links))


if __name__ == "__main__":
    unittest.main()
