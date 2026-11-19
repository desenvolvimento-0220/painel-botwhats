"""Regressão de POST nativo no Chrome/Edge headless, perfil descartável."""

import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
from urllib.request import build_opener, ProxyHandler

import uvicorn
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.routing import Route
from websockets.sync.client import connect

from backend.gateway import ConfigGateway, criar_gateway
from backend.session_store import SessoesServidor


NAVEGADOR = next((p for p in (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
) if p.is_file()), None)


class CDP:
    def __init__(self, conexao):
        self.conexao = conexao
        self.id = 0

    def chamar(self, metodo, params=None):
        self.id += 1
        self.conexao.send(json.dumps({"id": self.id, "method": metodo, "params": params or {}}))
        limite = time.monotonic() + 10
        while time.monotonic() < limite:
            mensagem = json.loads(self.conexao.recv(timeout=10))
            if mensagem.get("id") == self.id:
                return mensagem.get("result", {})
        raise TimeoutError("Navegador de teste não respondeu.")

    def avaliar(self, expressao):
        resultado = self.chamar("Runtime.evaluate", {"expression": expressao, "returnByValue": True})
        return resultado.get("result", {}).get("value")


@unittest.skipUnless(NAVEGADOR, "Chrome/Edge não instalado; executar homologação manual do formulário")
class TestFormularioNativo(unittest.TestCase):
    def test_no_referrer_reproduz_null_e_politica_corrigida_envia_origem(self):
        observado = []  # Apenas classificações fixas e status; nunca headers/cookies brutos.
        autenticar = MagicMock(return_value=(MagicMock(), object()))
        store = SessoesServidor()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        porta = sock.getsockname()[1]
        origem = f"http://127.0.0.1:{porta}"

        async def painel(request):
            return Response("Painel simulado")
        app = criar_gateway(ConfigGateway(origem, permitir_http_local=True), store, autenticar,
                            Starlette(routes=[Route("/", painel)]))

        class Observar:
            async def __call__(self, scope, receive, send):
                eh_post = scope["type"] == "http" and scope["path"] == "/auth/login" and scope["method"] == "POST"
                if eh_post:
                    recebido = Headers(scope=scope).get("origin")
                    classificacao = "nula" if recebido == "null" else "correta" if recebido == origem else "outra"
                async def enviar(mensagem):
                    if eh_post and mensagem["type"] == "http.response.start":
                        observado.append((classificacao, mensagem["status"]))
                    await send(mensagem)
                await app(scope, receive, enviar)

        server = uvicorn.Server(uvicorn.Config(Observar(), access_log=False, log_level="critical", lifespan="off"))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        browser = None
        try:
            limite = time.monotonic() + 10
            while not server.started and time.monotonic() < limite:
                time.sleep(0.05)
            self.assertTrue(server.started)
            with tempfile.TemporaryDirectory(prefix="botwhats-browser-") as perfil:
                self.assertEqual(Path(perfil).resolve().parent, Path(tempfile.gettempdir()).resolve())
                browser = subprocess.Popen([
                    str(NAVEGADOR), "--headless=new", "--no-first-run", "--no-default-browser-check",
                    "--disable-background-networking", "--disable-sync", "--disable-extensions",
                    "--disable-component-update", "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
                    "--user-data-dir=" + perfil, "about:blank",
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                try:
                    arquivo = Path(perfil) / "DevToolsActivePort"
                    limite = time.monotonic() + 15
                    while not arquivo.is_file() and time.monotonic() < limite:
                        time.sleep(0.05)
                    self.assertTrue(arquivo.is_file(), "Chrome headless não iniciou")
                    porta_cdp = int(arquivo.read_text().splitlines()[0])
                    opener = build_opener(ProxyHandler({}))
                    with opener.open(f"http://127.0.0.1:{porta_cdp}/json/list", timeout=5) as resposta:
                        paginas = json.load(resposta)
                    alvo = next(p for p in paginas if p["type"] == "page")
                    with connect(alvo["webSocketDebuggerUrl"], proxy=None, open_timeout=5, close_timeout=2) as ws:
                        cdp = CDP(ws)
                        def enviar_formulario(adulterar=False):
                            cdp.chamar("Page.navigate", {"url": origem + "/auth/login"})
                            limite = time.monotonic() + 10
                            while time.monotonic() < limite:
                                if cdp.avaliar("document.readyState === 'complete' && !!document.querySelector('form input[name=email]')"):
                                    break
                                time.sleep(0.05)
                            else:
                                self.fail("Formulário não carregou")
                            anterior = len(observado)
                            js = "document.querySelector('[name=email]').value='teste@exemplo.invalid';"
                            js += "document.querySelector('[name=senha]').value='simulada';"
                            if adulterar:
                                js += "document.querySelector('[name=csrf]').value='invalido';"
                            cdp.avaliar(js + "document.querySelector('form').requestSubmit(); true")
                            limite = time.monotonic() + 10
                            while len(observado) == anterior and time.monotonic() < limite:
                                time.sleep(0.05)
                            self.assertGreater(len(observado), anterior, "POST nativo não chegou")
                            return observado[-1]

                        with patch("backend.gateway.POLITICA_REFERENCIA", "no-referrer"):
                            self.assertEqual(enviar_formulario(), ("nula", 403))
                        autenticar.assert_not_called()
                        self.assertEqual(enviar_formulario(), ("correta", 303))
                        autenticar.assert_called_once()
                        self.assertEqual(enviar_formulario(adulterar=True), ("correta", 403))
                        autenticar.assert_called_once()
                finally:
                    browser.terminate()
                    browser.wait(timeout=10)
                    browser = None
        finally:
            if browser is not None:
                browser.kill()
                browser.wait(timeout=5)
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()


if __name__ == "__main__":
    unittest.main()
