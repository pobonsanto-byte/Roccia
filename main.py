import os
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
from decimal import Decimal
from urllib.parse import urlencode

import asyncio
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, abort, make_response
import discord
from discord import app_commands
from discord.ext import commands
from discord import ui, Interaction, ButtonStyle
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv

# Carregar variáveis de ambiente
load_dotenv()

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

# Configurações do site
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://seu-site.onrender.com/callback")
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))

# Configurações de pagamento
MERCADO_PAGO_ACCESS_TOKEN = os.getenv("MERCADO_PAGO_ACCESS_TOKEN")
MERCADO_PAGO_PUBLIC_KEY = os.getenv("MERCADO_PAGO_PUBLIC_KEY")
PIX_WEBHOOK_SECRET = os.getenv("PIX_WEBHOOK_SECRET", secrets.token_hex(32))

# Configurações de pontos
PONTOS_POR_REAL = int(os.getenv("PONTOS_POR_REAL", 10))

# Banco de dados - SQLite como fallback para Render (gratuito)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///meu_bot.db")

if not BOT_TOKEN or not GITHUB_TOKEN:
    raise SystemExit("Defina BOT_TOKEN e GITHUB_TOKEN nas variáveis de ambiente.")

GITHUB_API_CONTENT = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{DATA_FILE}"

# ========================
# FLASK APP
# ========================
app = Flask(__name__)
app.secret_key = SECRET_KEY

# ========================
# CONFIGURAÇÃO DO BANCO DE DADOS (SQLAlchemy)
# ========================
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import Integer, String, Float, DateTime, Boolean, Text, JSON, ForeignKey, Numeric
from typing import Optional, List

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

# Configurar banco de dados
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_size": 10,
    "max_overflow": 20,
    "pool_pre_ping": True,
}

db.init_app(app)

# ========================
# MODELOS
# ========================

class Usuario(db.Model):
    __tablename__ = 'usuarios'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    discord_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    nome: Mapped[str] = mapped_column(String(100), nullable=False)
    avatar: Mapped[Optional[str]] = mapped_column(String(200))
    pontos: Mapped[int] = mapped_column(Integer, default=0)
    data_cadastro: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

class Categoria(db.Model):
    __tablename__ = 'categorias'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(50), nullable=False)
    icone: Mapped[Optional[str]] = mapped_column(String(50))
    status: Mapped[bool] = mapped_column(Boolean, default=True)
    ordem: Mapped[int] = mapped_column(Integer, default=0)

class Servico(db.Model):
    __tablename__ = 'servicos'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(100), nullable=False)
    categoria_id: Mapped[Optional[int]] = mapped_column(ForeignKey('categorias.id'))
    descricao: Mapped[Optional[str]] = mapped_column(Text)
    preco: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    imagem: Mapped[Optional[str]] = mapped_column(String(500))
    status: Mapped[bool] = mapped_column(Boolean, default=True)
    destaque: Mapped[bool] = mapped_column(Boolean, default=False)
    tempo_estimado: Mapped[Optional[str]] = mapped_column(String(50))
    ordem: Mapped[int] = mapped_column(Integer, default=0)
    data_criacao: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    
    categoria = db.relationship('Categoria', backref='servicos')

class Pedido(db.Model):
    __tablename__ = 'pedidos'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    numero: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    usuario_id: Mapped[Optional[int]] = mapped_column(ForeignKey('usuarios.id'))
    servico_id: Mapped[Optional[int]] = mapped_column(ForeignKey('servicos.id'))
    valor: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default='aguardando_pagamento')
    dados_cliente: Mapped[Optional[dict]] = mapped_column(JSON)
    historico: Mapped[Optional[list]] = mapped_column(JSON)
    data_criacao: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    data_atualizacao: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    usuario = db.relationship('Usuario', backref='pedidos')
    servico = db.relationship('Servico', backref='pedidos')

class Pagamento(db.Model):
    __tablename__ = 'pagamentos'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    pedido_id: Mapped[Optional[int]] = mapped_column(ForeignKey('pedidos.id'))
    metodo: Mapped[str] = mapped_column(String(30), default='pix')
    valor: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default='pendente')
    dados_pagamento: Mapped[Optional[dict]] = mapped_column(JSON)
    data_criacao: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    data_pagamento: Mapped[Optional[datetime]] = mapped_column(DateTime)
    
    pedido = db.relationship('Pedido', backref='pagamentos')

class TransacaoPontos(db.Model):
    __tablename__ = 'transacoes_pontos'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[Optional[int]] = mapped_column(ForeignKey('usuarios.id'))
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)
    descricao: Mapped[Optional[str]] = mapped_column(String(200))
    data: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    
    usuario = db.relationship('Usuario', backref='transacoes_pontos')

class Resgate(db.Model):
    __tablename__ = 'resgates'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(100), nullable=False)
    pontos: Mapped[int] = mapped_column(Integer, nullable=False)
    tipo: Mapped[str] = mapped_column(String(30), default='desconto')
    valor: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))
    descricao: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[bool] = mapped_column(Boolean, default=True)
    data_criacao: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

class Cupom(db.Model):
    __tablename__ = 'cupons'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    tipo: Mapped[str] = mapped_column(String(20), default='porcentagem')
    valor: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    validade: Mapped[Optional[datetime]] = mapped_column(DateTime)
    quantidade_maxima: Mapped[int] = mapped_column(Integer, default=1)
    quantidade_usada: Mapped[int] = mapped_column(Integer, default=0)
    valor_minimo: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    usuarios_permitidos: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[bool] = mapped_column(Boolean, default=True)
    data_criacao: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

class Log(db.Model):
    __tablename__ = 'logs'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[Optional[str]] = mapped_column(String(30))
    mensagem: Mapped[str] = mapped_column(Text, nullable=False)
    usuario_id: Mapped[Optional[int]] = mapped_column(ForeignKey('usuarios.id'))
    ip: Mapped[Optional[str]] = mapped_column(String(50))
    data: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

