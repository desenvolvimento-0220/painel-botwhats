# HTTP 403 no login: causa, diagnóstico e homologação

## Causa reproduzida

A versão anterior emitia `Referrer-Policy: no-referrer`. No POST nativo de um
formulário HTML, essa política pode fazer o navegador enviar `Origin: null`,
mesmo para a mesma origem. O middleware rejeita o pedido antes do CSRF e do
Supabase. Um GET 200 não demonstra que a verificação do POST passou.

O checkout revisado em 18/11/2026 já continha um ajuste local para
`strict-origin-when-cross-origin`, que foi preservado. Um processo não reiniciado,
documento antigo ou cabeçalhos reescritos por proxy podem manter o comportamento
anterior. Não se inspecionou um POST real com credenciais do usuário nem se
confirmou qual processo/proxy gerou a requisição relatada.

O teste com Chrome headless, perfil descartável e autenticação simulada confirmou:

| Documento | POST nativo |
| --- | --- |
| Política histórica `no-referrer` | Origem nula; HTTP 403; autenticação não chamada. |
| Política corrigida no header e HTML | Origem correta; HTTP 303; sessão criada. |
| Política corrigida, CSRF adulterado | Origem correta; HTTP 403; autenticação não chamada novamente. |

Um cliente HTTP que define Origin manualmente ou um `fetch()` em modo CORS não
substitui esse teste de formulário nativo. A regra está no algoritmo normativo
[Fetch — append a request Origin header](https://fetch.spec.whatwg.org/#append-a-request-origin-header)
e na [especificação Referrer Policy](https://www.w3.org/TR/referrer-policy/).

## Correção

- Header e meta `referrer` usam a mesma constante
  `strict-origin-when-cross-origin`, com `<head>` e `<body>` explícitos.
- Login/logout mantêm POST para ação relativa da mesma origem, com
  `target="_self"`, UTF-8 e codificação URL-encoded explícitos. Nenhum JavaScript
  de produção tenta fabricar Origin ou transportar tokens.
- CSP mantém `form-action 'self'`, `base-uri 'none'` e proibição de frames.
- Verificação única de Host/Origin rejeita cabeçalhos duplicados. POST e
  WebSocket exigem a origem exata configurada. `null`, origem ausente ou
  divergente continuam recusados. Referer e Fetch Metadata não concedem acesso.
- CSRF mantém assinatura, comparação com cookie e validade de dez minutos.
  Corpo malformado/excessivo e campos duplicados são rejeitados antes do login.
- Falha na criação de sessão agora retorna 503, limpa o login recém-criado e não
  emite cookie de autenticação; não é confundida com credenciais recusadas.

Cookies, logout, isolamento, renovação e Dono mantêm as regras de
`SESSOES_PERSISTENTES.md`. As quatro páginas e o backup não foram alterados.

## Diagnósticos sem dados sensíveis

O iniciador habilita o logger `botwhats.auth` com formato fechado:

```text
auth fluxo=17 etapa=origem resultado=negado motivo=origem_nula
```

`fluxo` é um contador efêmero de requisições por processo, sem vínculo com usuário,
cookie ou sessão autenticada. Não é reaproveitado de um ID enviado pelo cliente.
Etapas, resultados e motivos pertencem a listas fixas. Não são registrados
cabeçalhos, corpos, URLs/query strings, IP, e-mail, User-Agent, credenciais,
cookies, tokens ou textos/tracebacks de exceções. O contexto acompanha o trabalho
de autenticação na thread do SDK.

| Etapa | Interpretação da falha |
| --- | --- |
| `origem` | Host inválido/duplicado ou origem nula, ausente, divergente ou duplicada. Antes do corpo; HTTP 403. |
| `formulario` | Codificação, tamanho, corpo ou campos duplicados; HTTP 400. |
| `csrf` | Cookie/campo ausente, divergência, assinatura/formato inválido ou prazo vencido; HTTP 403. |
| `validacao` | Campos obrigatórios ausentes, antes da criação do cliente. |
| `configuracao` | Criação/configuração do cliente indisponível. |
| `autenticacao` | Login falhou ou não retornou usuário/sessão. |
| `permissao` | RPC falhou ou não retornou o booleano True; nenhuma sessão do gateway criada. |
| `sessao` | Registro indisponível após autenticação/autorização; HTTP 503. |
| `resposta` | Sucesso concluído com `redirecionamento_303`. |

Procure a primeira linha `negado` ou `falha` do mesmo fluxo. Um evento posterior
`formulario/aprovado/renderizado` apenas indica o redesenho da página de erro.
Mensagens ao navegador continuam genéricas. Access logs e logs de transporte
continuam desativados; não habilite captura de cookies/corpos em ferramentas
externas. Não compartilhe HAR, dumps ou capturas do POST com credenciais.

## Homologação

1. Na raiz do BotWhats, confirme a branch e execute:

   ```powershell
   git branch --show-current
   $env:PYTHONPATH = (Get-Location).Path
   .\.venv\Scripts\python.exe -m unittest discover -s tests -v
   ```

   A suíte usa mocks e não chama o Supabase remoto. O teste de navegador usa
   Chrome/Edge instalado em um dos caminhos reconhecidos; em outro ambiente
   pode ser marcado como ignorado e requer homologação manual equivalente.

2. Reinicie o processo local correto com a versão nova:

   ```powershell
   .\.venv\Scripts\python.exe iniciar.py --origin http://127.0.0.1:8501 --allow-http-local
   ```

   Essa é a exceção de desenvolvimento HTTP em loopback. Para HTTPS, use
   certificado/chave ou o proxy TLS já existente conforme o README. Não troque
   host/porta entre GET e POST e não faça downgrade de HTTPS para HTTP.

3. Abra **uma nova página** `/auth/login`; não reutilize o documento antigo nem
   apenas reenvie seu POST. No Network do navegador, confira GET 200 e o header
   `Referrer-Policy: strict-origin-when-cross-origin`, além da meta correspondente
   no HTML. Confira a resposta final entregue pelo proxy, se houver. Não exporte
   a captura de rede.

4. O operador pode homologar a conta existente pelo próprio formulário. Não
   forneça credenciais ao agente nem as copie para terminal/logs. Esse passo
   utiliza o Auth remoto e não foi executado nesta correção; não exige criar
   usuários nem alterar RPC/RLS. Confira apenas a igualdade entre Origin do POST
   e a origem configurada. Espere POST 303 e painel 200. Para um 401, use as
   etapas seguras para distinguir configuração, autenticação e permissão.

5. Use F5, percorra as quatro páginas e confirme Sair. O logout deve remover o
   cookie e impedir a restauração após F5. Uma janela com armazenamento separado
   não deve herdar o login.

6. Homologue cenários negativos pela suíte simulada: origem inválida/nula/ausente/
   duplicada, CSRF inválido/expirado, autenticação recusada, Dono negado e falha na
   sessão. Não altere contas ou permissões remotas para testar esses casos.

Se um documento novo ainda enviar Origin nulo com política e ação corretas,
investigue extensão de privacidade, iframe/sandbox, downgrade ou proxy. O gateway
deve continuar recusando o POST. Corrija a navegação; não aceite `null` na lista
de origens. Nenhum ajuste ou publicação remota é necessário nesta correção.
