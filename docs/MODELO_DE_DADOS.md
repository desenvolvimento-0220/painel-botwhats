
# BotWhats — Modelo de Dados

## 1. Objetivo

Organizar o banco de dados PostgreSQL para
suportar múltiplas empresas com isolamento
de informações, usuários e assinaturas.

## 2. Tabelas principais

### usuarios
Representa os usuários do sistema.

Campos:
- id: UUID, chave primária
- nome: texto
- email: texto
- ativo: booleano
- criado_em: data e hora

A autenticação será realizada pelo Supabase Auth.
O id corresponderá ao usuário autenticado.

### empresas
Representa os clientes da plataforma.

Campos:
- id: UUID, chave primária
- nome: texto
- documento: texto, opcional
- ativo: booleano
- criado_em: data e hora

### usuarios_empresas
Relaciona usuários às empresas.

Campos:
- id: UUID, chave primária
- usuario_id: referência a usuarios
- empresa_id: referência a empresas
- papel: dono_empresa ou equipe
- ativo: booleano

### planos
Representa os planos comercializados.

Campos:
- id: UUID, chave primária
- nome: texto
- valor_mensal: decimal
- limite_instancias: inteiro
- ativo: booleano

### assinaturas
Controla o acesso contratado.

Campos:
- id: UUID, chave primária
- empresa_id: referência a empresas
- plano_id: referência a planos
- status: teste, ativa, vencida,
  suspensa ou cancelada
- inicio_em: data e hora
- vencimento_em: data e hora

### instancias_whatsapp
Armazena as conexões WhatsApp.

Campos:
- id: UUID, chave primária
- empresa_id: referência a empresas
- nome: texto
- identificador_externo: texto
- status: conectada ou desconectada

### regras
Armazena respostas automáticas.

Campos:
- id: UUID, chave primária
- empresa_id: referência a empresas
- palavra_chave: texto
- resposta: texto
- prioridade: inteiro
- ativa: booleano

### contatos
Armazena os contatos de cada empresa.

Campos:
- id: UUID, chave primária
- empresa_id: referência a empresas
- nome: texto
- telefone: texto
- criado_em: data e hora

### conversas
Representa os atendimentos.

Campos:
- id: UUID, chave primária
- empresa_id: referência a empresas
- contato_id: referência a contatos
- status: aberta, humana ou encerrada
- iniciada_em: data e hora

### mensagens
Armazena mensagens dos atendimentos.

Campos:
- id: UUID, chave primária
- empresa_id: referência a empresas
- conversa_id: referência a conversas
- direcao: entrada ou saida
- tipo: texto, imagem, audio, documento
- conteudo: texto
- criada_em: data e hora

## 3. Segurança multiempresa

Todas as tabelas com dados de empresas
deverão possuir empresa_id.

O Supabase utilizará políticas RLS
para restringir o acesso.

O backend deverá validar permissões
em todas as operações.

Usuários comuns não poderão acessar
informações de outras empresas.

## 4. Administrador da plataforma

Inicialmente existirá apenas o Dono.

O Dono poderá:
- Cadastrar empresas
- Cadastrar planos
- Gerenciar assinaturas
- Ativar ou suspender empresas

O acesso administrativo global será
controlado por permissões no servidor,
nunca por um campo enviado pelo navegador.

## 5. Regras de assinatura

Uma assinatura vencida deverá impedir
novas operações contratadas.

O vencimento não apagará automaticamente
os dados da empresa.

O Dono continuará podendo administrar
a situação da assinatura.

## 6. Implementação

Este documento representa o modelo inicial.

Antes de executar migrações SQL, serão
definidos relacionamentos, índices,
restrições e políticas RLS.