class Configuracao(db.Model):
    __tablename__ = 'configuracoes'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    chave: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    valor: Mapped[Optional[str]] = mapped_column(Text)
    descricao: Mapped[Optional[str]] = mapped_column(String(200))
    data_atualizacao: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

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
# ESTRUTURA DE DADOS (LEGADO)
# ========================
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
        "canal_rank": None,
        "canal_pedidos": None
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
    }
}

mensagens_recentes = {}
acoes_fila_bot = []
processador_acoes_task = None
processador_acoes_rodando = False

# ========================
# FUNÇÕES UTILITÁRIAS
# ========================

def agora_br():
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-3)))

def _gh_headers():
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

def carregar_dados_github():
    try:
        r = requests.get(GITHUB_API_CONTENT, headers=_gh_headers(), params={"ref": BRANCH}, timeout=15)
        if r.status_code == 200:
            js = r.json()
            conteudo_b64 = js.get("content", "")
            if conteudo_b64:
                raw = base64.b64decode(conteudo_b64)
                carregado = json.loads(raw.decode("utf-8"))
                dados.update(carregado)
                for key in ["fila", "botoes_cargos", "cargos_nivel", "canais_links_bloqueados", 
                           "links_fila", "anti_spam", "config", "reacoes_cargos"]:
                    if key not in dados:
                        dados[key] = {}
                if "botoes_precos" not in dados.get("links_fila", {}):
                    dados["links_fila"]["botoes_precos"] = []
                if "canal_pedidos" not in dados.get("config", {}):
                    dados["config"]["canal_pedidos"] = None
                print("✅ Dados carregados do GitHub.")
                return True
        else:
            print(f"⚠️ GitHub GET retornou {r.status_code}")
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
            print(f"❌ Erro ao salvar no GitHub: {put.status_code}")
    except Exception as e:
        print(f"❌ Exception saving to GitHub: {e}")
    return False

def adicionar_log(entrada, tipo="info", usuario_id=None):
    try:
        with app.app_context():
            log = Log(
                tipo=tipo,
                mensagem=entrada,
                usuario_id=usuario_id,
                ip=request.remote_addr if request else None
            )
            db.session.add(log)
            db.session.commit()
    except Exception as e:
        print(f"Erro ao adicionar log: {e}")
        ts = agora_br().isoformat()
        dados.setdefault("logs", []).append({"ts": ts, "entrada": entrada})
        try:
            salvar_dados_github(f"log: {entrada}")
        except Exception:
            pass

