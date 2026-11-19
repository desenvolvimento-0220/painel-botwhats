"""Inicialização local do servidor ASGI com cookie de sessão HttpOnly."""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from backend.gateway import ConfigGateway, criar_gateway
from backend.auth_diagnostics import configurar_diagnosticos


def main():
    parser = argparse.ArgumentParser(description="BotWhats com sessão persistente no servidor")
    parser.add_argument("--origin", required=True, help="Origem única do navegador, sem barra final")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--allow-http-local", action="store_true",
                        help="Exceção explícita para desenvolvimento HTTP em loopback")
    parser.add_argument("--tls-cert", help="Certificado TLS local em PEM")
    parser.add_argument("--tls-key", help="Chave TLS local em PEM")
    parser.add_argument("--behind-tls-proxy", action="store_true",
                        help="HTTPS terminado por um proxy confiável já configurado")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Porta deve estar entre 1 e 65535.")
    try:
        config = ConfigGateway(args.origin, permitir_http_local=args.allow_http_local)
    except ValueError as erro:
        parser.error(str(erro))
    if bool(args.tls_cert) != bool(args.tls_key):
        parser.error("Informe tanto --tls-cert quanto --tls-key.")
    if config.seguro and not (args.tls_cert and args.tls_key or args.behind_tls_proxy):
        parser.error("HTTPS exige certificado/chave ou --behind-tls-proxy explicitamente.")
    if not config.seguro and (args.tls_cert or args.behind_tls_proxy):
        parser.error("A origem deve usar HTTPS quando TLS estiver habilitado.")
    for arquivo in (args.tls_cert, args.tls_key):
        if arquivo and not Path(arquivo).is_file():
            parser.error("Arquivo TLS não encontrado.")
    # Opções públicas do Streamlit, definidas antes da criação de st.App.
    os.environ["STREAMLIT_SERVER_CORS_ALLOWED_ORIGINS"] = json.dumps([args.origin])
    os.environ["STREAMLIT_BROWSER_SERVER_ADDRESS"] = urlsplit(args.origin).hostname
    os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
    os.environ["STREAMLIT_SERVER_ENABLE_AGENT_API"] = "false"
    os.environ["STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION"] = "true"
    configurar_diagnosticos()
    app = criar_gateway(config)
    # Nunca abre interfaces públicas e nunca registra URLs ou corpos de login.
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False,
                proxy_headers=False, log_level="warning",
                ssl_certfile=args.tls_cert, ssl_keyfile=args.tls_key)


if __name__ == "__main__":
    main()
