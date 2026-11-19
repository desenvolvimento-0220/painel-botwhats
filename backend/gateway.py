"""Rotas e middleware ASGI nativos: cookies HttpOnly e sessões no servidor."""

from dataclasses import dataclass, field
import asyncio
import hashlib
import hmac
from html import escape
import ipaddress
import logging
import secrets
import time
from urllib.parse import parse_qs, urlsplit

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route, Mount

from backend.auth import autenticar_dono
from backend.session_store import SessoesServidor, sair_cliente
from backend.auth_diagnostics import fluxo_diagnostico, registrar


# no-referrer pode tornar Origin opaco em POSTs nativos de formulário.
POLITICA_REFERENCIA = "strict-origin-when-cross-origin"
INTERVALO_VERIFICACAO_WEBSOCKET = 1.0


def heartbeat_websocket(mensagem):
    """Heartbeat do Streamlit não é interação e não mantém o login vivo."""
    dados = mensagem.get("bytes")
    if dados is None:
        return False
    from google.protobuf.message import DecodeError
    from streamlit.proto.BackMsg_pb2 import BackMsg
    try:
        back = BackMsg()
        back.ParseFromString(dados)
        return back.WhichOneof("type") == "app_heartbeat"
    except DecodeError:
        return False


def motivo_origem_invalida(headers, config, mutacao):
    """Sem fallback por Referer/Fetch-Metadata e sem aceitar Origin: null."""
    hosts = headers.getlist("host")
    if not hosts:
        return "host_ausente"
    if len(hosts) != 1:
        return "host_duplicado"
    if hosts[0].lower() != urlsplit(config.origem).netloc.lower():
        return "host_divergente"
    origens = headers.getlist("origin")
    if len(origens) > 1:
        return "origem_duplicada"
    if not origens:
        return "origem_ausente" if mutacao else None
    if origens[0] == "null":
        return "origem_nula"
    if origens[0] != config.origem:
        return "origem_divergente"
    return None


