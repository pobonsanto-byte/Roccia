# ========================
# IMPORTS E CONFIGURAÇÃO INICIAL
# ========================

import os
os.environ["DISCORD_NO_VOICE"] = "1"

import sys
import json
import base64
import re
import requests
import time
import secrets
import hashlib
import hmac
import uuid
from io import BytesIO
from threading import Thread
from datetime import datetime, timezone, timedelta
from functools import wraps
from urllib.parse import urlencode
import asyncio

from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify, flash
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import discord
from discord import app_commands
from discord.ext import commands
from discord import ui, Interaction, ButtonStyle
from PIL import Image, ImageDraw, ImageFont

# ========================
# CONFIGURAÇÃO DO AMBIENTE
# ========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USER = os.getenv("GITHUB_USER", "pobonsanto-byte")
GITHUB_REPO = os.getenv("GITHUB_REPO", "imune-bot-data")
DATA_FILE = os.getenv("DATA_FILE", "data.json")
BRANCH = os.getenv("GITHUB_BRANCH", "main")
PORT = int(os.getenv("PORT", 8080))
GUILD_ID = os.getenv("GUILD_ID")

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://seu-site.onrender.com/callback")
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))

# ========================
# LOGS DE DIAGNÓSTICO
# ========================
print(f"🤖 BOT_TOKEN está configurado: {BOT_TOKEN is not None}")
print(f"🤖 BOT_TOKEN começa com: {BOT_TOKEN[:10] if BOT_TOKEN else 'N/A'}...")
print(f"📂 GITHUB_TOKEN está configurado: {GITHUB_TOKEN is not None}")
print(f"🏠 GUILD_ID: {GUILD_ID}")

# ========================
# FLASK APP
# ========================
app = Flask(__name__)
app.secret_key = SECRET_KEY

# ========================
# SEGURANÇA
# ========================
csrf = CSRFProtect(app)

# Flask-Limiter corrigido
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# ========================
# FUNÇÕES DO GITHUB
# ========================

def _gh_headers():
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

GITHUB_API_CONTENT = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{DATA_FILE}"

# Estrutura inicial dos dados
dados = {
    "xp": {},
    "nivel": {},
    "advertencias": {},
    "reacoes_cargos": {},
    "config": {
        "canal_boas_vindas": None,
        "mensagem_boas_vindas": "Olá {member}, seja bem-vindo(a)!",
        "fundo_boas_vindas": "",
        "taxa_xp": 3,
        "canal_levelup": None,
        "canal_logs": None,
        "canal_perfil": None,
        "canal_rank": None
    },
    "logs": [],
    "fila": {
        "nome": "Fila de Serviços",
        "configuracoes": {"tamanho_maximo": 50, "aberta": True},
        "entradas": [],
        "historico": []
    },
    "cargos_nivel": {},
    "canais_links_bloqueados": [],
    "botoes_cargos": {},
    "links_fila": {
        "discord_convite": "",
        "botoes_precos": []
    },
    "anti_spam": {
        "ativado": True,
        "limite_mensagens": 5,
        "intervalo_segundos": 5,
        "tempo_mute_minutos": 2,
        "remover_xp": True,
        "xp_penalidade": 50,
        "deletar_mensagens": True,
        "cargos_ignorados": ["Administrador", "Moderador", "Staff", "Dono"],
        "comandos_ignorados": [
            "$w", "$wa", "$wg", "$h", "$ha", "$hg",
            "$W", "$WA", "$WG", "$H", "$HA", "$HG",
            "$tu", "$TU", "$dk", "$mmi", "$vote", "$rolls", "$k", "$mu",
            "$daily", "$Daily", "$rep", "$Rep", "$rep+", "$Rep+",
            "$bitesthedust", "$kb", "$Kb", "$l", "$L", "$ldk", "$Ldk",
        ]
    },
    "usuarios": {},
    "categorias": [
        {"id": "cat1", "nome": "League of Legends", "slug": "league-of-legends", "ativo": True},
        {"id": "cat2", "nome": "Valorant", "slug": "valorant", "ativo": True},
        {"id": "cat3", "nome": "CS2", "slug": "cs2", "ativo": True},
        {"id": "cat4", "nome": "Fortnite", "slug": "fortnite", "ativo": True},
        {"id": "cat5", "nome": "Coaching", "slug": "coaching", "ativo": True},
        {"id": "cat6", "nome": "Boost", "slug": "boost", "ativo": True},
        {"id": "cat7", "nome": "Outros", "slug": "outros", "ativo": True}
    ],
    "servicos": [],
    "pedidos": [],
    "pagamentos": [],
    "transacoes_pontos": [],
    "recompensas": [
        {"id": "rec1", "nome": "5% de Desconto", "descricao": "Ganhe 5% de desconto na próxima compra", "pontos_necessarios": 100, "tipo": "desconto", "valor": 5, "status": "ativo", "ordem": 1},
        {"id": "rec2", "nome": "10% de Desconto", "descricao": "Ganhe 10% de desconto na próxima compra", "pontos_necessarios": 250, "tipo": "desconto", "valor": 10, "status": "ativo", "ordem": 2},
        {"id": "rec3", "nome": "20% de Desconto", "descricao": "Ganhe 20% de desconto na próxima compra", "pontos_necessarios": 500, "tipo": "desconto", "valor": 20, "status": "ativo", "ordem": 3},
        {"id": "rec4", "nome": "Serviço Grátis", "descricao": "Ganhe um serviço gratuito (valor até R$50)", "pontos_necessarios": 1000, "tipo": "servico_gratuito", "valor": 50, "status": "ativo", "ordem": 4}
    ],
    "resgates": [],
    "cupons": [],
    "cupons_utilizados": []
}

def carregar_dados_github():
    global dados
    try:
        r = requests.get(GITHUB_API_CONTENT, headers=_gh_headers(), params={"ref": BRANCH}, timeout=15)
        if r.status_code == 200:
            js = r.json()
            conteudo_b64 = js.get("content", "")
            if conteudo_b64:
                raw = base64.b64decode(conteudo_b64)
                carregado = json.loads(raw.decode("utf-8"))
                dados.update(carregado)
                
                if "usuarios" not in dados:
                    dados["usuarios"] = {}
                if "servicos" not in dados:
                    dados["servicos"] = []
                if "pedidos" not in dados:
                    dados["pedidos"] = []
                if "pagamentos" not in dados:
                    dados["pagamentos"] = []
                if "transacoes_pontos" not in dados:
                    dados["transacoes_pontos"] = []
                if "recompensas" not in dados:
                    dados["recompensas"] = [
                        {"id": "rec1", "nome": "5% de Desconto", "descricao": "Ganhe 5% de desconto na próxima compra", "pontos_necessarios": 100, "tipo": "desconto", "valor": 5, "status": "ativo", "ordem": 1},
                        {"id": "rec2", "nome": "10% de Desconto", "descricao": "Ganhe 10% de desconto na próxima compra", "pontos_necessarios": 250, "tipo": "desconto", "valor": 10, "status": "ativo", "ordem": 2},
                        {"id": "rec3", "nome": "20% de Desconto", "descricao": "Ganhe 20% de desconto na próxima compra", "pontos_necessarios": 500, "tipo": "desconto", "valor": 20, "status": "ativo", "ordem": 3},
                        {"id": "rec4", "nome": "Serviço Grátis", "descricao": "Ganhe um serviço gratuito (valor até R$50)", "pontos_necessarios": 1000, "tipo": "servico_gratuito", "valor": 50, "status": "ativo", "ordem": 4}
                    ]
                if "resgates" not in dados:
                    dados["resgates"] = []
                if "cupons" not in dados:
                    dados["cupons"] = []
                if "cupons_utilizados" not in dados:
                    dados["cupons_utilizados"] = []
                if "categorias" not in dados:
                    dados["categorias"] = [
                        {"id": "cat1", "nome": "League of Legends", "slug": "league-of-legends", "ativo": True},
                        {"id": "cat2", "nome": "Valorant", "slug": "valorant", "ativo": True},
                        {"id": "cat3", "nome": "CS2", "slug": "cs2", "ativo": True},
                        {"id": "cat4", "nome": "Fortnite", "slug": "fortnite", "ativo": True},
                        {"id": "cat5", "nome": "Coaching", "slug": "coaching", "ativo": True},
                        {"id": "cat6", "nome": "Boost", "slug": "boost", "ativo": True},
                        {"id": "cat7", "nome": "Outros", "slug": "outros", "ativo": True}
                    ]
                
                if "fila" not in dados:
                    dados["fila"] = {
                        "nome": "Fila de Serviços",
                        "configuracoes": {"tamanho_maximo": 50, "aberta": True},
                        "entradas": [],
                        "historico": []
                    }
                if "botoes_cargos" not in dados:
                    dados["botoes_cargos"] = {}
                if "cargos_nivel" not in dados:
                    dados["cargos_nivel"] = {}
                if "canais_links_bloqueados" not in dados:
                    dados["canais_links_bloqueados"] = []
                if "links_fila" not in dados:
                    dados["links_fila"] = {"discord_convite": "", "botoes_precos": []}
                if "anti_spam" not in dados:
                    dados["anti_spam"] = {
                        "ativado": True,
                        "limite_mensagens": 5,
                        "intervalo_segundos": 5,
                        "tempo_mute_minutos": 2,
                        "remover_xp": True,
                        "xp_penalidade": 50,
                        "deletar_mensagens": True,
                        "cargos_ignorados": ["Administrador", "Moderador", "Staff", "Dono"],
                        "comandos_ignorados": ["$w", "$wa", "$wg", "$h", "$ha", "$hg"]
                    }
                if "config" not in dados:
                    dados["config"] = {
                        "canal_boas_vindas": None,
                        "mensagem_boas_vindas": "Olá {member}, seja bem-vindo(a)!",
                        "fundo_boas_vindas": "",
                        "taxa_xp": 3,
                        "canal_levelup": None,
                        "canal_logs": None,
                        "canal_perfil": None,
                        "canal_rank": None
                    }
                
                print("✅ Dados carregados do GitHub.")
                return True
        else:
            print(f"⚠️ GitHub GET retornou {r.status_code} — iniciando com dados limpos.")
    except Exception as e:
        print(f"❌ Erro ao carregar dados do GitHub: {e}")
    return False

def salvar_dados_github(mensagem="Atualização do bot"):
    try:
        r = requests.get(GITHUB_API_CONTENT, headers=_gh_headers(), params={"ref": BRANCH}, timeout=15)
        sha = None
        if r.status_code == 200:
            sha = r.json().get("sha")

        conteudo = json.dumps(dados, ensure_ascii=False, indent=2).encode("utf-8")
        payload = {
            "message": f"{mensagem} @ {agora_br().isoformat()}",
            "content": base64.b64encode(conteudo).decode("utf-8"),
            "branch": BRANCH
        }
        if sha:
            payload["sha"] = sha

        put = requests.put(GITHUB_API_CONTENT, headers=_gh_headers(), json=payload, timeout=30)
        if put.status_code in (200, 201):
            print("✅ Dados salvos no GitHub.")
            return True
        else:
            print(f"❌ Erro ao salvar no GitHub: {put.status_code}, {put.text[:400]}")
    except Exception as e:
        print(f"❌ Exception saving to GitHub: {e}")
    return False

# ========================
# FUNÇÕES DE UTILIDADE
# ========================

def agora_br():
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-3)))

def gerar_id():
    return str(uuid.uuid4().hex[:8])

def gerar_numero_pedido():
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    codigo = str(uuid.uuid4().hex[:6].upper())
    return f"PED-{timestamp}-{codigo}"

def gerar_codigo_cupom():
    return str(uuid.uuid4().hex[:8].upper())

