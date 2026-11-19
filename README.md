# Painel BotWhats

Etapa 57 — prazos, renovação e homologação de sessões:
[docs/ETAPA57_SESSOES.md](docs/ETAPA57_SESSOES.md).

Diagnóstico e homologação do POST de login:
[docs/HOMOLOGACAO_LOGIN403.md](docs/HOMOLOGACAO_LOGIN403.md).

Painel Streamlit com login administrativo Supabase Auth. As quatro páginas são
Início (QR Code), Regras de Conversa, Disparo em Massa e Personalização. A conexão
WhatsApp, envio e salvamento de configurações continuam demonstrativos.

## Preparação no Windows

Na raiz do repositório:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Configure o `.env` local com `SUPABASE_URL` e `SUPABASE_PUBLISHABLE_KEY`, usando
o projeto e a RPC `sou_dono` já existentes. Não use `service_role` e não versione
credenciais. Esta implementação não cria usuários nem aplica mudanças remotas.

## Executar localmente

Para desenvolvimento exclusivamente em loopback, com exceção explícita de HTTP:

```powershell
.\.venv\Scripts\python.exe iniciar.py --origin http://127.0.0.1:8501 --allow-http-local
```

Abra `http://127.0.0.1:8501` no navegador. Acesse sempre o mesmo host configurado;
trocar `127.0.0.1` por `localhost` altera a origem. Nesse modo os cookies são
`HttpOnly` e `SameSite=Strict`, mas o transporte não tem TLS e o atributo `Secure`
é omitido. A exceção não é aceita para origens fora de loopback.

Para HTTPS local com certificado e chave já disponíveis:

```powershell
.\.venv\Scripts\python.exe iniciar.py --origin https://localhost:8501 --tls-cert caminho\cert.pem --tls-key caminho\key.pem
```

O certificado deve ser válido/confiável para o host usado. O cookie de login
será `__Host-botwhats_session`, `Secure`, `HttpOnly`, `SameSite=Strict`, sem Domain.
Se já existir um proxy TLS confiável, use `--origin https://seu-host` e
`--behind-tls-proxy`; o proxy precisa encaminhar Host, Origin, cookies e WebSocket.
Nenhum proxy ou publicação é configurado automaticamente. O servidor escuta
somente `127.0.0.1`, em um único processo.

Use **iniciar.py** como ponto de entrada. `streamlit run app.py` diretamente
bloqueia o painel, pois não inicializa as rotas que emitem cookies HttpOnly.

## Comportamento da sessão

O F5 restaura o login usando um identificador aleatório no cookie. Access token
e refresh token ficam apenas na memória do servidor, nunca na URL, em HTML,
localStorage ou sessionStorage. A identidade e a permissão Dono são consultadas
novamente antes de liberar o painel e em cada interação.

O limite é de oito horas por login e trinta minutos sem atividade. O SDK renova
os tokens quando necessário, com coordenação entre abas. Reiniciar o servidor
exige novo login. A página selecionada e rascunhos podem ser perdidos após F5.

**Sair** abre a confirmação de saída; o POST revoga o registro e remove o cookie,
mesmo se o Supabase estiver indisponível. Outras abas desse login perdem o acesso;
outros visitantes permanecem isolados. Nenhum token ou credencial é escrito em
logs. Não habilite logs de corpos/cookies em ferramentas externas.

Detalhes de arquitetura, HTTPS, CSRF, expiração e limitações:
[docs/SESSOES_PERSISTENTES.md](docs/SESSOES_PERSISTENTES.md).

O `app_backup.py` permanece como cópia histórica sem autenticação; o painel
atualizado é o `app.py`, servido por `iniciar.py`.

## Testes locais sem Supabase remoto

```powershell
$env:PYTHONPATH = (Get-Location).Path
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

A suíte usa mocks, Streamlit AppTest e uma integração isolada com `st.App` real
e WebSocket. O script manual `tests/testar_login_dono.py` acessa o Supabase somente
se executado diretamente e não participa dos testes simulados.

Versões de validação: Python 3.15.2, Streamlit 1.67.0, Supabase 2.34.1,
Starlette 1.10.0 e Uvicorn 0.54.0. O Streamlit está fixado na versão validada por
causa da integração ASGI. Atualizações devem repetir a suíte completa.
