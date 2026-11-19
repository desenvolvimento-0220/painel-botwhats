"""Etapa 57: relógios simulados, SDK com transporte falso e gate ASGI real."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import httpx
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect
from streamlit.proto.BackMsg_pb2 import BackMsg
from supabase import ClientOptions, create_client
from supabase_auth._sync.gotrue_client import EXPIRY_MARGIN

from backend.gateway import ConfigGateway, SessaoHTTP, criar_gateway, silenciar_logs_sensiveis
from backend.session_store import SessoesServidor


class Relogio:
    def __init__(self):
        self.mono = 100.0
        self.wall = 1_800_000_000.0

    def avancar(self, segundos):
        self.mono += segundos
        self.wall += segundos

    def __call__(self):
        return self.mono


def dono():
    cliente = MagicMock()
    cliente.auth.get_session.return_value = SimpleNamespace(access_token="SIMULADO")
    cliente.auth.refresh_session.return_value.session = cliente.auth.get_session.return_value
    cliente.auth.get_user.return_value.user.id = "dono-simulado"
    cliente.rpc.return_value.execute.return_value.data = True
    return cliente


class TestPrazosRegistro(unittest.TestCase):
    def setUp(self):
        self.clock = Relogio()
        self.store = SessoesServidor(relogio=self.clock)
        self.cliente = dono()
        self.sid = self.store.criar(self.cliente)

    def test_padrao_trinta_minutos_e_oito_horas(self):
        self.assertEqual(self.store.inatividade, 1800)
        self.assertEqual(self.store.duracao, 28800)

    def test_limite_exato_inatividade(self):
        for intervalo, esperado in ((1799.999, True), (1800, False), (1800.001, False)):
            with self.subTest(segundos=intervalo):
                clock = Relogio()
                store = SessoesServidor(relogio=clock)
                sid = store.criar(dono())
                clock.avancar(intervalo)
                self.assertIs(store.validar(sid), esperado)

    def test_limite_absoluto_com_atividade_continua(self):
        inicio = self.clock.mono
        while self.clock.mono - inicio + 1700 < 28800:
            self.clock.avancar(1700)
            self.assertTrue(self.store.validar(self.sid))
        self.clock.avancar(28800 - (self.clock.mono - inicio) - 0.001)
        self.assertTrue(self.store.validar(self.sid))
        self.clock.avancar(0.001)
        self.assertFalse(self.store.validar(self.sid))

    def test_atividade_reinicia_apenas_prazo_ocioso(self):
        limite = self.store._obter(self.sid).limite
        self.clock.avancar(1799)
        self.assertTrue(self.store.validar(self.sid))
        self.clock.avancar(1799)
        self.assertTrue(self.store.validar(self.sid))
        self.assertEqual(self.store._obter(self.sid).limite, limite)

    def test_sondagem_nao_prolonga_inatividade(self):
        self.clock.avancar(1799)
        self.assertTrue(self.store.validar(self.sid, atividade=False))
        self.clock.avancar(1)
        self.assertFalse(self.store.validar(self.sid))

    def test_refresh_forcado_nao_prolonga_inatividade(self):
        self.clock.avancar(1799)
        self.assertTrue(self.store.validar(self.sid, renovar=True))
        self.clock.avancar(1)
        self.assertFalse(self.store.validar(self.sid))

    def test_existe_expirado_remove_sem_io(self):
        self.clock.avancar(1800)
        self.assertFalse(self.store.existe(self.sid))
        self.assertIsNone(self.store._obter(self.sid))
        self.cliente.auth.get_session.assert_not_called()
        self.cliente.auth.sign_out.assert_not_called()

    def test_id_expirado_descartado_nao_reaparece(self):
        self.clock.avancar(1800)
        self.assertFalse(self.store.existe(self.sid))
        # Nem um relógio de teste defeituoso pode ressuscitar o registro removido.
        self.clock.mono = 100
        self.assertFalse(self.store.validar(self.sid))

    def test_relogio_civil_nao_controla_prazo_local(self):
        self.clock.wall += 1000000
        self.assertTrue(self.store.existe(self.sid))
        self.clock.wall -= 2000000
        self.clock.mono += 1800
        self.assertFalse(self.store.existe(self.sid))

    def test_validacao_que_cruza_prazo_nao_reabre_sessao(self):
        self.clock.avancar(1799)
        def remoto(jwt):
            self.clock.avancar(2)
            return SimpleNamespace(user=object())
        self.cliente.auth.get_user.side_effect = remoto
        self.assertFalse(self.store.validar(self.sid))
        self.assertIsNone(self.store._obter(self.sid))

    def test_logout_idempotente_e_irreversivel(self):
        self.store.revogar(self.sid)
        self.store.revogar(self.sid)
        self.assertFalse(self.store.validar(self.sid))
        self.cliente.auth.sign_out.assert_called_once_with({"scope": "local"})

    def test_logout_offline_nao_afeta_outro_visitante(self):
        outro = dono()
        sid_b = self.store.criar(outro)
        self.cliente.auth.sign_out.side_effect = RuntimeError("falha simulada")
        self.store.revogar(self.sid)
        self.assertFalse(self.store.validar(self.sid))
        self.assertTrue(self.store.validar(sid_b))
        outro.auth.sign_out.assert_not_called()

    def test_logout_retira_id_antes_de_aguardar_validacao_concorrente(self):
        entrou, liberar = threading.Event(), threading.Event()
        def remoto(jwt):
            entrou.set()
            if not liberar.wait(2):
                raise RuntimeError("timeout simulado")
            return SimpleNamespace(user=object())
        self.cliente.auth.get_user.side_effect = remoto
        with ThreadPoolExecutor(max_workers=2) as pool:
            validacao = pool.submit(self.store.validar, self.sid)
            self.assertTrue(entrou.wait(1))
            saida = pool.submit(self.store.revogar, self.sid)
            try:
                limite = time.monotonic() + 1
                while self.store.existe(self.sid) and time.monotonic() < limite:
                    time.sleep(0.005)
                self.assertFalse(self.store.existe(self.sid))
            finally:
                liberar.set()
            self.assertFalse(validacao.result(timeout=2))
            saida.result(timeout=2)
        self.cliente.auth.sign_out.assert_called_once_with({"scope": "local"})


class TestSDKRenovacao(unittest.TestCase):
    """SDK Supabase real, relógio civil simulado e httpx.MockTransport apenas."""
    def setUp(self):
        silenciar_logs_sensiveis()
        self.clock = Relogio()
        self.refreshes = 0
        self.geracao = 0
        self.refresh_negado = False
        self.usuario_negado = False
        self.dono = True
        self.falha_rede = False
        self.headers_atualizados = []  # Somente booleanos, nunca tokens/headers.
        self.refresh_atualizado = []
        self.user = {"id": "00000000-0000-0000-0000-000000000001", "aud": "authenticated",
                     "created_at": "2026-01-01T00:00:00Z", "app_metadata": {}, "user_metadata": {}}
        p = patch("supabase_auth._sync.gotrue_client.time.time", side_effect=lambda: self.clock.wall)
        p.start()
        self.addCleanup(p.stop)
        self.transporte = httpx.Client(transport=httpx.MockTransport(self.responder), trust_env=False)
        self.addCleanup(self.transporte.close)
        self.cliente = create_client("https://supabase.invalid", "sb_publishable_simulada", options=ClientOptions(
            auto_refresh_token=False, httpx_client=self.transporte,
        ))
        self.cliente.auth.sign_in_with_password({"email": "simulado@teste.invalid", "password": "simulada"})
        self.store = SessoesServidor(relogio=self.clock)
        self.sid = self.store.criar(self.cliente)

    def responder(self, request):
        if request.url.path == "/auth/v1/token":
            if request.url.params.get("grant_type") == "refresh_token":
                self.refreshes += 1
                atual = json.loads(request.content).get("refresh_token") == f"refresh-simulado-{self.geracao}"
                self.refresh_atualizado.append(atual)
                if not atual:
                    return httpx.Response(400, json={"code": "refresh_token_not_found", "msg": "Recusado (simulação)"})
                if self.falha_rede:
                    raise httpx.ConnectError("Indisponível no transporte simulado")
                if self.refresh_negado:
                    return httpx.Response(400, json={"code": "refresh_token_not_found", "msg": "Revogado (simulação)"})
                self.geracao += 1
            return httpx.Response(200, json={
                "access_token": f"access-simulado-{self.geracao}",
                "refresh_token": f"refresh-simulado-{self.geracao}",
                "token_type": "bearer", "expires_in": 3600,
                "expires_at": int(self.clock.wall + 3600), "user": self.user,
            })
        if request.url.path == "/auth/v1/user":
            if self.usuario_negado:
                return httpx.Response(401, json={"code": "bad_jwt", "msg": "Recusado (simulação)"})
            self.headers_atualizados.append(request.headers.get("authorization") == f"Bearer access-simulado-{self.geracao}")
            return httpx.Response(200, json=self.user)
        if request.url.path == "/rest/v1/rpc/sou_dono":
            self.headers_atualizados.append(request.headers.get("authorization") == f"Bearer access-simulado-{self.geracao}")
            return httpx.Response(200, json=self.dono)
        if request.url.path == "/auth/v1/logout":
            return httpx.Response(204)
        raise AssertionError("Rota inesperada no transporte simulado")

    def perto_da_expiracao(self, restante=11):
        for intervalo in (1700, 1700, 200 - restante):
            self.clock.avancar(intervalo)
            self.assertTrue(self.store.validar(self.sid))

    def test_renova_antes_da_expiracao_com_margem_do_sdk(self):
        self.assertEqual(EXPIRY_MARGIN, 10)
        self.perto_da_expiracao()
        self.assertEqual(self.refreshes, 0)
        self.clock.avancar(1)
        self.assertTrue(self.store.validar(self.sid))
        self.assertEqual(self.refreshes, 1)
        self.assertTrue(all(self.headers_atualizados))

    def test_rotacao_e_reutilizacao_da_sessao_atualizada(self):
        self.perto_da_expiracao()
        self.clock.avancar(1)
        self.assertTrue(self.store.validar(self.sid))
        self.assertTrue(self.store.validar(self.sid))
        self.assertEqual(self.refreshes, 1)
        self.assertTrue(all(self.headers_atualizados))
        self.perto_da_expiracao()
        self.clock.avancar(1)
        self.assertTrue(self.store.validar(self.sid))
        self.assertEqual(self.refreshes, 2)
        self.assertTrue(all(self.refresh_atualizado))

    def test_renovacoes_concorrentes_usam_uma_rotacao(self):
        self.perto_da_expiracao()
        self.clock.avancar(1)
        with ThreadPoolExecutor(max_workers=8) as pool:
            resultados = list(pool.map(lambda _: self.store.validar(self.sid), range(8)))
        self.assertTrue(all(resultados))
        self.assertEqual(self.refreshes, 1)

    def test_falha_na_renovacao_nao_produz_logs(self):
        self.perto_da_expiracao()
        self.falha_rede = True
        self.clock.avancar(1)
        with self.assertNoLogs(level="DEBUG"):
            self.assertFalse(self.store.validar(self.sid))

    def test_renovacao_nao_muda_limite_absoluto(self):
        limite = self.store._obter(self.sid).limite
        self.perto_da_expiracao()
        self.clock.avancar(1)
        self.assertTrue(self.store.validar(self.sid))
        self.assertEqual(self.store._obter(self.sid).limite, limite)

    def test_refresh_revogado_bloqueia_definitivamente(self):
        self.perto_da_expiracao()
        self.refresh_negado = True
        self.clock.avancar(1)
        self.assertFalse(self.store.validar(self.sid))
        self.refresh_negado = False
        self.assertFalse(self.store.validar(self.sid))

    def test_refresh_indisponivel_fecha_acesso(self):
        self.perto_da_expiracao()
        self.falha_rede = True
        self.clock.avancar(1)
        self.assertFalse(self.store.validar(self.sid))
        self.assertFalse(self.store.existe(self.sid))

    def test_access_token_recusado_bloqueia_sem_refresh(self):
        self.usuario_negado = True
        self.assertFalse(self.store.validar(self.sid))
        self.assertEqual(self.refreshes, 0)

    def test_permissao_retirada_apos_refresh_bloqueia(self):
        self.perto_da_expiracao()
        self.dono = False
        self.clock.avancar(1)
        self.assertFalse(self.store.validar(self.sid))
        self.assertGreaterEqual(self.refreshes, 1)

    def test_refresh_revogado_pode_ser_detectado_so_na_renovacao(self):
        self.refresh_negado = True
        self.assertTrue(self.store.validar(self.sid))  # Auth ainda aceita o JWT atual.
        self.perto_da_expiracao()
        self.clock.avancar(1)
        self.assertFalse(self.store.validar(self.sid))

    def test_expiracao_local_impede_refresh(self):
        self.clock.avancar(3600)
        self.assertFalse(self.store.existe(self.sid))
        self.assertFalse(self.store.validar(self.sid))
        self.assertEqual(self.refreshes, 0)

    def test_validacao_expirada_nao_renova_token_para_fazer_cleanup(self):
        self.clock.avancar(3600)
        self.assertFalse(self.store.validar(self.sid))
        self.assertEqual(self.refreshes, 0)


class TestHTTPPrazos(unittest.TestCase):
    def setUp(self):
        self.clock = Relogio()
        self.store = SessoesServidor(relogio=self.clock)
        self.cliente = dono()
        self.sid = self.store.criar(self.cliente)
        self.config = ConfigGateway("https://bot.test")
        async def pagina(request):
            return Response("ok")
        self.app = criar_gateway(self.config, self.store, app_streamlit=Starlette(routes=[Route("/{path:path}", pagina)]))
        self.http = TestClient(self.app, base_url=self.config.origem, follow_redirects=False)
        self.http.__enter__()
        self.addCleanup(self.http.__exit__, None, None, None)
        self.http.cookies.set(self.config.cookie, self.sid)

    def test_http_bloqueia_inatividade_e_apaga_cookie(self):
        self.clock.avancar(1800)
        resposta = self.http.get("/")
        self.assertEqual(resposta.status_code, 303)
        self.assertEqual(resposta.headers["location"], "/auth/login")
        self.assertIn("Max-Age=0", resposta.headers["set-cookie"])
        self.assertIsNone(self.store._obter(self.sid))

    def test_http_bloqueia_oito_horas_com_atividade(self):
        for _ in range(16):
            self.clock.avancar(1700)
            self.assertEqual(self.http.get("/").status_code, 200)
        self.clock.avancar(1600)
        self.assertEqual(self.http.get("/").status_code, 303)

    def test_health_nao_mantem_sessao_viva(self):
        for _ in range(5):
            self.clock.avancar(300)
            self.assertEqual(self.http.get("/_stcore/health").status_code, 200)
        self.clock.avancar(300)
        self.assertEqual(self.http.get("/").status_code, 303)

    def test_replay_de_cookie_revogado_nao_restaura(self):
        self.store.revogar(self.sid)
        for _ in range(2):
            self.http.cookies.set(self.config.cookie, self.sid)
            self.assertEqual(self.http.get("/").status_code, 303)


class TestWebSocketPrazos(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = Relogio()
        self.store = SessoesServidor(relogio=self.clock)
        self.cliente = dono()
        self.sid = self.store.criar(self.cliente)
        self.tarefas = []
        self.recebidas = []
        p = patch("backend.gateway.INTERVALO_VERIFICACAO_WEBSOCKET", 0.01)
        p.start()
        self.addCleanup(p.stop)

    async def asyncTearDown(self):
        for tarefa in self.tarefas:
            tarefa.cancel()
        await asyncio.gather(*self.tarefas, return_exceptions=True)

    async def abrir(self, sid=None, app=None, send_hook=None):
        entrada, saida = asyncio.Queue(), asyncio.Queue()
        async def enviar(mensagem):
            if send_hook:
                await send_hook(mensagem)
            await saida.put(mensagem)
        async def echo(scope, receive, send):
            ws = WebSocket(scope, receive, send)
            await ws.accept(subprotocol="streamlit")
            try:
                while True:
                    msg = await ws.receive()
                    if msg["type"] == "websocket.disconnect":
                        return
                    self.recebidas.append(True)
                    if msg.get("bytes") is not None:
                        await ws.send_bytes(msg["bytes"])
                    else:
                        await ws.send_text(msg["text"])
            except WebSocketDisconnect:
                pass
        config = ConfigGateway("https://bot.test")
        gateway = SessaoHTTP(app or echo, config, self.store)
        scope = {"type": "websocket", "path": "/_stcore/stream", "scheme": "wss", "query_string": b"",
                 "headers": [(b"host", b"bot.test"), (b"origin", b"https://bot.test"),
                             (b"sec-websocket-protocol", b"streamlit"),
                             (b"cookie", (config.cookie + "=" + (sid or self.sid)).encode())],
                 "subprotocols": ["streamlit"]}
        await entrada.put({"type": "websocket.connect"})
        tarefa = asyncio.create_task(gateway(scope, entrada.get, enviar))
        self.tarefas.append(tarefa)
        return entrada, saida

    async def mensagem(self, saida):
        return await asyncio.wait_for(saida.get(), timeout=1)

    async def test_handshake_expirado_bloqueado(self):
        self.clock.avancar(1800)
        _, saida = await self.abrir()
        self.assertEqual((await self.mensagem(saida))["type"], "websocket.close")
        self.cliente.auth.get_session.assert_not_called()

    async def test_socket_ocioso_fecha_ao_expirar_sem_nova_mensagem(self):
        _, saida = await self.abrir()
        self.assertEqual((await self.mensagem(saida))["type"], "websocket.accept")
        chamadas = self.cliente.auth.get_session.call_count
        self.clock.avancar(1800)
        self.assertEqual((await self.mensagem(saida))["code"], 1008)
        self.assertEqual(self.cliente.auth.get_session.call_count, chamadas)

    async def test_socket_fecha_no_limite_absoluto(self):
        _, saida = await self.abrir()
        await self.mensagem(saida)
        for _ in range(16):
            self.clock.avancar(1700)
            self.assertTrue(self.store.validar(self.sid))
        self.clock.avancar(1600)
        self.assertEqual((await self.mensagem(saida))["code"], 1008)

    async def test_mensagem_valida_antes_do_prazo_registra_atividade(self):
        entrada, saida = await self.abrir()
        await self.mensagem(saida)
        self.clock.avancar(1799)
        await entrada.put({"type": "websocket.receive", "text": "teste"})
        self.assertEqual((await self.mensagem(saida))["text"], "teste")
        self.clock.avancar(1799)
        self.assertTrue(self.store.existe(self.sid))

    async def test_heartbeat_nao_renova_nem_prolonga(self):
        entrada, saida = await self.abrir()
        await self.mensagem(saida)
        chamadas = self.cliente.auth.get_session.call_count
        back = BackMsg(app_heartbeat=True)
        self.clock.avancar(1799)
        await entrada.put({"type": "websocket.receive", "bytes": back.SerializeToString()})
        self.assertEqual((await self.mensagem(saida))["type"], "websocket.send")
        self.assertEqual(self.cliente.auth.get_session.call_count, chamadas)
        self.clock.avancar(1)
        self.assertEqual((await self.mensagem(saida))["code"], 1008)

    async def test_mensagem_com_dono_revogado_nao_chega_ao_app(self):
        entrada, saida = await self.abrir()
        await self.mensagem(saida)
        self.cliente.rpc.return_value.execute.return_value.data = False
        await entrada.put({"type": "websocket.receive", "text": "teste"})
        self.assertEqual((await self.mensagem(saida))["code"], 1008)
        self.assertFalse(self.recebidas)

    async def test_falha_refresh_durante_socket_fecha_acesso(self):
        entrada, saida = await self.abrir()
        await self.mensagem(saida)
        self.cliente.auth.get_session.side_effect = RuntimeError("recusado na simulação")
        await entrada.put({"type": "websocket.receive", "text": "teste"})
        self.assertEqual((await self.mensagem(saida))["code"], 1008)
        self.assertFalse(self.recebidas)

    async def test_logout_fecha_socket_ocioso_sem_afetar_outro(self):
        _, saida_a = await self.abrir()
        await self.mensagem(saida_a)
        sid_b = self.store.criar(dono())
        entrada_b, saida_b = await self.abrir(sid_b)
        await self.mensagem(saida_b)
        self.store.revogar(self.sid)
        self.assertEqual((await self.mensagem(saida_a))["code"], 1008)
        await entrada_b.put({"type": "websocket.receive", "text": "outro"})
        self.assertEqual((await self.mensagem(saida_b))["text"], "outro")

    async def test_envio_apos_expiracao_bloqueado(self):
        async def push(scope, receive, send):
            ws = WebSocket(scope, receive, send)
            await ws.accept()
            self.clock.avancar(1800)
            await ws.send_text("nao deve sair")
        _, saida = await self.abrir(app=push)
        self.assertEqual((await self.mensagem(saida))["type"], "websocket.accept")
        self.assertEqual((await self.mensagem(saida))["type"], "websocket.close")

    async def test_envio_aguardando_lock_reconfere_prazo(self):
        primeira_entrou, liberar, segunda_entrou = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def hook(mensagem):
            if mensagem.get("text") == "primeira":
                primeira_entrou.set()
                await liberar.wait()
        async def push(scope, receive, send):
            ws = WebSocket(scope, receive, send)
            await ws.accept()
            primeira = asyncio.create_task(ws.send_text("primeira"))
            await primeira_entrou.wait()
            async def enviar_segunda():
                segunda_entrou.set()
                await ws.send_text("segunda")
            segunda = asyncio.create_task(enviar_segunda())
            await segunda_entrou.wait()
            await asyncio.sleep(0)
            self.clock.avancar(1800)
            liberar.set()
            await asyncio.gather(primeira, segunda)
        # Demonstra o gate sob lock, independentemente do monitor periódico.
        with patch("backend.gateway.INTERVALO_VERIFICACAO_WEBSOCKET", 100):
            _, saida = await self.abrir(app=push, send_hook=hook)
            self.assertEqual((await self.mensagem(saida))["type"], "websocket.accept")
            self.assertEqual((await self.mensagem(saida))["text"], "primeira")
            self.assertEqual((await self.mensagem(saida))["type"], "websocket.close")


if __name__ == "__main__":
    unittest.main()
