# Etapa 57 — expiração e renovação de sessões

## Contrato homologado localmente

- Inatividade: bloqueio em `agora - ultima_atividade >= 1800` segundos.
- Duração absoluta: bloqueio em `agora >= inicio + 28800` segundos, mesmo com
  atividade contínua, F5 ou renovação de tokens.
- Os prazos locais usam relógio monotônico. O relógio civil do SDK serve somente
  para o vencimento dos tokens Supabase; os testes simulam ambos separadamente.
- F5 e requisições/interações autenticadas contam como atividade. Static assets,
  handshake, health/host-config, heartbeat e renovação forçada não estendem a
  inatividade. Código futuro que faça sondagens deve usar `atividade=False`.
- O SDK instalado (2.34.1) usa margem de dez segundos antes do vencimento em
  `get_session()`. Isso ocorre durante uma validação, não por timer que mantenha
  o login ocioso vivo. Depois há `get_user` e RPC `sou_dono is True`.
- HTTP expirado redireciona para `/auth/login` e remove o cookie. Handshake
  WebSocket expirado é recusado; socket já aberto é encerrado com 1008, inclusive
  sem mensagens novas, em até aproximadamente um segundo de monitoramento
  local mais o atraso de agendamento do event loop. Os gates de entrada/saída
  impedem tráfego depois de constatar a expiração, sem esperar o monitor.

## Problemas reproduzidos no código anterior

Cinco testes de regressão foram executados contra o checkout anterior e todos
falharam no comportamento esperado; passaram após as correções:

1. `existe()` retornava falso, mas mantinha o registro expirado. Agora o descarta
   sem chamadas de rede nem espera pelo lock do SDK.
2. Logout esperava a validação concorrente antes de retirar o identificador.
   Agora invalida primeiro, espera o lock apenas para o logout remoto e impede
   que a validação em andamento volte a conceder acesso.
3. Consultas automáticas a health atualizavam a atividade e podiam manter uma
   sessão ociosa viva. Agora sondagens não prolongam o prazo.
4. Socket aberto e sem tráfego não era fechado na expiração; somente a próxima
   mensagem detectava o prazo. Foi adicionado monitor local, sem SDK/rede.
5. Ao expirar localmente, a limpeza via `sign_out()` do SDK podia renovar um token
   vencido antes de encerrá-lo. Prazos vencidos agora são descartados localmente
   antes do SDK. Logout solicitado pelo usuário continua tentando `scope=local`.

As checagens de entrada do WebSocket foram reforçadas para renovar/revalidar a
permissão antes de entregar mensagens ao app. Heartbeats não acionam esse fluxo.
O envio é serializado e verifica o registro sob o lock antes de enviar dados.

## Cobertura automatizada

`tests/test_etapa57.py` usa relógios simulados, `httpx.MockTransport`, o SDK real
instalado, requests HTTP ao gateway e escopos ASGI WebSocket. Não usa credenciais
reais nem rede Supabase. A suíte anterior inclui ainda Chrome e `st.App` reais.

São verificados os limites exatos e imediatamente anteriores, oito horas com
atividade, alteração do relógio civil, sondagens, rotação sucessiva de refresh
tokens, atualização da autorização no Auth/RPC, renovação concorrente com uma
única rotação, rejeição de tokens, rede indisponível, permissão retirada, sessão
em voo no logout, replay de identificador, visitantes independentes e bloqueios
HTTP/WebSocket, inclusive envio e sockets ociosos.

## Homologação local sem esperar horas

Na raiz do BotWhats, confirme a branch e execute:

```powershell
git branch --show-current
$env:PYTHONPATH = (Get-Location).Path
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_etapa57.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q app.py iniciar.py backend tests
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

Os testes avançam os relógios em memória; não alteram o relógio do Windows,
reduzem os prazos de produção ou esperam trinta minutos/oito horas. Não altere
TTL remoto nem revogue contas reais para simular falhas.

Para checar a interface com a conta existente, o operador pode reiniciar o
processo local e usar o procedimento já aprovado de login/F5/Sair:

```powershell
.\.venv\Scripts\python.exe iniciar.py --origin http://127.0.0.1:8501 --allow-http-local
```

Abra exatamente essa origem, percorra as quatro páginas, use F5 e confirme Sair.
HTTP é uma exceção explícita para loopback; HTTPS com certificado/proxy mantém
Secure conforme o README. Não exporte HAR, cookies, corpos ou tokens. O login
real desse procedimento é executado pelo operador e não foi repetido nesta
etapa. A passagem do tempo e falhas são homologadas pela suíte simulada acima.

## Limites e pendências

Revogação local/logout do BotWhats é definitiva para aquele identificador.
Revogação **remota** do refresh token não significa invalidar instantaneamente
um access JWT ainda aceito pelo Supabase. O bloqueio ocorre quando o Auth recusa
o usuário/token, a RPC nega a permissão ou a renovação é recusada. Um teste cobre
explicitamente o caso em que o JWT atual continua válido até a tentativa de
renovação. Não se promete detecção instantânea de revogação remota.

A expiração local não garante revogação remota; o registro é descartado sem
renovar tokens para fazer cleanup. Um logout remoto pode falhar por rede, mas o
identificador já estará removido. Dados já desenhados em uma aba não podem ser
apagados pelo fechamento do socket. A renovação depende de validação quando há
uso da sessão; não há promessa de renovação em página totalmente ociosa.

O registro continua em memória, em um processo/worker. Reinício exige novo
login. Múltiplos workers exigiriam armazenamento compartilhado e coordenação.
O heartbeat protobuf é específico da versão Streamlit fixada e coberta pelos
testes; atualizações precisam repetir a suíte completa.

Não há pendência local de sessão impeditiva para **começar** a integração da
Evolution API em desenvolvimento. Isso não homologa a conexão WhatsApp nem
produção: a próxima fase precisa definir endpoint/credencial da Evolution,
isolamento das instâncias e proteger as operações reais de conexão/QR Code
também no backend, inclusive tarefas demoradas, sem depender só da tela.
Nenhuma chamada Evolution foi implementada ou realizada nesta etapa.

Referências: [sessão Python Supabase](https://supabase.com/docs/reference/python/auth-getsession),
[renovação](https://supabase.com/docs/reference/python/auth-refreshsession) e
[limites de revogação](https://supabase.com/docs/guides/auth/signout), além do
código da versão instalada do SDK. Nenhuma configuração remota foi alterada.