def cabecalhos_seguranca(autenticacao, seguro):
    headers = {"cache-control": "no-store", "pragma": "no-cache",
               "referrer-policy": POLITICA_REFERENCIA, "x-content-type-options": "nosniff",
               "x-frame-options": "DENY"}
    if autenticacao:
        headers["content-security-policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
    if seguro:
        headers["strict-transport-security"] = "max-age=31536000"
    return headers


class SessaoHTTP:
    """Protege HTTP e WebSocket antes que qualquer rota Streamlit seja executada."""
    def __init__(self, app, config, sessoes):
        self.app, self.config, self.sessoes = app, config, sessoes

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] in ("/auth/login", "/auth/logout"):
            operacao = {("/auth/login", "GET"): "login_get", ("/auth/login", "POST"): "login_post",
                        ("/auth/logout", "GET"): "logout_get", ("/auth/logout", "POST"): "logout_post"}
            with fluxo_diagnostico():
                registrar("requisicao", "iniciado", operacao.get((scope["path"], scope["method"]), "outro_metodo"))
                return await self._executar(scope, receive, send)
        return await self._executar(scope, receive, send)

    async def _executar(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        mutacao = scope["type"] == "websocket" or scope.get("method") not in ("GET", "HEAD")
        motivo = motivo_origem_invalida(headers, self.config, mutacao)
        if motivo:
            registrar("origem", "negado", motivo)
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            return await Response(status_code=403, headers=cabecalhos_seguranca(
                scope["path"].startswith("/auth/"), self.config.seguro))(scope, receive, send)
        registrar("origem", "aprovado")
        if scope["type"] == "http" and scope["path"] in ("/auth/login", "/auth/logout"):
            return await self.app(scope, receive, send)
        request = Request(dict(scope, type="http"))
        sid = request.cookies.get(self.config.cookie, "")
        autorizado = self.sessoes.existe(sid)
        if autorizado and not scope["path"].startswith("/static/"):
            # Handshake, health e host-config não prolongam uma sessão ociosa.
            atividade = (scope["type"] == "http" and (
                not scope["path"].startswith("/_stcore/")
                or scope["path"].startswith("/_stcore/upload_file")))
            autorizado = await run_in_threadpool(self.sessoes.validar, sid, atividade=atividade)
        if not autorizado:
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            resposta = RedirectResponse("/auth/login", status_code=303)
            resposta.delete_cookie(self.config.cookie, path="/", httponly=True,
                                   secure=self.config.seguro, samesite="strict")
            resposta.headers["cache-control"] = "no-store"
            return await resposta(scope, receive, send)

        # Remove identidade fornecida pelo cliente; apenas o cookie opaco é aceito.
        novos = [(k, v) for k, v in scope["headers"] if k.lower() != b"x-botwhats-session"]
        novos.append((b"x-botwhats-session", sid.encode("ascii")))
        scope = dict(scope, headers=novos)
        if scope["type"] == "websocket":
            # Nunca restaurar um AppSession antigo de outro login/visitante.
            # A identidade persistente é do gateway, não do protocolo Streamlit.
            protocolos = headers.get("sec-websocket-protocol", "").split(",")[:2]
            novos = [(k, v) for k, v in novos if k.lower() != b"sec-websocket-protocol"]
            if protocolos and protocolos[0]:
                novos.append((b"sec-websocket-protocol", ",".join(protocolos).encode("ascii")))
            scope = dict(scope, headers=novos, subprotocols=[p.strip() for p in protocolos if p.strip()])
            fechado = False
            lock_envio = asyncio.Lock()

            async def encerrar():
                nonlocal fechado
                async with lock_envio:
                    if not fechado:
                        fechado = True
                        try:
                            await send({"type": "websocket.close", "code": 1008})
                        except (OSError, RuntimeError):
                            pass

            async def envio(mensagem):
                nonlocal fechado
                async with lock_envio:
                    if fechado:
                        return
                    if mensagem["type"] == "websocket.send" and not self.sessoes.existe(sid):
                        fechado = True
                        return await send({"type": "websocket.close", "code": 1008})
                    if mensagem["type"] == "websocket.close":
                        fechado = True
                    await send(mensagem)

            async def recebimento():
                nonlocal fechado
                mensagem = await receive()
                if mensagem["type"] == "websocket.disconnect":
                    fechado = True
                    return mensagem
                if fechado or not self.sessoes.existe(sid):
                    await encerrar()
                    return {"type": "websocket.disconnect", "code": 1008}
                if mensagem["type"] == "websocket.receive" and not heartbeat_websocket(mensagem):
                    # Revalida/renova antes de entregar uma mensagem ao app.
                    if (not await run_in_threadpool(self.sessoes.validar, sid)
                            or fechado or not self.sessoes.existe(sid)):
                        await encerrar()
                        return {"type": "websocket.disconnect", "code": 1008}
                return mensagem

            async def vigiar():
                while not fechado:
                    await asyncio.sleep(INTERVALO_VERIFICACAO_WEBSOCKET)
                    if not self.sessoes.existe(sid):
                        await encerrar()
                        return

            tarefa_app = asyncio.create_task(self.app(scope, recebimento, envio))
            tarefa_vigia = asyncio.create_task(vigiar())
            try:
                await asyncio.wait((tarefa_app, tarefa_vigia), return_when=asyncio.FIRST_COMPLETED)
                if tarefa_app.done():
                    await tarefa_app
            finally:
                for tarefa in (tarefa_app, tarefa_vigia):
                    if not tarefa.done():
                        tarefa.cancel()
                await asyncio.gather(tarefa_app, tarefa_vigia, return_exceptions=True)
            return
        return await self.app(scope, receive, send)


def silenciar_logs_sensiveis():
    # Não habilitar logs de transporte, headers, bodies ou access logs.
    for nome in ("httpx", "httpcore", "supabase", "supabase_auth", "gotrue",
                 "postgrest", "websockets", "uvicorn.access"):
        logger = logging.getLogger(nome)
        logger.disabled = True
        logger.setLevel(logging.CRITICAL + 1)


def loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


@dataclass(frozen=True, repr=False)
class ConfigGateway:
    origem: str
    permitir_http_local: bool = False
    segredo_csrf: str = field(default_factory=lambda: secrets.token_urlsafe(48))

    def __post_init__(self):
        url = urlsplit(self.origem)
        if (url.scheme not in ("http", "https") or not url.hostname
                or url.username or url.password or url.path not in ("", "/")
                or url.query or url.fragment or self.origem.endswith("/")):
            raise ValueError("Origem deve conter apenas protocolo e host/porta, sem barra final.")
        if url.scheme != "https" and not (self.permitir_http_local and loopback(url.hostname)):
            raise ValueError("HTTPS obrigatório, exceto HTTP local explicitamente habilitado.")
        if len(self.segredo_csrf) < 32:
            raise ValueError("Segredo interno insuficiente.")

    @property
    def seguro(self):
        return urlsplit(self.origem).scheme == "https"

    @property
    def cookie(self):
        return "__Host-botwhats_session" if self.seguro else "botwhats_session_local"

    @property
    def cookie_csrf(self):
        return "__Host-botwhats_csrf" if self.seguro else "botwhats_csrf_local"


def criar_gateway(config, sessoes=None, autenticar=autenticar_dono, app_streamlit=None):
    silenciar_logs_sensiveis()
    sessoes = sessoes if sessoes is not None else SessoesServidor()

    def cookie(resposta, nome, valor, idade):
        resposta.set_cookie(nome, valor, max_age=idade, path="/", httponly=True,
                            secure=config.seguro, samesite="strict")

    def apagar_cookie(resposta, nome):
        resposta.delete_cookie(nome, path="/", httponly=True,
                               secure=config.seguro, samesite="strict")

    def csrf_novo():
        valor = f"{int(time.time())}.{secrets.token_urlsafe(32)}"
        assinatura = hmac.new(config.segredo_csrf.encode(), valor.encode(), hashlib.sha256).hexdigest()
        return valor + "." + assinatura

    def erro_csrf(request, formulario):
        recebido = formulario.get("csrf", [""])[0]
        esperado = request.cookies.get(config.cookie_csrf, "")
        if not esperado:
            return "cookie_csrf_ausente"
        if not recebido:
            return "csrf_ausente"
        if not esperado.isascii() or not recebido.isascii() or len(recebido) > 256:
            return "csrf_formato_invalido"
        if not hmac.compare_digest(recebido, esperado):
            return "csrf_divergente"
        try:
            instante, nonce, assinatura = recebido.split(".")
            idade = time.time() - int(instante)
            valor = f"{instante}.{nonce}"
            correto = hmac.new(config.segredo_csrf.encode(), valor.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(assinatura, correto):
                return "csrf_assinatura_invalida"
            if not 0 <= idade <= 600:
                return "csrf_expirado"
            return None
        except (ValueError, TypeError):
            return "csrf_formato_invalido"

    async def formulario(request):
        if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
            return {}, "tipo_conteudo_invalido"
        conteudo = bytearray()
        async for parte in request.stream():
            conteudo.extend(parte)
            if len(conteudo) > 8192:
                return {}, "corpo_excedido"
        try:
            dados = parse_qs(conteudo.decode("utf-8"), max_num_fields=8, keep_blank_values=True,
                             strict_parsing=True, errors="strict")
            if any(len(valores) != 1 for valores in dados.values()):
                return {}, "campos_duplicados"
            return dados, None
        except (UnicodeError, ValueError):
            return {}, "corpo_invalido"

    def pagina(request, sair=False, erro="", status=200):
        csrf = csrf_novo()
        destino = "/auth/logout" if sair else "/auth/login"
        campos = "" if sair else '''<label>E-mail <input name="email" type="email" autocomplete="username" required></label>
        <label>Senha <input name="senha" type="password" autocomplete="current-password" required></label>'''
        titulo = "Encerrar sessão" if sair else "Acesso administrativo"
        texto = "Sair" if sair else "Entrar"
        resposta = HTMLResponse(f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
        <meta name="referrer" content="{POLITICA_REFERENCIA}">
        <meta name="viewport" content="width=device-width, initial-scale=1"><title>BotWhats — {titulo}</title>
        <style>body{{font:18px system-ui;max-width:30rem;margin:5rem auto;padding:1rem}}
        label,input{{display:block;margin:1rem 0;width:100%}}input,button{{font:inherit;padding:.5rem}}
        .erro{{color:#a11}}</style></head><body><h1>🤖 BotWhats</h1><h2>{titulo}</h2>
        <p class="erro">{escape(erro)}</p><form method="post" action="{destino}" target="_self"
        accept-charset="UTF-8" enctype="application/x-www-form-urlencoded">
        <input type="hidden" name="csrf" value="{escape(csrf)}">{campos}<button>{texto}</button></form>
        <p><a href="/">Voltar ao painel</a></p></body></html>''', status_code=status)
        cookie(resposta, config.cookie_csrf, csrf, 600)
        registrar("formulario", "aprovado", "renderizado")
        return resposta

    async def login(request):
        if request.method == "GET":
            return pagina(request)
        form, erro = await formulario(request)
        if erro:
            registrar("formulario", "negado", erro)
            return pagina(request, erro="Formulário inválido. Tente novamente.", status=400)
        registrar("formulario", "aprovado")
        erro = erro_csrf(request, form)
        if erro:
            registrar("csrf", "negado", erro)
            return pagina(request, erro="Solicitação expirada. Tente novamente.", status=403)
        registrar("csrf", "aprovado")
        cliente = None
        registrar("login", "iniciado")
        try:
            cliente, _ = await run_in_threadpool(
                autenticar, form.get("email", [""])[0], form.get("senha", [""])[0],
            )
        except Exception:
            registrar("login", "negado", "autenticacao_falhou")
            if cliente is not None:
                await run_in_threadpool(sair_cliente, cliente)
            return pagina(request, erro="Não foi possível entrar. Verifique seu acesso e tente novamente.", status=401)
        registrar("sessao", "iniciado")
        try:
            sid = await run_in_threadpool(sessoes.criar, cliente)
        except Exception:
            registrar("sessao", "falha", "criacao_falhou")
            await run_in_threadpool(sair_cliente, cliente)
            return pagina(request, erro="Não foi possível concluir o acesso. Tente novamente.", status=503)
        registrar("sessao", "aprovado", "criada")
        # Nunca reutilizar um identificador escolhido/apresentado antes do login.
        await run_in_threadpool(sessoes.revogar, request.cookies.get(config.cookie))
        resposta = RedirectResponse("/", status_code=303)
        cookie(resposta, config.cookie, sid, sessoes.duracao)
        apagar_cookie(resposta, config.cookie_csrf)
        registrar("resposta", "aprovado", "redirecionamento_303")
        return resposta

    async def logout(request):
        if request.method == "GET":
            return pagina(request, sair=True)
        form, erro = await formulario(request)
        if erro:
            registrar("formulario", "negado", erro)
            return pagina(request, sair=True, erro="Formulário inválido. Tente novamente.", status=400)
        erro = erro_csrf(request, form)
        if erro:
            registrar("csrf", "negado", erro)
            return pagina(request, sair=True, erro="Solicitação expirada. Tente novamente.", status=403)
        registrar("csrf", "aprovado")
        await run_in_threadpool(sessoes.revogar, request.cookies.get(config.cookie))
        registrar("sessao", "aprovado", "revogada")
        resposta = RedirectResponse("/auth/login", status_code=303)
        apagar_cookie(resposta, config.cookie)
        apagar_cookie(resposta, config.cookie_csrf)
        return resposta

    from backend import session_runtime
    session_runtime.sessoes = sessoes
    if app_streamlit is None:
        from pathlib import Path
        import streamlit as st
        app_streamlit = st.App(str(Path(__file__).resolve().parents[1] / "app.py"))
        lifespan = app_streamlit.lifespan()
    else:
        lifespan = None
    app = Starlette(routes=[
        Route("/auth/login", login, methods=["GET", "POST"]),
        Route("/auth/logout", logout, methods=["GET", "POST"]),
        Mount("/", app=app_streamlit),
    ], lifespan=lifespan, debug=False)

    async def seguranca(request: Request, call_next):
        try:
            resposta = await call_next(request)
        except Exception:
            resposta = Response("Solicitação indisponível.", status_code=503)
        resposta.headers.update(cabecalhos_seguranca(request.url.path.startswith("/auth/"), config.seguro))
        return resposta

    app.add_middleware(BaseHTTPMiddleware, dispatch=seguranca)
    return SessaoHTTP(app, config, sessoes)
