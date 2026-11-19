"""Integração isolada com st.App real e WebSocket protobuf, sem Supabase remoto."""

import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
os.environ["STREAMLIT_SERVER_CORS_ALLOWED_ORIGINS"] = '["http://127.0.0.1:8501"]'
os.environ["STREAMLIT_SERVER_ENABLE_AGENT_API"] = "false"

from google.protobuf.json_format import MessageToDict
from starlette.testclient import TestClient
from streamlit.proto.BackMsg_pb2 import BackMsg
from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
from backend.gateway import ConfigGateway, criar_gateway
from backend.session_store import SessoesServidor


def main():
    config = ConfigGateway("http://127.0.0.1:8501", permitir_http_local=True)
    cliente = MagicMock()
    cliente.auth.get_session.return_value = SimpleNamespace(access_token="access-SIMULADO")
    cliente.auth.get_user.return_value.user.id = "dono-simulado"
    cliente.rpc.return_value.execute.return_value.data = True
    store = SessoesServidor()
    with patch("backend.auth.criar_cliente_supabase", side_effect=AssertionError("Remoto proibido")):
        app = criar_gateway(config, store, lambda e, s: (cliente, cliente.auth.get_session.return_value))
        with TestClient(app, base_url=config.origem, follow_redirects=False) as http:
            pagina = http.get("/auth/login")
            csrf = re.search(r'name="csrf" value="([A-Za-z0-9_.-]+)"', pagina.text).group(1)
            resposta = http.post("/auth/login", data={"csrf": csrf, "email": "dono@teste.invalid", "senha": "simulada"},
                                 headers={"origin": config.origem})
            assert resposta.status_code == 303, "Login simulado falhou"
            anterior = "appsession-antigo"
            for _ in range(2):
                assert http.get("/").status_code == 200, "Página inicial bloqueada"
                with http.websocket_connect("ws://127.0.0.1:8501/_stcore/stream",
                                             headers={"origin": config.origem},
                                             subprotocols=["streamlit", "xsrf-simulado", anterior]) as ws:
                    back = BackMsg()
                    back.rerun_script.SetInParent()
                    ws.send_bytes(back.SerializeToString())
                    encontrou_painel = False
                    atual = None
                    for _ in range(200):
                        msg = ForwardMsg()
                        msg.ParseFromString(ws.receive_bytes())
                        texto = json.dumps(MessageToDict(msg), ensure_ascii=False)
                        assert "access-SIMULADO" not in texto, "Token não pode sair no WebSocket"
                        if "Conexão do WhatsApp" in texto:
                            encontrou_painel = True
                        if msg.WhichOneof("type") == "new_session":
                            atual = msg.new_session.initialize.session_id
                        if msg.WhichOneof("type") == "script_finished":
                            break
                    assert encontrou_painel, "F5 não restaurou o painel no st.App real"
                    assert atual and atual != anterior, "AppSession anterior não deve ser retomado"
                    anterior = atual
            csrf = re.search(r'name="csrf" value="([A-Za-z0-9_.-]+)"', http.get("/auth/logout").text).group(1)
            assert http.post("/auth/logout", data={"csrf": csrf},
                             headers={"origin": config.origem}).status_code == 303
            assert http.get("/").status_code == 303, "Logout não bloqueou o painel"
    print("OK: st.App real, duas conexões WebSocket após login e logout bloqueando F5.")


if __name__ == "__main__":
    main()