def calcular_pontos(valor, taxa=10):
    return int(valor * taxa)

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario' not in session or not session['usuario'].get('is_admin', False):
            flash('Acesso negado. Área restrita a administradores.', 'danger')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario' not in session:
            flash('Faça login para acessar esta página.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def obter_usuario_sessao():
    if 'usuario' not in session:
        return None
    discord_id = session['usuario'].get('discord_id')
    if discord_id:
        return dados["usuarios"].get(discord_id)
    return None

def obter_ou_criar_usuario(discord_data):
    discord_id = str(discord_data['id'])
    if discord_id not in dados["usuarios"]:
        dados["usuarios"][discord_id] = {
            "discord_id": discord_id,
            "discord_nome": discord_data['username'],
            "discord_avatar": discord_data.get('avatar'),
            "email": None,
            "data_cadastro": datetime.utcnow().isoformat(),
            "ultimo_login": datetime.utcnow().isoformat(),
            "pontos": 0,
            "total_pontos_ganhos": 0,
            "total_pontos_gastos": 0,
            "is_admin": False,
            "is_active": True
        }
        salvar_dados_github(f"Novo usuário: {discord_data['username']}")
    else:
        dados["usuarios"][discord_id]["discord_nome"] = discord_data['username']
        dados["usuarios"][discord_id]["discord_avatar"] = discord_data.get('avatar')
        dados["usuarios"][discord_id]["ultimo_login"] = datetime.utcnow().isoformat()
        salvar_dados_github(f"Login: {discord_data['username']}")
    
    return dados["usuarios"][discord_id]

# ========================
# FUNÇÕES DE FILA
# ========================

def obter_dados_fila():
    dados.setdefault("fila", {
        "nome": "Fila de Serviços",
        "configuracoes": {"tamanho_maximo": 50, "aberta": True},
        "entradas": [],
        "historico": []
    })
    return dados["fila"]

def adicionar_fila(nome_usuario: str, servico: str, jogo: str = "", usuario_id: str = None):
    fila = obter_dados_fila()
    
    if not fila["configuracoes"]["aberta"]:
        return False, "Fila está fechada no momento"
    
    if len(fila["entradas"]) >= fila["configuracoes"]["tamanho_maximo"]:
        return False, "Fila está cheia"
    
    for entrada in fila["entradas"]:
        if entrada["nome_usuario"].lower() == nome_usuario.lower():
            return False, f"{nome_usuario} já está na fila"
    
    entrada = {
        "id": str(int(datetime.now().timestamp() * 1000)),
        "nome_usuario": nome_usuario,
        "servico": servico,
        "jogo": jogo,
        "usuario_id": usuario_id or nome_usuario,
        "timestamp": agora_br().isoformat(),
        "status": "aguardando",
        "posicao": len(fila["entradas"]) + 1
    }
    
    fila["entradas"].append(entrada)
    atualizar_posicoes(fila["entradas"])
    salvar_dados_github(f"fila_adicionar: {nome_usuario} - {servico} - {jogo}")
    return True, entrada

def remover_fila(entrada_id: str):
    fila = obter_dados_fila()
    
    for i, entrada in enumerate(fila["entradas"]):
        if entrada["id"] == entrada_id:
            removido = fila["entradas"].pop(i)
            removido["removido_em"] = agora_br().isoformat()
            fila["historico"].append(removido)
            if len(fila["historico"]) > 100:
                fila["historico"] = fila["historico"][-100:]
            atualizar_posicoes(fila["entradas"])
            salvar_dados_github(f"fila_remover: {removido['nome_usuario']}")
            return True, removido
    return False, None

def atualizar_posicoes(entradas):
    for i, entrada in enumerate(entradas):
        entrada["posicao"] = i + 1
        entrada["status"] = "aguardando"

def mover_cima(entrada_id: str):
    fila = obter_dados_fila()
    entradas = fila["entradas"]
    for i, entrada in enumerate(entradas):
        if entrada["id"] == entrada_id and i > 0:
            entradas[i], entradas[i-1] = entradas[i-1], entradas[i]
            atualizar_posicoes(entradas)
            salvar_dados_github("Fila: mover cima")
            return True, entrada
    return False, None

def mover_baixo(entrada_id: str):
    fila = obter_dados_fila()
    entradas = fila["entradas"]
    for i, entrada in enumerate(entradas):
        if entrada["id"] == entrada_id and i < len(entradas) - 1:
            entradas[i], entradas[i+1] = entradas[i+1], entradas[i]
            atualizar_posicoes(entradas)
            salvar_dados_github("Fila: mover baixo")
            return True, entrada
    return False, None

def concluir_servico(entrada_id: str):
    fila = obter_dados_fila()
    for i, entrada in enumerate(fila["entradas"]):
        if entrada["id"] == entrada_id:
            removido = fila["entradas"].pop(i)
            removido["status"] = "concluido"
            removido["concluido_em"] = agora_br().isoformat()
            fila["historico"].append(removido)
            atualizar_posicoes(fila["entradas"])
            salvar_dados_github(f"fila_concluir: {removido['nome_usuario']}")
            return True, removido
    return False, None

def limpar_fila():
    fila = obter_dados_fila()
    for entrada in fila["entradas"]:
        entrada["status"] = "limpo"
        entrada["limpo_em"] = agora_br().isoformat()
        fila["historico"].append(entrada)
    fila["entradas"] = []
    salvar_dados_github("fila_limpa")
    return True

def alternar_fila(aberto: bool = None):
    fila = obter_dados_fila()
    if aberto is None:
        fila["configuracoes"]["aberta"] = not fila["configuracoes"]["aberta"]
    else:
        fila["configuracoes"]["aberta"] = aberto
    salvar_dados_github("Fila: alternar status")
    return fila["configuracoes"]["aberta"]

def definir_tamanho_maximo(tamanho: int):
    fila = obter_dados_fila()
    fila["configuracoes"]["tamanho_maximo"] = max(1, min(tamanho, 100))
    salvar_dados_github("Fila: tamanho maximo")
    return fila["configuracoes"]["tamanho_maximo"]

def definir_nome_fila(nome: str):
    fila = obter_dados_fila()
    fila["nome"] = nome[:50]
    salvar_dados_github("Fila: nome")
    return fila["nome"]

def obter_links_fila():
    dados.setdefault("links_fila", {"discord_convite": "", "botoes_precos": []})
    return dados["links_fila"]

def salvar_links_fila(discord_convite: str):
    dados["links_fila"]["discord_convite"] = discord_convite or ""
    return salvar_dados_github("Links da fila atualizados")

def adicionar_botao_preco(nome: str, url: str):
    if not nome or not url:
        return False
    dados["links_fila"].setdefault("botoes_precos", [])
    dados["links_fila"]["botoes_precos"].append({"nome": nome[:30], "url": url[:500]})
    return salvar_dados_github(f"Botão de preço adicionado: {nome}")

def remover_botao_preco(index: int):
    botoes = dados["links_fila"].get("botoes_precos", [])
    if 0 <= index < len(botoes):
        removido = botoes.pop(index)
        salvar_dados_github(f"Botão de preço removido: {removido['nome']}")
        return True
    return False

def atualizar_botao_preco(index: int, nome: str, url: str):
    botoes = dados["links_fila"].get("botoes_precos", [])
    if 0 <= index < len(botoes):
        botoes[index] = {"nome": nome[:30], "url": url[:500]}
        salvar_dados_github(f"Botão de preço atualizado: {nome}")
        return True
    return False

# ========================
# FUNÇÕES DOS SERVIÇOS
# ========================

def obter_categorias():
    return dados.get("categorias", [])

def obter_categoria_por_slug(slug):
    for cat in dados.get("categorias", []):
        if cat.get("slug") == slug and cat.get("ativo", True):
            return cat
    return None

def obter_servicos(filtro_categoria=None, busca=None):
    servicos = dados.get("servicos", [])
    if filtro_categoria:
        servicos = [s for s in servicos if s.get("categoria_id") == filtro_categoria]
    if busca:
        busca_lower = busca.lower()
        servicos = [s for s in servicos if busca_lower in s.get("nome", "").lower() or busca_lower in s.get("descricao", "").lower()]
    return sorted(servicos, key=lambda s: (-s.get("destaque", False), s.get("ordem", 0)))

def obter_servico_por_slug(slug):
    for s in dados.get("servicos", []):
        if s.get("slug") == slug and s.get("status") == "ativo":
            return s
    return None

def obter_servico_por_id(servico_id):
    for s in dados.get("servicos", []):
        if s.get("id") == servico_id:
            return s
    return None

def criar_servico(dados_servico):
    servicos = dados.get("servicos", [])
    servicos.append(dados_servico)
    salvar_dados_github(f"Serviço criado: {dados_servico.get('nome')}")

def atualizar_servico(servico_id, dados_servico):
    servicos = dados.get("servicos", [])
    for i, s in enumerate(servicos):
        if s.get("id") == servico_id:
            servicos[i] = dados_servico
            salvar_dados_github(f"Serviço atualizado: {dados_servico.get('nome')}")
            return True
    return False

def deletar_servico(servico_id):
    servicos = dados.get("servicos", [])
    for i, s in enumerate(servicos):
        if s.get("id") == servico_id:
            servicos.pop(i)
            salvar_dados_github("Serviço deletado")
            return True
    return False

# ========================
# FUNÇÕES DOS PEDIDOS
# ========================

def obter_pedidos_usuario(discord_id):
    return [p for p in dados.get("pedidos", []) if p.get("usuario_id") == discord_id]

def obter_pedido_por_numero(numero):
    for p in dados.get("pedidos", []):
        if p.get("numero") == numero:
            return p
    return None

def criar_pedido(dados_pedido):
    pedidos = dados.get("pedidos", [])
    pedidos.append(dados_pedido)
    salvar_dados_github(f"Pedido criado: {dados_pedido.get('numero')}")
    return dados_pedido

def atualizar_pedido(numero, dados_pedido):
    pedidos = dados.get("pedidos", [])
    for i, p in enumerate(pedidos):
        if p.get("numero") == numero:
            pedidos[i] = dados_pedido
            salvar_dados_github(f"Pedido atualizado: {numero}")
            return True
    return False

# ========================
# FUNÇÕES DE PONTOS
# ========================

def adicionar_pontos(discord_id, quantidade, descricao, referencia_id=None):
    usuario = dados["usuarios"].get(discord_id)
    if not usuario:
        return False
    
    usuario["pontos"] = usuario.get("pontos", 0) + quantidade
    usuario["total_pontos_ganhos"] = usuario.get("total_pontos_ganhos", 0) + quantidade
    
    transacao = {
        "id": gerar_id(),
        "usuario_id": discord_id,
        "tipo": "ganho",
        "quantidade": quantidade,
        "descricao": descricao,
        "referencia_id": referencia_id,
        "data_criacao": datetime.utcnow().isoformat()
    }
    dados.setdefault("transacoes_pontos", []).append(transacao)
    salvar_dados_github(f"Pontos adicionados: {quantidade} para {discord_id}")
    return True

def gastar_pontos(discord_id, quantidade, descricao, referencia_id=None):
    usuario = dados["usuarios"].get(discord_id)
    if not usuario or usuario.get("pontos", 0) < quantidade:
        return False
    
    usuario["pontos"] = usuario.get("pontos", 0) - quantidade
    usuario["total_pontos_gastos"] = usuario.get("total_pontos_gastos", 0) + quantidade
    
    transacao = {
        "id": gerar_id(),
        "usuario_id": discord_id,
        "tipo": "gasto",
        "quantidade": quantidade,
        "descricao": descricao,
        "referencia_id": referencia_id,
        "data_criacao": datetime.utcnow().isoformat()
    }
    dados.setdefault("transacoes_pontos", []).append(transacao)
    salvar_dados_github(f"Pontos gastos: {quantidade} para {discord_id}")
    return True

# ========================
# SISTEMA DE AÇÕES DO BOT
# ========================

acoes_fila_bot = []
processador_acoes_task = None
processador_acoes_rodando = False

def executar_acao_bot(tipo_acao, **kwargs):
    acoes_fila_bot.append({
        "tipo": tipo_acao,
        "dados": kwargs,
        "timestamp": agora_br().isoformat()
    })
    print(f"🤖 [AÇÃO BOT] Adicionada ação: {tipo_acao}")
    return True

async def executar_acao_bot_interno(acao):
    tipo_acao = acao["tipo"]
    dados_acao = acao["dados"]
    
    print(f"\n{'='*50}")
    print(f"🤖 EXECUTANDO AÇÃO: {tipo_acao}")
    print(f"{'='*50}")
    
    if not bot.is_ready():
        print("❌ Bot não está pronto!")
        return False
    
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID else None
    if not guild:
        print(f"❌ Servidor {GUILD_ID} não encontrado!")
        return False
    
    try:
        if tipo_acao == "criar_embed":
            canal_id = int(dados_acao["canal_id"])
            canal = guild.get_channel(canal_id)
            if not canal:
                return False
            
            cor = discord.Color.blue()
            if dados_acao.get('cor'):
                try:
                    cor_hex = dados_acao['cor'].replace('#', '')
                    cor = discord.Color(int(cor_hex, 16))
                except:
                    pass
            
            embed = discord.Embed(
                title=dados_acao["titulo"],
                description=dados_acao["corpo"],
                color=cor
            )
            
            if dados_acao.get('url_imagem'):
                embed.set_image(url=dados_acao['url_imagem'])
            
            texto_mencao = ""
            if dados_acao.get('mencao') == 'everyone':
                texto_mencao = "@everyone"
            elif dados_acao.get('mencao') == 'here':
                texto_mencao = "@here"
            
            await canal.send(content=texto_mencao, embed=embed)
            print(f"✅ Embed enviada para #{canal.name}")
            return True
        
        elif tipo_acao == "notificar_pedido":
            pedido_numero = dados_acao.get("pedido_numero")
            status = dados_acao.get("status")
            
            canal_id = dados.get("config", {}).get("canal_logs")
            if canal_id:
                canal = guild.get_channel(int(canal_id))
                if canal:
                    embed = discord.Embed(
                        title=f"📦 Pedido {pedido_numero}",
                        description=f"Status: **{status}**",
                        color=discord.Color.green() if status in ['pago', 'finalizado'] else discord.Color.gold()
                    )
                    await canal.send(embed=embed)
            return True
        
        else:
            print(f"❌ Tipo de ação desconhecido: {tipo_acao}")
            return False
    
    except Exception as e:
        print(f"❌ Erro: {e}")
        return False

async def processar_acoes_bot_continuo():
    global processador_acoes_rodando
    
    print("\n" + "="*60)
    print("🚀 PROCESSADOR DE AÇÕES INICIADO")
    print("="*60)
    
    processador_acoes_rodando = True
    
    if not bot.is_ready():
        await bot.wait_until_ready()
        await asyncio.sleep(2)
    
    while processador_acoes_rodando and not bot.is_closed():
        try:
            if acoes_fila_bot:
                acao = acoes_fila_bot.pop(0)
                await executar_acao_bot_interno(acao)
            await asyncio.sleep(1)
        except Exception as e:
            print(f"⚠️ Erro no processador: {e}")
            await asyncio.sleep(5)
    
    print("⏹️ PROCESSADOR DE AÇÕES ENCERRADO")

def iniciar_processador_acoes():
    global processador_acoes_task, processador_acoes_rodando
    if processador_acoes_rodando:
        return False
    try:
        processador_acoes_task = bot.loop.create_task(processar_acoes_bot_continuo())
        print("✅ Processador de ações iniciado!")
        return True
    except Exception as e:
        print(f"❌ Erro ao iniciar processador: {e}")
        return False

# ========================
# TEMPLATES HTML (EMBUTIDOS)
# ========================

# Template da página inicial
HOME_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Imune Bot - Plataforma de Serviços</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0a0a0a, #1a1a1a); min-height: 100vh; color: #e0e0e0; }
        .header { background: #121212; padding: 20px; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; }
        .header h1 { color: #5865F2; font-size: 24px; }
        .header h1 span { color: #ffd93d; }
        .header-actions { display: flex; gap: 10px; flex-wrap: wrap; }
        .btn { padding: 10px 20px; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; text-decoration: none; display: inline-block; transition: all 0.3s; }
        .btn-primary { background: #5865F2; color: white; }
        .btn-primary:hover { background: #4752C4; transform: translateY(-2px); }
        .btn-success { background: #10b981; color: white; }
        .btn-success:hover { background: #059669; transform: translateY(-2px); }
        .btn-danger { background: #ef4444; color: white; }
        .btn-danger:hover { background: #dc2626; transform: translateY(-2px); }
        .btn-outline { background: transparent; color: #e0e0e0; border: 1px solid #555; }
        .btn-outline:hover { background: #333; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        .hero { text-align: center; padding: 60px 20px; }
        .hero h2 { font-size: 48px; background: linear-gradient(135deg, #5865F2, #ffd93d); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 20px; }
        .hero p { font-size: 20px; color: #aaa; max-width: 600px; margin: 0 auto 30px; }
        .status-bot { display: inline-block; padding: 10px 20px; border-radius: 10px; margin: 20px 0; font-weight: bold; }
        .online { background: #1a472a; color: #4ade80; border: 1px solid #2ecc71; }
        .offline { background: #7f1d1d; color: #f87171; border: 1px solid #ef4444; }
        .features { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin: 40px 0; }
        .feature-card { background: #121212; border: 1px solid #333; border-radius: 15px; padding: 25px; text-align: center; transition: all 0.3s; }
        .feature-card:hover { transform: translateY(-5px); border-color: #5865F2; }
        .feature-card .icon { font-size: 40px; margin-bottom: 15px; }
        .feature-card h3 { color: #5865F2; margin-bottom: 10px; }
        .feature-card p { color: #aaa; font-size: 14px; }
        .servicos-destaque { margin: 40px 0; }
        .servicos-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; margin-top: 20px; }
        .servico-card { background: #121212; border: 1px solid #333; border-radius: 15px; overflow: hidden; transition: all 0.3s; }
        .servico-card:hover { transform: translateY(-5px); border-color: #5865F2; }
        .servico-card .servico-img { width: 100%; height: 180px; background: #1a1a1a; display: flex; align-items: center; justify-content: center; font-size: 48px; }
        .servico-card .servico-info { padding: 20px; }
        .servico-card .servico-info h3 { color: #ffd93d; margin-bottom: 5px; }
        .servico-card .servico-info .preco { color: #4ade80; font-size: 20px; font-weight: bold; }
        .servico-card .servico-info .preco-antigo { color: #888; text-decoration: line-through; font-size: 14px; margin-left: 10px; }
        .servico-card .servico-info .desc { color: #aaa; font-size: 14px; margin: 10px 0; }
        .footer { text-align: center; padding: 30px; color: #666; border-top: 1px solid #333; margin-top: 40px; font-size: 14px; }
        @media (max-width: 768px) { .hero h2 { font-size: 32px; } .header { flex-direction: column; gap: 10px; text-align: center; } }
    </style>
</head>
<body>
    <header class="header">
        <h1>🎮 Imune <span>Bot</span></h1>
        <div class="header-actions">
            <a href="/servicos" class="btn btn-primary">📦 Serviços</a>
            <a href="/fila" class="btn btn-success">📋 Fila</a>
            {% if session.get('usuario') %}
                <a href="/dashboard" class="btn btn-primary">👤 Painel</a>
                <a href="/logout" class="btn btn-danger">🚪 Sair</a>
            {% else %}
                <a href="/login" class="btn btn-primary">🔐 Login</a>
            {% endif %}
        </div>
    </header>

    <div class="container">
        <div class="hero">
            <h2>🚀 Serviços para Jogadores</h2>
            <p>Compre serviços, acumule pontos e ganhe descontos exclusivos!</p>
            <div class="status-bot {{ classe_bot }}">{{ status_bot }}</div>
            <div>
                <a href="/servicos" class="btn btn-success" style="font-size:18px; padding:15px 40px;">🛒 Ver Serviços</a>
            </div>
        </div>

        <div class="features">
            <div class="feature-card">
                <div class="icon">🎯</div>
                <h3>Serviços Profissionais</h3>
                <p>Boost, coaching, elojob e muito mais para diversos jogos</p>
            </div>
            <div class="feature-card">
                <div class="icon">⭐</div>
                <h3>Sistema de Pontos</h3>
                <p>Ganhe pontos a cada compra e troque por descontos exclusivos</p>
            </div>
            <div class="feature-card">
                <div class="icon">💳</div>
                <h3>Pagamento via PIX</h3>
                <p>Pagamento rápido e seguro com PIX, confirmação automática</p>
            </div>
            <div class="feature-card">
                <div class="icon">📋</div>
                <h3>Fila de Serviços</h3>
                <p>Acompanhe sua posição na fila em tempo real</p>
            </div>
        </div>

        {% if servicos_destaque %}
        <div class="servicos-destaque">
            <h2 style="color: #5865F2;">🔥 Serviços em Destaque</h2>
            <div class="servicos-grid">
                {% for servico in servicos_destaque %}
                <div class="servico-card">
                    <div class="servico-img">{{ servico.get('icone', '🎮') }}</div>
                    <div class="servico-info">
                        <h3>{{ servico.nome }}</h3>
                        <div>
                            <span class="preco">R$ {{ "%.2f"|format(servico.preco) }}</span>
                            {% if servico.preco_promocional %}
                            <span class="preco-antigo">R$ {{ "%.2f"|format(servico.preco_promocional) }}</span>
                            {% endif %}
                        </div>
                        <p class="desc">{{ servico.descricao[:100] }}{% if servico.descricao|length > 100 %}...{% endif %}</p>
                        <a href="/servico/{{ servico.slug }}" class="btn btn-primary" style="width:100%; text-align:center;">Ver Detalhes</a>
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
        {% endif %}
    </div>

    <div class="footer">
        <p>© 2024 Imune Bot - Todos os direitos reservados</p>
        <p style="margin-top: 5px; font-size: 12px;">Feito com ❤️ para a comunidade gamer</p>
    </div>
</body>
</html>
"""

# Template da página de serviços
SERVICOS_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Serviços - Imune Bot</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0a0a0a, #1a1a1a); min-height: 100vh; color: #e0e0e0; }
        .header { background: #121212; padding: 20px; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; }
        .header h1 { color: #5865F2; font-size: 24px; }
        .header h1 span { color: #ffd93d; }
        .header-actions { display: flex; gap: 10px; flex-wrap: wrap; }
        .btn { padding: 10px 20px; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; text-decoration: none; display: inline-block; transition: all 0.3s; }
        .btn-primary { background: #5865F2; color: white; }
        .btn-primary:hover { background: #4752C4; transform: translateY(-2px); }
        .btn-success { background: #10b981; color: white; }
        .btn-success:hover { background: #059669; transform: translateY(-2px); }
        .btn-danger { background: #ef4444; color: white; }
        .btn-danger:hover { background: #dc2626; transform: translateY(-2px); }
        .btn-outline { background: transparent; color: #e0e0e0; border: 1px solid #555; }
        .btn-outline:hover { background: #333; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        .filtros { display: flex; gap: 15px; margin-bottom: 30px; flex-wrap: wrap; align-items: center; }
        .filtros select, .filtros input { padding: 10px 15px; background: #121212; border: 1px solid #333; border-radius: 8px; color: #e0e0e0; font-size: 14px; }
        .filtros select:focus, .filtros input:focus { outline: none; border-color: #5865F2; }
        .servicos-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; }
        .servico-card { background: #121212; border: 1px solid #333; border-radius: 15px; overflow: hidden; transition: all 0.3s; }
        .servico-card:hover { transform: translateY(-5px); border-color: #5865F2; }
        .servico-card .servico-img { width: 100%; height: 180px; background: #1a1a1a; display: flex; align-items: center; justify-content: center; font-size: 48px; }
        .servico-card .servico-info { padding: 20px; }
        .servico-card .servico-info h3 { color: #ffd93d; margin-bottom: 5px; }
        .servico-card .servico-info .categoria { color: #888; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; }
        .servico-card .servico-info .preco { color: #4ade80; font-size: 20px; font-weight: bold; }
        .servico-card .servico-info .preco-antigo { color: #888; text-decoration: line-through; font-size: 14px; margin-left: 10px; }
        .servico-card .servico-info .desc { color: #aaa; font-size: 14px; margin: 10px 0; }
        .servico-card .servico-info .btn { width: 100%; text-align: center; }
        .vazio { text-align: center; padding: 60px 20px; color: #888; }
        .vazio .icon { font-size: 64px; margin-bottom: 20px; }
        .pagination { display: flex; justify-content: center; gap: 10px; margin-top: 30px; }
        .pagination a { padding: 8px 16px; background: #121212; border: 1px solid #333; border-radius: 8px; color: #e0e0e0; text-decoration: none; }
        .pagination a:hover { background: #333; }
        .pagination a.active { background: #5865F2; border-color: #5865F2; }
        .footer { text-align: center; padding: 30px; color: #666; border-top: 1px solid #333; margin-top: 40px; font-size: 14px; }
        @media (max-width: 768px) { .header { flex-direction: column; gap: 10px; text-align: center; } .filtros { flex-direction: column; } }
    </style>
</head>
<body>
    <header class="header">
        <h1>🎮 Imune <span>Bot</span></h1>
        <div class="header-actions">
            <a href="/" class="btn btn-outline">🏠 Início</a>
            <a href="/fila" class="btn btn-success">📋 Fila</a>
            {% if session.get('usuario') %}
                <a href="/dashboard" class="btn btn-primary">👤 Painel</a>
                <a href="/logout" class="btn btn-danger">🚪 Sair</a>
            {% else %}
                <a href="/login" class="btn btn-primary">🔐 Login</a>
            {% endif %}
        </div>
    </header>

    <div class="container">
        <h2 style="color: #5865F2; margin-bottom: 20px;">📦 Nossos Serviços</h2>
        
        <div class="filtros">
            <select id="filtro-categoria" onchange="window.location.href='?categoria='+this.value">
                <option value="">Todas Categorias</option>
                {% for cat in categorias %}
                <option value="{{ cat.slug }}" {% if categoria_atual == cat.slug %}selected{% endif %}>{{ cat.nome }}</option>
                {% endfor %}
            </select>
            <form method="GET" style="display: flex; gap: 10px; flex:1;">
                <input type="text" name="busca" placeholder="🔍 Buscar serviço..." value="{{ busca }}" style="flex:1; padding: 10px 15px; background: #121212; border: 1px solid #333; border-radius: 8px; color: #e0e0e0;">
                <button type="submit" class="btn btn-primary">Buscar</button>
            </form>
        </div>

        {% if servicos %}
        <div class="servicos-grid">
            {% for servico in servicos %}
            <div class="servico-card">
                <div class="servico-img">{{ servico.get('icone', '🎮') }}</div>
                <div class="servico-info">
                    <span class="categoria">{{ servico.categoria_nome or 'Geral' }}</span>
                    <h3>{{ servico.nome }}</h3>
                    <div>
                        <span class="preco">R$ {{ "%.2f"|format(servico.preco) }}</span>
                        {% if servico.preco_promocional %}
                        <span class="preco-antigo">R$ {{ "%.2f"|format(servico.preco_promocional) }}</span>
                        {% endif %}
                    </div>
                    <p class="desc">{{ servico.descricao[:120] }}{% if servico.descricao|length > 120 %}...{% endif %}</p>
                    <a href="/servico/{{ servico.slug }}" class="btn btn-primary">Ver Detalhes</a>
                </div>
            </div>
            {% endfor %}
        </div>
        {% else %}
        <div class="vazio">
            <div class="icon">📭</div>
            <h3>Nenhum serviço encontrado</h3>
            <p style="color: #666;">Tente outra categoria ou termo de busca</p>
        </div>
        {% endif %}

        <div class="pagination">
            {% if page > 1 %}<a href="?page={{ page - 1 }}{% if categoria_atual %}&categoria={{ categoria_atual }}{% endif %}{% if busca %}&busca={{ busca }}{% endif %}">Anterior</a>{% endif %}
            <span style="padding: 8px 16px; color: #888;">Página {{ page }} de {{ total_pages }}</span>
            {% if page < total_pages %}<a href="?page={{ page + 1 }}{% if categoria_atual %}&categoria={{ categoria_atual }}{% endif %}{% if busca %}&busca={{ busca }}{% endif %}">Próxima</a>{% endif %}
        </div>
    </div>

    <div class="footer">
        <p>© 2024 Imune Bot - Todos os direitos reservados</p>
    </div>
</body>
</html>
"""

# Template de detalhes do serviço
DETALHES_SERVICO_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ servico.nome }} - Imune Bot</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0a0a0a, #1a1a1a); min-height: 100vh; color: #e0e0e0; }
        .header { background: #121212; padding: 20px; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; }
        .header h1 { color: #5865F2; font-size: 24px; }
        .header h1 span { color: #ffd93d; }
        .header-actions { display: flex; gap: 10px; flex-wrap: wrap; }
        .btn { padding: 10px 20px; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; text-decoration: none; display: inline-block; transition: all 0.3s; }
        .btn-primary { background: #5865F2; color: white; }
        .btn-primary:hover { background: #4752C4; transform: translateY(-2px); }
        .btn-success { background: #10b981; color: white; }
        .btn-success:hover { background: #059669; transform: translateY(-2px); }
        .btn-danger { background: #ef4444; color: white; }
        .btn-danger:hover { background: #dc2626; transform: translateY(-2px); }
        .btn-outline { background: transparent; color: #e0e0e0; border: 1px solid #555; }
        .btn-outline:hover { background: #333; }
        .btn-lg { padding: 15px 40px; font-size: 18px; }
        .container { max-width: 1000px; margin: 0 auto; padding: 20px; }
        .servico-detalhe { display: grid; grid-template-columns: 1fr 1fr; gap: 40px; margin: 30px 0; }
        .servico-imagem { background: #121212; border-radius: 15px; border: 1px solid #333; padding: 40px; text-align: center; font-size: 120px; min-height: 300px; display: flex; align-items: center; justify-content: center; }
        .servico-info h2 { color: #ffd93d; font-size: 32px; margin-bottom: 10px; }
        .servico-info .categoria { color: #888; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; }
        .servico-info .preco { color: #4ade80; font-size: 36px; font-weight: bold; margin: 15px 0; }
        .servico-info .preco-antigo { color: #888; text-decoration: line-through; font-size: 20px; margin-left: 15px; }
        .servico-info .desc { color: #aaa; font-size: 16px; line-height: 1.8; margin: 20px 0; }
        .servico-info .tempo { color: #ffd93d; margin: 10px 0; }
        .servico-info .btn { width: 100%; text-align: center; }
        .relacionados { margin-top: 40px; }
        .relacionados h3 { color: #5865F2; margin-bottom: 20px; }
        .relacionados-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; }
        .relacionados-grid .item { background: #121212; border: 1px solid #333; border-radius: 10px; padding: 15px; text-align: center; transition: all 0.3s; }
        .relacionados-grid .item:hover { border-color: #5865F2; transform: translateY(-3px); }
        .relacionados-grid .item .nome { color: #ffd93d; }
        .relacionados-grid .item .preco { color: #4ade80; }
        .footer { text-align: center; padding: 30px; color: #666; border-top: 1px solid #333; margin-top: 40px; font-size: 14px; }
        @media (max-width: 768px) { .servico-detalhe { grid-template-columns: 1fr; } .header { flex-direction: column; gap: 10px; text-align: center; } }
    </style>
</head>
<body>
    <header class="header">
        <h1>🎮 Imune <span>Bot</span></h1>
        <div class="header-actions">
            <a href="/" class="btn btn-outline">🏠 Início</a>
            <a href="/servicos" class="btn btn-primary">📦 Serviços</a>
            <a href="/fila" class="btn btn-success">📋 Fila</a>
            {% if session.get('usuario') %}
                <a href="/dashboard" class="btn btn-primary">👤 Painel</a>
                <a href="/logout" class="btn btn-danger">🚪 Sair</a>
            {% else %}
                <a href="/login" class="btn btn-primary">🔐 Login</a>
            {% endif %}
        </div>
    </header>

    <div class="container">
        <nav style="margin: 20px 0; color: #888;">
            <a href="/" style="color: #5865F2; text-decoration: none;">Início</a> &gt;
            <a href="/servicos" style="color: #5865F2; text-decoration: none;">Serviços</a> &gt;
            <span>{{ servico.nome }}</span>
        </nav>

        <div class="servico-detalhe">
            <div class="servico-imagem">{{ servico.get('icone', '🎮') }}</div>
            <div class="servico-info">
                <span class="categoria">{{ servico.categoria_nome or 'Geral' }}</span>
                <h2>{{ servico.nome }}</h2>
                <div>
                    <span class="preco">R$ {{ "%.2f"|format(servico.preco) }}</span>
                    {% if servico.preco_promocional %}
                    <span class="preco-antigo">R$ {{ "%.2f"|format(servico.preco_promocional) }}</span>
                    {% endif %}
                </div>
                {% if servico.tempo_estimado %}
                <div class="tempo">⏱️ Tempo estimado: {{ servico.tempo_estimado }}</div>
                {% endif %}
                <p class="desc">{{ servico.descricao|safe }}</p>
                <a href="/comprar/{{ servico.slug }}" class="btn btn-success btn-lg">🛒 Comprar Agora</a>
            </div>
        </div>

        {% if servicos_relacionados %}
        <div class="relacionados">
            <h3>🔄 Serviços Relacionados</h3>
            <div class="relacionados-grid">
                {% for s in servicos_relacionados %}
                <a href="/servico/{{ s.slug }}" style="text-decoration: none; color: inherit;">
                    <div class="item">
                        <div style="font-size: 32px;">{{ s.get('icone', '🎮') }}</div>
                        <div class="nome">{{ s.nome }}</div>
                        <div class="preco">R$ {{ "%.2f"|format(s.preco) }}</div>
                    </div>
                </a>
                {% endfor %}
            </div>
        </div>
        {% endif %}
    </div>

    <div class="footer">
        <p>© 2024 Imune Bot - Todos os direitos reservados</p>
    </div>
</body>
</html>
"""

# Template da fila
FILA_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="30">
    <title>{{ fila.nome }}</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0f0c29, #302b63, #24243e); min-height:100vh; padding:20px; color:#fff; }
        .container { max-width:800px; margin:0 auto; }
        .header { text-align:center; margin-bottom:30px; padding:20px; background:rgba(0,0,0,0.5); border-radius:20px; }
        h1 { background: linear-gradient(135deg, #ff6b6b, #ffd93d); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .status { display:inline-block; padding:5px 15px; border-radius:20px; }
        .status-aberta { background:#00b894; }
        .status-fechada { background:#d63031; }
        .links-container { display: flex; justify-content: center; gap: 20px; margin: 20px 0; flex-wrap: wrap; }
        .btn-link { display: inline-flex; align-items: center; gap: 10px; padding: 12px 24px; border-radius: 30px; text-decoration: none; font-weight: bold; transition: all 0.3s; }
        .btn-link-discord { background: #5865F2; color: white; }
        .btn-link-discord:hover { background: #4752C4; transform: translateY(-2px); }
        .btn-link-precos { background: #f59e0b; color: white; }
        .btn-link-precos:hover { background: #d97706; transform: translateY(-2px); }
        .btn-link-voltar { background: #6c757d; color: white; }
        .btn-link-voltar:hover { background: #5a6268; transform: translateY(-2px); }
        .lista-fila { background:rgba(0,0,0,0.4); border-radius:20px; overflow:hidden; }
        .cabecalho-fila { display:grid; grid-template-columns:60px 1fr 1fr 1fr 80px; padding:15px; background:rgba(255,255,255,0.1); font-weight:bold; }
        .item-fila { display:grid; grid-template-columns:60px 1fr 1fr 1fr 80px; padding:12px 15px; border-bottom:1px solid rgba(255,255,255,0.1); }
        .posicao { font-weight:bold; color:#ffd93d; }
        .servico { color:#a8e6cf; }
        .jogo { color:#ffb347; }
        .vazio { text-align:center; padding:40px; }
        .footer { text-align:center; margin-top:20px; font-size:0.8rem; color:#888; }
        .admin-actions { margin-top: 20px; display: flex; gap: 10px; flex-wrap: wrap; justify-content: center; }
        .btn-admin { padding: 8px 16px; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; text-decoration: none; color: white; }
        .btn-danger { background: #d63031; }
        .btn-danger:hover { background: #c0392b; }
        .btn-success { background: #00b894; }
        .btn-success:hover { background: #00a381; }
        .btn-primary { background: #5865F2; }
        .btn-primary:hover { background: #4752C4; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📋 {{ fila.nome }}</h1>
            <span class="status status-{{ 'aberta' if fila.configuracoes.aberta else 'fechada' }}">{{ '🟢 ABERTA' if fila.configuracoes.aberta else '🔴 FECHADA' }}</span>
            <div>📊 {{ fila.entradas|length }} / {{ fila.configuracoes.tamanho_maximo }} pessoas</div>
        </div>
        
        <div class="links-container">
            {% if links.discord_convite %}
            <a href="{{ links.discord_convite }}" target="_blank" class="btn-link btn-link-discord">💬 Entrar no Discord</a>
            {% endif %}
            {% for botao in links.botoes_precos %}
            <a href="{{ botao.url }}" target="_blank" class="btn-link btn-link-precos">💰 {{ botao.nome }}</a>
            {% endfor %}
            <a href="/" class="btn-link btn-link-voltar">🏠 Voltar</a>
        </div>
        
        <div class="lista-fila">
            <div class="cabecalho-fila"><span>#</span><span>Jogador</span><span>Serviço</span><span>Jogo</span><span></span></div>
            {% for e in fila.entradas %}
            <div class="item-fila">
                <span class="posicao">{{ e.posicao }}</span>
                <span>{{ e.nome_usuario }}</span>
                <span class="servico">{{ e.servico }}</span>
                <span class="jogo">{{ e.jogo or '' }}</span>
                <span>⏳</span>
            </div>
            {% else %}
            <div class="vazio">✨ Ninguém na fila</div>
            {% endfor %}
        </div>
        
        {% if session.get('usuario') and session.usuario.is_admin %}
        <div class="admin-actions">
            <a href="/admin/fila" class="btn-admin btn-primary">⚙️ Gerenciar Fila</a>
        </div>
        {% endif %}
        
        <div class="footer">Atualizado a cada 30s • {{ agora_br().strftime("%d/%m/%Y %H:%M:%S") }}</div>
    </div>
</body>
</html>
"""

# Template do Dashboard do Cliente
DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Painel - Imune Bot</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0a0a0a, #1a1a1a); min-height: 100vh; color: #e0e0e0; }
        .header { background: #121212; padding: 20px; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; }
        .header h1 { color: #5865F2; font-size: 24px; }
        .header h1 span { color: #ffd93d; }
        .header-actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
        .btn { padding: 10px 20px; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; text-decoration: none; display: inline-block; transition: all 0.3s; }
        .btn-primary { background: #5865F2; color: white; }
        .btn-primary:hover { background: #4752C4; transform: translateY(-2px); }
        .btn-success { background: #10b981; color: white; }
        .btn-success:hover { background: #059669; transform: translateY(-2px); }
        .btn-danger { background: #ef4444; color: white; }
        .btn-danger:hover { background: #dc2626; transform: translateY(-2px); }
        .btn-warning { background: #f59e0b; color: white; }
        .btn-warning:hover { background: #d97706; transform: translateY(-2px); }
        .btn-outline { background: transparent; color: #e0e0e0; border: 1px solid #555; }
        .btn-outline:hover { background: #333; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        .user-profile { display: flex; align-items: center; gap: 20px; background: #121212; border: 1px solid #333; border-radius: 15px; padding: 20px; margin-bottom: 30px; }
        .user-avatar { width: 80px; height: 80px; border-radius: 50%; border: 3px solid #5865F2; }
        .user-info h2 { color: #ffd93d; }
        .user-info .pontos { color: #4ade80; font-size: 20px; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-bottom: 30px; }
        .stat-card { background: #121212; border: 1px solid #333; border-radius: 12px; padding: 20px; text-align: center; }
        .stat-card .number { font-size: 28px; font-weight: bold; color: #ffd93d; }
        .stat-card .label { color: #888; font-size: 14px; margin-top: 5px; }
        .section-title { color: #5865F2; margin: 30px 0 20px; }
        .pedidos-list { display: flex; flex-direction: column; gap: 10px; }
        .pedido-item { background: #121212; border: 1px solid #333; border-radius: 10px; padding: 15px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; }
        .pedido-item .numero { color: #ffd93d; font-weight: bold; }
        .pedido-item .servico { color: #a8e6cf; }
        .pedido-item .valor { color: #4ade80; font-weight: bold; }
        .pedido-item .status { padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: bold; }
        .status-aguardando { background: #f59e0b; color: #000; }
        .status-pago { background: #3b82f6; color: #fff; }
        .status-em_andamento { background: #8b5cf6; color: #fff; }
        .status-finalizado { background: #10b981; color: #fff; }
        .status-cancelado { background: #ef4444; color: #fff; }
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        @media (max-width: 768px) { .grid-2 { grid-template-columns: 1fr; } .header { flex-direction: column; gap: 10px; text-align: center; } }
        .footer { text-align: center; padding: 30px; color: #666; border-top: 1px solid #333; margin-top: 40px; font-size: 14px; }
    </style>
</head>
<body>
    <header class="header">
        <h1>🎮 Imune <span>Bot</span></h1>
        <div class="header-actions">
            <a href="/" class="btn btn-outline">🏠 Início</a>
            <a href="/servicos" class="btn btn-primary">📦 Serviços</a>
            <a href="/fila" class="btn btn-success">📋 Fila</a>
            <a href="/logout" class="btn btn-danger">🚪 Sair</a>
        </div>
    </header>

    <div class="container">
        <div class="user-profile">
            {% if usuario.discord_avatar %}
            <img src="https://cdn.discordapp.com/avatars/{{ usuario.discord_id }}/{{ usuario.discord_avatar }}.png" class="user-avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
            {% else %}
            <img src="https://cdn.discordapp.com/embed/avatars/0.png" class="user-avatar">
            {% endif %}
            <div class="user-info">
                <h2>{{ usuario.discord_nome }}</h2>
                <div class="pontos">⭐ {{ usuario.pontos }} pontos</div>
                <div style="margin-top: 5px;">
                    <a href="/perfil" class="btn btn-outline" style="padding: 5px 15px; font-size: 12px;">👤 Ver Perfil</a>
                    <a href="/pontos" class="btn btn-warning" style="padding: 5px 15px; font-size: 12px;">⭐ Pontos</a>
                </div>
            </div>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="number">{{ pedidos_ativos|length }}</div>
                <div class="label">Pedidos Ativos</div>
            </div>
            <div class="stat-card">
                <div class="number">{{ pedidos_concluidos|length }}</div>
                <div class="label">Pedidos Concluídos</div>
            </div>
            <div class="stat-card">
                <div class="number">{{ usuario.pontos }}</div>
                <div class="label">Pontos</div>
            </div>
        </div>

        <h3 class="section-title">📦 Pedidos Ativos</h3>
        <div class="pedidos-list">
            {% if pedidos_ativos %}
                {% for pedido in pedidos_ativos %}
                <div class="pedido-item">
                    <span class="numero">{{ pedido.numero }}</span>
                    <span class="servico">{{ pedido.servico_nome or 'Serviço' }}</span>
                    <span class="valor">R$ {{ "%.2f"|format(pedido.valor_final) }}</span>
                    <span class="status status-{{ pedido.status }}">{{ pedido.status|replace('_', ' ')|title }}</span>
                    <a href="/pedido/{{ pedido.numero }}" class="btn btn-outline" style="padding: 5px 15px; font-size: 12px;">Ver</a>
                </div>
                {% endfor %}
            {% else %}
                <div style="color: #888; text-align: center; padding: 20px;">Nenhum pedido ativo</div>
            {% endif %}
        </div>

        <h3 class="section-title">✅ Pedidos Concluídos</h3>
        <div class="pedidos-list">
            {% if pedidos_concluidos %}
                {% for pedido in pedidos_concluidos %}
                <div class="pedido-item">
                    <span class="numero">{{ pedido.numero }}</span>
                    <span class="servico">{{ pedido.servico_nome or 'Serviço' }}</span>
                    <span class="valor">R$ {{ "%.2f"|format(pedido.valor_final) }}</span>
                    <span class="status status-finalizado">Finalizado</span>
                    <a href="/pedido/{{ pedido.numero }}" class="btn btn-outline" style="padding: 5px 15px; font-size: 12px;">Ver</a>
                </div>
                {% endfor %}
            {% else %}
                <div style="color: #888; text-align: center; padding: 20px;">Nenhum pedido concluído</div>
            {% endif %}
        </div>

        <div style="display: flex; gap: 15px; margin-top: 30px; flex-wrap: wrap;">
            <a href="/servicos" class="btn btn-success btn-lg" style="padding: 15px 40px; font-size: 18px;">🛒 Comprar Serviço</a>
            <a href="/pontos" class="btn btn-warning btn-lg" style="padding: 15px 40px; font-size: 18px;">⭐ Resgatar Pontos</a>
            <a href="/meus-pedidos" class="btn btn-primary btn-lg" style="padding: 15px 40px; font-size: 18px;">📋 Histórico</a>
        </div>
    </div>

    <div class="footer">
        <p>© 2024 Imune Bot - Todos os direitos reservados</p>
    </div>
</body>
</html>
"""

# Template da página de compra
COMPRAR_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Comprar {{ servico.nome }} - Imune Bot</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0a0a0a, #1a1a1a); min-height: 100vh; color: #e0e0e0; }
        .header { background: #121212; padding: 20px; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; }
        .header h1 { color: #5865F2; font-size: 24px; }
        .header h1 span { color: #ffd93d; }
        .header-actions { display: flex; gap: 10px; flex-wrap: wrap; }
        .btn { padding: 10px 20px; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; text-decoration: none; display: inline-block; transition: all 0.3s; }
        .btn-primary { background: #5865F2; color: white; }
        .btn-primary:hover { background: #4752C4; transform: translateY(-2px); }
        .btn-success { background: #10b981; color: white; }
        .btn-success:hover { background: #059669; transform: translateY(-2px); }
        .btn-danger { background: #ef4444; color: white; }
        .btn-danger:hover { background: #dc2626; transform: translateY(-2px); }
        .btn-outline { background: transparent; color: #e0e0e0; border: 1px solid #555; }
        .btn-outline:hover { background: #333; }
        .container { max-width: 800px; margin: 0 auto; padding: 20px; }
        .card { background: #121212; border: 1px solid #333; border-radius: 15px; padding: 30px; margin: 20px 0; }
        .card h2 { color: #ffd93d; margin-bottom: 15px; }
        .form-group { margin-bottom: 20px; }
        .form-group label { display: block; margin-bottom: 8px; color: #aaa; font-weight: 600; }
        .form-group input, .form-group textarea, .form-group select { width: 100%; padding: 12px 15px; background: #1a1a1a; border: 1px solid #333; border-radius: 8px; color: #e0e0e0; font-size: 14px; }
        .form-group input:focus, .form-group textarea:focus, .form-group select:focus { outline: none; border-color: #5865F2; }
        .form-group textarea { min-height: 80px; resize: vertical; }
        .resumo-compra { background: #1a1a1a; border-radius: 10px; padding: 20px; margin: 20px 0; }
        .resumo-compra .linha { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #333; }
        .resumo-compra .linha:last-child { border-bottom: none; font-weight: bold; font-size: 18px; color: #4ade80; }
        .cupom-input { display: flex; gap: 10px; }
        .cupom-input input { flex: 1; }
        .footer { text-align: center; padding: 30px; color: #666; border-top: 1px solid #333; margin-top: 40px; font-size: 14px; }
        @media (max-width: 768px) { .header { flex-direction: column; gap: 10px; text-align: center; } }
    </style>
</head>
<body>
    <header class="header">
        <h1>🎮 Imune <span>Bot</span></h1>
        <div class="header-actions">
            <a href="/" class="btn btn-outline">🏠 Início</a>
            <a href="/servicos" class="btn btn-primary">📦 Serviços</a>
            <a href="/fila" class="btn btn-success">📋 Fila</a>
            <a href="/dashboard" class="btn btn-primary">👤 Painel</a>
            <a href="/logout" class="btn btn-danger">🚪 Sair</a>
        </div>
    </header>

    <div class="container">
        <nav style="margin: 20px 0; color: #888;">
            <a href="/" style="color: #5865F2; text-decoration: none;">Início</a> &gt;
            <a href="/servicos" style="color: #5865F2; text-decoration: none;">Serviços</a> &gt;
            <a href="/servico/{{ servico.slug }}" style="color: #5865F2; text-decoration: none;">{{ servico.nome }}</a> &gt;
            <span>Compra</span>
        </nav>

        <div class="card">
            <h2>🛒 Finalizar Compra</h2>
            <p style="color: #888; margin-bottom: 20px;">Preencha os dados abaixo para concluir a compra do serviço <strong style="color: #ffd93d;">{{ servico.nome }}</strong></p>

            <form method="POST" action="/comprar/{{ servico.slug }}">
                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">

                <div class="form-group">
                    <label>Seu Nome no Discord</label>
                    <input type="text" name="nome_cliente" value="{{ usuario.discord_nome }}" readonly style="background: #0a0a0a; color: #888;">
                </div>

                <div class="form-group">
                    <label>ID do Discord (opcional)</label>
                    <input type="text" name="id_cliente" placeholder="Seu ID do Discord" value="{{ usuario.discord_id }}">
                </div>

                <div class="form-group">
                    <label>Observações (opcional)</label>
                    <textarea name="observacoes" placeholder="Informações adicionais sobre o serviço..."></textarea>
                </div>

                <div class="form-group">
                    <label>🎟️ Cupom de Desconto</label>
                    <div class="cupom-input">
                        <input type="text" name="cupom" id="cupom-input" placeholder="Digite o código do cupom">
                        <button type="button" class="btn btn-primary" onclick="aplicarCupom()">Aplicar</button>
                    </div>
                    <div id="cupom-result" style="margin-top: 8px; font-size: 14px;"></div>
                </div>

                <div class="resumo-compra">
                    <div class="linha">
                        <span>{{ servico.nome }}</span>
                        <span>R$ {{ "%.2f"|format(servico.preco) }}</span>
                    </div>
                    {% if servico.preco_promocional %}
                    <div class="linha" style="color: #4ade80;">
                        <span>Desconto Promocional</span>
                        <span>-R$ {{ "%.2f"|format(servico.preco - servico.preco_promocional) }}</span>
                    </div>
                    {% endif %}
                    <div class="linha" id="desconto-linha" style="display: none; color: #4ade80;">
                        <span>Desconto Cupom</span>
                        <span id="desconto-valor">-R$ 0.00</span>
                    </div>
                    <div class="linha">
                        <span>Total</span>
                        <span id="total-final">R$ {{ "%.2f"|format(servico.preco_promocional or servico.preco) }}</span>
                    </div>
                </div>

                <button type="submit" class="btn btn-success" style="width: 100%; padding: 15px; font-size: 18px;">✅ Confirmar Compra</button>
            </form>
        </div>

        <div class="card">
            <h3 style="color: #5865F2;">💡 Informações</h3>
            <ul style="color: #aaa; list-style: none; padding: 0;">
                <li style="padding: 8px 0;">✅ Após o pagamento, você receberá confirmação automática</li>
                <li style="padding: 8px 0;">⭐ Você ganha <strong style="color: #4ade80;">10 pontos</strong> por cada R$ 1 gasto</li>
                <li style="padding: 8px 0;">📋 O serviço será adicionado automaticamente à fila</li>
            </ul>
        </div>
    </div>

    <script>
        let precoOriginal = {{ servico.preco }};
        let precoPromocional = {{ servico.preco_promocional or servico.preco }};
        let descontoCupom = 0;

        function aplicarCupom() {
            const codigo = document.getElementById('cupom-input').value.trim().toUpperCase();
            if (!codigo) {
                document.getElementById('cupom-result').innerHTML = '<span style="color: #f59e0b;">Digite um código de cupom</span>';
                return;
            }

            fetch('/api/cupom/validar', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ codigo: codigo, valor: precoPromocional })
            })
            .then(res => res.json())
            .then(data => {
                if (data.sucesso) {
                    descontoCupom = data.desconto;
                    document.getElementById('cupom-result').innerHTML = `<span style="color: #4ade80;">✅ Cupom aplicado! Desconto de R$ ${data.desconto.toFixed(2)}</span>`;
                    document.getElementById('desconto-linha').style.display = 'flex';
                    document.getElementById('desconto-valor').textContent = `-R$ ${data.desconto.toFixed(2)}`;
                    const total = precoPromocional - data.desconto;
                    document.getElementById('total-final').textContent = `R$ ${total.toFixed(2)}`;
                } else {
                    document.getElementById('cupom-result').innerHTML = `<span style="color: #ef4444;">❌ ${data.mensagem}</span>`;
                    descontoCupom = 0;
                    document.getElementById('desconto-linha').style.display = 'none';
                    document.getElementById('total-final').textContent = `R$ ${precoPromocional.toFixed(2)}`;
                }
            })
            .catch(() => {
                document.getElementById('cupom-result').innerHTML = '<span style="color: #ef4444;">❌ Erro ao validar cupom</span>';
            });
        }
    </script>

    <div class="footer">
        <p>© 2024 Imune Bot - Todos os direitos reservados</p>
    </div>
</body>
</html>
"""

# Template do Admin Dashboard
ADMIN_DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin - Imune Bot</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0a0a0a, #1a1a1a); min-height: 100vh; color: #e0e0e0; }
        .header { background: #121212; padding: 20px; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; }
        .header h1 { color: #5865F2; font-size: 24px; }
        .header h1 span { color: #ffd93d; }
        .header-actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
        .btn { padding: 10px 20px; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; text-decoration: none; display: inline-block; transition: all 0.3s; }
        .btn-primary { background: #5865F2; color: white; }
        .btn-primary:hover { background: #4752C4; transform: translateY(-2px); }
        .btn-success { background: #10b981; color: white; }
        .btn-success:hover { background: #059669; transform: translateY(-2px); }
        .btn-danger { background: #ef4444; color: white; }
        .btn-danger:hover { background: #dc2626; transform: translateY(-2px); }
        .btn-warning { background: #f59e0b; color: white; }
        .btn-warning:hover { background: #d97706; transform: translateY(-2px); }
        .btn-outline { background: transparent; color: #e0e0e0; border: 1px solid #555; }
        .btn-outline:hover { background: #333; }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        .admin-badge { background: #f59e0b; color: #000; padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: bold; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 15px; margin: 20px 0; }
        .stat-card { background: #121212; border: 1px solid #333; border-radius: 12px; padding: 20px; text-align: center; }
        .stat-card .number { font-size: 28px; font-weight: bold; color: #ffd93d; }
        .stat-card .label { color: #888; font-size: 14px; margin-top: 5px; }
        .stat-card .sub { color: #4ade80; font-size: 12px; }
        .sidebar { display: flex; gap: 10px; margin: 20px 0; flex-wrap: wrap; }
        .sidebar a { padding: 10px 20px; background: #121212; border: 1px solid #333; border-radius: 8px; color: #e0e0e0; text-decoration: none; transition: all 0.3s; }
        .sidebar a:hover, .sidebar a.active { background: #5865F2; border-color: #5865F2; }
        .card { background: #121212; border: 1px solid #333; border-radius: 12px; padding: 20px; margin: 15px 0; }
        .card h3 { color: #5865F2; margin-bottom: 15px; }
        table { width: 100%; border-collapse: collapse; }
        th, td { text-align: left; padding: 10px; border-bottom: 1px solid #333; }
        th { color: #888; font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; }
        .status-badge { padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: bold; }
        .status-aguardando { background: #f59e0b; color: #000; }
        .status-pago { background: #3b82f6; color: #fff; }
        .status-em_andamento { background: #8b5cf6; color: #fff; }
        .status-finalizado { background: #10b981; color: #fff; }
        .status-cancelado { background: #ef4444; color: #fff; }
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        @media (max-width: 768px) { .grid-2 { grid-template-columns: 1fr; } .header { flex-direction: column; gap: 10px; text-align: center; } }
        .footer { text-align: center; padding: 30px; color: #666; border-top: 1px solid #333; margin-top: 40px; font-size: 14px; }
    </style>
</head>
<body>
    <header class="header">
        <h1>🎮 Imune <span>Bot</span></h1>
        <div class="header-actions">
            <span class="admin-badge">👑 ADMIN</span>
            <a href="/" class="btn btn-outline">🏠 Início</a>
            <a href="/dashboard" class="btn btn-primary">👤 Painel</a>
            <a href="/logout" class="btn btn-danger">🚪 Sair</a>
        </div>
    </header>

    <div class="container">
        <div class="stats-grid">
            <div class="stat-card"><div class="number">{{ total_pedidos }}</div><div class="label">Total Pedidos</div></div>
            <div class="stat-card"><div class="number">{{ pedidos_pendentes }}</div><div class="label">Pendentes</div><div class="sub">Aguardando pagamento</div></div>
            <div class="stat-card"><div class="number">{{ pedidos_em_andamento }}</div><div class="label">Em Andamento</div></div>
            <div class="stat-card"><div class="number">{{ total_clientes }}</div><div class="label">Clientes</div></div>
            <div class="stat-card"><div class="number">R$ {{ "%.2f"|format(faturamento_mes) }}</div><div class="label">Faturamento Mês</div></div>
            <div class="stat-card"><div class="number">R$ {{ "%.2f"|format(faturamento_total) }}</div><div class="label">Faturamento Total</div></div>
        </div>

        <div class="sidebar">
            <a href="/admin" class="active">📊 Dashboard</a>
            <a href="/admin/clientes">👥 Clientes</a>
            <a href="/admin/servicos">📦 Serviços</a>
            <a href="/admin/pedidos">📋 Pedidos</a>
            <a href="/admin/categorias">📂 Categorias</a>
            <a href="/admin/recompensas">⭐ Recompensas</a>
            <a href="/admin/cupons">🎟️ Cupons</a>
            <a href="/admin/fila">📋 Fila</a>
        </div>

        <div class="grid-2">
            <div class="card">
                <h3>📦 Pedidos Recentes</h3>
                <table>
                    <thead>
                        <tr><th>Pedido</th><th>Cliente</th><th>Valor</th><th>Status</th></tr>
                    </thead>
                    <tbody>
                        {% for p in pedidos_recentes[:5] %}
                        <tr>
                            <td><a href="/pedido/{{ p.numero }}" style="color: #ffd93d;">{{ p.numero }}</a></td>
                            <td>{{ p.cliente_nome or 'N/A' }}</td>
                            <td style="color: #4ade80;">R$ {{ "%.2f"|format(p.valor_final) }}</td>
                            <td><span class="status-badge status-{{ p.status }}">{{ p.status|replace('_', ' ')|title }}</span></td>
                        </tr>
                        {% else %}
                        <tr><td colspan="4" style="text-align:center; color:#888;">Nenhum pedido</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>

            <div class="card">
                <h3>🏆 Produtos Mais Vendidos</h3>
                <table>
                    <thead>
                        <tr><th>Serviço</th><th>Vendas</th><th>Faturamento</th></tr>
                    </thead>
                    <tbody>
                        {% for p in produtos_mais_vendidos %}
                        <tr>
                            <td>{{ p.nome }}</td>
                            <td>{{ p.total_pedidos }}</td>
                            <td style="color: #4ade80;">R$ {{ "%.2f"|format(p.total_faturamento) }}</td>
                        </tr>
                        {% else %}
                        <tr><td colspan="3" style="text-align:center; color:#888;">Nenhum dado</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <div class="footer">
        <p>© 2024 Imune Bot - Painel Administrativo</p>
    </div>
</body>
</html>
"""

# Template Admin Serviços
ADMIN_SERVICOS_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin - Serviços</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0a0a0a, #1a1a1a); min-height: 100vh; color: #e0e0e0; }
        .header { background: #121212; padding: 20px; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; }
        .header h1 { color: #5865F2; font-size: 24px; }
        .header h1 span { color: #ffd93d; }
        .header-actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
        .btn { padding: 10px 20px; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; text-decoration: none; display: inline-block; transition: all 0.3s; }
        .btn-primary { background: #5865F2; color: white; }
        .btn-primary:hover { background: #4752C4; transform: translateY(-2px); }
        .btn-success { background: #10b981; color: white; }
        .btn-success:hover { background: #059669; transform: translateY(-2px); }
        .btn-danger { background: #ef4444; color: white; }
        .btn-danger:hover { background: #dc2626; transform: translateY(-2px); }
        .btn-warning { background: #f59e0b; color: white; }
        .btn-warning:hover { background: #d97706; transform: translateY(-2px); }
        .btn-outline { background: transparent; color: #e0e0e0; border: 1px solid #555; }
        .btn-outline:hover { background: #333; }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        .admin-badge { background: #f59e0b; color: #000; padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: bold; }
        .sidebar { display: flex; gap: 10px; margin: 20px 0; flex-wrap: wrap; }
        .sidebar a { padding: 10px 20px; background: #121212; border: 1px solid #333; border-radius: 8px; color: #e0e0e0; text-decoration: none; transition: all 0.3s; }
        .sidebar a:hover, .sidebar a.active { background: #5865F2; border-color: #5865F2; }
        .card { background: #121212; border: 1px solid #333; border-radius: 12px; padding: 20px; margin: 15px 0; }
        .card h3 { color: #5865F2; margin-bottom: 15px; }
        table { width: 100%; border-collapse: collapse; }
        th, td { text-align: left; padding: 10px; border-bottom: 1px solid #333; }
        th { color: #888; font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; }
        .actions { display: flex; gap: 5px; flex-wrap: wrap; }
        .actions .btn { padding: 5px 10px; font-size: 12px; }
        .status-ativo { color: #4ade80; }
        .status-inativo { color: #ef4444; }
        .footer { text-align: center; padding: 30px; color: #666; border-top: 1px solid #333; margin-top: 40px; font-size: 14px; }
        .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.8); z-index: 1000; justify-content: center; align-items: center; }
        .modal-content { background: #121212; border: 1px solid #333; border-radius: 15px; padding: 30px; max-width: 600px; width: 90%; max-height: 90vh; overflow-y: auto; }
        .modal-content h2 { color: #ffd93d; margin-bottom: 20px; }
        .form-group { margin-bottom: 15px; }
        .form-group label { display: block; margin-bottom: 5px; color: #aaa; font-weight: 600; }
        .form-group input, .form-group textarea, .form-group select { width: 100%; padding: 10px; background: #1a1a1a; border: 1px solid #333; border-radius: 8px; color: #e0e0e0; }
        .form-group textarea { min-height: 80px; }
        .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
        .btn-modal { margin-top: 15px; }
        @media (max-width: 768px) { .header { flex-direction: column; gap: 10px; text-align: center; } .form-row { grid-template-columns: 1fr; } }
    </style>
</head>
<body>
    <header class="header">
        <h1>🎮 Imune <span>Bot</span></h1>
        <div class="header-actions">
            <span class="admin-badge">👑 ADMIN</span>
            <a href="/" class="btn btn-outline">🏠 Início</a>
            <a href="/admin" class="btn btn-primary">📊 Admin</a>
            <a href="/logout" class="btn btn-danger">🚪 Sair</a>
        </div>
    </header>

    <div class="container">
        <div class="sidebar">
            <a href="/admin">📊 Dashboard</a>
            <a href="/admin/clientes">👥 Clientes</a>
            <a href="/admin/servicos" class="active">📦 Serviços</a>
            <a href="/admin/pedidos">📋 Pedidos</a>
            <a href="/admin/categorias">📂 Categorias</a>
            <a href="/admin/recompensas">⭐ Recompensas</a>
            <a href="/admin/cupons">🎟️ Cupons</a>
            <a href="/admin/fila">📋 Fila</a>
        </div>

        <div class="card">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; margin-bottom: 15px;">
                <h3>📦 Serviços</h3>
                <button class="btn btn-success" onclick="abrirModal('novo')">➕ Novo Serviço</button>
            </div>
            <table>
                <thead>
                    <tr><th>Nome</th><th>Categoria</th><th>Preço</th><th>Status</th><th>Destaque</th><th>Ações</th></tr>
                </thead>
                <tbody>
                    {% for s in servicos %}
                    <tr>
                        <td>{{ s.nome }}</td>
                        <td>{{ s.categoria_nome or 'Geral' }}</td>
                        <td style="color: #4ade80;">R$ {{ "%.2f"|format(s.preco) }}</td>
                        <td class="status-{{ s.status }}">{{ s.status|title }}</td>
                        <td>{% if s.destaque %}⭐ Sim{% else %}❌{% endif %}</td>
                        <td class="actions">
                            <button class="btn btn-primary" onclick="abrirModal('editar', '{{ s.id }}')">✏️</button>
                            <button class="btn btn-danger" onclick="deletar('{{ s.id }}')">🗑️</button>
                        </td>
                    </tr>
                    {% else %}
                    <tr><td colspan="6" style="text-align:center; color:#888;">Nenhum serviço cadastrado</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>

    <!-- Modal -->
    <div id="modal" class="modal">
        <div class="modal-content">
            <h2 id="modal-title">Novo Serviço</h2>
            <form id="servico-form" method="POST">
                <input type="hidden" name="id" id="servico-id">
                <div class="form-group">
                    <label>Nome *</label>
                    <input type="text" name="nome" id="s-nome" required>
                </div>
                <div class="form-group">
                    <label>Slug (URL amigável)</label>
                    <input type="text" name="slug" id="s-slug" placeholder="exemplo-servico">
                </div>
                <div class="form-group">
                    <label>Categoria</label>
                    <select name="categoria_id" id="s-categoria">
                        <option value="">Sem categoria</option>
                        {% for cat in categorias %}
                        <option value="{{ cat.id }}">{{ cat.nome }}</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="form-group">
                    <label>Descrição</label>
                    <textarea name="descricao" id="s-descricao"></textarea>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>Preço (R$) *</label>
                        <input type="number" name="preco" id="s-preco" step="0.01" required>
                    </div>
                    <div class="form-group">
                        <label>Preço Promocional</label>
                        <input type="number" name="preco_promocional" id="s-preco-promo" step="0.01">
                    </div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>Tempo Estimado</label>
                        <input type="text" name="tempo_estimado" id="s-tempo" placeholder="Ex: 2-4 horas">
                    </div>
                    <div class="form-group">
                        <label>Ícone/Emoji</label>
                        <input type="text" name="icone" id="s-icone" placeholder="🎮">
                    </div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>Status</label>
                        <select name="status" id="s-status">
                            <option value="ativo">Ativo</option>
                            <option value="inativo">Inativo</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Ordem</label>
                        <input type="number" name="ordem" id="s-ordem" value="0">
                    </div>
                </div>
                <div class="form-group">
                    <label>
                        <input type="checkbox" name="destaque" id="s-destaque" value="1">
                        Destacar serviço
                    </label>
                </div>
                <div style="display: flex; gap: 10px; margin-top: 15px;">
                    <button type="submit" class="btn btn-success btn-modal">💾 Salvar</button>
                    <button type="button" class="btn btn-danger btn-modal" onclick="fecharModal()">❌ Cancelar</button>
                </div>
            </form>
        </div>
    </div>

    <script>
        function abrirModal(tipo, id) {
            const modal = document.getElementById('modal');
            const form = document.getElementById('servico-form');
            
            if (tipo === 'novo') {
                document.getElementById('modal-title').textContent = '📦 Novo Serviço';
                form.action = '/admin/servicos/novo';
                document.getElementById('servico-id').value = '';
                document.getElementById('s-nome').value = '';
                document.getElementById('s-slug').value = '';
                document.getElementById('s-categoria').value = '';
                document.getElementById('s-descricao').value = '';
                document.getElementById('s-preco').value = '';
                document.getElementById('s-preco-promo').value = '';
                document.getElementById('s-tempo').value = '';
                document.getElementById('s-icone').value = '🎮';
                document.getElementById('s-status').value = 'ativo';
                document.getElementById('s-ordem').value = '0';
                document.getElementById('s-destaque').checked = false;
            } else if (tipo === 'editar') {
                document.getElementById('modal-title').textContent = '✏️ Editar Serviço';
                form.action = '/admin/servicos/' + id + '/editar';
                
                fetch('/api/servico/' + id)
                    .then(res => res.json())
                    .then(data => {
                        if (data.sucesso) {
                            const s = data.servico;
                            document.getElementById('servico-id').value = s.id;
                            document.getElementById('s-nome').value = s.nome;
                            document.getElementById('s-slug').value = s.slug;
                            document.getElementById('s-categoria').value = s.categoria_id || '';
                            document.getElementById('s-descricao').value = s.descricao;
                            document.getElementById('s-preco').value = s.preco;
                            document.getElementById('s-preco-promo').value = s.preco_promocional || '';
                            document.getElementById('s-tempo').value = s.tempo_estimado || '';
                            document.getElementById('s-icone').value = s.icone || '🎮';
                            document.getElementById('s-status').value = s.status || 'ativo';
                            document.getElementById('s-ordem').value = s.ordem || 0;
                            document.getElementById('s-destaque').checked = s.destaque || false;
                        }
                    });
            }
            modal.style.display = 'flex';
        }

        function fecharModal() {
            document.getElementById('modal').style.display = 'none';
        }

        function deletar(id) {
            if (confirm('Tem certeza que deseja excluir este serviço?')) {
                fetch('/admin/servicos/' + id + '/deletar', { method: 'POST' })
                    .then(() => window.location.reload());
            }
        }

        document.getElementById('servico-form').addEventListener('submit', function(e) {
            e.preventDefault();
            fetch(this.action, {
                method: 'POST',
                body: new FormData(this)
            }).then(() => {
                fecharModal();
                window.location.reload();
            });
        });

        window.onclick = function(event) {
            if (event.target === document.getElementById('modal')) {
                fecharModal();
            }
        }
    </script>

    <div class="footer">
        <p>© 2024 Imune Bot - Painel Administrativo</p>
    </div>
</body>
</html>
"""

# ========================
# ROTAS DO SITE
# ========================

@app.route("/")
def home():
    status_bot = "✅ Bot Online" if bot.is_ready() else "❌ Bot Offline"
    classe_bot = "online" if bot.is_ready() else "offline"
    
    servicos = dados.get("servicos", [])
    servicos_destaque = [s for s in servicos if s.get("destaque") and s.get("status") == "ativo"][:6]
    
    return render_template_string(HOME_TEMPLATE,
                                status_bot=status_bot,
                                classe_bot=classe_bot,
                                servicos_destaque=servicos_destaque,
                                session=session)

@app.route("/login")
def login():
    if not CLIENT_ID or not CLIENT_SECRET:
        flash('CLIENT_ID ou CLIENT_SECRET não configurados.', 'danger')
        return redirect(url_for('home'))
    
    url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds"
    return redirect(url)

@app.route("/callback")
def callback():
    if not CLIENT_ID or not CLIENT_SECRET:
        flash('Erro de configuração.', 'danger')
        return redirect(url_for('home'))
    
    code = request.args.get('code')
    if not code:
        flash('Erro: código não recebido', 'danger')
        return redirect(url_for('home'))
    
    try:
        dados_req = {
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': REDIRECT_URI,
            'scope': 'identify guilds'
        }
        
        r = requests.post('https://discord.com/api/oauth2/token', data=dados_req)
        if r.status_code != 200:
            flash(f'Erro ao obter token: {r.text[:100]}', 'danger')
            return redirect(url_for('home'))
        
        access_token = r.json()['access_token']
        
        user_r = requests.get('https://discord.com/api/users/@me', headers={'Authorization': f'Bearer {access_token}'})
        if user_r.status_code != 200:
            flash('Erro ao obter informações', 'danger')
            return redirect(url_for('home'))
        
        user_data = user_r.json()
        
        # Verificar se é admin
        is_admin = False
        if GUILD_ID:
            guilds_r = requests.get('https://discord.com/api/users/@me/guilds', headers={'Authorization': f'Bearer {access_token}'})
            if guilds_r.status_code == 200:
                guilds = guilds_r.json()
                for guild in guilds:
                    if str(guild['id']) == GUILD_ID and (guild['permissions'] & 0x8):
                        is_admin = True
                        break
        
        # Criar ou atualizar usuário
        usuario = obter_ou_criar_usuario(user_data)
        if is_admin:
            usuario["is_admin"] = True
            salvar_dados_github(f"Admin atualizado: {user_data['username']}")
        
        session['usuario'] = {
            'discord_id': user_data['id'],
            'nome_usuario': user_data['username'],
            'avatar': user_data.get('avatar'),
            'is_admin': is_admin
        }
        
        flash('Login realizado com sucesso!', 'success')
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        flash(f'Erro interno: {str(e)}', 'danger')
        return redirect(url_for('home'))

@app.route("/logout")
def logout():
    session.clear()
    flash('Você foi desconectado.', 'info')
    return redirect(url_for('home'))

# ========================
# ROTAS DO CLIENTE
# ========================

@app.route("/dashboard")
@login_required
def dashboard():
    usuario = obter_usuario_sessao()
    if not usuario:
        return redirect(url_for('logout'))
    
    discord_id = usuario.get('discord_id')
    pedidos = [p for p in dados.get("pedidos", []) if p.get("usuario_id") == discord_id]
    
    pedidos_ativos = [p for p in pedidos if p.get("status") in ['aguardando_pagamento', 'pago', 'em_andamento']]
    pedidos_concluidos = [p for p in pedidos if p.get("status") == 'finalizado']
    
    return render_template_string(DASHBOARD_TEMPLATE,
                                usuario=usuario,
                                pedidos_ativos=pedidos_ativos,
                                pedidos_concluidos=pedidos_concluidos,
                                session=session)

@app.route("/servicos")
def listar_servicos():
    categoria_slug = request.args.get('categoria', '')
    busca = request.args.get('busca', '')
    page = request.args.get('page', 1, type=int)
    per_page = 12
    
    servicos = dados.get("servicos", [])
    servicos_ativos = [s for s in servicos if s.get("status") == "ativo"]
    
    if categoria_slug:
        categoria = obter_categoria_por_slug(categoria_slug)
        if categoria:
            servicos_ativos = [s for s in servicos_ativos if s.get("categoria_id") == categoria.get("id")]
    
    if busca:
        busca_lower = busca.lower()
        servicos_ativos = [s for s in servicos_ativos if busca_lower in s.get("nome", "").lower() or busca_lower in s.get("descricao", "").lower()]
    
    total = len(servicos_ativos)
    total_pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    servicos_paginados = servicos_ativos[start:start + per_page]
    
    categorias = obter_categorias()
    categorias_ativas = [c for c in categorias if c.get("ativo", True)]
    
    return render_template_string(SERVICOS_TEMPLATE,
                                servicos=servicos_paginados,
                                categorias=categorias_ativas,
                                categoria_atual=categoria_slug,
                                busca=busca,
                                page=page,
                                total_pages=total_pages,
                                session=session)

@app.route("/servico/<slug>")
def detalhes_servico(slug):
    servico = obter_servico_por_slug(slug)
    if not servico:
        flash('Serviço não encontrado.', 'danger')
        return redirect(url_for('listar_servicos'))
    
    servicos_relacionados = []
    for s in dados.get("servicos", []):
        if s.get("categoria_id") == servico.get("categoria_id") and s.get("id") != servico.get("id") and s.get("status") == "ativo":
            servicos_relacionados.append(s)
    
    return render_template_string(DETALHES_SERVICO_TEMPLATE,
                                servico=servico,
                                servicos_relacionados=servicos_relacionados[:4],
                                session=session)

@app.route("/comprar/<slug>", methods=['GET', 'POST'])
@login_required
def comprar_servico(slug):
    servico = obter_servico_por_slug(slug)
    if not servico:
        flash('Serviço não encontrado.', 'danger')
        return redirect(url_for('listar_servicos'))
    
    usuario = obter_usuario_sessao()
    if not usuario:
        return redirect(url_for('logout'))
    
    if request.method == 'POST':
        valor = servico.get('preco_promocional') or servico.get('preco', 0)
        desconto = 0
        
        codigo_cupom = request.form.get('cupom', '').strip().upper()
        if codigo_cupom:
            for cupom in dados.get("cupons", []):
                if cupom.get("codigo") == codigo_cupom and cupom.get("ativo", True):
                    if cupom.get("validade"):
                        try:
                            validade = datetime.fromisoformat(cupom.get("validade"))
                            if datetime.utcnow() > validade:
                                flash('Cupom expirado.', 'danger')
                                return redirect(url_for('detalhes_servico', slug=slug))
                        except:
                            pass
                    
                    if cupom.get("max_uso") and cupom.get("usos", 0) >= cupom.get("max_uso"):
                        flash('Cupom esgotado.', 'danger')
                        return redirect(url_for('detalhes_servico', slug=slug))
                    
                    if valor < cupom.get("valor_minimo", 0):
                        flash(f'Valor mínimo para este cupom é R${cupom.get("valor_minimo", 0):.2f}', 'danger')
                        return redirect(url_for('detalhes_servico', slug=slug))
                    
                    if cupom.get("tipo") == 'percentual':
                        desconto = valor * (cupom.get("valor", 0) / 100)
                    else:
                        desconto = min(cupom.get("valor", 0), valor)
                    
                    cupom["usos"] = cupom.get("usos", 0) + 1
                    salvar_dados_github(f"Cupom usado: {codigo_cupom}")
                    break
                else:
                    flash('Cupom inválido.', 'danger')
                    return redirect(url_for('detalhes_servico', slug=slug))
        
        valor_final = max(0, valor - desconto)
        
        pedido = {
            "id": gerar_id(),
            "numero": gerar_numero_pedido(),
            "usuario_id": usuario.get('discord_id'),
            "servico_id": servico.get('id'),
            "servico_nome": servico.get('nome'),
            "valor": valor,
            "desconto": desconto,
            "valor_final": valor_final,
            "status": "aguardando_pagamento",
            "dados_cliente": json.dumps({
                'nome_cliente': request.form.get('nome_cliente', ''),
                'id_cliente': request.form.get('id_cliente', ''),
                'observacoes': request.form.get('observacoes', '')
            }),
            "data_criacao": datetime.utcnow().isoformat(),
            "data_atualizacao": datetime.utcnow().isoformat(),
            "historico": json.dumps([{
                'data': datetime.utcnow().isoformat(),
                'status': 'aguardando_pagamento',
                'mensagem': 'Pedido criado, aguardando pagamento'
            }])
        }
        
        criar_pedido(pedido)
        
        adicionar_fila(
            usuario.get('discord_nome', 'Cliente'),
            servico.get('nome', 'Serviço'),
            usuario.get('discord_nome', ''),
            usuario.get('discord_id')
        )
        
        flash(f'Pedido {pedido["numero"]} criado! Aguarde a confirmação.', 'success')
        return redirect(url_for('detalhes_pedido', numero=pedido["numero"]))
    
    recompensas = [r for r in dados.get("recompensas", []) if r.get("status") == "ativo"]
    
    return render_template_string(COMPRAR_TEMPLATE,
                                servico=servico,
                                usuario=usuario,
                                recompensas=recompensas,
                                session=session,
                                csrf_token=lambda: '')

@app.route("/pedido/<numero>")
@login_required
def detalhes_pedido(numero):
    usuario = obter_usuario_sessao()
    if not usuario:
        return redirect(url_for('logout'))
    
    pedido = obter_pedido_por_numero(numero)
    if not pedido:
        flash('Pedido não encontrado.', 'danger')
        return redirect(url_for('meus_pedidos'))
    
    if pedido.get('usuario_id') != usuario.get('discord_id') and not usuario.get('is_admin'):
        flash('Você não tem permissão para ver este pedido.', 'danger')
        return redirect(url_for('meus_pedidos'))
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>Pedido {pedido['numero']}</title>
    <style>
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0a0a0a, #1a1a1a); min-height:100vh; color:#e0e0e0; padding:20px; }}
        .container {{ max-width:800px; margin:0 auto; }}
        .card {{ background:#121212; border:1px solid #333; border-radius:15px; padding:30px; margin:20px 0; }}
        .card h2 {{ color:#ffd93d; margin-bottom:15px; }}
        .status {{ padding:4px 12px; border-radius:20px; font-size:14px; font-weight:bold; }}
        .status-aguardando {{ background:#f59e0b; color:#000; }}
        .status-pago {{ background:#3b82f6; color:#fff; }}
        .status-em_andamento {{ background:#8b5cf6; color:#fff; }}
        .status-finalizado {{ background:#10b981; color:#fff; }}
        .status-cancelado {{ background:#ef4444; color:#fff; }}
        .linha {{ display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid #333; }}
        .btn {{ padding:10px 20px; border:none; border-radius:8px; cursor:pointer; font-weight:600; text-decoration:none; display:inline-block; }}
        .btn-primary {{ background:#5865F2; color:white; }}
        .btn-primary:hover {{ background:#4752C4; }}
        .btn-outline {{ background:transparent; color:#e0e0e0; border:1px solid #555; }}
        .btn-outline:hover {{ background:#333; }}
        .footer {{ text-align:center; padding:30px; color:#666; border-top:1px solid #333; margin-top:40px; }}
    </style>
    </head>
    <body>
    <div class="container">
        <a href="/dashboard" class="btn btn-outline">← Voltar</a>
        <div class="card">
            <h2>📦 Pedido {pedido['numero']}</h2>
            <div class="linha"><span>Status</span><span class="status status-{pedido['status']}">{pedido['status'].replace('_', ' ').title()}</span></div>
            <div class="linha"><span>Serviço</span><span>{pedido.get('servico_nome', 'N/A')}</span></div>
            <div class="linha"><span>Valor</span><span style="color:#4ade80;">R$ {pedido['valor_final']:.2f}</span></div>
            <div class="linha"><span>Data</span><span>{pedido.get('data_criacao', '')[:16]}</span></div>
        </div>
        <a href="/" class="btn btn-primary">🏠 Início</a>
    </div>
    <div class="footer"><p>© 2024 Imune Bot</p></div>
    </body>
    </html>
    """

@app.route("/meus-pedidos")
@login_required
def meus_pedidos():
    usuario = obter_usuario_sessao()
    if not usuario:
        return redirect(url_for('logout'))
    
    pedidos = [p for p in dados.get("pedidos", []) if p.get("usuario_id") == usuario.get('discord_id')]
    pedidos_ordenados = sorted(pedidos, key=lambda p: p.get("data_criacao", ''), reverse=True)
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>Meus Pedidos</title>
    <style>
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0a0a0a, #1a1a1a); min-height:100vh; color:#e0e0e0; padding:20px; }}
        .container {{ max-width:1000px; margin:0 auto; }}
        .header {{ background:#121212; padding:20px; border-bottom:1px solid #333; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; margin-bottom:20px; }}
        .header h1 {{ color:#5865F2; }}
        .btn {{ padding:10px 20px; border:none; border-radius:8px; cursor:pointer; font-weight:600; text-decoration:none; display:inline-block; }}
        .btn-primary {{ background:#5865F2; color:white; }}
        .btn-primary:hover {{ background:#4752C4; }}
        .btn-outline {{ background:transparent; color:#e0e0e0; border:1px solid #555; }}
        .btn-outline:hover {{ background:#333; }}
        .card {{ background:#121212; border:1px solid #333; border-radius:12px; padding:20px; margin:10px 0; }}
        .pedido-item {{ display:flex; justify-content:space-between; align-items:center; padding:10px 0; border-bottom:1px solid #333; flex-wrap:wrap; gap:10px; }}
        .pedido-item .numero {{ color:#ffd93d; font-weight:bold; }}
        .pedido-item .valor {{ color:#4ade80; font-weight:bold; }}
        .status {{ padding:4px 12px; border-radius:20px; font-size:12px; font-weight:bold; }}
        .status-aguardando {{ background:#f59e0b; color:#000; }}
        .status-pago {{ background:#3b82f6; color:#fff; }}
        .status-em_andamento {{ background:#8b5cf6; color:#fff; }}
        .status-finalizado {{ background:#10b981; color:#fff; }}
        .status-cancelado {{ background:#ef4444; color:#fff; }}
        .footer {{ text-align:center; padding:30px; color:#666; border-top:1px solid #333; margin-top:40px; }}
    </style>
    </head>
    <body>
    <div class="header">
        <h1>📋 Meus Pedidos</h1>
        <div><a href="/dashboard" class="btn btn-outline">← Voltar</a></div>
    </div>
    <div class="container">
        {''.join(f'''
        <div class="card">
            <div class="pedido-item">
                <span class="numero">{p['numero']}</span>
                <span>{p.get('servico_nome', 'Serviço')}</span>
                <span class="valor">R$ {p['valor_final']:.2f}</span>
                <span class="status status-{p['status']}">{p['status'].replace('_', ' ').title()}</span>
                <a href="/pedido/{p['numero']}" class="btn btn-primary" style="padding:5px 15px;font-size:12px;">Ver</a>
            </div>
        </div>
        ''' for p in pedidos_ordenados[:20])}
        {'' if pedidos_ordenados else '<div style="text-align:center;color:#888;padding:40px;">Nenhum pedido encontrado</div>'}
    </div>
    <div class="footer"><p>© 2024 Imune Bot</p></div>
    </body>
    </html>
    """

@app.route("/perfil")
@login_required
def perfil():
    usuario = obter_usuario_sessao()
    if not usuario:
        return redirect(url_for('logout'))
    
    transacoes = [t for t in dados.get("transacoes_pontos", []) if t.get("usuario_id") == usuario.get('discord_id')]
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>Meu Perfil</title>
    <style>
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0a0a0a, #1a1a1a); min-height:100vh; color:#e0e0e0; padding:20px; }}
        .container {{ max-width:800px; margin:0 auto; }}
        .header {{ background:#121212; padding:20px; border-bottom:1px solid #333; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; margin-bottom:20px; }}
        .header h1 {{ color:#5865F2; }}
        .btn {{ padding:10px 20px; border:none; border-radius:8px; cursor:pointer; font-weight:600; text-decoration:none; display:inline-block; }}
        .btn-primary {{ background:#5865F2; color:white; }}
        .btn-primary:hover {{ background:#4752C4; }}
        .btn-outline {{ background:transparent; color:#e0e0e0; border:1px solid #555; }}
        .btn-outline:hover {{ background:#333; }}
        .card {{ background:#121212; border:1px solid #333; border-radius:12px; padding:25px; margin:15px 0; }}
        .avatar {{ width:100px; height:100px; border-radius:50%; border:3px solid #5865F2; }}
        .pontos {{ color:#4ade80; font-size:24px; font-weight:bold; }}
        .linha {{ display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid #333; }}
        .footer {{ text-align:center; padding:30px; color:#666; border-top:1px solid #333; margin-top:40px; }}
        .badge {{ background:#f59e0b; color:#000; padding:2px 10px; border-radius:12px; font-size:12px; font-weight:bold; }}
    </style>
    </head>
    <body>
    <div class="header">
        <h1>👤 Meu Perfil</h1>
        <div><a href="/dashboard" class="btn btn-outline">← Voltar</a></div>
    </div>
    <div class="container">
        <div class="card" style="text-align:center;">
            <img src="https://cdn.discordapp.com/avatars/{usuario.get('discord_id')}/{usuario.get('discord_avatar', '')}.png" class="avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
            <h2 style="color:#ffd93d; margin-top:10px;">{usuario.get('discord_nome', 'Usuário')}</h2>
            <div class="pontos">⭐ {usuario.get('pontos', 0)} pontos</div>
            <div style="margin-top:10px;">
                <span class="badge">{'👑 Administrador' if usuario.get('is_admin') else '🎮 Cliente'}</span>
            </div>
        </div>
        
        <div class="card">
            <h3 style="color:#5865F2;">📊 Estatísticas</h3>
            <div class="linha"><span>Total de Pontos Ganhos</span><span>{usuario.get('total_pontos_ganhos', 0)}</span></div>
            <div class="linha"><span>Total de Pontos Gastos</span><span>{usuario.get('total_pontos_gastos', 0)}</span></div>
            <div class="linha"><span>Data de Cadastro</span><span>{usuario.get('data_cadastro', '')[:10]}</span></div>
        </div>
        
        <div class="card">
            <h3 style="color:#5865F2;">📜 Últimas Transações</h3>
            {''.join(f'''
            <div class="linha">
                <span>{t.get('descricao', 'Transação')}</span>
                <span style="color:{'#4ade80' if t.get('tipo') == 'ganho' else '#ef4444'};">{'+' if t.get('tipo') == 'ganho' else '-'}{t.get('quantidade', 0)}</span>
            </div>
            ''' for t in transacoes[:10])}
            {'' if transacoes else '<div style="color:#888;text-align:center;padding:10px;">Nenhuma transação</div>'}
        </div>
    </div>
    <div class="footer"><p>© 2024 Imune Bot</p></div>
    </body>
    </html>
    """

@app.route("/pontos")
@login_required
def pontos():
    usuario = obter_usuario_sessao()
    if not usuario:
        return redirect(url_for('logout'))
    
    recompensas = [r for r in dados.get("recompensas", []) if r.get("status") == "ativo"]
    transacoes = [t for t in dados.get("transacoes_pontos", []) if t.get("usuario_id") == usuario.get('discord_id')]
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>Pontos - Imune Bot</title>
    <style>
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0a0a0a, #1a1a1a); min-height:100vh; color:#e0e0e0; padding:20px; }}
        .container {{ max-width:1000px; margin:0 auto; }}
        .header {{ background:#121212; padding:20px; border-bottom:1px solid #333; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; margin-bottom:20px; }}
        .header h1 {{ color:#5865F2; }}
        .btn {{ padding:10px 20px; border:none; border-radius:8px; cursor:pointer; font-weight:600; text-decoration:none; display:inline-block; }}
        .btn-primary {{ background:#5865F2; color:white; }}
        .btn-primary:hover {{ background:#4752C4; }}
        .btn-success {{ background:#10b981; color:white; }}
        .btn-success:hover {{ background:#059669; }}
        .btn-outline {{ background:transparent; color:#e0e0e0; border:1px solid #555; }}
        .btn-outline:hover {{ background:#333; }}
        .card {{ background:#121212; border:1px solid #333; border-radius:12px; padding:25px; margin:15px 0; }}
        .grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:15px; }}
        .recompensa {{ background:#1a1a1a; border:1px solid #333; border-radius:10px; padding:20px; text-align:center; transition:all 0.3s; }}
        .recompensa:hover {{ border-color:#5865F2; transform:translateY(-3px); }}
        .recompensa .nome {{ color:#ffd93d; font-weight:bold; }}
        .recompensa .pontos {{ color:#4ade80; font-size:20px; }}
        .recompensa .desc {{ color:#888; font-size:14px; margin:10px 0; }}
        .linha {{ display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid #333; }}
        .footer {{ text-align:center; padding:30px; color:#666; border-top:1px solid #333; margin-top:40px; }}
        .pontos-grande {{ color:#4ade80; font-size:36px; font-weight:bold; }}
    </style>
    </head>
    <body>
    <div class="header">
        <h1>⭐ Sistema de Pontos</h1>
        <div><a href="/dashboard" class="btn btn-outline">← Voltar</a></div>
    </div>
    <div class="container">
        <div class="card" style="text-align:center;">
            <div class="pontos-grande">{usuario.get('pontos', 0)}</div>
            <div style="color:#888;">Pontos Disponíveis</div>
            <div style="margin-top:10px; color:#666; font-size:14px;">
                Ganhe 10 pontos por cada R$ 1 gasto em serviços
            </div>
        </div>
        
        <div class="card">
            <h3 style="color:#5865F2;">🎁 Resgatar Recompensas</h3>
            <div class="grid">
                {''.join(f'''
                <div class="recompensa">
                    <div class="nome">{r.get('nome')}</div>
                    <div class="pontos">{r.get('pontos_necessarios')} pts</div>
                    <div class="desc">{r.get('descricao', '')[:60]}</div>
                    <form method="POST" action="/resgatar/{r.get('id')}">
                        <button type="submit" class="btn btn-success" style="padding:5px 15px;font-size:12px;{'disabled' if usuario.get('pontos',0) < r.get('pontos_necessarios',0) else ''}">
                            {'✅ Resgatar' if usuario.get('pontos',0) >= r.get('pontos_necessarios',0) else '🔒 Pontos insuficientes'}
                        </button>
                    </form>
                </div>
                ''' for r in recompensas)}
                {'' if recompensas else '<div style="color:#888;text-align:center;padding:20px;grid-column:1/-1;">Nenhuma recompensa disponível</div>'}
            </div>
        </div>
        
        <div class="card">
            <h3 style="color:#5865F2;">📜 Histórico de Transações</h3>
            {''.join(f'''
            <div class="linha">
                <span>{t.get('descricao', 'Transação')}</span>
                <span style="color:{'#4ade80' if t.get('tipo') == 'ganho' else '#ef4444'};">{'+' if t.get('tipo') == 'ganho' else '-'}{t.get('quantidade', 0)}</span>
                <span style="color:#666;font-size:12px;">{t.get('data_criacao', '')[:16]}</span>
            </div>
            ''' for t in transacoes[:20])}
            {'' if transacoes else '<div style="color:#888;text-align:center;padding:10px;">Nenhuma transação</div>'}
        </div>
    </div>
    <div class="footer"><p>© 2024 Imune Bot</p></div>
    </body>
    </html>
    """

@app.route("/resgatar/<recompensa_id>", methods=['POST'])
@login_required
def resgatar_recompensa(recompensa_id):
    usuario = obter_usuario_sessao()
    if not usuario:
        return redirect(url_for('logout'))
    
    recompensa = None
    for r in dados.get("recompensas", []):
        if r.get("id") == recompensa_id and r.get("status") == "ativo":
            recompensa = r
            break
    
    if not recompensa:
        flash('Recompensa não encontrada.', 'danger')
        return redirect(url_for('pontos'))
    
    if usuario.get('pontos', 0) < recompensa.get('pontos_necessarios', 0):
        flash('Pontos insuficientes.', 'danger')
        return redirect(url_for('pontos'))
    
    codigo_cupom = gerar_codigo_cupom()
    resgate = {
        "id": gerar_id(),
        "usuario_id": usuario.get('discord_id'),
        "recompensa_id": recompensa.get('id'),
        "recompensa_nome": recompensa.get('nome'),
        "pontos_gastos": recompensa.get('pontos_necessarios'),
        "codigo_cupom": codigo_cupom,
        "status": "ativo",
        "data_criacao": datetime.utcnow().isoformat()
    }
    dados.setdefault("resgates", []).append(resgate)
    
    usuario["pontos"] = usuario.get("pontos", 0) - recompensa.get('pontos_necessarios')
    usuario["total_pontos_gastos"] = usuario.get("total_pontos_gastos", 0) + recompensa.get('pontos_necessarios')
    
    transacao = {
        "id": gerar_id(),
        "usuario_id": usuario.get('discord_id'),
        "tipo": "gasto",
        "quantidade": recompensa.get('pontos_necessarios'),
        "descricao": f"Resgate: {recompensa.get('nome')}",
        "referencia_id": resgate.get('id'),
        "data_criacao": datetime.utcnow().isoformat()
    }
    dados.setdefault("transacoes_pontos", []).append(transacao)
    
    salvar_dados_github(f"Resgate: {recompensa.get('nome')} - {usuario.get('discord_nome')}")
    
    flash(f'🎉 Recompensa resgatada! Código: {codigo_cupom}', 'success')
    return redirect(url_for('pontos'))

# ========================
# ROTAS ADMIN
# ========================

@app.route("/admin")
@admin_required
def admin_dashboard():
    usuario = obter_usuario_sessao()
    
    pedidos = dados.get("pedidos", [])
    total_pedidos = len(pedidos)
    pedidos_pendentes = len([p for p in pedidos if p.get("status") == "aguardando_pagamento"])
    pedidos_em_andamento = len([p for p in pedidos if p.get("status") == "em_andamento"])
    pedidos_finalizados = [p for p in pedidos if p.get("status") == "finalizado"]
    total_clientes = len(dados.get("usuarios", {}))
    
    faturamento_total = sum(p.get("valor_final", 0) for p in pedidos_finalizados)
    
    mes_atual = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0).isoformat()
    faturamento_mes = sum(p.get("valor_final", 0) for p in pedidos_finalizados if p.get("data_criacao", '') >= mes_atual)
    
    produtos = {}
    for p in pedidos:
        if p.get("status") in ["finalizado", "em_andamento"]:
            nome = p.get("servico_nome", "Outro")
            if nome not in produtos:
                produtos[nome] = {"total_pedidos": 0, "total_faturamento": 0}
            produtos[nome]["total_pedidos"] += 1
            produtos[nome]["total_faturamento"] += p.get("valor_final", 0)
    
    produtos_mais_vendidos = sorted(
        [{"nome": k, "total_pedidos": v["total_pedidos"], "total_faturamento": v["total_faturamento"]} 
         for k, v in produtos.items()],
        key=lambda x: x["total_pedidos"],
        reverse=True
    )[:5]
    
    pedidos_recentes = sorted(pedidos, key=lambda p: p.get("data_criacao", ''), reverse=True)[:10]
    
    return render_template_string(ADMIN_DASHBOARD_TEMPLATE,
                                total_pedidos=total_pedidos,
                                pedidos_pendentes=pedidos_pendentes,
                                pedidos_em_andamento=pedidos_em_andamento,
                                total_clientes=total_clientes,
                                faturamento_total=faturamento_total,
                                faturamento_mes=faturamento_mes,
                                produtos_mais_vendidos=produtos_mais_vendidos,
                                pedidos_recentes=pedidos_recentes)

@app.route("/admin/servicos")
@admin_required
def admin_servicos():
    servicos = dados.get("servicos", [])
    categorias = obter_categorias()
    
    for s in servicos:
        for c in categorias:
            if c.get("id") == s.get("categoria_id"):
                s["categoria_nome"] = c.get("nome")
                break
    
    return render_template_string(ADMIN_SERVICOS_TEMPLATE,
                                servicos=servicos,
                                categorias=categorias)

@app.route("/admin/servicos/novo", methods=['POST'])
@admin_required
def admin_servico_novo():
    nome = request.form.get('nome', '').strip()
    if not nome:
        flash('Nome é obrigatório.', 'danger')
        return redirect(url_for('admin_servicos'))
    
    slug = request.form.get('slug', '').strip()
    if not slug:
        slug = nome.lower().replace(' ', '-').replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
    
    for s in dados.get("servicos", []):
        if s.get("slug") == slug:
            flash('Slug já existe. Por favor, escolha outro.', 'danger')
            return redirect(url_for('admin_servicos'))
    
    servico = {
        "id": gerar_id(),
        "nome": nome,
        "slug": slug,
        "categoria_id": request.form.get('categoria_id') or None,
        "descricao": request.form.get('descricao', ''),
        "preco": float(request.form.get('preco', 0)),
        "preco_promocional": float(request.form.get('preco_promocional')) if request.form.get('preco_promocional') else None,
        "tempo_estimado": request.form.get('tempo_estimado', ''),
        "icone": request.form.get('icone', '🎮'),
        "status": request.form.get('status', 'ativo'),
        "destaque": bool(request.form.get('destaque')),
        "ordem": int(request.form.get('ordem', 0)),
        "data_criacao": datetime.utcnow().isoformat()
    }
    
    dados.setdefault("servicos", []).append(servico)
    salvar_dados_github(f"Serviço criado: {nome}")
    
    flash('Serviço criado com sucesso!', 'success')
    return redirect(url_for('admin_servicos'))

@app.route("/admin/servicos/<servico_id>/editar", methods=['POST'])
@admin_required
def admin_servico_editar(servico_id):
    servicos = dados.get("servicos", [])
    for i, s in enumerate(servicos):
        if s.get("id") == servico_id:
            servicos[i]["nome"] = request.form.get('nome', '').strip()
            servicos[i]["slug"] = request.form.get('slug', '').strip()
            servicos[i]["categoria_id"] = request.form.get('categoria_id') or None
            servicos[i]["descricao"] = request.form.get('descricao', '')
            servicos[i]["preco"] = float(request.form.get('preco', 0))
            servicos[i]["preco_promocional"] = float(request.form.get('preco_promocional')) if request.form.get('preco_promocional') else None
            servicos[i]["tempo_estimado"] = request.form.get('tempo_estimado', '')
            servicos[i]["icone"] = request.form.get('icone', '🎮')
            servicos[i]["status"] = request.form.get('status', 'ativo')
            servicos[i]["destaque"] = bool(request.form.get('destaque'))
            servicos[i]["ordem"] = int(request.form.get('ordem', 0))
            servicos[i]["data_atualizacao"] = datetime.utcnow().isoformat()
            
            salvar_dados_github(f"Serviço atualizado: {servicos[i]['nome']}")
            flash('Serviço atualizado com sucesso!', 'success')
            return redirect(url_for('admin_servicos'))
    
    flash('Serviço não encontrado.', 'danger')
    return redirect(url_for('admin_servicos'))

@app.route("/admin/servicos/<servico_id>/deletar", methods=['POST'])
@admin_required
def admin_servico_deletar(servico_id):
    servicos = dados.get("servicos", [])
    for i, s in enumerate(servicos):
        if s.get("id") == servico_id:
            servicos.pop(i)
            salvar_dados_github("Serviço deletado")
            flash('Serviço removido com sucesso!', 'success')
            return redirect(url_for('admin_servicos'))
    
    flash('Serviço não encontrado.', 'danger')
    return redirect(url_for('admin_servicos'))

@app.route("/api/servico/<servico_id>")
def api_servico(servico_id):
    for s in dados.get("servicos", []):
        if s.get("id") == servico_id:
            return jsonify({"sucesso": True, "servico": s})
    return jsonify({"sucesso": False, "mensagem": "Serviço não encontrado"})

@app.route("/api/cupom/validar", methods=['POST'])
def api_validar_cupom():
    data = request.json
    codigo = data.get('codigo', '').strip().upper()
    valor = data.get('valor', 0)
    
    for cupom in dados.get("cupons", []):
        if cupom.get("codigo") == codigo and cupom.get("ativo", True):
            if cupom.get("validade"):
                try:
                    validade = datetime.fromisoformat(cupom.get("validade"))
                    if datetime.utcnow() > validade:
                        return jsonify({"sucesso": False, "mensagem": "Cupom expirado"})
                except:
                    pass
            
            if cupom.get("max_uso") and cupom.get("usos", 0) >= cupom.get("max_uso"):
                return jsonify({"sucesso": False, "mensagem": "Cupom esgotado"})
            
            if valor < cupom.get("valor_minimo", 0):
                return jsonify({"sucesso": False, "mensagem": f"Valor mínimo: R${cupom.get('valor_minimo', 0):.2f}"})
            
            if cupom.get("tipo") == 'percentual':
                desconto = valor * (cupom.get("valor", 0) / 100)
            else:
                desconto = min(cupom.get("valor", 0), valor)
            
            return jsonify({
                "sucesso": True,
                "desconto": desconto,
                "mensagem": f"Desconto de R${desconto:.2f} aplicado"
            })
    
    return jsonify({"sucesso": False, "mensagem": "Cupom inválido"})

# ========================
# ROTAS DA FILA
# ========================

@app.route("/fila")
def fila_publica():
    fila = obter_dados_fila()
    links = obter_links_fila()
    botoes_precos = links.get("botoes_precos", [])
    
    return render_template_string(FILA_TEMPLATE,
                                fila=fila,
                                links=links,
                                botoes_precos=botoes_precos,
                                session=session,
                                agora_br=agora_br)

@app.route("/fila/embed")
def fila_embed():
    fila = obter_dados_fila()
    links = obter_links_fila()
    botoes_precos = links.get("botoes_precos", [])
    
    return f"""
    <!DOCTYPE html>
    <html><head><meta charset="UTF-8"><meta http-equiv="refresh" content="15">
    <style>body{{margin:0;padding:10px;background:transparent;color:white;font-size:14px;}}.container{{background:rgba(0,0,0,0.7);border-radius:10px;padding:10px;}}
    .status{{display:inline-block;padding:2px 10px;border-radius:10px;font-size:12px;}}
    .status-aberta{{background:#00b894;}}
    .status-fechada{{background:#d63031;}}
    .item{{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid rgba(255,255,255,0.05);}}
    .pos{{color:#ffd93d;font-weight:bold;}}
    .serv{{color:#a8e6cf;}}
    .vazio{{text-align:center;padding:20px;color:#888;}}
    </style></head>
    <body>
    <div class="container">
        <div style="text-align:center;margin-bottom:10px;">
            <strong>📋 {fila.get('nome', 'Fila')}</strong>
            <span class="status status-{'aberta' if fila['configuracoes']['aberta'] else 'fechada'}">{'ABERTA' if fila['configuracoes']['aberta'] else 'FECHADA'}</span>
        </div>
        {''.join(f'<div class="item"><span class="pos">#{e["posicao"]}</span><span>{e["nome_usuario"]}</span><span class="serv">{e["servico"]}</span><span>{e.get("jogo", "")}</span></div>' for e in fila["entradas"][:10]) or '<div class="vazio">✨ Fila vazia</div>'}
        <div style="text-align:center;margin-top:8px;font-size:10px;color:#888;">Total: {len(fila["entradas"])}</div>
    </div>
    </body>
    </html>
    """

@app.route("/fila/api")
def fila_api():
    fila = obter_dados_fila()
    return jsonify({
        "sucesso": True,
        "fila": {
            "nome": fila["nome"],
            "aberta": fila["configuracoes"]["aberta"],
            "tamanho_maximo": fila["configuracoes"]["tamanho_maximo"],
            "contagem": len(fila["entradas"]),
            "entradas": [{"posicao": e["posicao"], "nome_usuario": e["nome_usuario"], "servico": e["servico"], "jogo": e.get("jogo", ""), "timestamp": e["timestamp"], "id": e["id"]} for e in fila["entradas"]]
        }
    })

@app.route("/api/fila/adicionar", methods=["POST"])
def api_fila_adicionar():
    dados_req = request.json
    nome = dados_req.get("nome_usuario", "").strip()
    servico = dados_req.get("servico", "").strip()
    jogo = dados_req.get("jogo", "").strip()
    if not nome or not servico:
        return jsonify({"sucesso": False, "mensagem": "Nome e serviço são obrigatórios"})
    sucesso, resultado = adicionar_fila(nome, servico, jogo)
    return jsonify({"sucesso": sucesso, "mensagem": f"{nome} adicionado!" if sucesso else resultado})

@app.route("/api/fila/remover", methods=["POST"])
def api_fila_remover():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    sucesso, _ = remover_fila(request.json.get("entrada_id"))
    return jsonify({"sucesso": sucesso})

@app.route("/api/fila/mover-cima", methods=["POST"])
def api_fila_mover_cima():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    sucesso, _ = mover_cima(request.json.get("entrada_id"))
    return jsonify({"sucesso": sucesso})

@app.route("/api/fila/mover-baixo", methods=["POST"])
def api_fila_mover_baixo():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    sucesso, _ = mover_baixo(request.json.get("entrada_id"))
    return jsonify({"sucesso": sucesso})

@app.route("/api/fila/concluir", methods=["POST"])
def api_fila_concluir():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    sucesso, _ = concluir_servico(request.json.get("entrada_id"))
    return jsonify({"sucesso": sucesso})

@app.route("/api/fila/limpar", methods=["POST"])
def api_fila_limpar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    limpar_fila()
    return jsonify({"sucesso": True})

@app.route("/api/fila/configuracoes", methods=["GET", "POST"])
def api_fila_configuracoes():
    if request.method == "GET":
        fila = obter_dados_fila()
        links = obter_links_fila()
        return jsonify({"sucesso": True, "configuracoes": fila["configuracoes"], "nome": fila["nome"], "links": links})
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    if "aberta" in req:
        alternar_fila(req["aberta"])
    if "tamanho_maximo" in req:
        definir_tamanho_maximo(int(req["tamanho_maximo"]))
    if "nome" in req:
        definir_nome_fila(req["nome"])
    if "discord_convite" in req:
        salvar_links_fila(req.get("discord_convite", ""))
    return jsonify({"sucesso": True})

@app.route("/api/fila/botoes", methods=["GET"])
def api_fila_botoes():
    links = obter_links_fila()
    return jsonify({"sucesso": True, "botoes": links.get("botoes_precos", [])})

@app.route("/api/fila/botoes/adicionar", methods=["POST"])
def api_fila_botoes_adicionar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    nome = req.get("nome", "").strip()
    url = req.get("url", "").strip()
    if not nome or not url:
        return jsonify({"sucesso": False, "mensagem": "Nome e URL são obrigatórios"})
    adicionar_botao_preco(nome, url)
    return jsonify({"sucesso": True, "mensagem": f"Botão '{nome}' adicionado!"})

@app.route("/api/fila/botoes/remover", methods=["POST"])
def api_fila_botoes_remover():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    index = request.json.get("index")
    if index is None:
        return jsonify({"sucesso": False, "mensagem": "Índice não informado"})
    remover_botao_preco(int(index))
    return jsonify({"sucesso": True, "mensagem": "Botão removido!"})

@app.route("/api/fila/botoes/atualizar", methods=["POST"])
def api_fila_botoes_atualizar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    index = req.get("index")
    nome = req.get("nome", "").strip()
    url = req.get("url", "").strip()
    if index is None or not nome or not url:
        return jsonify({"sucesso": False, "mensagem": "Dados incompletos"})
    atualizar_botao_preco(int(index), nome, url)
    return jsonify({"sucesso": True, "mensagem": "Botão atualizado!"})

@app.route("/admin/fila")
@admin_required
def admin_fila():
    fila = obter_dados_fila()
    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>Admin - Fila</title>
    <style>
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0a0a0a, #1a1a1a); min-height:100vh; color:#e0e0e0; padding:20px; }}
        .container {{ max-width:1000px; margin:0 auto; }}
        .header {{ background:#121212; padding:20px; border-bottom:1px solid #333; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; margin-bottom:20px; }}
        .header h1 {{ color:#5865F2; }}
        .btn {{ padding:10px 20px; border:none; border-radius:8px; cursor:pointer; font-weight:600; text-decoration:none; display:inline-block; }}
        .btn-primary {{ background:#5865F2; color:white; }}
        .btn-primary:hover {{ background:#4752C4; }}
        .btn-success {{ background:#10b981; color:white; }}
        .btn-success:hover {{ background:#059669; }}
        .btn-danger {{ background:#ef4444; color:white; }}
        .btn-danger:hover {{ background:#dc2626; }}
        .btn-outline {{ background:transparent; color:#e0e0e0; border:1px solid #555; }}
        .btn-outline:hover {{ background:#333; }}
        .card {{ background:#121212; border:1px solid #333; border-radius:12px; padding:20px; margin:15px 0; }}
        table {{ width:100%; border-collapse:collapse; }}
        th, td {{ text-align:left; padding:10px; border-bottom:1px solid #333; }}
        th {{ color:#888; font-weight:600; font-size:12px; text-transform:uppercase; }}
        .actions {{ display:flex; gap:5px; flex-wrap:wrap; }}
        .actions .btn {{ padding:5px 10px; font-size:12px; }}
        .footer {{ text-align:center; padding:30px; color:#666; border-top:1px solid #333; margin-top:40px; }}
        .status {{ padding:2px 10px; border-radius:12px; font-size:12px; font-weight:bold; }}
        .status-aguardando {{ background:#f59e0b; color:#000; }}
        .status-concluido {{ background:#10b981; color:#fff; }}
    </style>
    </head>
    <body>
    <div class="header">
        <h1>📋 Gerenciar Fila</h1>
        <div>
            <a href="/admin" class="btn btn-outline">← Admin</a>
            <a href="/fila" class="btn btn-primary">🔍 Ver Fila</a>
        </div>
    </div>
    <div class="container">
        <div class="card">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
                <div>
                    <strong>Status:</strong> <span style="color:{'#4ade80' if fila['configuracoes']['aberta'] else '#ef4444'};">{'🟢 ABERTA' if fila['configuracoes']['aberta'] else '🔴 FECHADA'}</span>
                    <span style="margin-left:15px;"><strong>Total:</strong> {len(fila['entradas'])} / {fila['configuracoes']['tamanho_maximo']}</span>
                </div>
                <div class="actions">
                    <button onclick="alternarStatus()" class="btn {'btn-danger' if fila['configuracoes']['aberta'] else 'btn-success'}">{'🔒 Fechar' if fila['configuracoes']['aberta'] else '🔓 Abrir'}</button>
                    <button onclick="limparFila()" class="btn btn-danger">🗑️ Limpar</button>
                </div>
            </div>
        </div>
        
        <div class="card">
            <h3 style="color:#5865F2;">📋 Fila de Espera</h3>
            <table>
                <thead><tr><th>#</th><th>Jogador</th><th>Serviço</th><th>Jogo</th><th>Status</th><th>Ações</th></tr></thead>
                <tbody>
                    {''.join(f'''
                    <tr>
                        <td>{e['posicao']}</td>
                        <td>{e['nome_usuario']}</td>
                        <td>{e['servico']}</td>
                        <td>{e.get('jogo', '')}</td>
                        <td><span class="status status-{e['status']}">{e['status']|title}</span></td>
                        <td class="actions">
                            <button onclick="moverCima('{e['id']}')" class="btn btn-primary">⬆️</button>
                            <button onclick="moverBaixo('{e['id']}')" class="btn btn-primary">⬇️</button>
                            <button onclick="concluir('{e['id']}')" class="btn btn-success">✅</button>
                            <button onclick="remover('{e['id']}')" class="btn btn-danger">❌</button>
                        </td>
                    </tr>
                    ''' for e in fila['entradas'])}
                    {'' if fila['entradas'] else '<tr><td colspan="6" style="text-align:center;color:#888;">📭 Ninguém na fila</td></tr>'}
                </tbody>
            </table>
        </div>
    </div>
    
    <script>
        async function alternarStatus() {{
            const resp = await fetch('/api/fila/configuracoes', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{aberta: null}})
            }});
            window.location.reload();
        }}
        
        async function limparFila() {{
            if (confirm('LIMPAR TODA A FILA?')) {{
                await fetch('/api/fila/limpar', {{method: 'POST'}});
                window.location.reload();
            }}
        }}
        
        async function remover(id) {{
            if (confirm('Remover da fila?')) {{
                await fetch('/api/fila/remover', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{entrada_id:id}})}});
                window.location.reload();
            }}
        }}
        
        async function moverCima(id) {{
            await fetch('/api/fila/mover-cima', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{entrada_id:id}})}});
            window.location.reload();
        }}
        
        async function moverBaixo(id) {{
            await fetch('/api/fila/mover-baixo', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{entrada_id:id}})}});
            window.location.reload();
        }}
        
        async function concluir(id) {{
            if (confirm('Concluir serviço?')) {{
                await fetch('/api/fila/concluir', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{entrada_id:id}})}});
                window.location.reload();
            }}
        }}
    </script>
    
    <div class="footer"><p>© 2024 Imune Bot</p></div>
    </body>
    </html>
    """

# ========================
# BOT SETUP
# ========================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree

# ========================
# FUNÇÕES DO BOT
# ========================

def xp_por_mensagem():
    return 15

def xp_para_nivel(xp):
    nivel = int((xp / 100) ** 0.6) + 1
    return max(nivel, 1)

def verificar_comando_ignorado(conteudo: str) -> bool:
    conteudo_lower = conteudo.lower().strip()
    comandos_ignorados = dados.get("anti_spam", {}).get("comandos_ignorados", [])
    for comando in comandos_ignorados:
        if conteudo_lower.startswith(comando.lower()):
            return True
    return False

def verificar_cargo_ignorado(member: discord.Member) -> bool:
    cargos_ignorados = dados.get("anti_spam", {}).get("cargos_ignorados", [])
    cargos_membro = [role.name for role in member.roles]
    for cargo_ignorado in cargos_ignorados:
        if cargo_ignorado in cargos_membro:
            return True
    return False

mensagens_recentes = {}

def limpar_mensagens_antigas(user_id: int):
    if user_id not in mensagens_recentes:
        return
    intervalo = dados.get("anti_spam", {}).get("intervalo_segundos", 5)
    agora = time.time()
    mensagens_recentes[user_id] = [ts for ts in mensagens_recentes[user_id] if agora - ts < intervalo]
    if not mensagens_recentes[user_id]:
        del mensagens_recentes[user_id]

def registrar_mensagem(user_id: int) -> int:
    agora = time.time()
    if user_id not in mensagens_recentes:
        mensagens_recentes[user_id] = []
    mensagens_recentes[user_id].append(agora)
    limpar_mensagens_antigas(user_id)
    return len(mensagens_recentes.get(user_id, []))

async def aplicar_mute(member: discord.Member, duracao_minutos: int = 2):
    guild = member.guild
    mute_role = discord.utils.get(guild.roles, name="Muted")
    if not mute_role:
        try:
            mute_role = await guild.create_role(name="Muted", permissions=discord.Permissions.none())
            for channel in guild.channels:
                try:
                    await channel.set_permissions(mute_role, send_messages=False, add_reactions=False, speak=False)
                except:
                    pass
        except Exception as e:
            print(f"❌ Erro ao criar cargo de mute: {e}")
            return False
    try:
        await member.add_roles(mute_role, reason=f"Anti-spam: {duracao_minutos} minutos de mute")
        async def remover_mute():
            await asyncio.sleep(duracao_minutos * 60)
            try:
                await member.remove_roles(mute_role, reason="Fim do mute por spam")
            except:
                pass
        asyncio.create_task(remover_mute())
        return True
    except Exception as e:
        print(f"❌ Erro ao aplicar mute: {e}")
        return False

async def deletar_mensagens_spam(member: discord.Member, channel: discord.TextChannel, quantidade: int):
    if not dados.get("anti_spam", {}).get("deletar_mensagens", True):
        return
    try:
        async for msg in channel.history(limit=quantidade + 5):
            if msg.author == member:
                try:
                    await msg.delete()
                    await asyncio.sleep(0.5)
                except:
                    pass
    except:
        pass

async def remover_xp_por_spam(member: discord.Member):
    if not dados.get("anti_spam", {}).get("remover_xp", True):
        return False
    uid = str(member.id)
    penalidade = dados.get("anti_spam", {}).get("xp_penalidade", 50)
    xp_atual = dados.get("xp", {}).get(uid, 0)
    novo_xp = max(0, xp_atual - penalidade)
    dados["xp"][uid] = novo_xp
    novo_nivel = xp_para_nivel(novo_xp)
    dados["nivel"][uid] = novo_nivel
    salvar_dados_github(f"Anti-spam: {penalidade} XP removido de {member.name}")
    return True

async def verificar_canal_permitido(interaction: discord.Interaction, comando: str) -> bool:
    config = dados.get("config", {})
    canal_permitido = config.get(f"canal_{comando}", None)
    if not canal_permitido:
        return True
    if str(interaction.channel_id) == str(canal_permitido):
        return True
    return False

# ========================
# COMANDOS SLASH DO DISCORD
# ========================

@tree.command(name="perfil", description="Mostra o seu perfil com XP e nível")
@app_commands.describe(membro="Membro para ver o perfil (opcional)")
async def slash_perfil(interaction: discord.Interaction, membro: discord.Member = None):
    if not await verificar_canal_permitido(interaction, "perfil"):
        canal_permitido = dados.get("config", {}).get("canal_perfil")
        canal_menção = f"<#{canal_permitido}>" if canal_permitido else "nenhum canal configurado"
        await interaction.response.send_message(f"❌ O comando `/perfil` só pode ser usado no canal {canal_menção}!", ephemeral=True)
        return
    
    await interaction.response.defer(thinking=True)
    
    alvo = membro or interaction.user
    uid = str(alvo.id)
    xp = dados.get("xp", {}).get(uid, 0)
    nivel = dados.get("nivel", {}).get(uid, xp_para_nivel(xp))
    
    ranking = sorted(dados.get("xp", {}).items(), key=lambda t: t[1], reverse=True)
    pos = next((i+1 for i, (u, _) in enumerate(ranking) if u == uid), len(ranking))
    
    largura, altura = 900, 200
    img = Image.new("RGBA", (largura, altura), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    try:
        font_b = ImageFont.truetype(os.path.join(BASE_DIR, "DejaVuSans-Bold.ttf"), 32)
        font_s = ImageFont.truetype(os.path.join(BASE_DIR, "DejaVuSans.ttf"), 22)
    except:
        font_b = ImageFont.load_default()
        font_s = ImageFont.load_default()
    
    try:
        avatar_bytes = await alvo.avatar.read()
        avatar = Image.open(BytesIO(avatar_bytes)).convert("RGBA")
        avatar = avatar.resize((120, 120))
        mask = Image.new("L", (120, 120), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, 120, 120), fill=255)
        img.paste(avatar, (20, 40), mask)
    except:
        pass
    
    draw.text((160, 50), alvo.display_name, font=font_b, fill=(0, 255, 255))
    draw.text((largura - 220, 40), f"CLASSIFICAÇÃO #{pos}", font=font_s, fill=(0, 255, 255))
    draw.text((largura - 220, 80), f"NÍVEL {nivel}", font=font_s, fill=(255, 0, 255))
    
    proximo_xp = 100 + nivel * 50
    atual = xp % proximo_xp
    barra_total_w, barra_h = 560, 36
    x0, y0 = 160, 140
    raio = barra_h // 2
    
    draw.rounded_rectangle([x0, y0, x0 + barra_total_w, y0 + barra_h], radius=raio, fill=(50, 50, 50))
    preenchimento_w = int(barra_total_w * min(1.0, atual / proximo_xp))
    if preenchimento_w > 0:
        barra_preenchida = Image.new("RGBA", (preenchimento_w, barra_h), (0, 0, 0, 0))
        fill_draw = ImageDraw.Draw(barra_preenchida)
        fill_draw.rounded_rectangle([0, 0, preenchimento_w, barra_h], radius=raio, fill=(0, 200, 255))
        img.paste(barra_preenchida, (x0, y0), barra_preenchida)
    
    texto_xp = f"{atual} / {proximo_xp} XP"
    bbox = draw.textbbox((0, 0), texto_xp, font=font_s)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    text_x = x0 + (barra_total_w - text_w) // 2
    text_y = y0 + (barra_h - text_h) // 2
    draw.text((text_x, text_y), texto_xp, font=font_s, fill=(255, 255, 255))
    
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    arquivo = discord.File(buf, filename="perfil.png")
    await interaction.followup.send(file=arquivo)

@tree.command(name="rank", description="Mostra o ranking dos 10 maiores XP")
async def slash_rank(interaction: discord.Interaction):
    if not await verificar_canal_permitido(interaction, "rank"):
        canal_permitido = dados.get("config", {}).get("canal_rank")
        canal_menção = f"<#{canal_permitido}>" if canal_permitido else "nenhum canal configurado"
        await interaction.response.send_message(f"❌ O comando `/rank` só pode ser usado no canal {canal_menção}!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    ranking = sorted(dados.get("xp", {}).items(), key=lambda t: t[1], reverse=True)[:10]
    linhas = []
    for i, (uid, xp) in enumerate(ranking, 1):
        user = interaction.guild.get_member(int(uid))
        nome = user.display_name if user else f"Usuário {uid}"
        nivel = dados.get("nivel", {}).get(uid, xp_para_nivel(xp))
        linhas.append(f"{i}. **{nome}** — {xp} XP (Nível {nivel})")
    
    texto = "\n".join(linhas) if linhas else "Sem dados ainda."
    embed = discord.Embed(title="🏆 Top 10 Ranking de XP", description=texto, color=discord.Color.gold())
    await interaction.followup.send(embed=embed)

# ========================
# EVENTOS DO BOT
# ========================

@bot.event
async def on_ready():
    print(f"\n{'='*50}")
    print(f"🤖 BOT INICIADO: {bot.user}")
    print(f"{'='*50}")
    
    print("📂 Carregando dados do GitHub...")
    carregar_dados_github()
    
    print("⚙️ Sincronizando comandos slash...")
    try:
        if GUILD_ID:
            await tree.sync(guild=discord.Object(id=int(GUILD_ID)))
            print(f"✅ Comandos sincronizados no servidor")
        else:
            await tree.sync()
            print("✅ Comandos globais sincronizados")
    except Exception as e:
        print(f"❌ Erro ao sincronizar: {e}")
    
    await asyncio.sleep(2)
    iniciar_processador_acoes()
    
    config = dados.get("config", {})
    print(f"{'='*50}")
    print(f"✨ BOT PRONTO! Comandos: /perfil e /rank")
    print(f"🛡️ Anti-Spam: {'ATIVADO' if dados.get('anti_spam', {}).get('ativado', True) else 'DESATIVADO'}")
    print(f"📢 Canal do /perfil: {config.get('canal_perfil') or 'TODOS OS CANAIS'}")
    print(f"📢 Canal do /rank: {config.get('canal_rank') or 'TODOS OS CANAIS'}")
    print(f"{'='*50}\n")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    
    conteudo = message.content.strip()
    anti_spam_config = dados.get("anti_spam", {})
    
    if verificar_comando_ignorado(conteudo):
        await bot.process_commands(message)
        return
    
    if anti_spam_config.get("ativado", True):
        if not verificar_cargo_ignorado(message.author):
            quantidade = registrar_mensagem(message.author.id)
            limite = anti_spam_config.get("limite_mensagens", 5)
            
            if quantidade > limite:
                duracao = anti_spam_config.get("tempo_mute_minutos", 2)
                sucesso = await aplicar_mute(message.author, duracao)
                
                if sucesso:
                    if anti_spam_config.get("deletar_mensagens", True):
                        await deletar_mensagens_spam(message.author, message.channel, quantidade)
                    
                    xp_removido = await remover_xp_por_spam(message.author) if anti_spam_config.get("remover_xp", True) else False
                    
                    xp_msg = f" e teve **{anti_spam_config.get('xp_penalidade', 50)} XP removido**" if xp_removido else ""
                    try:
                        await message.author.send(f"⚠️ **Você foi mutado por {duracao} minutos** devido a spam no servidor {message.guild.name}!{xp_msg}")
                    except:
                        await message.channel.send(f"⚠️ {message.author.mention}, você foi mutado por **{duracao} minutos** por spam!{xp_msg}")
                return
    
    canais_bloqueados = dados.get("canais_links_bloqueados", [])
    if message.channel.id in canais_bloqueados:
        url_pattern = r"https?://[^\s]+"
        if re.search(url_pattern, conteudo):
            cargos_ignorados = {"Administrador", "Moderador"}
            if not any(r.name in cargos_ignorados for r in message.author.roles):
                try:
                    await message.delete()
                    await message.channel.send(f"⚠️ {message.author.mention}, links não são permitidos aqui!")
                except:
                    pass
                return
    
    dados.setdefault("xp", {})
    dados.setdefault("nivel", {})
    
    taxa_xp = dados.get("config", {}).get("taxa_xp", 3)
    ganho_xp = max(1, xp_por_mensagem() // taxa_xp)
    dados["xp"][str(message.author.id)] = dados["xp"].get(str(message.author.id), 0) + ganho_xp
    
    xp_atual = dados["xp"][str(message.author.id)]
    nivel_atual = xp_para_nivel(xp_atual)
    nivel_anterior = dados["nivel"].get(str(message.author.id), 1)
    
    if nivel_atual > nivel_anterior:
        dados["nivel"][str(message.author.id)] = nivel_atual
        
        canal_levelup_id = dados.get("config", {}).get("canal_levelup")
        if canal_levelup_id:
            canal = message.guild.get_channel(int(canal_levelup_id))
            if canal:
                await canal.send(f"🎉 {message.author.mention} subiu para o nível **{nivel_atual}**!")
        
        cargo_id = dados.get("cargos_nivel", {}).get(str(nivel_atual))
        if cargo_id:
            cargo = message.guild.get_role(int(cargo_id))
            if cargo:
                try:
                    await message.author.add_roles(cargo, reason=f"Nível {nivel_atual}")
                except:
                    pass
    
    try:
        salvar_dados_github("XP update")
    except:
        pass
    
    await bot.process_commands(message)

# ========================
# AUTO PING
# ========================
def auto_ping():
    while True:
        try:
            url = os.environ.get("REPLIT_URL") or os.environ.get("SELF_URL")
            if url:
                requests.get(url)
            time.sleep(300)
        except:
            pass

Thread(target=auto_ping, daemon=True).start()

# ========================
# INICIAR BOT E FLASK
# ========================

def run_flask():
    """Inicia o servidor Flask"""
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

def run_bot():
    """Inicia o bot do Discord"""
    try:
        print("🤖 Iniciando bot do Discord...")
        bot.run(BOT_TOKEN)
    except Exception as e:
        print(f"❌ Erro ao iniciar o bot: {e}")

if __name__ == "__main__":
    # Para execução local
    import threading
    
    # Inicia o bot em uma thread separada
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Aguarda o bot iniciar
    time.sleep(2)
    
    # Inicia o Flask
    run_flask()
else:
    # Para execução no Gunicorn (Render)
    # O bot precisa ser iniciado em background
    import threading
    import asyncio
    
    def start_bot_async():
        """Inicia o bot de forma assíncrona"""
        try:
            # Usa asyncio para rodar o bot
            asyncio.run(bot.start(BOT_TOKEN))
        except Exception as e:
            print(f"❌ Erro ao iniciar bot: {e}")
    
    bot_thread = threading.Thread(target=start_bot_async, daemon=True)
    bot_thread.start()
    print("✅ Bot iniciado em background")