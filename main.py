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

# Importar modelos e database
from database import db, init_db, get_db
from models import (
    Usuario, Servico, Categoria, Pedido, Pagamento, 
    TransacaoPontos, Resgate, Cupom, Log, Configuracao,
    CategoriaSchema, ServicoSchema, PedidoSchema, UsuarioSchema,
    PagamentoSchema, ResgateSchema, CupomSchema
)

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

# Configurações do banco de dados
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://usuario:senha@localhost:5432/meu_bot")

if not BOT_TOKEN or not GITHUB_TOKEN:
    raise SystemExit("Defina BOT_TOKEN e GITHUB_TOKEN nas variáveis de ambiente.")

GITHUB_API_CONTENT = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{DATA_FILE}"

# ========================
# FLASK APP
# ========================
app = Flask(__name__, template_folder='templates')
app.secret_key = SECRET_KEY

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
# ESTRUTURA DE DADOS (LEGADO - Mantido para compatibilidade)
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
    }
}

# Dicionário para armazenar mensagens recentes dos usuários
mensagens_recentes = {}

# Fila de ações do site
acoes_fila_bot = []
processador_acoes_task = None
processador_acoes_rodando = False

# ========================
# FUNÇÕES DE SEGURANÇA
# ========================

def csrf_protect(f):
    """Decorator para proteção CSRF"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method in ['POST', 'PUT', 'DELETE']:
            token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
            if not token or token != session.get('csrf_token'):
                abort(403, "CSRF token inválido")
        return f(*args, **kwargs)
    return decorated_function

def rate_limit(limit=60, window=60):
    """Decorator para rate limiting"""
    from collections import defaultdict
    import time
    requests = defaultdict(list)
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        ip = request.remote_addr
        now = time.time()
        requests[ip] = [t for t in requests[ip] if now - t < window]
        if len(requests[ip]) >= limit:
            abort(429, "Muitas requisições. Tente novamente mais tarde.")
        requests[ip].append(now)
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Decorator para verificar se o usuário é administrador"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('usuario') or not session['usuario'].get('eh_admin'):
            flash('Acesso restrito a administradores.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def login_required(f):
    """Decorator para verificar se o usuário está logado"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('usuario'):
            flash('Por favor, faça login para acessar esta página.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def gerar_csrf_token():
    """Gera um token CSRF"""
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']

# ========================
# FUNÇÕES UTILITÁRIAS
# ========================

def agora_br():
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-3)))

def _gh_headers():
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

def carregar_dados_github():
    """Carrega dados do GitHub para compatibilidade com funcionalidades legadas"""
    try:
        r = requests.get(GITHUB_API_CONTENT, headers=_gh_headers(), params={"ref": BRANCH}, timeout=15)
        if r.status_code == 200:
            js = r.json()
            conteudo_b64 = js.get("content", "")
            if conteudo_b64:
                raw = base64.b64decode(conteudo_b64)
                carregado = json.loads(raw.decode("utf-8"))
                dados.update(carregado)
                # Garantir que estruturas existam
                for key in ["fila", "botoes_cargos", "cargos_nivel", "canais_links_bloqueados", 
                           "links_fila", "anti_spam", "config", "reacoes_cargos"]:
                    if key not in dados:
                        dados[key] = {}
                if "botoes_precos" not in dados.get("links_fila", {}):
                    dados["links_fila"]["botoes_precos"] = []
                print("✅ Dados carregados do GitHub.")
                return True
        else:
            print(f"⚠️ GitHub GET retornou {r.status_code} — iniciando com dados limpos.")
    except Exception as e:
        print(f"❌ Erro ao carregar dados do GitHub: {e}")
    return False

def salvar_dados_github(mensagem="Atualização do bot"):
    """Salva dados no GitHub para compatibilidade com funcionalidades legadas"""
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

def adicionar_log(entrada, tipo="info", usuario_id=None):
    """Adiciona um log no banco de dados"""
    try:
        with get_db() as db_session:
            log = Log(
                tipo=tipo,
                mensagem=entrada,
                usuario_id=usuario_id,
                ip=request.remote_addr if request else None
            )
            db_session.add(log)
            db_session.commit()
    except Exception as e:
        print(f"Erro ao adicionar log: {e}")
        # Fallback para o sistema antigo
        ts = agora_br().isoformat()
        dados.setdefault("logs", []).append({"ts": ts, "entrada": entrada})
        try:
            salvar_dados_github(f"log: {entrada}")
        except Exception:
            pass

def xp_por_mensagem():
    return 15

def xp_para_nivel(xp):
    nivel = int((xp / 100) ** 0.6) + 1
    return max(nivel, 1)

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

def formatar_preco(valor):
    """Formata um valor para moeda brasileira"""
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def gerar_numero_pedido():
    """Gera um número único para pedido"""
    return f"PED-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

def gerar_codigo_cupom():
    """Gera um código aleatório para cupom"""
    return f"CP-{secrets.token_hex(4).upper()}"

# ========================
# FUNÇÕES ANTI-SPAM E IGNORADOS
# ========================

def verificar_comando_ignorado(conteudo: str) -> bool:
    """Verifica se a mensagem é um comando ignorado"""
    conteudo_lower = conteudo.lower().strip()
    comandos_ignorados = dados.get("anti_spam", {}).get("comandos_ignorados", [])
    
    for comando in comandos_ignorados:
        if conteudo_lower.startswith(comando.lower()):
            return True
        if conteudo_lower == comando.lower():
            return True
    
    return False

def verificar_cargo_ignorado(member: discord.Member) -> bool:
    """Verifica se o membro tem cargo que ignora o anti-spam"""
    cargos_ignorados = dados.get("anti_spam", {}).get("cargos_ignorados", [])
    cargos_membro = [role.name for role in member.roles]
    for cargo_ignorado in cargos_ignorados:
        if cargo_ignorado in cargos_membro:
            return True
    return False

def limpar_mensagens_antigas(user_id: int):
    """Remove mensagens mais antigas que o intervalo configurado"""
    if user_id not in mensagens_recentes:
        return
    
    intervalo = dados.get("anti_spam", {}).get("intervalo_segundos", 5)
    agora = time.time()
    mensagens_recentes[user_id] = [
        ts for ts in mensagens_recentes[user_id] 
        if agora - ts < intervalo
    ]
    
    if not mensagens_recentes[user_id]:
        del mensagens_recentes[user_id]

def registrar_mensagem(user_id: int) -> int:
    """Registra uma mensagem e retorna quantas mensagens o usuário enviou no intervalo"""
    agora = time.time()
    
    if user_id not in mensagens_recentes:
        mensagens_recentes[user_id] = []
    
    mensagens_recentes[user_id].append(agora)
    limpar_mensagens_antigas(user_id)
    
    return len(mensagens_recentes.get(user_id, []))

async def aplicar_mute(member: discord.Member, duracao_minutos: int = 2):
    """Aplica mute temporário no membro"""
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
            print(f"✅ Cargo 'Muted' criado no servidor {guild.name}")
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
    """Deleta as mensagens de spam do usuário"""
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
    """Remove XP do usuário por spam"""
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

# ========================
# SISTEMA DE FILA (LEGADO)
# ========================

def obter_dados_fila():
    dados.setdefault("fila", {
        "nome": "Fila de Serviços",
        "configuracoes": {"tamanho_maximo": 50, "aberta": True},
        "entradas": [],
        "historico": []
    })
    return dados["fila"]

def salvar_fila():
    return salvar_dados_github("Atualização da fila")

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
    salvar_fila()
    adicionar_log(f"fila_adicionar: {nome_usuario} - {servico} - {jogo}")
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
            salvar_fila()
            adicionar_log(f"fila_remover: {removido['nome_usuario']}")
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
            salvar_fila()
            return True, entrada
    return False, None

def mover_baixo(entrada_id: str):
    fila = obter_dados_fila()
    entradas = fila["entradas"]
    for i, entrada in enumerate(entradas):
        if entrada["id"] == entrada_id and i < len(entradas) - 1:
            entradas[i], entradas[i+1] = entradas[i+1], entradas[i]
            atualizar_posicoes(entradas)
            salvar_fila()
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
            salvar_fila()
            adicionar_log(f"fila_concluir: {removido['nome_usuario']}")
            return True, removido
    return False, None

def limpar_fila():
    fila = obter_dados_fila()
    for entrada in fila["entradas"]:
        entrada["status"] = "limpo"
        entrada["limpo_em"] = agora_br().isoformat()
        fila["historico"].append(entrada)
    fila["entradas"] = []
    salvar_fila()
    adicionar_log("fila_limpa")
    return True

def alternar_fila(aberto: bool = None):
    fila = obter_dados_fila()
    if aberto is None:
        fila["configuracoes"]["aberta"] = not fila["configuracoes"]["aberta"]
    else:
        fila["configuracoes"]["aberta"] = aberto
    salvar_fila()
    return fila["configuracoes"]["aberta"]

def definir_tamanho_maximo(tamanho: int):
    fila = obter_dados_fila()
    fila["configuracoes"]["tamanho_maximo"] = max(1, min(tamanho, 100))
    salvar_fila()
    return fila["configuracoes"]["tamanho_maximo"]

def definir_nome_fila(nome: str):
    fila = obter_dados_fila()
    fila["nome"] = nome[:50]
    salvar_fila()
    return fila["nome"]

# ========================
# FUNÇÕES PARA LINKS DA FILA
# ========================
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
# SISTEMA DE PAGAMENTO PIX (Mercado Pago)
# ========================

