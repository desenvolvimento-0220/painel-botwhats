
# BotWhats — Arquitetura SaaS

## 1. Objetivo

Criar uma plataforma SaaS de automação de atendimento
por WhatsApp para múltiplas empresas.

Cada empresa terá configurações, contatos, conversas,
regras de automação e conexão WhatsApp independentes.

## 2. Tecnologias

- Interface inicial: Python + Streamlit
- Backend: Python + FastAPI
- Banco de dados: PostgreSQL / Supabase
- Autenticação: Supabase Auth
- WhatsApp: Evolution API
- Inteligência artificial: Google Gemini
- Hospedagem futura: Oracle Cloud
- Versionamento: GitHub

## 3. Perfis de acesso

### Dono da plataforma

- Acesso administrativo global
- Cadastro e gerenciamento de empresas
- Gerenciamento de assinaturas
- Ativação e suspensão de empresas
- Visualização de indicadores operacionais
- Administração das configurações da plataforma

### Administrador da empresa (futuro)

- Acesso somente à própria empresa
- Conexão do próprio WhatsApp
- Gerenciamento de contatos e conversas
- Configuração de regras e inteligência artificial
- Acompanhamento da assinatura

### Equipe da empresa (futuro)

- Atendimento de conversas autorizadas
- Permissões limitadas conforme função

## 4. Multiempresa

Toda informação pertencente a uma empresa
deverá ser vinculada a um empresa_id.

O isolamento será aplicado no banco e no backend.

Nenhum usuário de uma empresa poderá acessar
dados de outra empresa sem autorização explícita.

## 5. Assinaturas

Estados previstos:

- teste
- ativa
- vencida
- suspensa
- cancelada

O backend verificará a situação da assinatura
antes de liberar funcionalidades contratadas.

Assinaturas vencidas não poderão continuar
utilizando automações quando o plano exigir bloqueio.

Os dados serão preservados conforme a política
de retenção e exclusão da plataforma.

## 6. Segurança

- Autenticação obrigatória
- Permissões verificadas no servidor
- Isolamento multiempresa
- RLS nas tabelas aplicáveis
- Chaves e senhas fora do Git
- Registro de operações administrativas
- Backups e recuperação
- Comunicação HTTPS em produção

## 7. Primeira versão

- Login do Dono
- Cadastro de empresas
- Cadastro e situação de assinaturas
- Estrutura multiempresa
- Motor básico de regras
- Simulador de conversas

## 8. Funcionalidades futuras

- Login de clientes
- Gerenciamento de equipes
- QR Code e conexão WhatsApp
- Integração com Gemini
- Respostas com imagens, áudio e PDF
- Campanhas com consentimento
- Cobrança recorrente automatizada
- Relatórios e indicadores

## 9. Organização do projeto

app/      Interface Streamlit
backend/  API e regras de negócio
database/ Migrações e banco de dados
tests/    Testes automatizados
docs/     Documentação técnica