def formatar_preco(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def gerar_numero_pedido():
    return f"PED-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

def gerar_codigo_cupom():
    return f"CP-{secrets.token_hex(4).upper()}"

def escape_html(texto):
    if not texto:
        return ""
    return (texto
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )

# ========================
# FUNÇÕES DA FILA (LEGADO)
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
    salvar_dados_github(f"fila_adicionar: {nome_usuario} - {servico}")
    return True, entrada

def remover_fila(entrada_id: str):
    fila = obter_dados_fila()
    for i, entrada in enumerate(fila["entradas"]):
        if entrada["id"] == entrada_id:
            removido = fila["entradas"].pop(i)
            fila["historico"].append(removido)
            if len(fila["historico"]) > 100:
                fila["historico"] = fila["historico"][-100:]
            salvar_dados_github(f"fila_remover: {removido['nome_usuario']}")
            return True, removido
    return False, None

def concluir_servico(entrada_id: str):
    fila = obter_dados_fila()
    for i, entrada in enumerate(fila["entradas"]):
        if entrada["id"] == entrada_id:
            removido = fila["entradas"].pop(i)
            removido["status"] = "concluido"
            removido["concluido_em"] = agora_br().isoformat()
            fila["historico"].append(removido)
            salvar_dados_github(f"fila_concluir: {removido['nome_usuario']}")
            return True, removido
    return False, None

def limpar_fila():
    fila = obter_dados_fila()
    fila["entradas"] = []
    salvar_dados_github("fila_limpa")
    return True

def alternar_fila(aberto: bool = None):
    fila = obter_dados_fila()
    if aberto is None:
        fila["configuracoes"]["aberta"] = not fila["configuracoes"]["aberta"]
    else:
        fila["configuracoes"]["aberta"] = aberto
    salvar_dados_github("fila_alternada")
    return fila["configuracoes"]["aberta"]

def definir_tamanho_maximo(tamanho: int):
    fila = obter_dados_fila()
    fila["configuracoes"]["tamanho_maximo"] = max(1, min(tamanho, 100))
    salvar_dados_github("fila_tamanho")
    return fila["configuracoes"]["tamanho_maximo"]

def definir_nome_fila(nome: str):
    fila = obter_dados_fila()
    fila["nome"] = nome[:50]
    salvar_dados_github("fila_nome")
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
# FUNÇÕES ANTI-SPAM
# ========================

def verificar_comando_ignorado(conteudo: str) -> bool:
    conteudo_lower = conteudo.lower().strip()
    comandos_ignorados = dados.get("anti_spam", {}).get("comandos_ignorados", [])
    for comando in comandos_ignorados:
        if conteudo_lower.startswith(comando.lower()):
            return True
        if conteudo_lower == comando.lower():
            return True
    return False

def verificar_cargo_ignorado(member: discord.Member) -> bool:
    cargos_ignorados = dados.get("anti_spam", {}).get("cargos_ignorados", [])
    cargos_membro = [role.name for role in member.roles]
    for cargo_ignorado in cargos_ignorados:
        if cargo_ignorado in cargos_membro:
            return True
    return False

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

# ========================
# FUNÇÕES DE PAGAMENTO
# ========================

def criar_pagamento_pix(servico_nome, valor, usuario_id, pedido_id):
    if not MERCADO_PAGO_ACCESS_TOKEN:
        return {"erro": "API de pagamento não configurada"}
    try:
        url = "https://api.mercadopago.com/v1/payments"
        headers = {
            "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "transaction_amount": float(valor),
            "description": f"{servico_nome} - Pedido #{pedido_id}",
            "payment_method_id": "pix",
            "payer": {
                "email": f"cliente_{usuario_id}@bot.com",
                "identification": {
                    "type": "CPF",
                    "number": "12345678909"
                }
            },
            "metadata": {
                "pedido_id": pedido_id,
                "usuario_id": usuario_id,
                "servico": servico_nome
            }
        }
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        if response.status_code in [200, 201]:
            data = response.json()
            return {
                "sucesso": True,
                "id": data.get("id"),
                "qr_code": data.get("point_of_interaction", {}).get("transaction_data", {}).get("qr_code"),
                "qr_code_base64": data.get("point_of_interaction", {}).get("transaction_data", {}).get("qr_code_base64"),
                "ticket_url": data.get("point_of_interaction", {}).get("transaction_data", {}).get("ticket_url"),
                "status": data.get("status"),
                "payment_id": data.get("id"),
                "pix_copia_e_cola": data.get("point_of_interaction", {}).get("transaction_data", {}).get("qr_code")
            }
        else:
            return {"sucesso": False, "erro": f"Erro {response.status_code}: {response.text[:200]}"}
    except Exception as e:
        return {"sucesso": False, "erro": str(e)}

def verificar_pagamento_pix(payment_id):
    if not MERCADO_PAGO_ACCESS_TOKEN:
        return {"sucesso": False, "erro": "API não configurada"}
    try:
        url = f"https://api.mercadopago.com/v1/payments/{payment_id}"
        headers = {"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"}
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            return {
                "sucesso": True,
                "status": data.get("status"),
                "status_detail": data.get("status_detail"),
                "payment_id": data.get("id")
            }
        else:
            return {"sucesso": False, "erro": f"Erro {response.status_code}"}
    except Exception as e:
        return {"sucesso": False, "erro": str(e)}

# ========================
# SISTEMA DE AÇÕES DO BOT
# ========================

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
            return True
        
        elif tipo_acao == "notificar_pedido":
            canal_id = dados_acao.get("canal_id")
            if not canal_id:
                return False
            canal = guild.get_channel(int(canal_id))
            if not canal:
                return False
            mensagem = dados_acao.get("mensagem", "")
            embed_data = dados_acao.get("embed")
            if embed_data:
                embed = discord.Embed(
                    title=embed_data.get("title", "Novo Pedido"),
                    description=embed_data.get("description", ""),
                    color=discord.Color.green() if embed_data.get("color") == "green" else discord.Color.blue()
                )
                if embed_data.get("fields"):
                    for field in embed_data["fields"]:
                        embed.add_field(
                            name=field.get("name", ""),
                            value=field.get("value", ""),
                            inline=field.get("inline", False)
                        )
                if embed_data.get("thumbnail"):
                    embed.set_thumbnail(url=embed_data["thumbnail"])
                if embed_data.get("image"):
                    embed.set_image(url=embed_data["image"])
                await canal.send(content=mensagem, embed=embed)
            else:
                await canal.send(mensagem)
            return True
        
        else:
            print(f"❌ Tipo de ação desconhecido: {tipo_acao}")
            return False
    
    except Exception as e:
        print(f"❌ Erro: {e}")
        return False

async def processar_acoes_bot_continuo():
    global processador_acoes_rodando
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
# ROTAS DO SITE
# ========================

@app.route("/", methods=["GET"])
def home():
    status_bot = "✅ Bot Online" if bot.is_ready() else "❌ Bot Offline"
    classe_bot = "online" if bot.is_ready() else "offline"
    usuario = session.get('usuario')
    
    with app.app_context():
        servicos_destaque = Servico.query.filter(
            Servico.destaque == True,
            Servico.status == True
        ).order_by(Servico.ordem).limit(6).all()
        categorias = Categoria.query.filter(Categoria.status == True).all()
    
    return render_template(
        'index.html',
        status_bot=status_bot,
        classe_bot=classe_bot,
        usuario=usuario,
        servicos_destaque=servicos_destaque,
        categorias=categorias,
        bot=bot
    )

@app.route("/servicos")
def servicos():
    categoria_id = request.args.get('categoria', type=int)
    busca = request.args.get('busca', '')
    
    with app.app_context():
        query = Servico.query.filter(Servico.status == True)
        if categoria_id:
            query = query.filter(Servico.categoria_id == categoria_id)
        if busca:
            query = query.filter(
                Servico.nome.ilike(f"%{busca}%") | 
                Servico.descricao.ilike(f"%{busca}%")
            )
        servicos = query.order_by(Servico.destaque.desc(), Servico.ordem).all()
        categorias = Categoria.query.filter(Categoria.status == True).all()
    
    return render_template(
        'servicos.html',
        servicos=servicos,
        categorias=categorias,
        categoria_selecionada=categoria_id,
        busca=busca,
        usuario=session.get('usuario'),
        formatar_preco=formatar_preco
    )

@app.route("/servico/<int:servico_id>")
def servico_detalhe(servico_id):
    with app.app_context():
        servico = Servico.query.filter(
            Servico.id == servico_id,
            Servico.status == True
        ).first()
        if not servico:
            flash('Serviço não encontrado.', 'danger')
            return redirect(url_for('servicos'))
        relacionados = Servico.query.filter(
            Servico.categoria_id == servico.categoria_id,
            Servico.id != servico_id,
            Servico.status == True
        ).limit(4).all()
    
    return render_template(
        'servico_detalhe.html',
        servico=servico,
        relacionados=relacionados,
        usuario=session.get('usuario'),
        formatar_preco=formatar_preco
    )

@app.route("/servico/<int:servico_id>/comprar", methods=['POST'])
def comprar_servico(servico_id):
    if 'usuario' not in session:
        flash('Faça login para comprar.', 'warning')
        return redirect(url_for('login'))
    
    with app.app_context():
        servico = Servico.query.filter(
            Servico.id == servico_id,
            Servico.status == True
        ).first()
        if not servico:
            flash('Serviço não encontrado.', 'danger')
            return redirect(url_for('servicos'))
        
        usuario = Usuario.query.filter(
            Usuario.discord_id == session['usuario']['id']
        ).first()
        if not usuario:
            usuario = Usuario(
                discord_id=session['usuario']['id'],
                nome=session['usuario']['nome_usuario'],
                avatar=session['usuario'].get('avatar'),
                data_cadastro=datetime.now()
            )
            db.session.add(usuario)
            db.session.commit()
        
        pedido = Pedido(
            numero=gerar_numero_pedido(),
            usuario_id=usuario.id,
            servico_id=servico.id,
            valor=servico.preco,
            status='aguardando_pagamento',
            data_criacao=datetime.now(),
            dados_cliente={
                "discord_id": session['usuario']['id'],
                "discord_nome": session['usuario']['nome_usuario']
            }
        )
        db.session.add(pedido)
        db.session.commit()
        
        resultado_pix = criar_pagamento_pix(
            servico.nome,
            float(servico.preco),
            usuario.id,
            pedido.id
        )
        
        if resultado_pix.get('sucesso'):
            pagamento = Pagamento(
                pedido_id=pedido.id,
                metodo='pix',
                valor=servico.preco,
                status='pendente',
                dados_pagamento={
                    "payment_id": resultado_pix.get('payment_id'),
                    "qr_code": resultado_pix.get('qr_code'),
                    "qr_code_base64": resultado_pix.get('qr_code_base64'),
                    "ticket_url": resultado_pix.get('ticket_url'),
                    "pix_copia_e_cola": resultado_pix.get('pix_copia_e_cola')
                },
                data_criacao=datetime.now()
            )
            db.session.add(pagamento)
            db.session.commit()
            
            canal_pedidos = dados.get("config", {}).get("canal_pedidos")
            if canal_pedidos:
                executar_acao_bot(
                    "notificar_pedido",
                    canal_id=canal_pedidos,
                    mensagem="🆕 **Novo Pedido Criado!**",
                    embed={
                        "title": f"Pedido #{pedido.numero}",
                        "description": f"**Cliente:** {usuario.nome}\n**Serviço:** {servico.nome}\n**Valor:** {formatar_preco(servico.preco)}\n**Status:** Aguardando pagamento",
                        "color": "blue"
                    }
                )
            
            adicionar_log(f"Novo pedido criado: {pedido.numero} - {usuario.nome}", "pedido", usuario.id)
            flash(f'Pedido #{pedido.numero} criado com sucesso!', 'success')
            return redirect(url_for('pedido_detalhe', pedido_id=pedido.id))
        else:
            db.session.delete(pedido)
            db.session.commit()
            flash(f'Erro ao gerar pagamento: {resultado_pix.get("erro", "Erro desconhecido")}', 'danger')
            return redirect(url_for('servico_detalhe', servico_id=servico_id))

@app.route("/pedido/<int:pedido_id>")
def pedido_detalhe(pedido_id):
    if 'usuario' not in session:
        flash('Faça login para ver seus pedidos.', 'warning')
        return redirect(url_for('login'))
    
    with app.app_context():
        pedido = Pedido.query.filter(Pedido.id == pedido_id).first()
        if not pedido:
            flash('Pedido não encontrado.', 'danger')
            return redirect(url_for('meus_pedidos'))
        
        usuario = Usuario.query.filter(
            Usuario.discord_id == session['usuario']['id']
        ).first()
        if not usuario or (pedido.usuario_id != usuario.id and not session['usuario'].get('eh_admin')):
            flash('Você não tem permissão para ver este pedido.', 'danger')
            return redirect(url_for('meus_pedidos'))
        
        pagamento = Pagamento.query.filter(
            Pagamento.pedido_id == pedido.id
        ).first()
        servico = Servico.query.filter(Servico.id == pedido.servico_id).first()
    
    return render_template(
        'pedido_detalhe.html',
        pedido=pedido,
        pagamento=pagamento,
        servico=servico,
        usuario=session.get('usuario'),
        formatar_preco=formatar_preco
    )

@app.route("/meus-pedidos")
def meus_pedidos():
    if 'usuario' not in session:
        flash('Faça login para ver seus pedidos.', 'warning')
        return redirect(url_for('login'))
    
    with app.app_context():
        usuario = Usuario.query.filter(
            Usuario.discord_id == session['usuario']['id']
        ).first()
        if not usuario:
            flash('Usuário não encontrado.', 'danger')
            return redirect(url_for('home'))
        pedidos = Pedido.query.filter(
            Pedido.usuario_id == usuario.id
        ).order_by(Pedido.data_criacao.desc()).all()
    
    return render_template(
        'meus_pedidos.html',
        pedidos=pedidos,
        usuario=session.get('usuario'),
        formatar_preco=formatar_preco
    )

@app.route("/perfil")
def perfil():
    if 'usuario' not in session:
        flash('Faça login para ver seu perfil.', 'warning')
        return redirect(url_for('login'))
    
    with app.app_context():
        usuario = Usuario.query.filter(
            Usuario.discord_id == session['usuario']['id']
        ).first()
        if not usuario:
            usuario = Usuario(
                discord_id=session['usuario']['id'],
                nome=session['usuario']['nome_usuario'],
                avatar=session['usuario'].get('avatar'),
                data_cadastro=datetime.now()
            )
            db.session.add(usuario)
            db.session.commit()
        
        total_pedidos = Pedido.query.filter(Pedido.usuario_id == usuario.id).count()
        pedidos_concluidos = Pedido.query.filter(
            Pedido.usuario_id == usuario.id,
            Pedido.status == 'finalizado'
        ).count()
        total_gasto = db.session.query(db.func.sum(Pedido.valor)).filter(
            Pedido.usuario_id == usuario.id,
            Pedido.status == 'finalizado'
        ).scalar() or 0
        pontos = TransacaoPontos.query.filter(
            TransacaoPontos.usuario_id == usuario.id
        ).order_by(TransacaoPontos.data.desc()).limit(10).all()
        total_pontos = usuario.pontos or 0
    
    return render_template(
        'perfil.html',
        usuario=usuario,
        total_pedidos=total_pedidos,
        pedidos_concluidos=pedidos_concluidos,
        total_gasto=total_gasto,
        pontos=pontos,
        total_pontos=total_pontos,
        session_usuario=session.get('usuario'),
        formatar_preco=formatar_preco
    )

@app.route("/pontos")
def pontos():
    if 'usuario' not in session:
        flash('Faça login para ver seus pontos.', 'warning')
        return redirect(url_for('login'))
    
    with app.app_context():
        usuario = Usuario.query.filter(
            Usuario.discord_id == session['usuario']['id']
        ).first()
        if not usuario:
            flash('Usuário não encontrado.', 'danger')
            return redirect(url_for('home'))
        historico = TransacaoPontos.query.filter(
            TransacaoPontos.usuario_id == usuario.id
        ).order_by(TransacaoPontos.data.desc()).all()
        recompensas = Resgate.query.filter(Resgate.status == True).order_by(Resgate.pontos).all()
    
    return render_template(
        'pontos.html',
        usuario=usuario,
        historico=historico,
        recompensas=recompensas,
        session_usuario=session.get('usuario'),
        formatar_preco=formatar_preco
    )

@app.route("/resgatar", methods=['POST'])
def resgatar_pontos():
    if 'usuario' not in session:
        flash('Faça login para resgatar.', 'warning')
        return redirect(url_for('login'))
    
    recompensa_id = request.form.get('recompensa_id', type=int)
    if not recompensa_id:
        flash('Selecione uma recompensa.', 'danger')
        return redirect(url_for('pontos'))
    
    with app.app_context():
        usuario = Usuario.query.filter(
            Usuario.discord_id == session['usuario']['id']
        ).first()
        if not usuario:
            flash('Usuário não encontrado.', 'danger')
            return redirect(url_for('home'))
        recompensa = Resgate.query.filter(
            Resgate.id == recompensa_id,
            Resgate.status == True
        ).first()
        if not recompensa:
            flash('Recompensa não encontrada.', 'danger')
            return redirect(url_for('pontos'))
        if usuario.pontos < recompensa.pontos:
            flash(f'Você precisa de {recompensa.pontos} pontos.', 'danger')
            return redirect(url_for('pontos'))
        
        codigo_cupom = gerar_codigo_cupom()
        cupom = Cupom(
            codigo=codigo_cupom,
            tipo=recompensa.tipo,
            valor=recompensa.valor,
            validade=datetime.now() + timedelta(days=30),
            quantidade_maxima=1,
            quantidade_usada=0,
            status=True
        )
        db.session.add(cupom)
        
        transacao = TransacaoPontos(
            usuario_id=usuario.id,
            tipo='gasto',
            quantidade=-recompensa.pontos,
            descricao=f'Resgate: {recompensa.nome} - Cupom {codigo_cupom}',
            data=datetime.now()
        )
        db.session.add(transacao)
        usuario.pontos = (usuario.pontos or 0) - recompensa.pontos
        db.session.commit()
        
        flash(f'✅ Recompensa resgatada! Cupom: {codigo_cupom}', 'success')
        return redirect(url_for('pontos'))

@app.route("/fila")
def fila_publica():
    fila = obter_dados_fila()
    links = obter_links_fila()
    botoes_precos = links.get("botoes_precos", [])
    
    return render_template(
        'fila_publica.html',
        fila=fila,
        links=links,
        botoes_precos=botoes_precos,
        agora=agora_br()
    )

@app.route("/fila/embed")
def fila_embed():
    fila = obter_dados_fila()
    return render_template('fila_embed.html', fila=fila)

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

# ========================
# WEBHOOK PIX
# ========================

@app.route("/webhook/pix", methods=['POST'])
def webhook_pix():
    if PIX_WEBHOOK_SECRET:
        signature = request.headers.get('X-Signature')
        if signature:
            data = request.get_data()
            expected = hmac.new(
                PIX_WEBHOOK_SECRET.encode(),
                data,
                hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                abort(403, "Assinatura inválida")
    
    data = request.json
    if not data:
        return jsonify({"erro": "Dados inválidos"}), 400
    
    print(f"📨 Webhook PIX recebido: {data}")
    
    if data.get('type') == 'payment':
        payment_id = data.get('data', {}).get('id')
        if payment_id:
            return processar_pagamento_pix(payment_id)
    
    return jsonify({"status": "ok"}), 200

def processar_pagamento_pix(payment_id):
    resultado = verificar_pagamento_pix(payment_id)
    if not resultado.get('sucesso'):
        return jsonify({"erro": resultado.get('erro', 'Erro')}), 400
    
    status = resultado.get('status')
    if status == 'approved':
        with app.app_context():
            pagamento = Pagamento.query.filter(
                Pagamento.dados_pagamento['payment_id'].astext == str(payment_id)
            ).first()
            if not pagamento:
                return jsonify({"erro": "Pagamento não encontrado"}), 404
            if pagamento.status != 'pendente':
                return jsonify({"status": "já processado"}), 200
            
            pagamento.status = 'aprovado'
            pagamento.data_pagamento = datetime.now()
            
            pedido = Pedido.query.filter(Pedido.id == pagamento.pedido_id).first()
            if pedido:
                pedido.status = 'pago'
                usuario = Usuario.query.filter(Usuario.id == pedido.usuario_id).first()
                if usuario:
                    pontos_ganhos = int(float(pedido.valor) * PONTOS_POR_REAL)
                    usuario.pontos = (usuario.pontos or 0) + pontos_ganhos
                    transacao = TransacaoPontos(
                        usuario_id=usuario.id,
                        tipo='ganho',
                        quantidade=pontos_ganhos,
                        descricao=f'Compra: {pedido.numero}',
                        data=datetime.now()
                    )
                    db.session.add(transacao)
                    adicionar_log(
                        f"Pagamento aprovado: {pedido.numero} - {usuario.nome} ganhou {pontos_ganhos} pontos",
                        "pagamento",
                        usuario.id
                    )
                if pedido.servico:
                    nome_servico = pedido.servico.nome
                    nome_usuario = usuario.nome if usuario else "Cliente"
                    adicionar_fila(nome_usuario, nome_servico, "", str(usuario.discord_id) if usuario else None)
            
            db.session.commit()
            
            canal_pedidos = dados.get("config", {}).get("canal_pedidos")
            if canal_pedidos and pedido:
                executar_acao_bot(
                    "notificar_pedido",
                    canal_id=canal_pedidos,
                    mensagem="✅ **Pagamento Confirmado!**",
                    embed={
                        "title": f"Pedido #{pedido.numero} - PAGO",
                        "description": f"**Cliente:** {usuario.nome if usuario else 'Desconhecido'}\n**Serviço:** {pedido.servico.nome if pedido.servico else 'Serviço'}\n**Valor:** {formatar_preco(pedido.valor)}",
                        "color": "green"
                    }
                )
    
    return jsonify({"status": "ok"}), 200

# ========================
# ROTAS ADMINISTRATIVAS (SIMPLIFICADAS PARA COMPATIBILIDADE)
# ========================

@app.route("/admin")
def admin_dashboard():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        flash('Acesso restrito a administradores.', 'danger')
        return redirect(url_for('home'))
    
    with app.app_context():
        total_clientes = Usuario.query.count()
        total_servicos = Servico.query.filter(Servico.status == True).count()
        pedidos_hoje = Pedido.query.filter(
            Pedido.data_criacao >= datetime.now().replace(hour=0, minute=0, second=0)
        ).count()
        pedidos_pendentes = Pedido.query.filter(Pedido.status == 'aguardando_pagamento').count()
        pedidos_pagos = Pedido.query.filter(Pedido.status == 'pago').count()
        pedidos_finalizados = Pedido.query.filter(Pedido.status == 'finalizado').count()
        total_vendido = db.session.query(db.func.sum(Pedido.valor)).filter(
            Pedido.status == 'finalizado'
        ).scalar() or 0
        pedidos_recentes = Pedido.query.order_by(Pedido.data_criacao.desc()).limit(10).all()
        
        pontos_distribuidos = db.session.query(db.func.sum(TransacaoPontos.quantidade)).filter(
            TransacaoPontos.tipo == 'ganho'
        ).scalar() or 0
    
    return render_template(
        'admin/dashboard.html',
        total_clientes=total_clientes,
        total_servicos=total_servicos,
        pedidos_hoje=pedidos_hoje,
        pedidos_pendentes=pedidos_pendentes,
        pedidos_pagos=pedidos_pagos,
        pedidos_finalizados=pedidos_finalizados,
        total_vendido=total_vendido,
        pedidos_recentes=pedidos_recentes,
        pontos_distribuidos=pontos_distribuidos,
        usuario=session.get('usuario'),
        formatar_preco=formatar_preco,
        bot=bot
    )

@app.route("/admin/servicos")
def admin_servicos():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        flash('Acesso restrito a administradores.', 'danger')
        return redirect(url_for('home'))
    
    with app.app_context():
        servicos = Servico.query.order_by(Servico.ordem).all()
        categorias = Categoria.query.all()
    
    return render_template(
        'admin/servicos.html',
        servicos=servicos,
        categorias=categorias,
        usuario=session.get('usuario'),
        formatar_preco=formatar_preco
    )

@app.route("/admin/servicos/criar", methods=['POST'])
def admin_servicos_criar():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        flash('Acesso restrito a administradores.', 'danger')
        return redirect(url_for('home'))
    
    nome = request.form.get('nome', '').strip()
    categoria_id = request.form.get('categoria_id', type=int)
    descricao = request.form.get('descricao', '').strip()
    preco = request.form.get('preco', type=float)
    imagem = request.form.get('imagem', '').strip()
    status = request.form.get('status') == 'on'
    destaque = request.form.get('destaque') == 'on'
    tempo_estimado = request.form.get('tempo_estimado', '').strip()
    ordem = request.form.get('ordem', type=int) or 0
    
    if not nome or not preco:
        flash('Nome e preço são obrigatórios.', 'danger')
        return redirect(url_for('admin_servicos'))
    
    with app.app_context():
        servico = Servico(
            nome=nome,
            categoria_id=categoria_id,
            descricao=descricao,
            preco=preco,
            imagem=imagem,
            status=status,
            destaque=destaque,
            tempo_estimado=tempo_estimado,
            ordem=ordem
        )
        db.session.add(servico)
        db.session.commit()
        flash(f'Serviço "{nome}" criado com sucesso!', 'success')
    
    return redirect(url_for('admin_servicos'))

@app.route("/admin/servicos/<int:servico_id>/editar", methods=['POST'])
def admin_servicos_editar(servico_id):
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        flash('Acesso restrito a administradores.', 'danger')
        return redirect(url_for('home'))
    
    with app.app_context():
        servico = Servico.query.filter(Servico.id == servico_id).first()
        if not servico:
            flash('Serviço não encontrado.', 'danger')
            return redirect(url_for('admin_servicos'))
        servico.nome = request.form.get('nome', '').strip()
        servico.categoria_id = request.form.get('categoria_id', type=int)
        servico.descricao = request.form.get('descricao', '').strip()
        servico.preco = request.form.get('preco', type=float)
        servico.imagem = request.form.get('imagem', '').strip()
        servico.status = request.form.get('status') == 'on'
        servico.destaque = request.form.get('destaque') == 'on'
        servico.tempo_estimado = request.form.get('tempo_estimado', '').strip()
        servico.ordem = request.form.get('ordem', type=int) or 0
        db.session.commit()
        flash(f'Serviço "{servico.nome}" atualizado!', 'success')
    
    return redirect(url_for('admin_servicos'))

@app.route("/admin/servicos/<int:servico_id>/excluir", methods=['POST'])
def admin_servicos_excluir(servico_id):
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        flash('Acesso restrito a administradores.', 'danger')
        return redirect(url_for('home'))
    
    with app.app_context():
        servico = Servico.query.filter(Servico.id == servico_id).first()
        if not servico:
            flash('Serviço não encontrado.', 'danger')
            return redirect(url_for('admin_servicos'))
        nome = servico.nome
        db.session.delete(servico)
        db.session.commit()
        flash(f'Serviço "{nome}" excluído!', 'success')
    
    return redirect(url_for('admin_servicos'))

@app.route("/admin/pedidos")
def admin_pedidos():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        flash('Acesso restrito a administradores.', 'danger')
        return redirect(url_for('home'))
    
    status_filtro = request.args.get('status', '')
    with app.app_context():
        query = Pedido.query
        if status_filtro:
            query = query.filter(Pedido.status == status_filtro)
        pedidos = query.order_by(Pedido.data_criacao.desc()).all()
    
    return render_template(
        'admin/pedidos.html',
        pedidos=pedidos,
        status_filtro=status_filtro,
        usuario=session.get('usuario'),
        formatar_preco=formatar_preco
    )

@app.route("/admin/pedidos/<int:pedido_id>/status", methods=['POST'])
def admin_pedidos_status(pedido_id):
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        flash('Acesso restrito a administradores.', 'danger')
        return redirect(url_for('home'))
    
    novo_status = request.form.get('status', '')
    observacao = request.form.get('observacao', '').strip()
    if not novo_status:
        flash('Status não informado.', 'danger')
        return redirect(url_for('admin_pedidos'))
    
    with app.app_context():
        pedido = Pedido.query.filter(Pedido.id == pedido_id).first()
        if not pedido:
            flash('Pedido não encontrado.', 'danger')
            return redirect(url_for('admin_pedidos'))
        pedido.status = novo_status
        if observacao:
            if pedido.historico is None:
                pedido.historico = []
            pedido.historico.append({
                "data": datetime.now().isoformat(),
                "status": novo_status,
                "observacao": observacao,
                "admin": session['usuario']['nome_usuario']
            })
        db.session.commit()
        flash(f'Pedido {pedido.numero} atualizado para "{novo_status}"!', 'success')
    
    return redirect(url_for('admin_pedidos'))

@app.route("/admin/clientes")
def admin_clientes():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        flash('Acesso restrito a administradores.', 'danger')
        return redirect(url_for('home'))
    
    with app.app_context():
        clientes = Usuario.query.order_by(Usuario.pontos.desc()).all()
    
    return render_template(
        'admin/clientes.html',
        clientes=clientes,
        usuario=session.get('usuario')
    )

@app.route("/admin/configuracoes")
def admin_configuracoes():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        flash('Acesso restrito a administradores.', 'danger')
        return redirect(url_for('home'))
    
    return render_template(
        'admin/configuracoes.html',
        usuario=session.get('usuario'),
        dados=dados
    )

@app.route("/admin/configuracoes/salvar", methods=['POST'])
def admin_configuracoes_salvar():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        flash('Acesso restrito a administradores.', 'danger')
        return redirect(url_for('home'))
    
    canal_pedidos = request.form.get('canal_pedidos', '').strip()
    dados["config"]["canal_pedidos"] = canal_pedidos if canal_pedidos else None
    salvar_dados_github("Configurações atualizadas")
    flash('Configurações salvas com sucesso!', 'success')
    return redirect(url_for('admin_configuracoes'))

@app.route("/admin/logs")
def admin_logs():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        flash('Acesso restrito a administradores.', 'danger')
        return redirect(url_for('home'))
    
    with app.app_context():
        logs = Log.query.order_by(Log.data.desc()).limit(200).all()
    
    return render_template(
        'admin/logs.html',
        logs=logs,
        usuario=session.get('usuario')
    )

# ========================
# AUTENTICAÇÃO
# ========================

@app.route("/login")
def login():
    if not CLIENT_ID or not CLIENT_SECRET:
        return "Erro: CLIENT_ID ou CLIENT_SECRET não configurados.", 500
    url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds"
    return redirect(url)

@app.route("/callback")
def callback():
    if not CLIENT_ID or not CLIENT_SECRET:
        return "Erro de configuração.", 500
    code = request.args.get('code')
    if not code:
        return "Erro: código não recebido", 400
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
            return f"Erro ao obter token: {r.text[:100]}", 400
        access_token = r.json()['access_token']
        user_r = requests.get('https://discord.com/api/users/@me', headers={'Authorization': f'Bearer {access_token}'})
        if user_r.status_code != 200:
            return "Erro ao obter informações", 400
        user_data = user_r.json()
        
        eh_admin = False
        if GUILD_ID:
            guilds_r = requests.get('https://discord.com/api/users/@me/guilds', headers={'Authorization': f'Bearer {access_token}'})
            if guilds_r.status_code == 200:
                guilds = guilds_r.json()
                for guild in guilds:
                    if str(guild['id']) == GUILD_ID and (guild.get('permissions', 0) & 0x8):
                        eh_admin = True
                        break
        
        with app.app_context():
            usuario = Usuario.query.filter(Usuario.discord_id == user_data['id']).first()
            if not usuario:
                usuario = Usuario(
                    discord_id=user_data['id'],
                    nome=user_data['username'],
                    avatar=user_data.get('avatar'),
                    data_cadastro=datetime.now(),
                    pontos=0
                )
                db.session.add(usuario)
                db.session.commit()
            else:
                usuario.nome = user_data['username']
                usuario.avatar = user_data.get('avatar')
                db.session.commit()
        
        session['usuario'] = {
            'id': user_data['id'],
            'nome_usuario': user_data['username'],
            'avatar': user_data.get('avatar'),
            'eh_admin': eh_admin
        }
        
        if eh_admin:
            return redirect(url_for('admin_dashboard'))
        else:
            return redirect(url_for('home'))
    except Exception as e:
        return f"Erro interno: {str(e)}", 500

@app.route("/logout")
def logout():
    session.clear()
    flash('Você foi desconectado.', 'info')
    return redirect(url_for('home'))

# ========================
# COMANDOS DO BOT
# ========================

@tree.command(name="perfil", description="Mostra o seu perfil com XP e nível")
@app_commands.describe(membro="Membro para ver o perfil (opcional)")
async def slash_perfil(interaction: discord.Interaction, membro: discord.Member = None):
    await interaction.response.defer(thinking=True)
    alvo = membro or interaction.user
    uid = str(alvo.id)
    xp = dados.get("xp", {}).get(uid, 0)
    nivel = dados.get("nivel", {}).get(uid, int((xp / 100) ** 0.6) + 1)
    ranking = sorted(dados.get("xp", {}).items(), key=lambda t: t[1], reverse=True)
    pos = next((i+1 for i, (u, _) in enumerate(ranking) if u == uid), len(ranking))
    
    largura, altura = 900, 200
    img = Image.new("RGBA", (largura, altura), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    
    try:
        font_b = ImageFont.truetype("DejaVuSans-Bold.ttf", 32)
        font_s = ImageFont.truetype("DejaVuSans.ttf", 22)
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
    text_x = x0 + (barra_total_w - (bbox[2] - bbox[0])) // 2
    text_y = y0 + (barra_h - (bbox[3] - bbox[1])) // 2
    draw.text((text_x, text_y), texto_xp, font=font_s, fill=(255, 255, 255))
    
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    arquivo = discord.File(buf, filename="perfil.png")
    await interaction.followup.send(file=arquivo)

@tree.command(name="rank", description="Mostra o ranking dos 10 maiores XP")
async def slash_rank(interaction: discord.Interaction):
    await interaction.response.defer()
    ranking = sorted(dados.get("xp", {}).items(), key=lambda t: t[1], reverse=True)[:10]
    linhas = []
    for i, (uid, xp) in enumerate(ranking, 1):
        user = interaction.guild.get_member(int(uid))
        nome = user.display_name if user else f"Usuário {uid}"
        nivel = dados.get("nivel", {}).get(uid, int((xp / 100) ** 0.6) + 1)
        linhas.append(f"{i}. **{nome}** — {xp} XP (Nível {nivel})")
    texto = "\n".join(linhas) if linhas else "Sem dados ainda."
    embed = discord.Embed(title="🏆 Top 10 Ranking de XP", description=texto, color=discord.Color.gold())
    await interaction.followup.send(embed=embed)

@tree.command(name="servicos", description="Mostra os serviços disponíveis")
async def slash_servicos(interaction: discord.Interaction):
    await interaction.response.defer()
    with app.app_context():
        servicos = Servico.query.filter(
            Servico.status == True,
            Servico.destaque == True
        ).order_by(Servico.ordem).limit(5).all()
    
    if not servicos:
        await interaction.followup.send("❌ Nenhum serviço disponível no momento.")
        return
    
    embed = discord.Embed(
        title="🎮 Serviços Disponíveis",
        description="Confira nossos serviços em destaque:",
        color=discord.Color.blue()
    )
    for servico in servicos:
        embed.add_field(
            name=servico.nome,
            value=f"{servico.descricao[:100] if servico.descricao else ''}...\n💰 {formatar_preco(servico.preco)}",
            inline=False
        )
    embed.set_footer(text=f"Visite o site para mais serviços")
    await interaction.followup.send(embed=embed)

# ========================
# EVENTOS DO BOT
# ========================

@bot.event
async def on_ready():
    print(f"\n{'='*50}")
    print(f"🤖 BOT INICIADO: {bot.user}")
    print(f"{'='*50}")
    
    with app.app_context():
        db.create_all()
    
    carregar_dados_github()
    
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
    print(f"✨ BOT PRONTO! Comandos: /perfil, /rank, /servicos")
    print(f"📢 Canal de pedidos: {config.get('canal_pedidos') or 'NÃO CONFIGURADO'}")
    print(f"{'='*50}\n")

@bot.event
async def on_member_join(member: discord.Member):
    ch_id = dados.get("config", {}).get("canal_boas_vindas")
    canal = None
    if ch_id:
        canal = member.guild.get_channel(int(ch_id))
    if not canal:
        canal = discord.utils.get(member.guild.text_channels, name="boas-vindas")
    if not canal:
        return
    msg = dados.get("config", {}).get("mensagem_boas_vindas", "Olá {member}, seja bem-vindo(a)!")
    msg = msg.replace("{member}", member.mention)
    await canal.send(msg)

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    conteudo = message.content.strip()
    
    if verificar_comando_ignorado(conteudo):
        await bot.process_commands(message)
        return
    
    # XP System
    dados.setdefault("xp", {})
    dados.setdefault("nivel", {})
    taxa_xp = dados.get("config", {}).get("taxa_xp", 3)
    ganho_xp = max(1, 15 // taxa_xp)
    dados["xp"][str(message.author.id)] = dados["xp"].get(str(message.author.id), 0) + ganho_xp
    xp_atual = dados["xp"][str(message.author.id)]
    nivel_atual = int((xp_atual / 100) ** 0.6) + 1
    nivel_anterior = dados["nivel"].get(str(message.author.id), 1)
    if nivel_atual > nivel_anterior:
        dados["nivel"][str(message.author.id)] = nivel_atual
        canal_levelup_id = dados.get("config", {}).get("canal_levelup")
        if canal_levelup_id:
            canal = message.guild.get_channel(int(canal_levelup_id))
            if canal:
                await canal.send(f"🎉 {message.author.mention} subiu para o nível **{nivel_atual}**!")
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
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

Thread(target=run_flask, daemon=True).start()

if __name__ == "__main__":
    try:
        bot.run(BOT_TOKEN)
    except Exception as e:
        print("Erro ao iniciar o bot:", e)