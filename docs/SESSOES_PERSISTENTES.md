# Sessão persistente Supabase no Streamlit

## Escolha de arquitetura

O login anterior ficava exclusivamente em `st.session_state`, que não é uma
identidade persistente entre conexões WebSocket. O F5 podia iniciar uma nova
sessão Streamlit e exigir outro login. Componentes que escrevem cookies com
JavaScript não conseguem criar cookies `HttpOnly`.

A integração usa o servidor ASGI nativo `st.App` do Streamlit **1.67.0**, com rotas
Starlette de login/logout e um middleware que protege HTTP e WebSocket. Existe
apenas um processo e uma porta local; não há proxy próprio, API interna de tokens,
alteração de APIs privadas do Streamlit ou necessidade de mudar o Supabase.

O ponto de entrada passa a ser `iniciar.py`. Executar `streamlit run app.py`
diretamente não inicializa o registro de sessões e o painel bloqueia o acesso.

## Fluxo

1. Um visitante sem cookie válido é direcionado a `/auth/login`.
2. O formulário envia e-mail e senha por **POST**, para a mesma origem. A resposta
   do Supabase precisa conter usuário e sessão; a RPC existente `sou_dono` precisa
   retornar o booleano `True`.
3. O servidor cria um identificador aleatório de **256 bits**, sem dados do usuário
   e sem JWT. O registro em memória é indexado pelo SHA-256 desse identificador.
4. A resposta HTTP grava o identificador em um cookie. O cliente Supabase,
   access token e refresh token permanecem exclusivamente na memória do processo.
5. Após F5, o navegador reapresenta o cookie automaticamente. O middleware valida
   a identidade e a permissão, e injeta o identificador em um cabeçalho interno do
   escopo ASGI. Cabeçalhos desse nome enviados pelo visitante são removidos.
6. Antes da navegação e em cada execução do script, o painel volta a validar
   identidade e permissão no servidor. `session_state` não concede acesso.

Não se colocam tokens Supabase em cookies, URLs, query parameters, HTML,
localStorage, sessionStorage ou mensagens WebSocket. A senha existe no formulário
de login e no POST de autenticação; não é persistida nem devolvida no HTML.

O identificador do cookie também é uma credencial de sessão: deve ser protegido
como tal. Não o copie para logs, ferramentas de análise ou serviços de terceiros.

## Cookies, HTTPS e CSRF

Em HTTPS, o cookie de autenticação se chama `__Host-botwhats_session`:

- `Secure`, `HttpOnly`, `SameSite=Strict` e `Path=/`;
- sem atributo `Domain`, restrito ao host de origem;
- `Max-Age=28800` (oito horas), também limitado pelo registro no servidor;
- substituído por um identificador novo após cada login;
- expirado pela resposta HTTP de logout com os mesmos atributos de escopo.

Login e logout usam tokens CSRF assinados, cookie próprio `HttpOnly`,
comparação com o campo oculto do formulário e prazo de dez minutos. POSTs exigem
`Origin` exatamente igual à origem configurada. O middleware também verifica
`Host` e a origem dos WebSockets. Não existe logout por GET: **Sair** abre uma
página de confirmação; o botão dessa página envia POST e remove o cookie.

O cookie CSRF não contém tokens Supabase. Os cookies XSRF internos do Streamlit
podem ter atributos diferentes porque o frontend usa seu próprio protocolo;
eles não transportam a sessão Supabase.

O iniciador só escuta `127.0.0.1`, inclusive com TLS. Para HTTPS, forneça certificado
e chave locais ou declare explicitamente um proxy TLS confiável já existente.
O iniciador não cria certificados, proxy, domínio ou publicação.

Para desenvolvimento, há uma exceção **explícita** `--allow-http-local`, aceita
somente para origens HTTP em loopback (`127.0.0.1`, `localhost` ou `::1`). Nesse
modo, os cookies têm nomes diferentes, continuam `HttpOnly` e `SameSite=Strict`,
mas não possuem `Secure`, pois o transporte é HTTP. Não use essa exceção em LAN,
Internet, com credenciais reais em rede não confiável ou como configuração de
produção. Cookies não são isolados por porta: evite outros serviços não
confiáveis no mesmo host durante o desenvolvimento local.

## Renovação, expiração e isolamento

Cada login cria um cliente Supabase separado. Um lock por registro serializa
as consultas e a rotação do refresh token entre abas e reconexões. Não existe
cliente Supabase global compartilhando credenciais entre visitantes.

O SDK tem `auto_refresh_token=False`: não há timer mantendo sessões ociosas vivas.
`get_session()` renova quando necessário durante a validação, seguido de
`get_user(jwt=...)` e da RPC `sou_dono`. A função de renovação forçada também
revalida a permissão. Erros de rede, tokens inválidos, usuário ausente ou resultado
de autorização diferente de `True` bloqueiam e revogam o registro local.

