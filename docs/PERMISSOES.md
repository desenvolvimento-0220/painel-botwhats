# BotWhats — Permissões e Segurança

## 1. Perfis de acesso

### Dono da plataforma
- Gerencia empresas, planos e assinaturas.
- Pode ativar e suspender empresas.
- Gerencia as configurações globais.
- Não acessa conversas privadas automaticamente.
- Operações administrativas devem ser auditadas.

### Administrador da empresa (futuro)
- Gerencia somente sua empresa.
- Configura WhatsApp, regras e inteligência artificial.
- Consulta contatos e conversas autorizadas.
- Não acessa dados de outras empresas.

### Equipe da empresa (futuro)
- Possui acesso limitado às funções autorizadas.
- Não gerencia assinaturas ou configurações globais.

## 2. Autenticação

- Utilizar Supabase Auth.
- Não armazenar senhas em tabelas próprias.
- Não permitir cadastro público inicialmente.
- Criar o primeiro Dono por procedimento administrativo.
- Verificar identidade e permissões no servidor.

## 3. Segurança multiempresa

- Vincular dados de clientes a empresa_id.
- Aplicar Row Level Security (RLS).
- Validar vínculos de usuários com empresas.
- Impedir acesso cruzado entre empresas.
- Não confiar somente no empresa_id enviado pelo navegador.

## 4. Assinaturas

- Verificar status e vencimento no backend.
- Suspender operações contratadas quando necessário.
- Impedir que clientes alterem o próprio vencimento.
- Permitir reativação pelo Dono.
- Preservar dados conforme a política de retenção.

## 5. Segurança das integrações

- Armazenar chaves e tokens em ambiente seguro.
- Não publicar credenciais no GitHub.
- Validar webhooks recebidos.
- Proteger endpoints administrativos.
- Registrar falhas sem expor informações sensíveis.

## 6. Auditoria

Registrar:
- Usuário responsável pela operação.
- Empresa afetada.
- Tipo de operação.
- Data e hora.
- Resultado da operação.

## 7. Primeira versão

- Apenas o Dono terá acesso ao painel.
- O Dono poderá cadastrar empresas.
- Clientes ainda não terão login.
- Assinaturas serão administradas manualmente.
- Integrações externas permanecerão em testes.