def criar_pagamento_pix(servico_nome, valor, usuario_id, pedido_id):
    """Cria um pagamento PIX via Mercado Pago"""
    if not MERCADO_PAGO_ACCESS_TOKEN:
        return {"erro": "API de pagamento não configurada"}
    
    try:
        url = "https://api.mercadopago.com/v1/payments"
        headers = {
            "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        # Gerar ID externo único
        external_id = f"pedido_{pedido_id}_{int(time.time())}"
        
        payload = {
            "transaction_amount": float(valor),
            "description": f"{servico_nome} - Pedido #{pedido_id}",
            "payment_method_id": "pix",
            "payer": {
                "email": f"cliente_{usuario_id}@bot.com",
                "identification": {
                    "type": "CPF",
                    "number": "12345678909"  # Isso deve ser dinâmico em produção
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
    """Verifica o status de um pagamento PIX"""
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
# SISTEMA DE AÇÕES DO SITE
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
        
        elif tipo_acao == "criar_reacao_cargo":
            canal_id = int(dados_acao["canal_id"])
            canal = guild.get_channel(canal_id)
            if not canal:
                return False
            
            mensagem = await canal.send(dados_acao["conteudo"])
            mensagem_id = str(mensagem.id)
            
            pares_str = dados_acao.get("emoji_cargo", "")
            pares = []
            par_atual = ""
            contador_chaves = 0
            
            for char in pares_str:
                if char == '<':
                    contador_chaves += 1
                elif char == '>':
                    contador_chaves -= 1
                if char == ',' and contador_chaves == 0:
                    if par_atual.strip():
                        pares.append(par_atual.strip())
                        par_atual = ""
                else:
                    par_atual += char
            if par_atual.strip():
                pares.append(par_atual.strip())
            
            EMOJI_RE = re.compile(r"<a?:([a-zA-Z0-9_]+):([0-9]+)>")
            EMOJI_NOME_RE = re.compile(r":([a-zA-Z0-9_]+):")
            
            def processar_emoji_str(emoji_str, guild):
                if not emoji_str:
                    return None
                emoji_str = emoji_str.strip()
                m = EMOJI_RE.match(emoji_str)
                if m:
                    nome, id_str = m.groups()
                    try:
                        eid = int(id_str)
                        animado = emoji_str.startswith('<a:')
                        if guild:
                            e = discord.utils.get(guild.emojis, id=eid)
                            if e:
                                return e
                        return discord.PartialEmoji(name=nome, id=eid, animated=animado)
                    except:
                        pass
                m2 = EMOJI_NOME_RE.match(emoji_str)
                if m2:
                    nome_emoji = m2.group(1)
                    if guild:
                        emoji = discord.utils.get(guild.emojis, name=nome_emoji)
                        if emoji:
                            return emoji
                    emojis_padrao = {
                        "thumbsup": "👍", "thumbsdown": "👎", "check": "✅", "x": "❌",
                        "warning": "⚠️", "exclamation": "❗", "question": "❓", "star": "⭐",
                        "heart": "❤️", "fire": "🔥", "rocket": "🚀", "tada": "🎉"
                    }
                    if nome_emoji.lower() in emojis_padrao:
                        return emojis_padrao[nome_emoji.lower()]
                    return emoji_str
                return emoji_str
            
            dados_reacoes = {}
            for par in pares:
                par = par.strip()
                if not par:
                    continue
                if ":" in par:
                    try:
                        emoji_str, nome_cargo = par.split(":", 1)
                        cargo = discord.utils.get(guild.roles, name=nome_cargo.strip())
                        if not cargo:
                            continue
                        emoji_processado = processar_emoji_str(emoji_str.strip(), guild)
                        if not emoji_processado:
                            continue
                        if isinstance(emoji_processado, (discord.Emoji, discord.PartialEmoji)):
                            await mensagem.add_reaction(emoji_processado)
                            chave = str(emoji_processado.id)
                        else:
                            await mensagem.add_reaction(emoji_processado)
                            chave = str(emoji_processado)
                        dados_reacoes[chave] = str(cargo.id)
                    except:
                        continue
            
            if dados_reacoes:
                dados.setdefault("reacoes_cargos", {})[mensagem_id] = dados_reacoes
                salvar_dados_github("Reação cargo via site")
                return True
            else:
                try:
                    await mensagem.delete()
                except:
                    pass
                return False
        
        elif tipo_acao == "criar_botoes_cargo":
            canal_id = int(dados_acao["canal_id"])
            canal = guild.get_channel(canal_id)
            if not canal:
                return False
            
            pares = dados_acao.get("cargos", "").split(",")
            dicionario_botoes = {}
            for par in pares:
                if ":" in par:
                    try:
                        nome_botao, nome_cargo = par.split(":", 1)
                        cargo = discord.utils.get(guild.roles, name=nome_cargo.strip())
                        if cargo:
                            dicionario_botoes[nome_botao.strip()] = cargo.id
                    except:
                        pass
            
            if dicionario_botoes:
                class PersistentRoleButton(ui.Button):
                    def __init__(self, label: str, cargo_id: int, mensagem_id: int):
                        super().__init__(label=label, style=ButtonStyle.primary)
                        self.cargo_id = cargo_id
                        self.mensagem_id = mensagem_id
                    async def callback(self, interaction: Interaction):
                        guild = interaction.guild
                        membro = interaction.user
                        cargo = guild.get_role(self.cargo_id)
                        if not cargo:
                            await interaction.response.send_message("Cargo não encontrado.", ephemeral=True)
                            return
                        if cargo in membro.roles:
                            await membro.remove_roles(cargo, reason="Botão de cargo")
                            await interaction.response.send_message(f"Você **removeu** o cargo {cargo.mention}.", ephemeral=True)
                        else:
                            await membro.add_roles(cargo, reason="Botão de cargo")
                            await interaction.response.send_message(f"Você **recebeu** o cargo {cargo.mention}.", ephemeral=True)
                        adicionar_log(f"botao_cargo: usuario={membro.id} cargo={cargo.id}")
                
                class PersistentRoleButtonView(ui.View):
                    def __init__(self, mensagem_id: int, dicionario_botoes: dict):
                        super().__init__(timeout=None)
                        self.mensagem_id = mensagem_id
                        for label, cargo_id in dicionario_botoes.items():
                            self.add_item(PersistentRoleButton(label=label, cargo_id=cargo_id, mensagem_id=mensagem_id))
                
                view = PersistentRoleButtonView(0, dicionario_botoes)
                enviado = await canal.send(dados_acao["conteudo"], view=view)
                view.mensagem_id = enviado.id
                for item in view.children:
                    if isinstance(item, PersistentRoleButton):
                        item.mensagem_id = enviado.id
                dados.setdefault("botoes_cargos", {})[str(enviado.id)] = dicionario_botoes
                salvar_dados_github("Botões de cargo via site")
                return True
            return False
        
        elif tipo_acao == "advertir_membro":
            membro_id = int(dados_acao["membro_id"])
            membro = guild.get_member(membro_id)
            if not membro:
                return False
            
            entrada = {
                "por": "admin_site",
                "motivo": dados_acao["motivo"],
                "ts": agora_br().strftime("%d/%m/%Y %H:%M"),
                "admin": dados_acao.get('admin', 'Admin')
            }
            dados.setdefault("advertencias", {}).setdefault(str(membro.id), []).append(entrada)
            salvar_dados_github(f"Advertência via site: {membro.display_name}")
            return True
        
        elif tipo_acao == "configurar_boas_vindas":
            config = dados.setdefault("config", {})
            if 'canal_id' in dados_acao:
                config["canal_boas_vindas"] = dados_acao['canal_id']
            if 'mensagem' in dados_acao:
                config["mensagem_boas_vindas"] = dados_acao['mensagem']
            if 'imagem_url' in dados_acao:
                config["fundo_boas_vindas"] = dados_acao['imagem_url']
            salvar_dados_github("Config boas-vindas atualizada")
            return True
        
        elif tipo_acao == "configurar_xp":
            config = dados.setdefault("config", {})
            if 'taxa' in dados_acao:
                config["taxa_xp"] = dados_acao['taxa']
            if 'canal_id' in dados_acao:
                config["canal_levelup"] = dados_acao['canal_id']
            salvar_dados_github("Config XP atualizada")
            return True
        
        elif tipo_acao == "configurar_comandos":
            config = dados.setdefault("config", {})
            if 'canal_perfil' in dados_acao:
                canal_perfil_atual = config.get("canal_perfil")
                novo_canal_perfil = dados_acao['canal_perfil']
                if novo_canal_perfil and canal_perfil_atual == novo_canal_perfil:
                    config["canal_perfil"] = None
                else:
                    config["canal_perfil"] = novo_canal_perfil if novo_canal_perfil else None
            
            if 'canal_rank' in dados_acao:
                canal_rank_atual = config.get("canal_rank")
                novo_canal_rank = dados_acao['canal_rank']
                if novo_canal_rank and canal_rank_atual == novo_canal_rank:
                    config["canal_rank"] = None
                else:
                    config["canal_rank"] = novo_canal_rank if novo_canal_rank else None
            
            salvar_dados_github("Config canais de comandos atualizada")
            return True
        
        elif tipo_acao == "adicionar_cargo_nivel":
            dados.setdefault("cargos_nivel", {})[str(dados_acao['nivel'])] = dados_acao['cargo_id']
            salvar_dados_github(f"Cargo para nível {dados_acao['nivel']} adicionado")
            return True
        
        elif tipo_acao == "remover_cargo_nivel":
            nivel = str(dados_acao['nivel'])
            if nivel in dados.get("cargos_nivel", {}):
                del dados["cargos_nivel"][nivel]
                salvar_dados_github(f"Cargo do nível {nivel} removido")
            return True
        
        elif tipo_acao == "alternar_bloqueio_links":
            canal_id = int(dados_acao["canal_id"])
            canais = dados.setdefault("canais_links_bloqueados", [])
            if canal_id in canais:
                canais.remove(canal_id)
            else:
                canais.append(canal_id)
            salvar_dados_github(f"Bloqueio de links alternado no canal {canal_id}")
            return True
        
        elif tipo_acao == "configurar_anti_spam":
            anti_spam = dados.setdefault("anti_spam", {})
            if 'ativado' in dados_acao:
                anti_spam["ativado"] = dados_acao['ativado']
            if 'limite_mensagens' in dados_acao:
                anti_spam["limite_mensagens"] = dados_acao['limite_mensagens']
            if 'intervalo_segundos' in dados_acao:
                anti_spam["intervalo_segundos"] = dados_acao['intervalo_segundos']
            if 'tempo_mute_minutos' in dados_acao:
                anti_spam["tempo_mute_minutos"] = dados_acao['tempo_mute_minutos']
            if 'remover_xp' in dados_acao:
                anti_spam["remover_xp"] = dados_acao['remover_xp']
            if 'xp_penalidade' in dados_acao:
                anti_spam["xp_penalidade"] = dados_acao['xp_penalidade']
            if 'deletar_mensagens' in dados_acao:
                anti_spam["deletar_mensagens"] = dados_acao['deletar_mensagens']
            if 'cargos_ignorados' in dados_acao:
                anti_spam["cargos_ignorados"] = [c.strip() for c in dados_acao['cargos_ignorados'].split(",") if c.strip()]
            if 'comandos_ignorados' in dados_acao:
                anti_spam["comandos_ignorados"] = [c.strip() for c in dados_acao['comandos_ignorados'].split(",") if c.strip()]
            salvar_dados_github("Config anti-spam atualizada")
            return True
        
        elif tipo_acao == "notificar_pedido":
            """Notifica sobre um pedido no Discord"""
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
# ROTAS PÚBLICAS DO SITE
# ========================

@app.route("/", methods=["GET"])
def home():
    """Página inicial do site"""
    status_bot = "✅ Bot Online" if bot.is_ready() else "❌ Bot Offline"
    classe_bot = "online" if bot.is_ready() else "offline"
    
    usuario = session.get('usuario')
    
    # Buscar serviços em destaque
    with get_db() as db_session:
        servicos_destaque = db_session.query(Servico).filter(
            Servico.destaque == True,
            Servico.status == True
        ).order_by(Servico.ordem).limit(6).all()
        
        categorias = db_session.query(Categoria).filter(Categoria.status == True).all()
    
    return render_template(
        'index.html',
        status_bot=status_bot,
        classe_bot=classe_bot,
        usuario=usuario,
        servicos_destaque=servicos_destaque,
        categorias=categorias
    )

@app.route("/servicos")
def servicos():
    """Página de serviços"""
    categoria_id = request.args.get('categoria', type=int)
    busca = request.args.get('busca', '')
    
    with get_db() as db_session:
        query = db_session.query(Servico).filter(Servico.status == True)
        
        if categoria_id:
            query = query.filter(Servico.categoria_id == categoria_id)
        
        if busca:
            query = query.filter(
                Servico.nome.ilike(f"%{busca}%") | 
                Servico.descricao.ilike(f"%{busca}%")
            )
        
        servicos = query.order_by(Servico.destaque.desc(), Servico.ordem).all()
        categorias = db_session.query(Categoria).filter(Categoria.status == True).all()
    
    return render_template(
        'servicos.html',
        servicos=servicos,
        categorias=categorias,
        categoria_selecionada=categoria_id,
        busca=busca,
        usuario=session.get('usuario')
    )

@app.route("/servico/<int:servico_id>")
def servico_detalhe(servico_id):
    """Página de detalhe de um serviço"""
    with get_db() as db_session:
        servico = db_session.query(Servico).filter(
            Servico.id == servico_id,
            Servico.status == True
        ).first()
        
        if not servico:
            flash('Serviço não encontrado.', 'danger')
            return redirect(url_for('servicos'))
        
        # Buscar serviços relacionados (mesma categoria)
        relacionados = db_session.query(Servico).filter(
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
@login_required
@csrf_protect
def comprar_servico(servico_id):
    """Processa a compra de um serviço"""
    with get_db() as db_session:
        servico = db_session.query(Servico).filter(
            Servico.id == servico_id,
            Servico.status == True
        ).first()
        
        if not servico:
            flash('Serviço não encontrado.', 'danger')
            return redirect(url_for('servicos'))
        
        # Buscar ou criar usuário
        usuario = db_session.query(Usuario).filter(
            Usuario.discord_id == session['usuario']['id']
        ).first()
        
        if not usuario:
            usuario = Usuario(
                discord_id=session['usuario']['id'],
                nome=session['usuario']['nome_usuario'],
                avatar=session['usuario'].get('avatar'),
                data_cadastro=datetime.now()
            )
            db_session.add(usuario)
            db_session.commit()
        
        # Criar pedido
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
        db_session.add(pedido)
        db_session.commit()
        
        # Criar pagamento PIX
        resultado_pix = criar_pagamento_pix(
            servico.nome,
            float(servico.preco),
            usuario.id,
            pedido.id
        )
        
        if resultado_pix.get('sucesso'):
            # Salvar pagamento
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
            db_session.add(pagamento)
            db_session.commit()
            
            # Notificar admin no Discord
            canal_pedidos = dados.get("config", {}).get("canal_pedidos")
            if canal_pedidos:
                executar_acao_bot(
                    "notificar_pedido",
                    canal_id=canal_pedidos,
                    mensagem="🆕 **Novo Pedido Criado!**",
                    embed={
                        "title": f"Pedido #{pedido.numero}",
                        "description": f"**Cliente:** {usuario.nome}\n**Serviço:** {servico.nome}\n**Valor:** {formatar_preco(servico.preco)}\n**Status:** Aguardando pagamento",
                        "color": "blue",
                        "thumbnail": usuario.avatar or None
                    }
                )
            
            # Adicionar log
            adicionar_log(f"Novo pedido criado: {pedido.numero} - {usuario.nome}", "pedido", usuario.id)
            
            flash(f'Pedido #{pedido.numero} criado com sucesso! Aguarde o pagamento.', 'success')
            return redirect(url_for('pedido_detalhe', pedido_id=pedido.id))
        else:
            # Se falhar, remover o pedido
            db_session.delete(pedido)
            db_session.commit()
            flash(f'Erro ao gerar pagamento: {resultado_pix.get("erro", "Erro desconhecido")}', 'danger')
            return redirect(url_for('servico_detalhe', servico_id=servico_id))

@app.route("/pedido/<int:pedido_id>")
@login_required
def pedido_detalhe(pedido_id):
    """Página de detalhe de um pedido"""
    with get_db() as db_session:
        pedido = db_session.query(Pedido).filter(Pedido.id == pedido_id).first()
        
        if not pedido:
            flash('Pedido não encontrado.', 'danger')
            return redirect(url_for('meus_pedidos'))
        
        # Verificar se o pedido pertence ao usuário
        usuario = db_session.query(Usuario).filter(
            Usuario.discord_id == session['usuario']['id']
        ).first()
        
        if not usuario or (pedido.usuario_id != usuario.id and not session['usuario'].get('eh_admin')):
            flash('Você não tem permissão para ver este pedido.', 'danger')
            return redirect(url_for('meus_pedidos'))
        
        # Buscar pagamento
        pagamento = db_session.query(Pagamento).filter(
            Pagamento.pedido_id == pedido.id
        ).first()
        
        # Buscar serviço
        servico = db_session.query(Servico).filter(Servico.id == pedido.servico_id).first()
    
    return render_template(
        'pedido_detalhe.html',
        pedido=pedido,
        pagamento=pagamento,
        servico=servico,
        usuario=session.get('usuario'),
        formatar_preco=formatar_preco
    )

@app.route("/meus-pedidos")
@login_required
def meus_pedidos():
    """Página de pedidos do usuário"""
    with get_db() as db_session:
        usuario = db_session.query(Usuario).filter(
            Usuario.discord_id == session['usuario']['id']
        ).first()
        
        if not usuario:
            flash('Usuário não encontrado.', 'danger')
            return redirect(url_for('home'))
        
        pedidos = db_session.query(Pedido).filter(
            Pedido.usuario_id == usuario.id
        ).order_by(Pedido.data_criacao.desc()).all()
    
    return render_template(
        'meus_pedidos.html',
        pedidos=pedidos,
        usuario=session.get('usuario'),
        formatar_preco=formatar_preco
    )

@app.route("/perfil")
@login_required
def perfil():
    """Página de perfil do usuário"""
    with get_db() as db_session:
        usuario = db_session.query(Usuario).filter(
            Usuario.discord_id == session['usuario']['id']
        ).first()
        
        if not usuario:
            # Criar usuário se não existir
            usuario = Usuario(
                discord_id=session['usuario']['id'],
                nome=session['usuario']['nome_usuario'],
                avatar=session['usuario'].get('avatar'),
                data_cadastro=datetime.now()
            )
            db_session.add(usuario)
            db_session.commit()
        
        # Buscar estatísticas
        total_pedidos = db_session.query(Pedido).filter(
            Pedido.usuario_id == usuario.id
        ).count()
        
        pedidos_concluidos = db_session.query(Pedido).filter(
            Pedido.usuario_id == usuario.id,
            Pedido.status == 'finalizado'
        ).count()
        
        total_gasto = db_session.query(Pedido).filter(
            Pedido.usuario_id == usuario.id,
            Pedido.status == 'finalizado'
        ).with_entities(db.func.sum(Pedido.valor)).scalar() or 0
        
        # Buscar pontos
        pontos = db_session.query(TransacaoPontos).filter(
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
@login_required
def pontos():
    """Página de pontos do usuário"""
    with get_db() as db_session:
        usuario = db_session.query(Usuario).filter(
            Usuario.discord_id == session['usuario']['id']
        ).first()
        
        if not usuario:
            flash('Usuário não encontrado.', 'danger')
            return redirect(url_for('home'))
        
        # Buscar histórico de pontos
        historico = db_session.query(TransacaoPontos).filter(
            TransacaoPontos.usuario_id == usuario.id
        ).order_by(TransacaoPontos.data.desc()).all()
        
        # Buscar recompensas disponíveis
        recompensas = db_session.query(Resgate).filter(
            Resgate.status == True
        ).order_by(Resgate.pontos).all()
    
    return render_template(
        'pontos.html',
        usuario=usuario,
        historico=historico,
        recompensas=recompensas,
        session_usuario=session.get('usuario'),
        formatar_preco=formatar_preco
    )

@app.route("/resgatar", methods=['POST'])
@login_required
@csrf_protect
def resgatar_pontos():
    """Resgata pontos por uma recompensa"""
    recompensa_id = request.form.get('recompensa_id', type=int)
    
    if not recompensa_id:
        flash('Selecione uma recompensa.', 'danger')
        return redirect(url_for('pontos'))
    
    with get_db() as db_session:
        usuario = db_session.query(Usuario).filter(
            Usuario.discord_id == session['usuario']['id']
        ).first()
        
        if not usuario:
            flash('Usuário não encontrado.', 'danger')
            return redirect(url_for('home'))
        
        recompensa = db_session.query(Resgate).filter(
            Resgate.id == recompensa_id,
            Resgate.status == True
        ).first()
        
        if not recompensa:
            flash('Recompensa não encontrada.', 'danger')
            return redirect(url_for('pontos'))
        
        if usuario.pontos < recompensa.pontos:
            flash(f'Você precisa de {recompensa.pontos} pontos para resgatar esta recompensa.', 'danger')
            return redirect(url_for('pontos'))
        
        # Gerar cupom
        codigo_cupom = gerar_codigo_cupom()
        
        # Criar cupom
        cupom = Cupom(
            codigo=codigo_cupom,
            tipo=recompensa.tipo,
            valor=recompensa.valor,
            validade=datetime.now() + timedelta(days=30),
            quantidade_maxima=1,
            quantidade_usada=0,
            status=True
        )
        db_session.add(cupom)
        
        # Registrar transação de pontos
        transacao = TransacaoPontos(
            usuario_id=usuario.id,
            tipo='gasto',
            quantidade=-recompensa.pontos,
            descricao=f'Resgate: {recompensa.nome} - Cupom {codigo_cupom}',
            data=datetime.now()
        )
        db_session.add(transacao)
        
        # Atualizar pontos do usuário
        usuario.pontos = (usuario.pontos or 0) - recompensa.pontos
        
        # Registrar log
        adicionar_log(
            f"Resgate de pontos: {usuario.nome} resgatou {recompensa.nome} por {recompensa.pontos} pontos. Cupom: {codigo_cupom}",
            "resgate",
            usuario.id
        )
        
        db_session.commit()
        
        flash(f'✅ Recompensa resgatada! Cupom: {codigo_cupom}. Use antes de {cupom.validade.strftime("%d/%m/%Y")}', 'success')
        return redirect(url_for('pontos'))

@app.route("/fila")
def fila_publica():
    """Página pública da fila"""
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
    """Embed da fila para iframe"""
    fila = obter_dados_fila()
    return render_template('fila_embed.html', fila=fila)

@app.route("/fila/api")
def fila_api():
    """API da fila"""
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

# ========================
# WEBHOOK DE PAGAMENTO
# ========================

@app.route("/webhook/pix", methods=['POST'])
def webhook_pix():
    """Webhook para receber confirmações de pagamento PIX"""
    # Verificar assinatura
    signature = request.headers.get('X-Signature')
    if signature and PIX_WEBHOOK_SECRET:
        # Verificar se a assinatura é válida
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
    
    # Log do webhook
    print(f"📨 Webhook PIX recebido: {data}")
    adicionar_log(f"Webhook PIX: {data.get('action', 'unknown')}", "pagamento")
    
    # Processar conforme a estrutura do Mercado Pago
    if data.get('type') == 'payment':
        payment_id = data.get('data', {}).get('id')
        
        if payment_id:
            return processar_pagamento_pix(payment_id)
    
    return jsonify({"status": "ok"}), 200

def processar_pagamento_pix(payment_id):
    """Processa a confirmação de pagamento PIX"""
    # Verificar status do pagamento
    resultado = verificar_pagamento_pix(payment_id)
    
    if not resultado.get('sucesso'):
        return jsonify({"erro": resultado.get('erro', 'Erro ao verificar pagamento')}), 400
    
    status = resultado.get('status')
    
    if status == 'approved':
        # Pagamento aprovado
        with get_db() as db_session:
            # Buscar pagamento pelo payment_id
            pagamento = db_session.query(Pagamento).filter(
                Pagamento.dados_pagamento['payment_id'].astext == str(payment_id)
            ).first()
            
            if not pagamento:
                return jsonify({"erro": "Pagamento não encontrado"}), 404
            
            if pagamento.status != 'pendente':
                return jsonify({"status": "pagamento já processado"}), 200
            
            # Atualizar pagamento
            pagamento.status = 'aprovado'
            pagamento.data_pagamento = datetime.now()
            
            # Atualizar pedido
            pedido = db_session.query(Pedido).filter(Pedido.id == pagamento.pedido_id).first()
            if pedido:
                pedido.status = 'pago'
                
                # Adicionar pontos ao usuário
                usuario = db_session.query(Usuario).filter(Usuario.id == pedido.usuario_id).first()
                if usuario:
                    pontos_ganhos = int(float(pedido.valor) * PONTOS_POR_REAL)
                    usuario.pontos = (usuario.pontos or 0) + pontos_ganhos
                    
                    # Registrar transação de pontos
                    transacao = TransacaoPontos(
                        usuario_id=usuario.id,
                        tipo='ganho',
                        quantidade=pontos_ganhos,
                        descricao=f'Compra: {pedido.numero} - {pedido.servico.nome if pedido.servico else "Serviço"}',
                        data=datetime.now()
                    )
                    db_session.add(transacao)
                    
                    adicionar_log(
                        f"Pagamento aprovado: {pedido.numero} - {usuario.nome} ganhou {pontos_ganhos} pontos",
                        "pagamento",
                        usuario.id
                    )
                
                # Adicionar à fila
                if pedido.servico:
                    nome_servico = pedido.servico.nome
                    nome_usuario = usuario.nome if usuario else "Cliente"
                    adicionar_fila(nome_usuario, nome_servico, "", str(usuario.discord_id))
            
            db_session.commit()
            
            # Notificar no Discord
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
            
            # Enviar mensagem privada para o usuário no Discord
            if pedido and usuario:
                try:
                    user = bot.get_user(int(usuario.discord_id))
                    if user:
                        embed = discord.Embed(
                            title="✅ Pagamento Confirmado!",
                            description=f"Seu pedido **{pedido.numero}** foi pago com sucesso!",
                            color=discord.Color.green()
                        )
                        embed.add_field(
                            name="Serviço",
                            value=pedido.servico.nome if pedido.servico else "Serviço",
                            inline=True
                        )
                        embed.add_field(
                            name="Valor",
                            value=formatar_preco(pedido.valor),
                            inline=True
                        )
                        embed.add_field(
                            name="Pontos Ganhos",
                            value=f"+{pontos_ganhos} pontos" if pontos_ganhos else "0 pontos",
                            inline=True
                        )
                        await user.send(embed=embed)
                except:
                    pass
    
    return jsonify({"status": "ok"}), 200

# ========================
# APIs PÚBLICAS
# ========================

@app.route("/api/fila/adicionar", methods=["POST"])
@rate_limit(limit=30, window=60)
def api_fila_adicionar():
    """API para adicionar à fila"""
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

# ========================
# APIs DE SERVIDOR
# ========================

@app.route("/api/servidor/canais")
def api_servidor_canais():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID and bot.is_ready() else None
    if not guild:
        return jsonify({"sucesso": False, "canais": []})
    return jsonify({"sucesso": True, "canais": [{"id": str(c.id), "nome": c.name} for c in guild.text_channels]})

@app.route("/api/servidor/cargos")
def api_servidor_cargos():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID and bot.is_ready() else None
    if not guild:
        return jsonify({"sucesso": False, "cargos": []})
    return jsonify({"sucesso": True, "cargos": [{"id": str(r.id), "nome": r.name} for r in guild.roles if r.name != "@everyone"]})

@app.route("/api/servidor/membros")
def api_servidor_membros():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID and bot.is_ready() else None
    if not guild:
        return jsonify({"sucesso": False, "membros": []})
    membros = [{"id": str(m.id), "nome": m.display_name} for m in guild.members if not m.bot][:100]
    return jsonify({"sucesso": True, "membros": membros})

# ========================
# APIs DE CONFIGURAÇÃO (LEGADO)
# ========================

@app.route("/api/anti_spam", methods=["GET", "POST"])
def api_anti_spam():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    
    if request.method == "GET":
        anti_spam = dados.get("anti_spam", {})
        return jsonify({
            "sucesso": True,
            "config": {
                "ativado": anti_spam.get("ativado", True),
                "limite_mensagens": anti_spam.get("limite_mensagens", 5),
                "intervalo_segundos": anti_spam.get("intervalo_segundos", 5),
                "tempo_mute_minutos": anti_spam.get("tempo_mute_minutos", 2),
                "remover_xp": anti_spam.get("remover_xp", True),
                "xp_penalidade": anti_spam.get("xp_penalidade", 50),
                "deletar_mensagens": anti_spam.get("deletar_mensagens", True),
                "cargos_ignorados": ",".join(anti_spam.get("cargos_ignorados", ["Administrador", "Moderador", "Staff", "Dono"])),
                "comandos_ignorados": ",".join(anti_spam.get("comandos_ignorados", [
                    "$w", "$wa", "$wg", "$h", "$ha", "$hg",
                    "$W", "$WA", "$WG", "$H", "$HA", "$HG",
                    "$tu", "$TU", "$dk", "$mmi", "$vote", "$rolls", "$k", "$mu"
                ]))
            }
        })
    
    req = request.json
    executar_acao_bot("configurar_anti_spam", **req)
    return jsonify({"sucesso": True, "mensagem": "Configuração anti-spam salva!"})

@app.route("/api/config/boasvindas", methods=["GET", "POST"])
def api_config_boasvindas():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    
    if request.method == "GET":
        config = dados.get("config", {})
        return jsonify({
            "sucesso": True,
            "canal": config.get("canal_boas_vindas", ""),
            "mensagem": config.get("mensagem_boas_vindas", "Olá {member}, seja bem-vindo(a)!"),
            "imagem": config.get("fundo_boas_vindas", "")
        })
    
    req = request.json
    executar_acao_bot("configurar_boas_vindas", **req)
    return jsonify({"sucesso": True, "mensagem": "Configuração salva!"})

@app.route("/api/config/xp", methods=["GET", "POST"])
def api_config_xp():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    
    if request.method == "GET":
        config = dados.get("config", {})
        return jsonify({
            "sucesso": True,
            "taxa": config.get("taxa_xp", 3),
            "canal": config.get("canal_levelup", "")
        })
    
    req = request.json
    executar_acao_bot("configurar_xp", **req)
    return jsonify({"sucesso": True, "mensagem": "Configuração salva!"})

@app.route("/api/config/comandos", methods=["GET", "POST"])
def api_config_comandos():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    
    if request.method == "GET":
        config = dados.get("config", {})
        return jsonify({
            "sucesso": True,
            "canal_perfil": config.get("canal_perfil", ""),
            "canal_rank": config.get("canal_rank", "")
        })
    
    req = request.json
    executar_acao_bot("configurar_comandos", **req)
    return jsonify({"sucesso": True, "mensagem": "Configuração de comandos salva!"})

@app.route("/api/config/pedidos", methods=["GET", "POST"])
def api_config_pedidos():
    """Configuração do canal de pedidos"""
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False}), 401
    
    if request.method == "GET":
        config = dados.get("config", {})
        return jsonify({
            "sucesso": True,
            "canal_pedidos": config.get("canal_pedidos", "")
        })
    
    req = request.json
    config = dados.setdefault("config", {})
    config["canal_pedidos"] = req.get("canal_id")
    salvar_dados_github("Config canal de pedidos atualizada")
    return jsonify({"sucesso": True, "mensagem": "Canal de pedidos configurado!"})

@app.route("/api/cargos/nivel", methods=["GET", "POST", "DELETE"])
def api_cargos_nivel():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    
    if request.method == "GET":
        return jsonify({"sucesso": True, "cargos": dados.get("cargos_nivel", {})})
    
    elif request.method == "POST":
        req = request.json
        executar_acao_bot("adicionar_cargo_nivel", nivel=req.get('nivel'), cargo_id=req.get('cargo_id'))
        return jsonify({"sucesso": True, "mensagem": "Cargo adicionado!"})
    
    elif request.method == "DELETE":
        nivel = request.args.get('nivel')
        if nivel:
            executar_acao_bot("remover_cargo_nivel", nivel=nivel)
        return jsonify({"sucesso": True, "mensagem": "Cargo removido!"})

@app.route("/api/config/links", methods=["GET", "POST"])
def api_config_links():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    
    if request.method == "GET":
        return jsonify({"sucesso": True, "canais": dados.get("canais_links_bloqueados", [])})
    
    req = request.json
    executar_acao_bot("alternar_bloqueio_links", canal_id=req.get('canal_id'))
    return jsonify({"sucesso": True, "mensagem": "Configuração salva!"})

# ========================
# APIs DE COMANDOS (LEGADO)
# ========================

@app.route("/api/comando/embed", methods=["POST"])
def api_comando_embed():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    sucesso = executar_acao_bot("criar_embed", **req)
    return jsonify({"sucesso": sucesso, "mensagem": "✅ Embed criada!" if sucesso else "❌ Falha"})

@app.route("/api/comando/advertir", methods=["POST"])
def api_comando_advertir():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    sucesso = executar_acao_bot("advertir_membro", membro_id=req.get('membro_id'), motivo=req.get('motivo'), admin=session['usuario']['nome_usuario'])
    return jsonify({"sucesso": sucesso, "mensagem": "✅ Advertência aplicada!" if sucesso else "❌ Falha"})

@app.route("/api/comando/limpar_advertencias", methods=["POST"])
def api_comando_limpar_advertencias():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    membro_id = str(request.json.get('membro_id'))
    if membro_id in dados.get("advertencias", {}):
        dados["advertencias"].pop(membro_id)
        salvar_dados_github(f"Advertências limpas: {membro_id}")
        return jsonify({"sucesso": True, "mensagem": "✅ Advertências removidas!"})
    return jsonify({"sucesso": False, "mensagem": "❌ Membro sem advertências"})

@app.route("/api/reacao_cargo/criar", methods=["POST"])
def api_reacao_cargo_criar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    sucesso = executar_acao_bot("criar_reacao_cargo", **req)
    return jsonify({"sucesso": sucesso, "mensagem": "✅ Reaction role criada!" if sucesso else "❌ Falha"})

@app.route("/api/botoes_cargo/criar", methods=["POST"])
def api_botoes_cargo_criar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    sucesso = executar_acao_bot("criar_botoes_cargo", **req)
    return jsonify({"sucesso": sucesso, "mensagem": "✅ Botões criados!" if sucesso else "❌ Falha"})

@app.route("/api/membro/advertencias")
def api_membro_advertencias():
    membro_id = request.args.get('membro_id')
    if not membro_id:
        return jsonify({"sucesso": False, "advertencias": []})
    warns = dados.get("advertencias", {}).get(str(membro_id), [])
    return jsonify({"sucesso": True, "advertencias": warns})

# ========================
# ROTAS ADMINISTRATIVAS
# ========================

@app.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    """Dashboard administrativo"""
    with get_db() as db_session:
        # Estatísticas
        total_clientes = db_session.query(Usuario).count()
        total_servicos = db_session.query(Servico).filter(Servico.status == True).count()
        
        # Pedidos
        pedidos_hoje = db_session.query(Pedido).filter(
            Pedido.data_criacao >= datetime.now().replace(hour=0, minute=0, second=0)
        ).count()
        
        pedidos_pendentes = db_session.query(Pedido).filter(
            Pedido.status == 'aguardando_pagamento'
        ).count()
        
        pedidos_pagos = db_session.query(Pedido).filter(
            Pedido.status == 'pago'
        ).count()
        
        pedidos_finalizados = db_session.query(Pedido).filter(
            Pedido.status == 'finalizado'
        ).count()
        
        # Financeiro
        total_vendido = db_session.query(Pedido).filter(
            Pedido.status == 'finalizado'
        ).with_entities(db.func.sum(Pedido.valor)).scalar() or 0
        
        # Pedidos recentes
        pedidos_recentes = db_session.query(Pedido).order_by(
            Pedido.data_criacao.desc()
        ).limit(10).all()
        
        # Serviços mais vendidos
        servicos_mais_vendidos = db_session.query(
            Servico.nome,
            db.func.count(Pedido.id).label('total')
        ).join(Pedido, Pedido.servico_id == Servico.id).filter(
            Pedido.status == 'finalizado'
        ).group_by(Servico.id).order_by(
            db.func.count(Pedido.id).desc()
        ).limit(5).all()
        
        # Pontos distribuídos
        pontos_distribuidos = db_session.query(TransacaoPontos).filter(
            TransacaoPontos.tipo == 'ganho'
        ).with_entities(db.func.sum(TransacaoPontos.quantidade)).scalar() or 0
        
        # Faturamento mensal
        mes_atual = datetime.now().replace(day=1, hour=0, minute=0, second=0)
        faturamento_mensal = db_session.query(Pedido).filter(
            Pedido.status == 'finalizado',
            Pedido.data_criacao >= mes_atual
        ).with_entities(db.func.sum(Pedido.valor)).scalar() or 0
        
        # Faturamento semanal
        semana_atual = datetime.now() - timedelta(days=7)
        faturamento_semanal = db_session.query(Pedido).filter(
            Pedido.status == 'finalizado',
            Pedido.data_criacao >= semana_atual
        ).with_entities(db.func.sum(Pedido.valor)).scalar() or 0
        
        # Faturamento diário
        hoje = datetime.now().replace(hour=0, minute=0, second=0)
        faturamento_diario = db_session.query(Pedido).filter(
            Pedido.status == 'finalizado',
            Pedido.data_criacao >= hoje
        ).with_entities(db.func.sum(Pedido.valor)).scalar() or 0
    
    return render_template(
        'admin/dashboard.html',
        total_clientes=total_clientes,
        total_servicos=total_servicos,
        pedidos_hoje=pedidos_hoje,
        pedidos_pendentes=pedidos_pendentes,
        pedidos_pagos=pedidos_pagos,
        pedidos_finalizados=pedidos_finalizados,
        total_vendido=total_vendido,
        faturamento_diario=faturamento_diario,
        faturamento_semanal=faturamento_semanal,
        faturamento_mensal=faturamento_mensal,
        pedidos_recentes=pedidos_recentes,
        servicos_mais_vendidos=servicos_mais_vendidos,
        pontos_distribuidos=pontos_distribuidos,
        usuario=session.get('usuario'),
        formatar_preco=formatar_preco
    )

# Rotas administrativas de Serviços
@app.route("/admin/servicos")
@login_required
@admin_required
def admin_servicos():
    """Gerenciamento de serviços"""
    with get_db() as db_session:
        servicos = db_session.query(Servico).order_by(Servico.ordem).all()
        categorias = db_session.query(Categoria).all()
    
    return render_template(
        'admin/servicos.html',
        servicos=servicos,
        categorias=categorias,
        usuario=session.get('usuario'),
        formatar_preco=formatar_preco
    )

@app.route("/admin/servicos/criar", methods=['POST'])
@login_required
@admin_required
@csrf_protect
def admin_servicos_criar():
    """Cria um novo serviço"""
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
    
    with get_db() as db_session:
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
        db_session.add(servico)
        db_session.commit()
        
        adicionar_log(f"Serviço criado: {nome}", "servico")
        flash(f'Serviço "{nome}" criado com sucesso!', 'success')
    
    return redirect(url_for('admin_servicos'))

@app.route("/admin/servicos/<int:servico_id>/editar", methods=['POST'])
@login_required
@admin_required
@csrf_protect
def admin_servicos_editar(servico_id):
    """Edita um serviço existente"""
    with get_db() as db_session:
        servico = db_session.query(Servico).filter(Servico.id == servico_id).first()
        
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
        
        db_session.commit()
        
        adicionar_log(f"Serviço editado: {servico.nome}", "servico")
        flash(f'Serviço "{servico.nome}" atualizado com sucesso!', 'success')
    
    return redirect(url_for('admin_servicos'))

@app.route("/admin/servicos/<int:servico_id>/excluir", methods=['POST'])
@login_required
@admin_required
@csrf_protect
def admin_servicos_excluir(servico_id):
    """Exclui um serviço"""
    with get_db() as db_session:
        servico = db_session.query(Servico).filter(Servico.id == servico_id).first()
        
        if not servico:
            flash('Serviço não encontrado.', 'danger')
            return redirect(url_for('admin_servicos'))
        
        nome = servico.nome
        db_session.delete(servico)
        db_session.commit()
        
        adicionar_log(f"Serviço excluído: {nome}", "servico")
        flash(f'Serviço "{nome}" excluído com sucesso!', 'success')
    
    return redirect(url_for('admin_servicos'))

# Rotas administrativas de Categorias
@app.route("/admin/categorias")
@login_required
@admin_required
def admin_categorias():
    """Gerenciamento de categorias"""
    with get_db() as db_session:
        categorias = db_session.query(Categoria).order_by(Categoria.nome).all()
    
    return render_template(
        'admin/categorias.html',
        categorias=categorias,
        usuario=session.get('usuario')
    )

@app.route("/admin/categorias/criar", methods=['POST'])
@login_required
@admin_required
@csrf_protect
def admin_categorias_criar():
    """Cria uma nova categoria"""
    nome = request.form.get('nome', '').strip()
    icone = request.form.get('icone', '').strip()
    status = request.form.get('status') == 'on'
    ordem = request.form.get('ordem', type=int) or 0
    
    if not nome:
        flash('Nome da categoria é obrigatório.', 'danger')
        return redirect(url_for('admin_categorias'))
    
    with get_db() as db_session:
        categoria = Categoria(
            nome=nome,
            icone=icone,
            status=status,
            ordem=ordem
        )
        db_session.add(categoria)
        db_session.commit()
        
        adicionar_log(f"Categoria criada: {nome}", "categoria")
        flash(f'Categoria "{nome}" criada com sucesso!', 'success')
    
    return redirect(url_for('admin_categorias'))

@app.route("/admin/categorias/<int:categoria_id>/editar", methods=['POST'])
@login_required
@admin_required
@csrf_protect
def admin_categorias_editar(categoria_id):
    """Edita uma categoria existente"""
    with get_db() as db_session:
        categoria = db_session.query(Categoria).filter(Categoria.id == categoria_id).first()
        
        if not categoria:
            flash('Categoria não encontrada.', 'danger')
            return redirect(url_for('admin_categorias'))
        
        categoria.nome = request.form.get('nome', '').strip()
        categoria.icone = request.form.get('icone', '').strip()
        categoria.status = request.form.get('status') == 'on'
        categoria.ordem = request.form.get('ordem', type=int) or 0
        
        db_session.commit()
        
        adicionar_log(f"Categoria editada: {categoria.nome}", "categoria")
        flash(f'Categoria "{categoria.nome}" atualizada com sucesso!', 'success')
    
    return redirect(url_for('admin_categorias'))

@app.route("/admin/categorias/<int:categoria_id>/excluir", methods=['POST'])
@login_required
@admin_required
@csrf_protect
def admin_categorias_excluir(categoria_id):
    """Exclui uma categoria"""
    with get_db() as db_session:
        categoria = db_session.query(Categoria).filter(Categoria.id == categoria_id).first()
        
        if not categoria:
            flash('Categoria não encontrada.', 'danger')
            return redirect(url_for('admin_categorias'))
        
        # Verificar se tem serviços associados
        servicos_count = db_session.query(Servico).filter(Servico.categoria_id == categoria_id).count()
        if servicos_count > 0:
            flash(f'Não é possível excluir a categoria "{categoria.nome}" pois possui {servicos_count} serviço(s) associado(s).', 'danger')
            return redirect(url_for('admin_categorias'))
        
        nome = categoria.nome
        db_session.delete(categoria)
        db_session.commit()
        
        adicionar_log(f"Categoria excluída: {nome}", "categoria")
        flash(f'Categoria "{nome}" excluída com sucesso!', 'success')
    
    return redirect(url_for('admin_categorias'))

# Rotas administrativas de Pedidos
@app.route("/admin/pedidos")
@login_required
@admin_required
def admin_pedidos():
    """Gerenciamento de pedidos"""
    status_filtro = request.args.get('status', '')
    busca = request.args.get('busca', '')
    
    with get_db() as db_session:
        query = db_session.query(Pedido)
        
        if status_filtro:
            query = query.filter(Pedido.status == status_filtro)
        
        if busca:
            query = query.filter(
                Pedido.numero.ilike(f"%{busca}%") |
                Pedido.dados_cliente['discord_nome'].astext.ilike(f"%{busca}%")
            )
        
        pedidos = query.order_by(Pedido.data_criacao.desc()).all()
        
        # Estatísticas
        total_pedidos = db_session.query(Pedido).count()
        pedidos_aguardando = db_session.query(Pedido).filter(Pedido.status == 'aguardando_pagamento').count()
        pedidos_pagos = db_session.query(Pedido).filter(Pedido.status == 'pago').count()
        pedidos_andamento = db_session.query(Pedido).filter(Pedido.status == 'em_andamento').count()
        pedidos_finalizados = db_session.query(Pedido).filter(Pedido.status == 'finalizado').count()
        pedidos_cancelados = db_session.query(Pedido).filter(Pedido.status == 'cancelado').count()
    
    return render_template(
        'admin/pedidos.html',
        pedidos=pedidos,
        total_pedidos=total_pedidos,
        pedidos_aguardando=pedidos_aguardando,
        pedidos_pagos=pedidos_pagos,
        pedidos_andamento=pedidos_andamento,
        pedidos_finalizados=pedidos_finalizados,
        pedidos_cancelados=pedidos_cancelados,
        status_filtro=status_filtro,
        busca=busca,
        usuario=session.get('usuario'),
        formatar_preco=formatar_preco
    )

@app.route("/admin/pedidos/<int:pedido_id>/status", methods=['POST'])
@login_required
@admin_required
@csrf_protect
def admin_pedidos_status(pedido_id):
    """Atualiza o status de um pedido"""
    novo_status = request.form.get('status', '')
    observacao = request.form.get('observacao', '').strip()
    
    if not novo_status:
        flash('Status não informado.', 'danger')
        return redirect(url_for('admin_pedidos'))
    
    with get_db() as db_session:
        pedido = db_session.query(Pedido).filter(Pedido.id == pedido_id).first()
        
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
        
        db_session.commit()
        
        adicionar_log(f"Pedido {pedido.numero} atualizado para status: {novo_status}", "pedido")
        
        # Notificar cliente no Discord
        if pedido.usuario:
            try:
                user = bot.get_user(int(pedido.usuario.discord_id))
                if user:
                    status_emoji = {
                        'aguardando_pagamento': '⏳',
                        'pago': '✅',
                        'em_andamento': '🔄',
                        'finalizado': '🎉',
                        'cancelado': '❌'
                    }.get(novo_status, '📋')
                    
                    status_nome = {
                        'aguardando_pagamento': 'Aguardando Pagamento',
                        'pago': 'Pago',
                        'em_andamento': 'Em Andamento',
                        'finalizado': 'Finalizado',
                        'cancelado': 'Cancelado'
                    }.get(novo_status, novo_status)
                    
                    embed = discord.Embed(
                        title=f"{status_emoji} Status do Pedido Atualizado",
                        description=f"Seu pedido **{pedido.numero}** está agora com status: **{status_nome}**",
                        color=discord.Color.green() if novo_status in ['pago', 'finalizado'] else discord.Color.blue()
                    )
                    
                    if observacao:
                        embed.add_field(name="Observação", value=observacao, inline=False)
                    
                    await user.send(embed=embed)
            except:
                pass
        
        flash(f'Pedido {pedido.numero} atualizado para "{novo_status}"!', 'success')
    
    return redirect(url_for('admin_pedidos'))

# Rotas administrativas de Clientes
@app.route("/admin/clientes")
@login_required
@admin_required
def admin_clientes():
    """Gerenciamento de clientes"""
    busca = request.args.get('busca', '')
    
    with get_db() as db_session:
        query = db_session.query(Usuario)
        
        if busca:
            query = query.filter(Usuario.nome.ilike(f"%{busca}%"))
        
        clientes = query.order_by(Usuario.pontos.desc()).all()
        
        # Estatísticas
        total_clientes = db_session.query(Usuario).count()
        clientes_com_pontos = db_session.query(Usuario).filter(Usuario.pontos > 0).count()
        total_pontos = db_session.query(Usuario).with_entities(db.func.sum(Usuario.pontos)).scalar() or 0
        
        # Top clientes
        top_clientes = db_session.query(Usuario).order_by(
            Usuario.pontos.desc()
        ).limit(5).all()
    
    return render_template(
        'admin/clientes.html',
        clientes=clientes,
        total_clientes=total_clientes,
        clientes_com_pontos=clientes_com_pontos,
        total_pontos=total_pontos,
        top_clientes=top_clientes,
        busca=busca,
        usuario=session.get('usuario'),
        formatar_preco=formatar_preco
    )

@app.route("/admin/clientes/<int:usuario_id>/pontos", methods=['POST'])
@login_required
@admin_required
@csrf_protect
def admin_clientes_pontos(usuario_id):
    """Adiciona ou remove pontos de um cliente"""
    quantidade = request.form.get('quantidade', type=int)
    descricao = request.form.get('descricao', '').strip()
    tipo = request.form.get('tipo', 'ganho')
    
    if not quantidade or quantidade == 0:
        flash('Quantidade deve ser diferente de zero.', 'danger')
        return redirect(url_for('admin_clientes'))
    
    with get_db() as db_session:
        usuario = db_session.query(Usuario).filter(Usuario.id == usuario_id).first()
        
        if not usuario:
            flash('Usuário não encontrado.', 'danger')
            return redirect(url_for('admin_clientes'))
        
        if tipo == 'gasto':
            quantidade = -abs(quantidade)
        else:
            quantidade = abs(quantidade)
        
        usuario.pontos = (usuario.pontos or 0) + quantidade
        
        # Registrar transação
        transacao = TransacaoPontos(
            usuario_id=usuario.id,
            tipo=tipo,
            quantidade=quantidade,
            descricao=descricao or f'Ajuste manual por admin: {session["usuario"]["nome_usuario"]}',
            data=datetime.now()
        )
        db_session.add(transacao)
        db_session.commit()
        
        adicionar_log(
            f"Ajuste de pontos: {usuario.nome} {'ganhou' if quantidade > 0 else 'perdeu'} {abs(quantidade)} pontos - {descricao}",
            "pontos",
            usuario.id
        )
        
        flash(f'Pontos de {usuario.nome} ajustados com sucesso!', 'success')
    
    return redirect(url_for('admin_clientes'))

# Rotas administrativas de Pontos
@app.route("/admin/pontos")
@login_required
@admin_required
def admin_pontos():
    """Gerenciamento de pontos e recompensas"""
    with get_db() as db_session:
        recompensas = db_session.query(Resgate).all()
        transacoes = db_session.query(TransacaoPontos).order_by(
            TransacaoPontos.data.desc()
        ).limit(50).all()
        
        # Estatísticas
        total_pontos = db_session.query(Usuario).with_entities(db.func.sum(Usuario.pontos)).scalar() or 0
        total_ganhos = db_session.query(TransacaoPontos).filter(
            TransacaoPontos.tipo == 'ganho'
        ).with_entities(db.func.sum(TransacaoPontos.quantidade)).scalar() or 0
        total_gastos = db_session.query(TransacaoPontos).filter(
            TransacaoPontos.tipo == 'gasto'
        ).with_entities(db.func.sum(TransacaoPontos.quantidade)).scalar() or 0
    
    return render_template(
        'admin/pontos.html',
        recompensas=recompensas,
        transacoes=transacoes,
        total_pontos=total_pontos,
        total_ganhos=total_ganhos,
        total_gastos=abs(total_gastos) if total_gastos else 0,
        usuario=session.get('usuario'),
        formatar_preco=formatar_preco
    )

@app.route("/admin/recompensas/criar", methods=['POST'])
@login_required
@admin_required
@csrf_protect
def admin_recompensas_criar():
    """Cria uma nova recompensa"""
    nome = request.form.get('nome', '').strip()
    pontos = request.form.get('pontos', type=int)
    tipo = request.form.get('tipo', 'desconto')
    valor = request.form.get('valor', type=float)
    descricao = request.form.get('descricao', '').strip()
    status = request.form.get('status') == 'on'
    
    if not nome or not pontos or pontos <= 0:
        flash('Nome e pontos são obrigatórios.', 'danger')
        return redirect(url_for('admin_pontos'))
    
    with get_db() as db_session:
        recompensa = Resgate(
            nome=nome,
            pontos=pontos,
            tipo=tipo,
            valor=valor,
            descricao=descricao,
            status=status
        )
        db_session.add(recompensa)
        db_session.commit()
        
        adicionar_log(f"Recompensa criada: {nome} - {pontos} pontos", "recompensa")
        flash(f'Recompensa "{nome}" criada com sucesso!', 'success')
    
    return redirect(url_for('admin_pontos'))

@app.route("/admin/recompensas/<int:recompensa_id>/editar", methods=['POST'])
@login_required
@admin_required
@csrf_protect
def admin_recompensas_editar(recompensa_id):
    """Edita uma recompensa existente"""
    with get_db() as db_session:
        recompensa = db_session.query(Resgate).filter(Resgate.id == recompensa_id).first()
        
        if not recompensa:
            flash('Recompensa não encontrada.', 'danger')
            return redirect(url_for('admin_pontos'))
        
        recompensa.nome = request.form.get('nome', '').strip()
        recompensa.pontos = request.form.get('pontos', type=int)
        recompensa.tipo = request.form.get('tipo', 'desconto')
        recompensa.valor = request.form.get('valor', type=float)
        recompensa.descricao = request.form.get('descricao', '').strip()
        recompensa.status = request.form.get('status') == 'on'
        
        db_session.commit()
        
        adicionar_log(f"Recompensa editada: {recompensa.nome}", "recompensa")
        flash(f'Recompensa "{recompensa.nome}" atualizada com sucesso!', 'success')
    
    return redirect(url_for('admin_pontos'))

@app.route("/admin/recompensas/<int:recompensa_id>/excluir", methods=['POST'])
@login_required
@admin_required
@csrf_protect
def admin_recompensas_excluir(recompensa_id):
    """Exclui uma recompensa"""
    with get_db() as db_session:
        recompensa = db_session.query(Resgate).filter(Resgate.id == recompensa_id).first()
        
        if not recompensa:
            flash('Recompensa não encontrada.', 'danger')
            return redirect(url_for('admin_pontos'))
        
        nome = recompensa.nome
        db_session.delete(recompensa)
        db_session.commit()
        
        adicionar_log(f"Recompensa excluída: {nome}", "recompensa")
        flash(f'Recompensa "{nome}" excluída com sucesso!', 'success')
    
    return redirect(url_for('admin_pontos'))

# Rotas administrativas de Cupons
@app.route("/admin/cupons")
@login_required
@admin_required
def admin_cupons():
    """Gerenciamento de cupons"""
    with get_db() as db_session:
        cupons = db_session.query(Cupom).order_by(Cupom.data_criacao.desc()).all()
        
        # Estatísticas
        total_cupons = db_session.query(Cupom).count()
        cupons_ativos = db_session.query(Cupom).filter(Cupom.status == True).count()
        cupons_usados = db_session.query(Cupom).filter(Cupom.quantidade_usada > 0).count()
    
    return render_template(
        'admin/cupons.html',
        cupons=cupons,
        total_cupons=total_cupons,
        cupons_ativos=cupons_ativos,
        cupons_usados=cupons_usados,
        usuario=session.get('usuario')
    )

@app.route("/admin/cupons/criar", methods=['POST'])
@login_required
@admin_required
@csrf_protect
def admin_cupons_criar():
    """Cria um novo cupom"""
    codigo = request.form.get('codigo', '').strip()
    tipo = request.form.get('tipo', 'porcentagem')
    valor = request.form.get('valor', type=float)
    validade = request.form.get('validade', '')
    quantidade_maxima = request.form.get('quantidade_maxima', type=int) or 1
    valor_minimo = request.form.get('valor_minimo', type=float) or 0
    usuarios_permitidos = request.form.get('usuarios_permitidos', '').strip()
    status = request.form.get('status') == 'on'
    
    if not codigo or not valor:
        flash('Código e valor são obrigatórios.', 'danger')
        return redirect(url_for('admin_cupons'))
    
    with get_db() as db_session:
        cupom = Cupom(
            codigo=codigo.upper(),
            tipo=tipo,
            valor=valor,
            validade=datetime.strptime(validade, '%Y-%m-%d') if validade else None,
            quantidade_maxima=quantidade_maxima,
            valor_minimo=valor_minimo,
            usuarios_permitidos=usuarios_permitidos,
            status=status
        )
        db_session.add(cupom)
        db_session.commit()
        
        adicionar_log(f"Cupom criado: {codigo}", "cupom")
        flash(f'Cupom "{codigo}" criado com sucesso!', 'success')
    
    return redirect(url_for('admin_cupons'))

@app.route("/admin/cupons/<int:cupom_id>/editar", methods=['POST'])
@login_required
@admin_required
@csrf_protect
def admin_cupons_editar(cupom_id):
    """Edita um cupom existente"""
    with get_db() as db_session:
        cupom = db_session.query(Cupom).filter(Cupom.id == cupom_id).first()
        
        if not cupom:
            flash('Cupom não encontrado.', 'danger')
            return redirect(url_for('admin_cupons'))
        
        cupom.codigo = request.form.get('codigo', '').strip().upper()
        cupom.tipo = request.form.get('tipo', 'porcentagem')
        cupom.valor = request.form.get('valor', type=float)
        cupom.validade = datetime.strptime(request.form.get('validade', ''), '%Y-%m-%d') if request.form.get('validade') else None
        cupom.quantidade_maxima = request.form.get('quantidade_maxima', type=int) or 1
        cupom.valor_minimo = request.form.get('valor_minimo', type=float) or 0
        cupom.usuarios_permitidos = request.form.get('usuarios_permitidos', '').strip()
        cupom.status = request.form.get('status') == 'on'
        
        db_session.commit()
        
        adicionar_log(f"Cupom editado: {cupom.codigo}", "cupom")
        flash(f'Cupom "{cupom.codigo}" atualizado com sucesso!', 'success')
    
    return redirect(url_for('admin_cupons'))

@app.route("/admin/cupons/<int:cupom_id>/excluir", methods=['POST'])
@login_required
@admin_required
@csrf_protect
def admin_cupons_excluir(cupom_id):
    """Exclui um cupom"""
    with get_db() as db_session:
        cupom = db_session.query(Cupom).filter(Cupom.id == cupom_id).first()
        
        if not cupom:
            flash('Cupom não encontrado.', 'danger')
            return redirect(url_for('admin_cupons'))
        
        codigo = cupom.codigo
        db_session.delete(cupom)
        db_session.commit()
        
        adicionar_log(f"Cupom excluído: {codigo}", "cupom")
        flash(f'Cupom "{codigo}" excluído com sucesso!', 'success')
    
    return redirect(url_for('admin_cupons'))

# Rotas administrativas de Configurações
@app.route("/admin/configuracoes")
@login_required
@admin_required
def admin_configuracoes():
    """Página de configurações"""
    with get_db() as db_session:
        configuracoes = db_session.query(Configuracao).all()
    
    return render_template(
        'admin/configuracoes.html',
        configuracoes=configuracoes,
        usuario=session.get('usuario')
    )

@app.route("/admin/configuracoes/salvar", methods=['POST'])
@login_required
@admin_required
@csrf_protect
def admin_configuracoes_salvar():
    """Salva as configurações"""
    chave = request.form.get('chave', '').strip()
    valor = request.form.get('valor', '').strip()
    descricao = request.form.get('descricao', '').strip()
    
    if not chave:
        flash('Chave é obrigatória.', 'danger')
        return redirect(url_for('admin_configuracoes'))
    
    with get_db() as db_session:
        config = db_session.query(Configuracao).filter(Configuracao.chave == chave).first()
        
        if config:
            config.valor = valor
            config.descricao = descricao
        else:
            config = Configuracao(
                chave=chave,
                valor=valor,
                descricao=descricao
            )
            db_session.add(config)
        
        db_session.commit()
        
        adicionar_log(f"Configuração salva: {chave} = {valor}", "configuracao")
        flash(f'Configuração "{chave}" salva com sucesso!', 'success')
    
    return redirect(url_for('admin_configuracoes'))

# Rotas administrativas de Logs
@app.route("/admin/logs")
@login_required
@admin_required
def admin_logs():
    """Página de logs"""
    with get_db() as db_session:
        logs = db_session.query(Log).order_by(Log.data.desc()).limit(200).all()
    
    return render_template(
        'admin/logs.html',
        logs=logs,
        usuario=session.get('usuario')
    )

# ========================
# SISTEMA DE LOGIN
# ========================

@app.route("/login")
def login():
    """Login com Discord OAuth2"""
    if not CLIENT_ID or not CLIENT_SECRET:
        return "Erro: CLIENT_ID ou CLIENT_SECRET não configurados.", 500
    
    # Gerar CSRF token
    gerar_csrf_token()
    
    url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds"
    return redirect(url)

@app.route("/callback")
def callback():
    """Callback do Discord OAuth2"""
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
        
        # Verificar se é administrador
        eh_admin = False
        if GUILD_ID:
            guilds_r = requests.get('https://discord.com/api/users/@me/guilds', headers={'Authorization': f'Bearer {access_token}'})
            if guilds_r.status_code == 200:
                guilds = guilds_r.json()
                for guild in guilds:
                    if str(guild['id']) == GUILD_ID and (guild.get('permissions', 0) & 0x8):
                        eh_admin = True
                        break
        
        # Criar ou atualizar usuário no banco
        with get_db() as db_session:
            usuario = db_session.query(Usuario).filter(
                Usuario.discord_id == user_data['id']
            ).first()
            
            if not usuario:
                usuario = Usuario(
                    discord_id=user_data['id'],
                    nome=user_data['username'],
                    avatar=user_data.get('avatar'),
                    data_cadastro=datetime.now(),
                    pontos=0
                )
                db_session.add(usuario)
                db_session.commit()
            else:
                usuario.nome = user_data['username']
                usuario.avatar = user_data.get('avatar')
                db_session.commit()
        
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
    """Logout"""
    session.clear()
    flash('Você foi desconectado.', 'info')
    return redirect(url_for('home'))

# ========================
# FUNÇÃO PARA VERIFICAR CANAL PERMITIDO
# ========================

async def verificar_canal_permitido(interaction: discord.Interaction, comando: str) -> bool:
    """Verifica se o comando pode ser usado no canal atual"""
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
        config = dados.get("config", {})
        canal_permitido = config.get("canal_perfil")
        if canal_permitido:
            canal_menção = f"<#{canal_permitido}>"
        else:
            canal_menção = "nenhum canal configurado"
        await interaction.response.send_message(
            f"❌ O comando `/perfil` só pode ser usado no canal {canal_menção}!",
            ephemeral=True
        )
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
    
    font_b = ImageFont.truetype(os.path.join(BASE_DIR, "DejaVuSans-Bold.ttf"),32)
    font_s = ImageFont.truetype(os.path.join(BASE_DIR, "DejaVuSans.ttf"),22)
    
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
        config = dados.get("config", {})
        canal_permitido = config.get("canal_rank")
        if canal_permitido:
            canal_menção = f"<#{canal_permitido}>"
        else:
            canal_menção = "nenhum canal configurado"
        await interaction.response.send_message(
            f"❌ O comando `/rank` só pode ser usado no canal {canal_menção}!",
            ephemeral=True
        )
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
    
    embed = discord.Embed(
        title="🏆 Top 10 Ranking de XP",
        description=texto,
        color=discord.Color.gold()
    )
    await interaction.followup.send(embed=embed)

@tree.command(name="servicos", description="Mostra os serviços disponíveis")
async def slash_servicos(interaction: discord.Interaction):
    """Comando para ver serviços disponíveis"""
    await interaction.response.defer()
    
    with get_db() as db_session:
        servicos = db_session.query(Servico).filter(
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
            value=f"{servico.descricao[:100]}...\n💰 {formatar_preco(servico.preco)}\n{'⏱️ ' + servico.tempo_estimado if servico.tempo_estimado else ''}",
            inline=False
        )
    
    embed.set_footer(text=f"Visite o site para mais serviços: {request.url_root}")
    
    await interaction.followup.send(embed=embed)

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
# EVENTOS DO BOT
# ========================

@bot.event
async def on_ready():
    print(f"\n{'='*50}")
    print(f"🤖 BOT INICIADO: {bot.user}")
    print(f"{'='*50}")
    
    # Inicializar banco de dados
    init_db()
    
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
    
    print("🔄 Restaurando botões persistentes...")
    botoes_cargos = dados.get("botoes_cargos", {})
    restaurados = 0
    for msg_id_str, dicionario_botoes in botoes_cargos.items():
        try:
            msg_id = int(msg_id_str)
            for guild in bot.guilds:
                for channel in guild.text_channels:
                    try:
                        mensagem = await channel.fetch_message(msg_id)
                        if mensagem:
                            class PersistentRoleButton(ui.Button):
                                def __init__(self, label: str, cargo_id: int, mensagem_id: int):
                                    super().__init__(label=label, style=ButtonStyle.primary)
                                    self.cargo_id = cargo_id
                                    self.mensagem_id = mensagem_id
                                async def callback(self, interaction: Interaction):
                                    guild = interaction.guild
                                    membro = interaction.user
                                    cargo = guild.get_role(self.cargo_id)
                                    if not cargo:
                                        await interaction.response.send_message("Cargo não encontrado.", ephemeral=True)
                                        return
                                    if cargo in membro.roles:
                                        await membro.remove_roles(cargo, reason="Botão de cargo")
                                        await interaction.response.send_message(f"Você **removeu** o cargo {cargo.mention}.", ephemeral=True)
                                    else:
                                        await membro.add_roles(cargo, reason="Botão de cargo")
                                        await interaction.response.send_message(f"Você **recebeu** o cargo {cargo.mention}.", ephemeral=True)
                                    adicionar_log(f"botao_cargo: usuario={membro.id} cargo={cargo.id}")
                            
                            class PersistentRoleButtonView(ui.View):
                                def __init__(self, mensagem_id: int, dicionario_botoes: dict):
                                    super().__init__(timeout=None)
                                    self.mensagem_id = mensagem_id
                                    for label, cargo_id in dicionario_botoes.items():
                                        self.add_item(PersistentRoleButton(label=label, cargo_id=cargo_id, mensagem_id=mensagem_id))
                            
                            view = PersistentRoleButtonView(msg_id, dicionario_botoes)
                            await mensagem.edit(view=view)
                            restaurados += 1
                            break
                    except:
                        continue
                if restaurados > 0:
                    break
        except:
            pass
    print(f"✅ {restaurados}/{len(botoes_cargos)} botões restaurados")
    
    await asyncio.sleep(2)
    iniciar_processador_acoes()
    
    config = dados.get("config", {})
    links = obter_links_fila()
    print(f"{'='*50}")
    print(f"✨ BOT PRONTO! Comandos: /perfil e /rank")
    print(f"🛡️ Anti-Spam: {'ATIVADO' if dados.get('anti_spam', {}).get('ativado', True) else 'DESATIVADO'}")
    print(f"🚫 Comandos da Mudae: NÃO ganham XP e NÃO contam como spam")
    print(f"📢 Canal do /perfil: {config.get('canal_perfil') or 'TODOS OS CANAIS'}")
    print(f"📢 Canal do /rank: {config.get('canal_rank') or 'TODOS OS CANAIS'}")
    print(f"📢 Canal de pedidos: {config.get('canal_pedidos') or 'NÃO CONFIGURADO'}")
    botoes_qtd = len(links.get("botoes_precos", []))
    if links.get('discord_convite') or botoes_qtd > 0:
        print(f"🔗 Links da fila configurados: {botoes_qtd} botão(ões) de preço")
    print(f"💡 Dica: Selecione o mesmo canal duas vezes no painel para remover a restrição!")
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
    
    fundo_url = dados.get("config", {}).get("fundo_boas_vindas", "")
    
    largura, altura = 900, 300
    img = Image.new("RGBA", (largura, altura), (0, 0, 0, 255))
    
    if fundo_url:
        try:
            response = requests.get(fundo_url)
            bg = Image.open(BytesIO(response.content)).convert("RGBA")
            bg = bg.resize((largura, altura))
            img.paste(bg, (0, 0))
        except:
            pass
    
    overlay = Image.new("RGBA", (largura, altura), (50, 50, 50, 150))
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)
    
    try:
        avatar_bytes = await member.avatar.read()
        avatar = Image.open(BytesIO(avatar_bytes)).convert("RGBA")
        avatar = avatar.resize((150, 150))
        mask = Image.new("L", (150, 150), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, 150, 150), fill=255)
        img.paste(avatar, (375, 30), mask)
    except:
        pass
    
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_s = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
    except:
        font = ImageFont.load_default()
        font_s = ImageFont.load_default()
    
    nome = member.display_name
    bbox = draw.textbbox((0, 0), nome, font=font)
    text_x = (largura - (bbox[2] - bbox[0])) // 2
    draw.text((text_x, 200), nome, font=font, fill=(0, 255, 255))
    
    texto_membro = f"Membro #{len(member.guild.members)}"
    bbox2 = draw.textbbox((0, 0), texto_membro, font=font_s)
    text_x2 = (largura - (bbox2[2] - bbox2[0])) // 2
    draw.text((text_x2, 250), texto_membro, font=font_s, fill=(255, 255, 255))
    
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    arquivo = discord.File(buf, filename="welcome.png")
    
    await canal.send(content=msg, file=arquivo)

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    msgmap = dados.get("reacoes_cargos", {}).get(str(payload.message_id))
    if not msgmap:
        return
    
    role_id = None
    if payload.emoji.id and str(payload.emoji.id) in msgmap:
        role_id = msgmap[str(payload.emoji.id)]
    elif str(payload.emoji) in msgmap:
        role_id = msgmap[str(payload.emoji)]
    
    if not role_id:
        return
    
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    member = guild.get_member(payload.user_id)
    if not member or member.bot:
        return
    role = guild.get_role(int(role_id))
    if role:
        await member.add_roles(role, reason="Reaction role")

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    msgmap = dados.get("reacoes_cargos", {}).get(str(payload.message_id))
    if not msgmap:
        return
    
    role_id = None
    if payload.emoji.id and str(payload.emoji.id) in msgmap:
        role_id = msgmap[str(payload.emoji.id)]
    elif str(payload.emoji) in msgmap:
        role_id = msgmap[str(payload.emoji)]
    
    if not role_id:
        return
    
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    member = guild.get_member(payload.user_id)
    if not member or member.bot:
        return
    role = guild.get_role(int(role_id))
    if role:
        await member.remove_roles(role, reason="Reaction role")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    
    conteudo = message.content.strip()
    anti_spam_config = dados.get("anti_spam", {})
    
    eh_comando_ignorado = verificar_comando_ignorado(conteudo)
    
    if eh_comando_ignorado:
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
                    
                    xp_removido = False
                    if anti_spam_config.get("remover_xp", True):
                        xp_removido = await remover_xp_por_spam(message.author)
                    
                    xp_msg = f" e teve **{anti_spam_config.get('xp_penalidade', 50)} XP removido**" if xp_removido else ""
                    try:
                        await message.author.send(f"⚠️ **Você foi mutado por {duracao} minutos** devido a spam no servidor {message.guild.name}!{xp_msg}\nPor favor, evite enviar muitas mensagens repetidas em um curto período.\n")
                    except:
                        await message.channel.send(f"⚠️ {message.author.mention}, você foi mutado por **{duracao} minutos** por spam!{xp_msg}")
                    
                    adicionar_log(f"anti_spam: {message.author.name} mutado por {duracao} min | {quantidade} msgs em {anti_spam_config.get('intervalo_segundos', 5)}s | XP removido: {xp_removido}")
                
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