A versão 2.34.1 do SDK renova em uma margem de dez segundos antes do vencimento,
durante a validação, sem estender o limite absoluto do registro. Sondagens de
infraestrutura, handshake e renovação forçada não atualizam a atividade. O
heartbeat de aplicação do Streamlit não chama o SDK nem mantém a sessão viva.

O prazo absoluto é de oito horas; a inatividade máxima é de trinta minutos.
Os prazos são medidos com relógio monotônico. Há limite de 1024 registros para
conter o uso de memória. Registros expirados são descartados também nas checagens
locais, antes de qualquer renovação. Não se chama logout SDK apenas para limpar
um prazo vencido, pois esse logout pode renovar tokens internamente. Não existe
persistência de tokens em disco. Expiração local não garante revogação imediata
no Supabase para registros descartados pela limpeza.

O gateway rejeita HTTP e novos handshakes expirados. Um monitor local de um
segundo encerra sockets ociosos expirados/revogados com código 1008, sem rede ou
renovação. Envio e recebimento também têm checagens imediatas. Mensagens de
aplicação, exceto heartbeat, revalidam identidade/Dono antes de chegar ao app.

A retomada de `AppSession` antigo no protocolo WebSocket é desabilitada pelo
middleware. Uma nova conexão começa com estado visual novo, restaurando somente
a identidade autenticada pelo cookie. O painel também limpa seus rascunhos
quando muda o identificador do visitante.

## Logout

O POST `/auth/logout` remove primeiro o registro local, depois tenta
`sign_out({"scope": "local"})`, e expira os cookies. Uma falha na rede não deixa
o cookie antigo restaurar o painel. Outros visitantes e outros logins Supabase
não são encerrados.

O identificador é removido antes de aguardar locks ou validações concorrentes.
Uma validação que termina após a revogação não pode restaurar o acesso.

As abas do mesmo navegador que compartilham esse cookie pertencem ao mesmo
login: todas perdem acesso após a saída. HTTPs e mensagens WebSocket posteriores
são bloqueados. O conteúdo já desenhado em outra aba pode continuar visível até
ela interagir/recarregar; não há mecanismo para apagar pixels já apresentados.

Se o Supabase estiver indisponível, a revogação remota pode falhar. O registro e o
acesso locais ainda são removidos. JWTs já emitidos pelo Supabase têm seu próprio
prazo de validade; a aplicação não promete invalidá-los instantaneamente.

## Limitações operacionais

- A persistência cobre F5 e novas conexões enquanto o mesmo servidor está ativo,
  respeitando os prazos. Reiniciar o processo exige novo login, mesmo que o cookie
  ainda exista. Isso é intencional para evitar gravar tokens em disco.
- O registro é de um único processo/worker. Réplicas, múltiplos workers e alta
  disponibilidade exigiriam um armazenamento central de sessões com proteção
  dos tokens, expiração, revogação e coordenação de refresh. Não usar `--workers`.
- A identidade persiste; a página selecionada e rascunhos não são prometidos
  após F5. As quatro páginas e suas funções demonstrativas continuam iguais.
- `SameSite=Strict`, origem única e `X-Frame-Options: DENY` impedem embedding
  entre sites e fluxos de login em origens diferentes. Não há suporte a subpath.
- O endpoint de saúde e as rotas de mídia também ficam protegidos. A API de
  agentes do Streamlit é desabilitada pelo iniciador.
- Não há rate limiter próprio de login; mantêm-se os controles do Supabase e do
  ambiente. A publicação futura precisa de uma revisão operacional específica.
- Não foram alterados usuários, RPCs, RLS, configurações ou dados remotos. Os
  testes usam autenticação simulada; não comprovam a configuração remota real.

## Logs e testes

O iniciador desativa access logs, debug e logs de transporte de autenticação.
O código não imprime credenciais, cookies ou tokens e não inclui exceções remotas
nas mensagens ao visitante. Não habilite logs de corpo/cabeçalho ou integrações
de captura de sessão em proxies e ferramentas externas.

Os testes cobrem cookies, flags de segurança, CSRF, origem, adulteração, F5,
logout offline, revogação, isolamento, concorrência de refresh e as quatro
páginas. `tests/smoke_asgi.py` cria um `st.App` real em processo isolado, efetua
login simulado e executa o painel em duas conexões WebSocket novas via protobuf.
Também confirma que o logout impede a restauração. O cliente HTTP de teste usa
HTTPS/WSS para exercitar o envio de cookies `Secure`, sem servidor remoto.

Referências oficiais: [st.App](https://docs.streamlit.io/develop/api-reference/server/st.app),
[st.context](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.context),
[cookies Starlette](https://starlette.dev/responses/),
[sessão Supabase](https://supabase.com/docs/reference/python/auth-getsession) e
[logout Supabase](https://supabase.com/docs/reference/python/auth-signout).
