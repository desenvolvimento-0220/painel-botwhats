"""Sessões opacas: os tokens Supabase nunca saem da memória do gateway."""

from dataclasses import dataclass, field
import hashlib
import re
import secrets
import threading
import time

from backend.auth import validar_cliente_dono


def chave_sessao(sid):
    if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", sid):
        return None
    return hashlib.sha256(sid.encode("ascii")).digest()


def sair_cliente(cliente):
    try:
        cliente.auth.sign_out({"scope": "local"})
    except Exception:
        # Não imprimir exceções: podem conter dados do transporte ou da sessão.
        pass


@dataclass(repr=False)
class Registro:
    cliente: object
    limite: float
    atividade: float
    lock: object = field(default_factory=threading.RLock)


class SessoesServidor:
    def __init__(self, duracao=8 * 3600, inatividade=30 * 60, capacidade=1024,
                 relogio=time.monotonic):
        self.duracao = duracao
        self.inatividade = inatividade
        self.capacidade = capacidade
        self.relogio = relogio
        self._registros = {}
        self._lock = threading.RLock()

    def _expirado(self, registro):
        agora = self.relogio()
        return agora >= registro.limite or agora - registro.atividade >= self.inatividade

    def criar(self, cliente):
        agora = self.relogio()
        with self._lock:
            # Sem IO ou renovação em segundo plano. Expirados não são restauráveis.
            self._registros = {k: r for k, r in self._registros.items()
                               if not self._expirado(r)}
            if len(self._registros) >= self.capacidade:
                raise RuntimeError("Limite de sessões atingido.")
            sid = secrets.token_urlsafe(32)
            self._registros[chave_sessao(sid)] = Registro(
                cliente, agora + self.duracao, agora,
            )
            return sid

    def _obter(self, sid):
        chave = chave_sessao(sid)
        with self._lock:
            return self._registros.get(chave)

    def existe(self, sid):
        # Chamado pelo event loop: somente memória, sem lock de IO do SDK.
        chave = chave_sessao(sid)
        with self._lock:
            registro = self._registros.get(chave)
            if registro is None:
                return False
            if self._expirado(registro):
                self._registros.pop(chave, None)
                return False
            return True

    def validar(self, sid, renovar=False, atividade=True):
        registro = self._obter(sid)
        if registro is None:
            return False
        # Serializa a rotação do refresh token inclusive entre abas e após F5.
        with registro.lock:
            if self._obter(sid) is not registro or not self.existe(sid):
                return False
            try:
                validar_cliente_dono(registro.cliente, renovar=renovar)
                if self._obter(sid) is not registro or not self.existe(sid):
                    return False
                # Renovação/sondagens de infraestrutura não são atividade humana.
                if atividade and not renovar:
                    registro.atividade = self.relogio()
                return True
            except Exception:
                self.revogar(sid)
                return False

    def revogar(self, sid):
        # Invalida antes de esperar uma validação concorrente ou a rede.
        with self._lock:
            registro = self._registros.pop(chave_sessao(sid), None)
        if registro is None:
            return
        with registro.lock:
            sair_cliente(registro.cliente)
