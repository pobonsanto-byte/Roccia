import os
import json
import base64
import re
import requests
import time
import secrets
from io import BytesIO
from threading import Thread
from datetime import datetime, timezone, timedelta
from functools import wraps
import asyncio
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
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

# Configurações do site
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://seu-site.onrender.com/callback")
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))

if not BOT_TOKEN or not GITHUB_TOKEN:
    raise SystemExit("Defina BOT_TOKEN e GITHUB_TOKEN nas variáveis de ambiente.")

GITHUB_API_CONTENT = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{DATA_FILE}"

# ========================
# Sistema de ações
# ========================
acoes_fila_bot = []
processador_acoes_task = None
processador_acoes_rodando = False

# ========================
# FLASK APP
# ========================
app = Flask(__name__)
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
# ESTRUTURA DE DADOS
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
    },
    # NOVOS BLOCOS PARA SISTEMA DE CLIENTES E FIDELIDADE
    "clientes": {},
    "servicos": {},
    "solicitacoes": {},
    "fidelidade": {
        "pontos_por_real": 1,
        "recompensas": [],
        "cupons": {}
    }
}

# Dicionário para armazenar mensagens recentes dos usuários
mensagens_recentes = {}

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
                
                # Garantir que todas as chaves existam
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
                        "comandos_ignorados": [
                            "$w", "$wa", "$wg", "$h", "$ha", "$hg",
                            "$W", "$WA", "$WG", "$H", "$HA", "$HG",
                            "$tu", "$TU", "$dk", "$mmi", "$vote", "$rolls", "$k", "$mu"
                        ]
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
                if "botoes_precos" not in dados.get("links_fila", {}):
                    dados["links_fila"]["botoes_precos"] = []
                
                # Garantir novos blocos
                if "clientes" not in dados:
                    dados["clientes"] = {}
                if "servicos" not in dados:
                    dados["servicos"] = {}
                if "solicitacoes" not in dados:
                    dados["solicitacoes"] = {}
                if "fidelidade" not in dados:
                    dados["fidelidade"] = {
                        "pontos_por_real": 1,
                        "recompensas": [],
                        "cupons": {}
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

def adicionar_log(entrada):
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

def gerar_codigo_cupom():
    import random
    import string
    prefixo = "ZANKON"
    sufixo = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    return f"{prefixo}-{sufixo}"

# ========================
# FUNÇÕES ANTI-SPAM E IGNORADOS
# ========================

def verificar_comando_ignorado(conteudo: str) -> bool:
    """Verifica se a mensagem é um comando ignorado (não conta como spam e NÃO ganha XP)"""
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
# SISTEMA DE FILA
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
# FUNÇÕES PARA LINKS DA FILA (MÚLTIPLOS BOTÕES)
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
# FUNÇÕES PARA SISTEMA DE CLIENTES E FIDELIDADE
# ========================

def obter_cliente(discord_id: str):
    """Obtém os dados de um cliente pelo ID do Discord"""
    return dados.get("clientes", {}).get(str(discord_id))

def criar_cliente(discord_id: str, discord_nome: str, uid: str, nick_jogo: str):
    """Cria um novo cliente"""
    clientes = dados.setdefault("clientes", {})
    if str(discord_id) in clientes:
        return False, "Cliente já existe"
    
    # Verificar se o UID já está vinculado a outro Discord
    for did, cliente in clientes.items():
        if cliente.get("uid") == uid:
            return False, f"UID {uid} já está vinculado a outro Discord"
    
    clientes[str(discord_id)] = {
        "discord_id": str(discord_id),
        "discord_nome": discord_nome,
        "uid": uid,
        "nick_jogo": nick_jogo,
        "pontos": 0,
        "total_acumulado": 0,
        "total_utilizado": 0,
        "ultima_compra": None,
        "ultimo_resgate": None,
        "historico": [],
        "servicos_andamento": [],
        "cupons": []
    }
    salvar_dados_github(f"Novo cliente: {discord_nome} (UID: {uid})")
    return True, "Cliente criado com sucesso"

def atualizar_cliente(discord_id: str, **kwargs):
    """Atualiza dados de um cliente"""
    cliente = obter_cliente(discord_id)
    if not cliente:
        return False, "Cliente não encontrado"
    
    for key, value in kwargs.items():
        if key in cliente:
            cliente[key] = value
    
    salvar_dados_github(f"Cliente atualizado: {cliente.get('discord_nome')}")
    return True, "Cliente atualizado"

def adicionar_pontos_cliente(discord_id: str, pontos: int, motivo: str = ""):
    """Adiciona pontos a um cliente"""
    cliente = obter_cliente(discord_id)
    if not cliente:
        return False, "Cliente não encontrado"
    
    cliente["pontos"] = cliente.get("pontos", 0) + pontos
    cliente["total_acumulado"] = cliente.get("total_acumulado", 0) + pontos
    cliente["ultima_compra"] = agora_br().isoformat()
    
    cliente.setdefault("historico", []).append({
        "tipo": "pontos_adicionados",
        "pontos": pontos,
        "motivo": motivo,
        "data": agora_br().isoformat()
    })
    
    salvar_dados_github(f"Pontos adicionados: {pontos} para {cliente.get('discord_nome')}")
    return True, f"{pontos} pontos adicionados"

def remover_pontos_cliente(discord_id: str, pontos: int, motivo: str = ""):
    """Remove pontos de um cliente (para resgate)"""
    cliente = obter_cliente(discord_id)
    if not cliente:
        return False, "Cliente não encontrado"
    
    if cliente.get("pontos", 0) < pontos:
        return False, "Pontos insuficientes"
    
    cliente["pontos"] = cliente.get("pontos", 0) - pontos
    cliente["total_utilizado"] = cliente.get("total_utilizado", 0) + pontos
    cliente["ultimo_resgate"] = agora_br().isoformat()
    
    cliente.setdefault("historico", []).append({
        "tipo": "pontos_removidos",
        "pontos": pontos,
        "motivo": motivo,
        "data": agora_br().isoformat()
    })
    
    salvar_dados_github(f"Pontos removidos: {pontos} de {cliente.get('discord_nome')}")
    return True, f"{pontos} pontos removidos"

def criar_cupom(discord_id: str, tipo: str, valor: str):
    """Cria um cupom para um cliente"""
    codigo = gerar_codigo_cupom()
    cupom = {
        "codigo": codigo,
        "discord_id": str(discord_id),
        "uid": obter_cliente(discord_id).get("uid", ""),
        "tipo": tipo,
        "valor": valor,
        "data_criacao": agora_br().isoformat(),
        "validade": (agora_br() + timedelta(days=30)).isoformat(),
        "status": "ativo",
        "usado_em": None
    }
    
    dados.setdefault("fidelidade", {}).setdefault("cupons", {})[codigo] = cupom
    
    cliente = obter_cliente(discord_id)
    if cliente:
        cliente.setdefault("cupons", []).append(codigo)
    
    salvar_dados_github(f"Cupom criado: {codigo} para {cliente.get('discord_nome')}")
    return True, cupom

def usar_cupom(codigo: str, discord_id: str):
    """Utiliza um cupom"""
    cupons = dados.get("fidelidade", {}).get("cupons", {})
    cupom = cupons.get(codigo)
    
    if not cupom:
        return False, "Cupom não encontrado"
    
    if cupom.get("discord_id") != str(discord_id):
        return False, "Este cupom não pertence a você"
    
    if cupom.get("status") != "ativo":
        return False, "Cupom já foi utilizado ou está expirado"
    
    if agora_br() > datetime.fromisoformat(cupom.get("validade")):
        cupom["status"] = "expirado"
        salvar_dados_github(f"Cupom {codigo} expirado")
        return False, "Cupom expirado"
    
    cupom["status"] = "utilizado"
    cupom["usado_em"] = agora_br().isoformat()
    
    cliente = obter_cliente(discord_id)
    if cliente:
        cliente.setdefault("historico", []).append({
            "tipo": "cupom_utilizado",
            "codigo": codigo,
            "valor": cupom.get("valor"),
            "data": agora_br().isoformat()
        })
    
    salvar_dados_github(f"Cupom utilizado: {codigo}")
    return True, cupom

def criar_servico(nome: str, categoria: str, descricao: str, valor: float, pontos: int, imagem: str = ""):
    """Cria um novo serviço"""
    servicos = dados.setdefault("servicos", {})
    id_servico = str(int(datetime.now().timestamp()))
    
    servicos[id_servico] = {
        "id": id_servico,
        "nome": nome,
        "categoria": categoria,
        "descricao": descricao,
        "valor": valor,
        "pontos": pontos,
        "imagem": imagem,
        "status": "ativo",
        "criado_em": agora_br().isoformat()
    }
    salvar_dados_github(f"Serviço criado: {nome}")
    return True, id_servico

def atualizar_servico(id_servico: str, **kwargs):
    """Atualiza um serviço"""
    servicos = dados.get("servicos", {})
    if id_servico not in servicos:
        return False, "Serviço não encontrado"
    
    for key, value in kwargs.items():
        if key in servicos[id_servico]:
            servicos[id_servico][key] = value
    
    salvar_dados_github(f"Serviço atualizado: {servicos[id_servico].get('nome')}")
    return True, "Serviço atualizado"

def excluir_servico(id_servico: str):
    """Exclui um serviço"""
    servicos = dados.get("servicos", {})
    if id_servico in servicos:
        nome = servicos[id_servico].get("nome", "")
        del servicos[id_servico]
        salvar_dados_github(f"Serviço excluído: {nome}")
        return True, "Serviço excluído"
    return False, "Serviço não encontrado"

def criar_solicitacao(discord_id: str, servico_id: str, jogo: str, observacoes: str, cupom_codigo: str = None):
    """Cria uma solicitação de serviço"""
    cliente = obter_cliente(discord_id)
    if not cliente:
        return False, "Cliente não encontrado"
    
    servicos = dados.get("servicos", {})
    servico = servicos.get(servico_id)
    if not servico:
        return False, "Serviço não encontrado"
    
    if servico.get("status") != "ativo":
        return False, "Serviço está inativo"
    
    id_solicitacao = str(int(datetime.now().timestamp() * 1000))
    
    dados.setdefault("solicitacoes", {})[id_solicitacao] = {
        "id": id_solicitacao,
        "cliente_id": str(discord_id),
        "cliente_nome": cliente.get("discord_nome", ""),
        "cliente_uid": cliente.get("uid", ""),
        "servico_id": servico_id,
        "servico_nome": servico.get("nome", ""),
        "servico_valor": servico.get("valor", 0),
        "servico_pontos": servico.get("pontos", 0),
        "jogo": jogo,
        "observacoes": observacoes,
        "cupom_codigo": cupom_codigo,
        "status": "pendente",
        "data_solicitacao": agora_br().isoformat(),
        "data_aprovacao": None,
        "data_conclusao": None,
        "admin_aprovacao": None,
        "admin_conclusao": None,
        "motivo_recusa": None
    }
    
    salvar_dados_github(f"Solicitação criada: {cliente.get('discord_nome')} - {servico.get('nome')}")
    return True, id_solicitacao

def aprovar_solicitacao(id_solicitacao: str, admin_id: str):
    """Aprova uma solicitação e adiciona na fila"""
    solicitacoes = dados.get("solicitacoes", {})
    solicitacao = solicitacoes.get(id_solicitacao)
    if not solicitacao:
        return False, "Solicitação não encontrada"
    
    if solicitacao.get("status") != "pendente":
        return False, "Solicitação já foi processada"
    
    # Verificar cupom
    codigo_cupom = solicitacao.get("cupom_codigo")
    if codigo_cupom:
        resultado, _ = usar_cupom(codigo_cupom, solicitacao.get("cliente_id"))
        if not resultado:
            return False, "Cupom inválido ou expirado"
    
    # Adicionar na fila
    nome_usuario = solicitacao.get("cliente_nome", "Cliente")
    servico_nome = solicitacao.get("servico_nome", "Serviço")
    jogo = solicitacao.get("jogo", "")
    
    sucesso, entrada = adicionar_fila(nome_usuario, servico_nome, jogo, solicitacao.get("cliente_id"))
    if not sucesso:
        return False, f"Erro ao adicionar na fila: {entrada}"
    
    solicitacao["status"] = "aprovada"
    solicitacao["data_aprovacao"] = agora_br().isoformat()
    solicitacao["admin_aprovacao"] = admin_id
    
    # Adicionar aos serviços em andamento do cliente
    cliente = obter_cliente(solicitacao.get("cliente_id"))
    if cliente:
        cliente.setdefault("servicos_andamento", []).append({
            "solicitacao_id": id_solicitacao,
            "servico": servico_nome,
            "data": agora_br().isoformat()
        })
    
    salvar_dados_github(f"Solicitação aprovada: {id_solicitacao}")
    return True, entrada

def recusar_solicitacao(id_solicitacao: str, motivo: str, admin_id: str):
    """Recusa uma solicitação"""
    solicitacoes = dados.get("solicitacoes", {})
    solicitacao = solicitacoes.get(id_solicitacao)
    if not solicitacao:
        return False, "Solicitação não encontrada"
    
    if solicitacao.get("status") != "pendente":
        return False, "Solicitação já foi processada"
    
    solicitacao["status"] = "recusada"
    solicitacao["motivo_recusa"] = motivo
    solicitacao["data_recusa"] = agora_br().isoformat()
    solicitacao["admin_recusa"] = admin_id
    
    salvar_dados_github(f"Solicitação recusada: {id_solicitacao}")
    return True, "Solicitação recusada"

def concluir_solicitacao(id_solicitacao: str, admin_id: str):
    """Conclui uma solicitação e adiciona pontos ao cliente"""
    solicitacoes = dados.get("solicitacoes", {})
    solicitacao = solicitacoes.get(id_solicitacao)
    if not solicitacao:
        return False, "Solicitação não encontrada"
    
    if solicitacao.get("status") != "aprovada":
        return False, "Solicitação não está em andamento"
    
    cliente_id = solicitacao.get("cliente_id")
    
    # Adicionar pontos ao cliente
    pontos = solicitacao.get("servico_pontos", 0)
    if pontos > 0 and cliente_id:
        adicionar_pontos_cliente(cliente_id, pontos, f"Serviço: {solicitacao.get('servico_nome')}")
    
    solicitacao["status"] = "concluida"
    solicitacao["data_conclusao"] = agora_br().isoformat()
    solicitacao["admin_conclusao"] = admin_id
    
    # Remover dos serviços em andamento do cliente
    cliente = obter_cliente(cliente_id)
    if cliente:
        cliente.setdefault("servicos_andamento", []).append({
            "solicitacao_id": id_solicitacao,
            "servico": solicitacao.get("servico_nome"),
            "data_conclusao": agora_br().isoformat(),
            "pontos_ganhos": pontos
        })
        # Limpar andamento - manter apenas os que ainda estão ativos
        cliente["servicos_andamento"] = [s for s in cliente.get("servicos_andamento", []) 
                                         if s.get("solicitacao_id") != id_solicitacao]
        
        cliente.setdefault("historico", []).append({
            "tipo": "servico_concluido",
            "servico": solicitacao.get("servico_nome"),
            "valor": solicitacao.get("servico_valor"),
            "pontos_ganhos": pontos,
            "admin": admin_id,
            "data": agora_br().isoformat()
        })
    
    # Tentar concluir também na fila
    fila = obter_dados_fila()
    for entrada in fila["entradas"]:
        if entrada.get("usuario_id") == cliente_id and entrada.get("servico") == solicitacao.get("servico_nome"):
            concluir_servico(entrada["id"])
            break
    
    salvar_dados_github(f"Solicitação concluída: {id_solicitacao}")
    return True, "Serviço concluído com sucesso"

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
# ROTAS DO SITE
# ========================

@app.route("/", methods=["GET"])
def home():
    status_bot = "✅ Bot Online" if bot.is_ready() else "❌ Bot Offline"
    classe_bot = "online" if bot.is_ready() else "offline"
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Painel de Controle</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0a0a0a, #1a1a1a); margin: 0; padding: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center; color: #e0e0e0; }}
            .container {{ background: #121212; border-radius: 20px; padding: 40px; text-align: center; max-width: 500px; width: 90%; border: 1px solid #333; }}
            h1 {{ color: #5865F2; margin-bottom: 10px; }}
            .status {{ padding: 10px; border-radius: 10px; margin: 20px 0; font-weight: bold; }}
            .online {{ background: #1a472a; color: #4ade80; border: 1px solid #2ecc71; }}
            .offline {{ background: #7f1d1d; color: #f87171; border: 1px solid #ef4444; }}
            .btn {{ display: inline-block; background: #5865F2; color: white; padding: 12px 30px; border-radius: 8px; text-decoration: none; font-weight: bold; margin: 10px; transition: all 0.3s; }}
            .btn:hover {{ background: #4752C4; transform: translateY(-2px); }}
            .btn-success {{ background: #10b981; }}
            .btn-success:hover {{ background: #059669; }}
            .features {{ text-align: left; margin: 20px 0; padding: 15px; background: #1a1a1a; border-radius: 10px; border: 1px solid #333; }}
            .features h3 {{ color: #5865F2; }}
            .features li {{ margin: 8px 0; padding-left: 10px; list-style: none; }}
            .features li:before {{ content: "✅"; margin-right: 10px; color: #5865F2; }}
            .btn-group {{ display: flex; flex-wrap: wrap; justify-content: center; gap: 10px; margin-top: 15px; }}
            code {{ background: #1a1a1a; padding: 2px 6px; border-radius: 4px; color: #4ade80; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎮 Painel de Controle</h1>
            <div class="status {classe_bot}">{status_bot}</div>
            <div class="features">
                <h3>🚀 Funcionalidades:</h3>
                <ul>
                    <li>Sistema de XP e Níveis</li>
                    <li>Reação com Cargos</li>
                    <li>Boas-vindas Personalizadas</li>
                    <li>Sistema de Moderação</li>
                    <li>Botões de Cargos</li>
                    <li>Sistema de Fila de Serviços</li>
                    <li>Anti-Spam Automático</li>
                    <li>Sistema de Fidelidade</li>
                    <li>Área do Cliente</li>
                    <li>Loja de Fidelidade</li>
                </ul>
            </div>
            {"<a href='/login' class='btn'>🔐 Login com Discord</a>" if 'usuario' not in session else f'<p>Olá, {session["usuario"]["nome_usuario"]}!</p><div class="btn-group"><a href="/dashboard" class="btn">🚀 Painel Admin</a><a href="/cliente" class="btn btn-success">👤 Área do Cliente</a><a href="/fila" class="btn">📋 Fila</a><a href="/fidelidade/regras" class="btn">📜 Regras</a><a href="/logout" class="btn">🚪 Sair</a></div>'}
            <p style="margin-top: 20px; color: #888;">Use <code>/perfil</code> e <code>/rank</code> no Discord</p>
        </div>
    </body>
    </html>
    '''

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
        
        guilds_r = requests.get('https://discord.com/api/users/@me/guilds', headers={'Authorization': f'Bearer {access_token}'})
        guilds = guilds_r.json() if guilds_r.status_code == 200 else []
        
        is_member = any(str(guild['id']) == GUILD_ID for guild in guilds)
        
        if not is_member:
            return "<h2>⚠️ Acesso Restrito</h2><p>Você precisa ser membro do servidor para acessar.</p><a href='/'>Voltar</a>", 403
        
        is_admin = any(str(guild['id']) == GUILD_ID and (guild['permissions'] & 0x8) for guild in guilds)
        
        session['usuario'] = {
            'id': user_data['id'],
            'nome_usuario': user_data['username'],
            'avatar': user_data.get('avatar'),
            'eh_membro': True,
            'eh_admin': is_admin
        }
        
        if is_admin:
            return redirect(url_for('dashboard'))
        else:
            return redirect(url_for('cliente_area'))
        
    except Exception as e:
        return f"Erro interno: {str(e)}", 500

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('home'))

# ========================
# ROTAS DA FILA
# ========================

@app.route("/fila")
def fila_publica():
    fila = obter_dados_fila()
    links = obter_links_fila()
    botoes_precos = links.get("botoes_precos", [])
    
    botoes_html = ""
    for botao in botoes_precos:
        botoes_html += f'<a href="{escape_html(botao["url"])}" target="_blank" class="btn-link btn-link-precos">💰 {escape_html(botao["nome"])}</a>'
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta http-equiv="refresh" content="30">
        <title>{escape_html(fila["nome"])}</title>
        <style>
            * {{ margin:0; padding:0; box-sizing:border-box; }}
            body {{ font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0f0c29, #302b63, #24243e); min-height:100vh; padding:20px; color:#fff; }}
            .container {{ max-width:800px; margin:0 auto; }}
            .header {{ text-align:center; margin-bottom:30px; padding:20px; background:rgba(0,0,0,0.5); border-radius:20px; }}
            h1 {{ background: linear-gradient(135deg, #ff6b6b, #ffd93d); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
            .status {{ display:inline-block; padding:5px 15px; border-radius:20px; }}
            .status-aberta {{ background:#00b894; }}
            .status-fechada {{ background:#d63031; }}
            .links-container {{ display: flex; justify-content: center; gap: 20px; margin: 20px 0; flex-wrap: wrap; }}
            .btn-link {{ display: inline-flex; align-items: center; gap: 10px; padding: 12px 24px; border-radius: 30px; text-decoration: none; font-weight: bold; transition: all 0.3s; }}
            .btn-link-discord {{ background: #5865F2; color: white; }}
            .btn-link-discord:hover {{ background: #4752C4; transform: translateY(-2px); }}
            .btn-link-precos {{ background: #f59e0b; color: white; }}
            .btn-link-precos:hover {{ background: #d97706; transform: translateY(-2px); }}
            .lista-fila {{ background:rgba(0,0,0,0.4); border-radius:20px; overflow:hidden; }}
            .cabecalho-fila {{ display:grid; grid-template-columns:60px 1fr 1fr 1fr 80px; padding:15px; background:rgba(255,255,255,0.1); font-weight:bold; }}
            .item-fila {{ display:grid; grid-template-columns:60px 1fr 1fr 1fr 80px; padding:12px 15px; border-bottom:1px solid rgba(255,255,255,0.1); }}
            .posicao {{ font-weight:bold; color:#ffd93d; }}
            .servico {{ color:#a8e6cf; }}
            .jogo {{ color:#ffb347; }}
            .vazio {{ text-align:center; padding:40px; }}
            .footer {{ text-align:center; margin-top:20px; font-size:0.8rem; color:#888; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>📋 {escape_html(fila["nome"])}</h1>
                <span class="status status-{'aberta' if fila['configuracoes']['aberta'] else 'fechada'}">{'🟢 ABERTA' if fila['configuracoes']['aberta'] else '🔴 FECHADA'}</span>
                <div>📊 {len(fila["entradas"])} / {fila["configuracoes"]["tamanho_maximo"]} pessoas</div>
            </div>
            
            <div class="links-container">
                {'<a href="' + escape_html(links["discord_convite"]) + '" target="_blank" class="btn-link btn-link-discord">💬 Entrar no Discord</a>' if links.get("discord_convite") else ''}
                {botoes_html}
            </div>
            
            <div class="lista-fila">
                <div class="cabecalho-fila"><span>#</span><span>Jogador</span><span>Serviço</span><span>Jogo</span><span></span></div>
                {''.join(f'<div class="item-fila"><span class="posicao">{e["posicao"]}</span><span>{escape_html(e["nome_usuario"])}</span><span class="servico">{escape_html(e["servico"])}</span><span class="jogo">{escape_html(e.get("jogo", ""))}</span><span>⏳</span></div>' for e in fila["entradas"]) or '<div class="vazio">✨ Ninguém na fila</div>'}
            </div>
            <div class="footer">Atualizado a cada 30s • {agora_br().strftime("%d/%m/%Y %H:%M:%S")}</div>
        </div>
    </body>
    </html>
    '''

@app.route("/fila/embed")
def fila_embed():
    fila = obter_dados_fila()
    entradas_html = ""
    for e in fila["entradas"][:10]:
        entradas_html += f'<div style="display:flex;justify-content:space-between;padding:5px 0;"><span style="color:#ffd93d;">#{e["posicao"]}</span><span>{escape_html(e["nome_usuario"])}</span><span style="color:#a8e6cf;">{escape_html(e["servico"])}</span><span style="color:#ffb347;">{escape_html(e.get("jogo", ""))}</span></div>'
    if not entradas_html:
        entradas_html = '<div style="text-align:center;padding:20px;">✨ Fila vazia</div>'
    return f'''
    <!DOCTYPE html>
    <html><head><meta charset="UTF-8"><meta http-equiv="refresh" content="15"><style>body{{margin:0;padding:10px;background:transparent;color:white;font-size:14px;}}.container{{background:rgba(0,0,0,0.7);border-radius:10px;padding:10px;}}</style></head>
    <body><div class="container"><div style="text-align:center;margin-bottom:10px;"><strong>📋 {escape_html(fila["nome"])}</strong><span style="background:{'#00b894' if fila['configuracoes']['aberta'] else '#d63031'};padding:2px 8px;border-radius:10px;margin-left:5px;">{'ABERTA' if fila['configuracoes']['aberta'] else 'FECHADA'}</span></div>{entradas_html}<div style="text-align:center;margin-top:8px;font-size:10px;color:#888;">Total: {len(fila["entradas"])}</div></div></body>
    </html>
    '''

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

# ========================
# APIs DA FILA
# ========================

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
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    sucesso, _ = remover_fila(request.json.get("entrada_id"))
    return jsonify({"sucesso": sucesso})

@app.route("/api/fila/mover-cima", methods=["POST"])
def api_fila_mover_cima():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    sucesso, _ = mover_cima(request.json.get("entrada_id"))
    return jsonify({"sucesso": sucesso})

@app.route("/api/fila/mover-baixo", methods=["POST"])
def api_fila_mover_baixo():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    sucesso, _ = mover_baixo(request.json.get("entrada_id"))
    return jsonify({"sucesso": sucesso})

@app.route("/api/fila/concluir", methods=["POST"])
def api_fila_concluir():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    sucesso, _ = concluir_servico(request.json.get("entrada_id"))
    return jsonify({"sucesso": sucesso})

@app.route("/api/fila/limpar", methods=["POST"])
def api_fila_limpar():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    limpar_fila()
    return jsonify({"sucesso": True})

@app.route("/api/fila/configuracoes", methods=["GET", "POST"])
def api_fila_configuracoes():
    if request.method == "GET":
        fila = obter_dados_fila()
        links = obter_links_fila()
        return jsonify({"sucesso": True, "configuracoes": fila["configuracoes"], "nome": fila["nome"], "links": links})
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
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

# ========================
# APIs DOS BOTÕES DE PREÇO
# ========================

@app.route("/api/fila/botoes", methods=["GET"])
def api_fila_botoes():
    links = obter_links_fila()
    return jsonify({"sucesso": True, "botoes": links.get("botoes_precos", [])})

@app.route("/api/fila/botoes/adicionar", methods=["POST"])
def api_fila_botoes_adicionar():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    req = request.json
    nome = req.get("nome", "").strip()
    url = req.get("url", "").strip()
    if not nome or not url:
        return jsonify({"sucesso": False, "mensagem": "Nome e URL são obrigatórios"})
    adicionar_botao_preco(nome, url)
    return jsonify({"sucesso": True, "mensagem": f"Botão '{nome}' adicionado!"})

@app.route("/api/fila/botoes/remover", methods=["POST"])
def api_fila_botoes_remover():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    index = request.json.get("index")
    if index is None:
        return jsonify({"sucesso": False, "mensagem": "Índice não informado"})
    remover_botao_preco(int(index))
    return jsonify({"sucesso": True, "mensagem": "Botão removido!"})

@app.route("/api/fila/botoes/atualizar", methods=["POST"])
def api_fila_botoes_atualizar():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    req = request.json
    index = req.get("index")
    nome = req.get("nome", "").strip()
    url = req.get("url", "").strip()
    if index is None or not nome or not url:
        return jsonify({"sucesso": False, "mensagem": "Dados incompletos"})
    atualizar_botao_preco(int(index), nome, url)
    return jsonify({"sucesso": True, "mensagem": "Botão atualizado!"})

# ========================
# APIs DE CONFIGURAÇÃO
# ========================

@app.route("/api/servidor/canais")
def api_servidor_canais():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID and bot.is_ready() else None
    if not guild:
        return jsonify({"sucesso": False, "canais": []})
    return jsonify({"sucesso": True, "canais": [{"id": str(c.id), "nome": c.name} for c in guild.text_channels]})

@app.route("/api/servidor/cargos")
def api_servidor_cargos():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID and bot.is_ready() else None
    if not guild:
        return jsonify({"sucesso": False, "cargos": []})
    return jsonify({"sucesso": True, "cargos": [{"id": str(r.id), "nome": r.name} for r in guild.roles if r.name != "@everyone"]})

@app.route("/api/servidor/membros")
def api_servidor_membros():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID and bot.is_ready() else None
    if not guild:
        return jsonify({"sucesso": False, "membros": []})
    membros = [{"id": str(m.id), "nome": m.display_name} for m in guild.members if not m.bot][:100]
    return jsonify({"sucesso": True, "membros": membros})

@app.route("/api/anti_spam", methods=["GET", "POST"])
def api_anti_spam():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    
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
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    
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
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    
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
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    
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

@app.route("/api/cargos/nivel", methods=["GET", "POST", "DELETE"])
def api_cargos_nivel():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    
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
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    
    if request.method == "GET":
        return jsonify({"sucesso": True, "canais": dados.get("canais_links_bloqueados", [])})
    
    req = request.json
    executar_acao_bot("alternar_bloqueio_links", canal_id=req.get('canal_id'))
    return jsonify({"sucesso": True, "mensagem": "Configuração salva!"})

# ========================
# APIs DE COMANDOS
# ========================

@app.route("/api/comando/embed", methods=["POST"])
def api_comando_embed():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    req = request.json
    sucesso = executar_acao_bot("criar_embed", **req)
    return jsonify({"sucesso": sucesso, "mensagem": "✅ Embed criada!" if sucesso else "❌ Falha"})

@app.route("/api/comando/advertir", methods=["POST"])
def api_comando_advertir():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    req = request.json
    sucesso = executar_acao_bot("advertir_membro", membro_id=req.get('membro_id'), motivo=req.get('motivo'), admin=session['usuario']['nome_usuario'])
    return jsonify({"sucesso": sucesso, "mensagem": "✅ Advertência aplicada!" if sucesso else "❌ Falha"})

@app.route("/api/comando/limpar_advertencias", methods=["POST"])
def api_comando_limpar_advertencias():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    membro_id = str(request.json.get('membro_id'))
    if membro_id in dados.get("advertencias", {}):
        dados["advertencias"].pop(membro_id)
        salvar_dados_github(f"Advertências limpas: {membro_id}")
        return jsonify({"sucesso": True, "mensagem": "✅ Advertências removidas!"})
    return jsonify({"sucesso": False, "mensagem": "❌ Membro sem advertências"})

@app.route("/api/reacao_cargo/criar", methods=["POST"])
def api_reacao_cargo_criar():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    req = request.json
    sucesso = executar_acao_bot("criar_reacao_cargo", **req)
    return jsonify({"sucesso": sucesso, "mensagem": "✅ Reaction role criada!" if sucesso else "❌ Falha"})

@app.route("/api/botoes_cargo/criar", methods=["POST"])
def api_botoes_cargo_criar():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    req = request.json
    sucesso = executar_acao_bot("criar_botoes_cargo", **req)
    return jsonify({"sucesso": sucesso, "mensagem": "✅ Botões criados!" if sucesso else "❌ Falha"})

# ========================
# APIs PARA SISTEMA DE CLIENTES E SERVIÇOS
# ========================

@app.route("/api/cliente/status", methods=["GET"])
def api_cliente_status():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não logado"}), 401
    
    discord_id = session['usuario']['id']
    cliente = obter_cliente(discord_id)
    
    if not cliente:
        return jsonify({"sucesso": False, "cadastrado": False})
    
    return jsonify({
        "sucesso": True,
        "cadastrado": True,
        "cliente": {
            "discord_id": cliente.get("discord_id"),
            "discord_nome": cliente.get("discord_nome"),
            "uid": cliente.get("uid"),
            "nick_jogo": cliente.get("nick_jogo"),
            "pontos": cliente.get("pontos", 0),
            "total_acumulado": cliente.get("total_acumulado", 0),
            "total_utilizado": cliente.get("total_utilizado", 0),
            "ultima_compra": cliente.get("ultima_compra"),
            "ultimo_resgate": cliente.get("ultimo_resgate")
        }
    })

@app.route("/api/cliente/cadastrar", methods=["POST"])
def api_cliente_cadastrar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não logado"}), 401
    
    req = request.json
    uid = req.get("uid", "").strip()
    nick_jogo = req.get("nick_jogo", "").strip()
    
    if not uid or not nick_jogo:
        return jsonify({"sucesso": False, "mensagem": "UID e Nick do jogo são obrigatórios"})
    
    discord_id = session['usuario']['id']
    discord_nome = session['usuario']['nome_usuario']
    
    # Verificar se já está cadastrado
    if obter_cliente(discord_id):
        return jsonify({"sucesso": False, "mensagem": "Você já está cadastrado"})
    
    sucesso, mensagem = criar_cliente(discord_id, discord_nome, uid, nick_jogo)
    return jsonify({"sucesso": sucesso, "mensagem": mensagem})

@app.route("/api/cliente/atualizar", methods=["POST"])
def api_cliente_atualizar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não logado"}), 401
    
    discord_id = session['usuario']['id']
    req = request.json
    
    sucesso, mensagem = atualizar_cliente(discord_id, **req)
    return jsonify({"sucesso": sucesso, "mensagem": mensagem})

@app.route("/api/cliente/historico")
def api_cliente_historico():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não logado"}), 401
    
    discord_id = session['usuario']['id']
    cliente = obter_cliente(discord_id)
    
    if not cliente:
        return jsonify({"sucesso": False, "mensagem": "Cliente não encontrado"})
    
    return jsonify({
        "sucesso": True,
        "historico": cliente.get("historico", [])[-50:]  # Últimos 50 itens
    })

@app.route("/api/cliente/cupons")
def api_cliente_cupons():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não logado"}), 401
    
    discord_id = session['usuario']['id']
    cliente = obter_cliente(discord_id)
    
    if not cliente:
        return jsonify({"sucesso": False, "mensagem": "Cliente não encontrado"})
    
    cupons_cliente = []
    todos_cupons = dados.get("fidelidade", {}).get("cupons", {})
    
    for codigo in cliente.get("cupons", []):
        if codigo in todos_cupons:
            cupons_cliente.append(todos_cupons[codigo])
    
    return jsonify({"sucesso": True, "cupons": cupons_cliente})

@app.route("/api/cliente/servicos_andamento")
def api_cliente_servicos_andamento():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não logado"}), 401
    
    discord_id = session['usuario']['id']
    cliente = obter_cliente(discord_id)
    
    if not cliente:
        return jsonify({"sucesso": False, "mensagem": "Cliente não encontrado"})
    
    return jsonify({
        "sucesso": True,
        "servicos": cliente.get("servicos_andamento", [])
    })

# ========================
# APIs DE SERVIÇOS
# ========================

@app.route("/api/servicos", methods=["GET"])
def api_servicos_listar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não logado"}), 401
    
    servicos = dados.get("servicos", {})
    mostrar_inativos = session['usuario'].get('eh_admin', False)
    
    lista = []
    for sid, servico in servicos.items():
        if not mostrar_inativos and servico.get("status") != "ativo":
            continue
        lista.append(servico)
    
    return jsonify({"sucesso": True, "servicos": lista})

@app.route("/api/servicos", methods=["POST"])
def api_servicos_criar():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    
    req = request.json
    sucesso, resultado = criar_servico(
        req.get("nome", ""),
        req.get("categoria", ""),
        req.get("descricao", ""),
        float(req.get("valor", 0)),
        int(req.get("pontos", 0)),
        req.get("imagem", "")
    )
    return jsonify({"sucesso": sucesso, "mensagem": "Serviço criado!" if sucesso else resultado, "id": resultado if sucesso else None})

@app.route("/api/servicos/<id_servico>", methods=["PUT", "DELETE"])
def api_servicos_editar(id_servico):
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    
    if request.method == "DELETE":
        sucesso, mensagem = excluir_servico(id_servico)
        return jsonify({"sucesso": sucesso, "mensagem": mensagem})
    
    req = request.json
    sucesso, mensagem = atualizar_servico(id_servico, **req)
    return jsonify({"sucesso": sucesso, "mensagem": mensagem})

# ========================
# APIs DE SOLICITAÇÕES
# ========================

@app.route("/api/solicitacoes", methods=["GET"])
def api_solicitacoes_listar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não logado"}), 401
    
    solicitacoes = dados.get("solicitacoes", {})
    is_admin = session['usuario'].get('eh_admin', False)
    discord_id = session['usuario']['id']
    
    lista = []
    for sid, solicitacao in solicitacoes.items():
        if is_admin:
            lista.append(solicitacao)
        elif solicitacao.get("cliente_id") == discord_id:
            lista.append(solicitacao)
    
    # Ordenar por data mais recente
    lista.sort(key=lambda x: x.get("data_solicitacao", ""), reverse=True)
    
    return jsonify({"sucesso": True, "solicitacoes": lista})

@app.route("/api/solicitacoes", methods=["POST"])
def api_solicitacoes_criar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não logado"}), 401
    
    req = request.json
    discord_id = session['usuario']['id']
    
    cliente = obter_cliente(discord_id)
    if not cliente:
        return jsonify({"sucesso": False, "mensagem": "Você precisa estar cadastrado"})
    
    sucesso, resultado = criar_solicitacao(
        discord_id,
        req.get("servico_id", ""),
        req.get("jogo", ""),
        req.get("observacoes", ""),
        req.get("cupom_codigo", "")
    )
    return jsonify({"sucesso": sucesso, "mensagem": "Solicitação enviada!" if sucesso else resultado, "id": resultado if sucesso else None})

@app.route("/api/solicitacoes/<id_solicitacao>/aprovar", methods=["POST"])
def api_solicitacoes_aprovar(id_solicitacao):
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    
    admin_id = session['usuario']['id']
    sucesso, resultado = aprovar_solicitacao(id_solicitacao, admin_id)
    return jsonify({"sucesso": sucesso, "mensagem": "Solicitação aprovada!" if sucesso else resultado})

@app.route("/api/solicitacoes/<id_solicitacao>/recusar", methods=["POST"])
def api_solicitacoes_recusar(id_solicitacao):
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    
    req = request.json
    admin_id = session['usuario']['id']
    motivo = req.get("motivo", "Sem motivo informado")
    sucesso, resultado = recusar_solicitacao(id_solicitacao, motivo, admin_id)
    return jsonify({"sucesso": sucesso, "mensagem": resultado})

@app.route("/api/solicitacoes/<id_solicitacao>/concluir", methods=["POST"])
def api_solicitacoes_concluir(id_solicitacao):
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    
    admin_id = session['usuario']['id']
    sucesso, resultado = concluir_solicitacao(id_solicitacao, admin_id)
    return jsonify({"sucesso": sucesso, "mensagem": resultado})

# ========================
# APIs DE FIDELIDADE
# ========================

@app.route("/api/fidelidade/config", methods=["GET", "POST"])
def api_fidelidade_config():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    
    if request.method == "GET":
        return jsonify({
            "sucesso": True,
            "config": dados.get("fidelidade", {})
        })
    
    req = request.json
    fidelidade = dados.setdefault("fidelidade", {})
    
    if "pontos_por_real" in req:
        fidelidade["pontos_por_real"] = float(req["pontos_por_real"])
    
    salvar_dados_github("Configuração de fidelidade atualizada")
    return jsonify({"sucesso": True, "mensagem": "Configuração salva!"})

@app.route("/api/fidelidade/recompensas", methods=["GET", "POST", "DELETE"])
def api_fidelidade_recompensas():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    
    fidelidade = dados.setdefault("fidelidade", {})
    
    if request.method == "GET":
        return jsonify({"sucesso": True, "recompensas": fidelidade.get("recompensas", [])})
    
    elif request.method == "POST":
        req = request.json
        recompensa = {
            "id": str(int(datetime.now().timestamp())),
            "nome": req.get("nome", ""),
            "pontos": int(req.get("pontos", 0)),
            "descricao": req.get("descricao", ""),
            "tipo": req.get("tipo", "desconto"),
            "valor": req.get("valor", ""),
            "status": "ativo"
        }
        fidelidade.setdefault("recompensas", []).append(recompensa)
        salvar_dados_github(f"Recompensa adicionada: {recompensa['nome']}")
        return jsonify({"sucesso": True, "mensagem": "Recompensa adicionada!", "recompensa": recompensa})
    
    elif request.method == "DELETE":
        recompensa_id = request.args.get("id")
        if not recompensa_id:
            return jsonify({"sucesso": False, "mensagem": "ID da recompensa necessário"})
        
        recompensas = fidelidade.get("recompensas", [])
        for i, r in enumerate(recompensas):
            if r.get("id") == recompensa_id:
                recompensas.pop(i)
                salvar_dados_github(f"Recompensa removida: {r.get('nome')}")
                return jsonify({"sucesso": True, "mensagem": "Recompensa removida!"})
        
        return jsonify({"sucesso": False, "mensagem": "Recompensa não encontrada"})

@app.route("/api/fidelidade/recompensas/<recompensa_id>", methods=["PUT"])
def api_fidelidade_recompensa_editar(recompensa_id):
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    
    req = request.json
    fidelidade = dados.setdefault("fidelidade", {})
    recompensas = fidelidade.get("recompensas", [])
    
    for i, r in enumerate(recompensas):
        if r.get("id") == recompensa_id:
            for key, value in req.items():
                if key in r:
                    r[key] = value
            salvar_dados_github(f"Recompensa atualizada: {r.get('nome')}")
            return jsonify({"sucesso": True, "mensagem": "Recompensa atualizada!"})
    
    return jsonify({"sucesso": False, "mensagem": "Recompensa não encontrada"})

@app.route("/api/fidelidade/resgatar", methods=["POST"])
def api_fidelidade_resgatar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não logado"}), 401
    
    req = request.json
    recompensa_id = req.get("recompensa_id")
    discord_id = session['usuario']['id']
    
    cliente = obter_cliente(discord_id)
    if not cliente:
        return jsonify({"sucesso": False, "mensagem": "Cliente não encontrado"})
    
    fidelidade = dados.get("fidelidade", {})
    recompensas = fidelidade.get("recompensas", [])
    
    recompensa = None
    for r in recompensas:
        if r.get("id") == recompensa_id and r.get("status") == "ativo":
            recompensa = r
            break
    
    if not recompensa:
        return jsonify({"sucesso": False, "mensagem": "Recompensa não encontrada ou indisponível"})
    
    pontos_necessarios = recompensa.get("pontos", 0)
    if cliente.get("pontos", 0) < pontos_necessarios:
        return jsonify({"sucesso": False, "mensagem": f"Pontos insuficientes. Você tem {cliente.get('pontos', 0)} pontos"})
    
    # Remover pontos
    sucesso, mensagem = remover_pontos_cliente(discord_id, pontos_necessarios, f"Resgate: {recompensa.get('nome')}")
    if not sucesso:
        return jsonify({"sucesso": False, "mensagem": mensagem})
    
    # Criar cupom
    tipo = recompensa.get("tipo", "desconto")
    valor = recompensa.get("valor", "")
    sucesso, cupom = criar_cupom(discord_id, tipo, valor)
    
    if not sucesso:
        return jsonify({"sucesso": False, "mensagem": "Erro ao criar cupom"})
    
    return jsonify({
        "sucesso": True,
        "mensagem": f"Recompensa resgatada! Código: {cupom.get('codigo')}",
        "cupom": cupom
    })

# ========================
# APIs DE CLIENTES (ADMIN)
# ========================

@app.route("/api/admin/clientes", methods=["GET"])
def api_admin_clientes():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    
    clientes = dados.get("clientes", {})
    return jsonify({"sucesso": True, "clientes": list(clientes.values())})

@app.route("/api/admin/clientes/<discord_id>", methods=["GET", "PUT"])
def api_admin_cliente(discord_id):
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    
    if request.method == "GET":
        cliente = obter_cliente(discord_id)
        if not cliente:
            return jsonify({"sucesso": False, "mensagem": "Cliente não encontrado"})
        return jsonify({"sucesso": True, "cliente": cliente})
    
    elif request.method == "PUT":
        req = request.json
        sucesso, mensagem = atualizar_cliente(discord_id, **req)
        return jsonify({"sucesso": sucesso, "mensagem": mensagem})

@app.route("/api/admin/clientes/<discord_id>/pontos", methods=["POST"])
def api_admin_cliente_pontos(discord_id):
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    
    req = request.json
    pontos = int(req.get("pontos", 0))
    acao = req.get("acao", "adicionar")
    
    if acao == "adicionar":
        sucesso, mensagem = adicionar_pontos_cliente(discord_id, pontos, req.get("motivo", "Admin"))
    elif acao == "remover":
        sucesso, mensagem = remover_pontos_cliente(discord_id, pontos, req.get("motivo", "Admin"))
    else:
        return jsonify({"sucesso": False, "mensagem": "Ação inválida"})
    
    return jsonify({"sucesso": sucesso, "mensagem": mensagem})

@app.route("/api/admin/solicitacoes", methods=["GET"])
def api_admin_solicitacoes():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401
    
    solicitacoes = dados.get("solicitacoes", {})
    return jsonify({"sucesso": True, "solicitacoes": list(solicitacoes.values())})

# ========================
# DASHBOARD PRINCIPAL (ADMIN)
# ========================

@app.route("/dashboard")
def dashboard():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return redirect(url_for('login'))
    
    usuario = session['usuario']
    config = dados.get("config", {})
    fila = obter_dados_fila()
    anti_spam = dados.get("anti_spam", {})
    links = obter_links_fila()
    botoes_precos = links.get("botoes_precos", [])
    clientes = dados.get("clientes", {})
    servicos = dados.get("servicos", {})
    solicitacoes = dados.get("solicitacoes", {})
    fidelidade = dados.get("fidelidade", {})
    recompensas = fidelidade.get("recompensas", [])
    
    botoes_precos_json = json.dumps(botoes_precos)
    recompensas_json = json.dumps(recompensas)
    servicos_json = json.dumps(list(servicos.values()))
    solicitacoes_pendentes = json.dumps([s for s in solicitacoes.values() if s.get("status") == "pendente"])
    
    return f'''
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Painel Admin - Bot</title>
        <style>
            :root {{ --primary: #5865F2; --primary-dark: #4752C4; --success: #10b981; --danger: #ef4444; --warning: #f59e0b; --dark: #1a1a1a; --darker: #121212; --light: #e0e0e0; --gray: #333; }}
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: 'Segoe UI', sans-serif; background: var(--darker); color: var(--light); }}
            header {{ background: var(--dark); padding: 1rem 2rem; border-bottom: 1px solid var(--gray); }}
            .header-content {{ display: flex; justify-content: space-between; align-items: center; max-width: 1400px; margin: 0 auto; flex-wrap: wrap; gap: 10px; }}
            h1 {{ color: var(--primary); }}
            .user-info {{ display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }}
            .avatar {{ width: 40px; height: 40px; border-radius: 50%; border: 2px solid var(--primary); }}
            .btn {{ padding: 0.5rem 1rem; border: none; border-radius: 5px; cursor: pointer; font-weight: 600; text-decoration: none; display: inline-block; transition: all 0.2s; font-size: 0.9rem; }}
            .btn-primary {{ background: var(--primary); color: white; }}
            .btn-primary:hover {{ background: var(--primary-dark); }}
            .btn-success {{ background: var(--success); color: white; }}
            .btn-success:hover {{ background: #059669; }}
            .btn-danger {{ background: var(--danger); color: white; }}
            .btn-danger:hover {{ background: #dc2626; }}
            .btn-warning {{ background: var(--warning); color: white; }}
            .btn-warning:hover {{ background: #d97706; }}
            .btn-sm {{ padding: 0.25rem 0.5rem; font-size: 0.8rem; }}
            .btn-xs {{ padding: 0.15rem 0.4rem; font-size: 0.7rem; }}
            .container {{ max-width: 1400px; margin: 2rem auto; padding: 0 1rem; }}
            .tab-nav {{ display: flex; gap: 0.5rem; margin-bottom: 1rem; border-bottom: 2px solid var(--gray); flex-wrap: wrap; }}
            .tab-btn {{ padding: 0.75rem 1.5rem; background: var(--gray); border: none; border-radius: 5px 5px 0 0; cursor: pointer; font-weight: 600; color: var(--light); transition: all 0.2s; }}
            .tab-btn:hover {{ background: #444; }}
            .tab-btn.active {{ background: var(--primary); color: white; }}
            .tab {{ display: none; animation: fadeIn 0.3s; }}
            .tab.active {{ display: block; }}
            @keyframes fadeIn {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
            .card {{ background: var(--dark); border-radius: 10px; padding: 1.5rem; margin: 1rem 0; border: 1px solid var(--gray); }}
            .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1rem; }}
            .stat-card {{ background: linear-gradient(135deg, var(--primary), var(--primary-dark)); color: white; padding: 1.5rem; border-radius: 10px; text-align: center; }}
            .stat-card h3 {{ font-size: 2rem; }}
            .form-group {{ margin-bottom: 1.5rem; }}
            label {{ display: block; margin-bottom: 0.5rem; font-weight: 600; color: var(--primary); }}
            .form-control {{ width: 100%; padding: 0.75rem; background: var(--darker); border: 1px solid var(--gray); border-radius: 5px; color: var(--light); }}
            .form-control:focus {{ outline: none; border-color: var(--primary); }}
            .form-control option {{ background: var(--darker); }}
            .alert {{ padding: 1rem; border-radius: 5px; margin: 1rem 0; display: none; }}
            .alert-success {{ background: #1a472a; color: #4ade80; border: 1px solid #2ecc71; }}
            .alert-error {{ background: #7f1d1d; color: #f87171; border: 1px solid #ef4444; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ text-align: left; padding: 12px; border-bottom: 1px solid var(--gray); }}
            th {{ background: var(--gray); }}
            .grid-2 {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 1rem; }}
            .grid-3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; }}
            .grid-4 {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; }}
            @media (max-width: 768px) {{ .grid-2, .grid-3, .grid-4 {{ grid-template-columns: 1fr; }} }}
            .switch {{ position: relative; display: inline-block; width: 60px; height: 34px; }}
            .switch input {{ opacity: 0; width: 0; height: 0; }}
            .slider {{ position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #ccc; transition: .4s; border-radius: 34px; }}
            .slider:before {{ position: absolute; content: ""; height: 26px; width: 26px; left: 4px; bottom: 4px; background-color: white; transition: .4s; border-radius: 50%; }}
            input:checked + .slider {{ background-color: #2196F3; }}
            input:checked + .slider:before {{ transform: translateX(26px); }}
            .info-box {{ background: #1a1a2e; border-left: 4px solid #5865F2; padding: 1rem; margin: 1rem 0; border-radius: 5px; }}
            .config-badge {{ display: inline-block; background: #2196F3; color: white; padding: 2px 8px; border-radius: 10px; font-size: 11px; margin-left: 5px; }}
            .botoes-lista {{ display: flex; flex-direction: column; gap: 10px; margin-top: 15px; }}
            .botao-item {{ background: #1a1a1a; padding: 10px; border-radius: 8px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }}
            .botao-info {{ flex: 1; }}
            .botao-nome {{ font-weight: bold; color: #f59e0b; }}
            .botao-url {{ font-size: 12px; color: #888; word-break: break-all; }}
            .botao-acoes {{ display: flex; gap: 8px; flex-wrap: wrap; }}
            .status-badge {{ padding: 2px 10px; border-radius: 20px; font-size: 12px; }}
            .status-ativo {{ background: #1a472a; color: #4ade80; }}
            .status-inativo {{ background: #7f1d1d; color: #f87171; }}
            .status-pendente {{ background: #7f6d1d; color: #fbbf24; }}
            .status-aprovado {{ background: #1a472a; color: #4ade80; }}
            .status-concluido {{ background: #1a3a6a; color: #60a5fa; }}
            .status-recusado {{ background: #7f1d1d; color: #f87171; }}
            .modal {{ display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.8); z-index: 1000; justify-content: center; align-items: center; }}
            .modal.active {{ display: flex; }}
            .modal-content {{ background: var(--dark); padding: 2rem; border-radius: 10px; max-width: 600px; width: 90%; max-height: 90vh; overflow-y: auto; border: 1px solid var(--gray); }}
            .modal-close {{ float: right; background: none; border: none; color: var(--light); font-size: 1.5rem; cursor: pointer; }}
            .scroll-table {{ overflow-x: auto; }}
        </style>
    </head>
    <body>
        <header>
            <div class="header-content">
                <h1>🎮 Painel Admin</h1>
                <div class="user-info">
                    <img src="https://cdn.discordapp.com/avatars/{usuario['id']}/{usuario.get('avatar', '')}.png" class="avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                    <span>{usuario['nome_usuario']}</span>
                    <a href="/" class="btn btn-primary">🏠 Início</a>
                    <a href="/cliente" class="btn btn-success">👤 Área Cliente</a>
                    <a href="/fila" class="btn btn-primary">📋 Fila</a>
                    <a href="/logout" class="btn btn-danger">🚪 Sair</a>
                </div>
            </div>
        </header>
        
        <div class="container">
            <div class="tab-nav">
                <button class="tab-btn active" onclick="showTab('inicio')">🏠 Início</button>
                <button class="tab-btn" onclick="showTab('servicos')">🛒 Serviços</button>
                <button class="tab-btn" onclick="showTab('solicitacoes')">📋 Solicitações</button>
                <button class="tab-btn" onclick="showTab('clientes')">👥 Clientes</button>
                <button class="tab-btn" onclick="showTab('fidelidade')">⭐ Fidelidade</button>
                <button class="tab-btn" onclick="showTab('fila')">📋 Fila</button>
                <button class="tab-btn" onclick="showTab('comandos')">⚡ Comandos</button>
                <button class="tab-btn" onclick="showTab('configuracoes')">⚙️ Configurações</button>
            </div>
            
            <!-- Aba Início -->
            <div id="inicio" class="tab active">
                <div class="grid-3">
                    <div class="card">
                        <h2>📊 Estatísticas</h2>
                        <div class="stats-grid">
                            <div class="stat-card"><h3>{len(clientes)}</h3><p>Clientes</p></div>
                            <div class="stat-card"><h3>{len(servicos)}</h3><p>Serviços</p></div>
                            <div class="stat-card"><h3>{len([s for s in solicitacoes.values() if s.get('status') == 'pendente'])}</h3><p>Solicitações Pendentes</p></div>
                            <div class="stat-card"><h3>{len(fila['entradas'])}</h3><p>Na Fila</p></div>
                        </div>
                    </div>
                    <div class="card">
                        <h2>⚡ Status</h2>
                        <p><strong>Bot:</strong> {'✅ Online' if bot.is_ready() else '❌ Offline'}</p>
                        <p><strong>Anti-Spam:</strong> {'✅ Ativo' if anti_spam.get('ativado', True) else '❌ Desativado'}</p>
                        <p><strong>Pontos por R$:</strong> {fidelidade.get('pontos_por_real', 1)}</p>
                        <p><strong>Total de Cupons:</strong> {len(fidelidade.get('cupons', {}))}</p>
                    </div>
                    <div class="card">
                        <h2>📜 Regras</h2>
                        <a href="/fidelidade/regras" class="btn btn-primary" target="_blank">Ver Regras do Sistema</a>
                        <p style="margin-top: 10px; font-size: 0.9rem; color: #888;">Sistema de Fidelidade ZankonYTB</p>
                    </div>
                </div>
            </div>
            
            <!-- Aba Serviços -->
            <div id="servicos" class="tab">
                <div class="card">
                    <h2>🛒 Gerenciar Serviços</h2>
                    <div class="grid-3">
                        <div class="form-group">
                            <label>Nome do Serviço</label>
                            <input type="text" id="servico-nome" class="form-control" placeholder="Ex: Build Completa">
                        </div>
                        <div class="form-group">
                            <label>Categoria</label>
                            <input type="text" id="servico-categoria" class="form-control" placeholder="Ex: Builds, Quests">
                        </div>
                        <div class="form-group">
                            <label>Valor (R$)</label>
                            <input type="number" id="servico-valor" class="form-control" placeholder="20" step="0.01">
                        </div>
                        <div class="form-group">
                            <label>Pontos Gerados</label>
                            <input type="number" id="servico-pontos" class="form-control" placeholder="20">
                        </div>
                        <div class="form-group">
                            <label>Imagem (URL)</label>
                            <input type="url" id="servico-imagem" class="form-control" placeholder="https://exemplo.com/imagem.jpg">
                        </div>
                        <div class="form-group">
                            <label>Status</label>
                            <select id="servico-status" class="form-control">
                                <option value="ativo">Ativo</option>
                                <option value="inativo">Inativo</option>
                            </select>
                        </div>
                    </div>
                    <div class="form-group">
                        <label>Descrição</label>
                        <textarea id="servico-descricao" class="form-control" rows="2" placeholder="Descrição do serviço"></textarea>
                    </div>
                    <button onclick="criarServico()" class="btn btn-success">➕ Adicionar Serviço</button>
                    <div id="servico-alert" class="alert"></div>
                </div>
                
                <div class="card">
                    <h2>📋 Lista de Serviços</h2>
                    <div class="scroll-table">
                        <table>
                            <thead>
                                <tr><th>Nome</th><th>Categoria</th><th>Valor</th><th>Pontos</th><th>Status</th><th>Ações</th></tr>
                            </thead>
                            <tbody id="servicos-tabela"></tbody>
                        </table>
                    </div>
                </div>
            </div>
            
            <!-- Aba Solicitações -->
            <div id="solicitacoes" class="tab">
                <div class="card">
                    <h2>📋 Solicitações Pendentes</h2>
                    <div class="scroll-table">
                        <table>
                            <thead>
                                <tr><th>Cliente</th><th>UID</th><th>Serviço</th><th>Valor</th><th>Data</th><th>Ações</th></tr>
                            </thead>
                            <tbody id="solicitacoes-pendentes"></tbody>
                        </table>
                    </div>
                </div>
                <div class="card">
                    <h2>📋 Histórico de Solicitações</h2>
                    <div class="scroll-table">
                        <table>
                            <thead>
                                <tr><th>Cliente</th><th>Serviço</th><th>Status</th><th>Data</th><th>Admin</th></tr>
                            </thead>
                            <tbody id="solicitacoes-historico"></tbody>
                        </table>
                    </div>
                </div>
            </div>
            
            <!-- Aba Clientes -->
            <div id="clientes" class="tab">
                <div class="card">
                    <h2>👥 Gerenciar Clientes</h2>
                    <div class="form-group">
                        <label>Buscar Cliente</label>
                        <input type="text" id="buscar-cliente" class="form-control" placeholder="Nome, UID ou Discord ID" onkeyup="filtrarClientes()">
                    </div>
                    <div class="scroll-table">
                        <table>
                            <thead>
                                <tr><th>Discord</th><th>UID</th><th>Nick</th><th>Pontos</th><th>Total</th><th>Ações</th></tr>
                            </thead>
                            <tbody id="clientes-tabela"></tbody>
                        </table>
                    </div>
                </div>
            </div>
            
            <!-- Aba Fidelidade -->
            <div id="fidelidade" class="tab">
                <div class="card">
                    <h2>⭐ Configuração de Fidelidade</h2>
                    <div class="grid-2">
                        <div class="form-group">
                            <label>Pontos por R$ 1,00</label>
                            <input type="number" id="pontos-por-real" class="form-control" value="{fidelidade.get('pontos_por_real', 1)}" step="0.1" min="0.1">
                        </div>
                    </div>
                    <button onclick="salvarConfigFidelidade()" class="btn btn-primary">💾 Salvar Configuração</button>
                    <div id="fidelidade-alert" class="alert"></div>
                </div>
                
                <div class="card">
                    <h2>🎁 Recompensas</h2>
                    <div class="grid-3">
                        <div class="form-group">
                            <label>Nome da Recompensa</label>
                            <input type="text" id="recompensa-nome" class="form-control" placeholder="Ex: Desafio Rápido Grátis">
                        </div>
                        <div class="form-group">
                            <label>Pontos Necessários</label>
                            <input type="number" id="recompensa-pontos" class="form-control" placeholder="100">
                        </div>
                        <div class="form-group">
                            <label>Tipo</label>
                            <select id="recompensa-tipo" class="form-control">
                                <option value="desconto">Desconto</option>
                                <option value="servico">Serviço Grátis</option>
                                <option value="beneficio">Benefício</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Valor/Descrição</label>
                            <input type="text" id="recompensa-valor" class="form-control" placeholder="Ex: R$5 ou '1 Dia de Quests'">
                        </div>
                        <div class="form-group" style="grid-column: span 2;">
                            <label>Descrição Detalhada</label>
                            <textarea id="recompensa-descricao" class="form-control" rows="2" placeholder="Descrição da recompensa"></textarea>
                        </div>
                    </div>
                    <button onclick="adicionarRecompensa()" class="btn btn-success">➕ Adicionar Recompensa</button>
                    <div id="recompensa-alert" class="alert"></div>
                </div>
                
                <div class="card">
                    <h2>📋 Lista de Recompensas</h2>
                    <div class="scroll-table">
                        <table>
                            <thead>
                                <tr><th>Nome</th><th>Pontos</th><th>Tipo</th><th>Valor</th><th>Status</th><th>Ações</th></tr>
                            </thead>
                            <tbody id="recompensas-tabela"></tbody>
                        </table>
                    </div>
                </div>
            </div>
            
            <!-- Aba Fila (existente) -->
            <div id="fila" class="tab">
                <div class="card">
                    <h2>📋 Configurações da Fila</h2>
                    <div class="grid-2">
                        <div><label>Nome da Fila</label><input type="text" id="fila-nome" class="form-control" value="{escape_html(fila['nome'])}"></div>
                        <div><label>Tamanho Máximo</label><input type="number" id="fila-max" class="form-control" value="{fila['configuracoes']['tamanho_maximo']}" min="1" max="100"></div>
                    </div>
                    
                    <h3 style="margin-top: 20px;">🔗 Links do Discord (convite)</h3>
                    <div class="form-group">
                        <label>Link do Discord (convite)</label>
                        <input type="url" id="link-discord" class="form-control" placeholder="https://discord.gg/seuconvite" value="{escape_html(links.get('discord_convite', ''))}">
                    </div>
                    
                    <h3 style="margin-top: 20px;">💰 Botões de Preço (Múltiplos)</h3>
                    <div class="form-group">
                        <label>Novo Botão - Nome</label>
                        <input type="text" id="novo-botao-nome" class="form-control" placeholder="Ex: Tabela de Preços">
                    </div>
                    <div class="form-group">
                        <label>Novo Botão - URL</label>
                        <input type="url" id="novo-botao-url" class="form-control" placeholder="https://docs.google.com/...">
                    </div>
                    <button onclick="adicionarBotaoPreco()" class="btn btn-success">➕ Adicionar Botão</button>
                    
                    <div id="botoes-precos-lista" class="botoes-lista" style="margin-top: 20px;"></div>
                    
                    <div style="display: flex; gap: 1rem; margin-top: 1rem; flex-wrap: wrap;">
                        <button onclick="salvarConfigFila()" class="btn btn-primary">💾 Salvar Configurações</button>
                        <button onclick="alternarStatusFila()" id="toggle-fila-btn" class="btn {'btn-success' if fila['configuracoes']['aberta'] else 'btn-danger'}">{'🔓 Fechar Fila' if fila['configuracoes']['aberta'] else '🔒 Abrir Fila'}</button>
                        <button onclick="limparFila()" class="btn btn-danger">🗑️ Limpar Fila</button>
                    </div>
                    <div id="fila-status" style="margin-top: 1rem; padding: 0.5rem; background: #1a1a1a; border-radius: 5px;">Status: {'🟢 ABERTA' if fila['configuracoes']['aberta'] else '🔴 FECHADA'} | {len(fila['entradas'])}/{fila['configuracoes']['tamanho_maximo']}</div>
                </div>
                
                <div class="card">
                    <h2>📋 Lista de Espera</h2>
                    <div class="scroll-table">
                        <table>
                            <thead>
                                <tr><th>#</th><th>Jogador</th><th>Serviço</th><th>Jogo</th><th>Entrada</th><th>Ações</th></tr>
                            </thead>
                            <tbody id="fila-tabela"><tr><td colspan="6">Carregando...</td></tr></tbody>
                        </table>
                    </div>
                    <div style="margin-top: 10px;"><button onclick="atualizarFila()" class="btn btn-primary">🔄 Atualizar</button></div>
                </div>
            </div>
            
            <!-- Aba Comandos (existente) -->
            <div id="comandos" class="tab">
                <div class="card">
                    <h2>📝 Criar Embed Personalizada</h2>
                    <div class="form-group">
                        <label>Canal</label>
                        <select id="embed-canal" class="form-control"></select>
                    </div>
                    <div class="form-group">
                        <label>Título</label>
                        <input type="text" id="embed-titulo" class="form-control" placeholder="Título da mensagem">
                    </div>
                    <div class="form-group">
                        <label>Corpo da Mensagem</label>
                        <textarea id="embed-corpo" class="form-control" rows="3" placeholder="Conteúdo da mensagem"></textarea>
                    </div>
                    <div class="form-group">
                        <label>Cor (hexadecimal)</label>
                        <input type="text" id="embed-cor" class="form-control" value="#5865F2" placeholder="#5865F2">
                    </div>
                    <div class="form-group">
                        <label>Imagem (URL opcional)</label>
                        <input type="url" id="embed-imagem" class="form-control" placeholder="https://exemplo.com/imagem.jpg">
                    </div>
                    <div class="form-group">
                        <label>Menção</label>
                        <select id="embed-mencao" class="form-control"><option value="">Nenhuma</option><option value="everyone">@everyone</option><option value="here">@here</option></select>
                    </div>
                    <button onclick="criarEmbed()" class="btn btn-primary">📝 Criar Embed</button>
                    <div id="embed-alert" class="alert"></div>
                </div>
            </div>
            
            <!-- Aba Configurações -->
            <div id="configuracoes" class="tab">
                <div class="card">
                    <h2>⚙️ Configurações Gerais</h2>
                    <div class="grid-2">
                        <div>
                            <h3>👋 Boas-vindas</h3>
                            <div class="form-group">
                                <label>Canal de Boas-vindas</label>
                                <select id="welcome-canal" class="form-control"></select>
                            </div>
                            <div class="form-group">
                                <label>Mensagem</label>
                                <textarea id="welcome-mensagem" class="form-control" rows="2"></textarea>
                            </div>
                            <div class="form-group">
                                <label>Imagem de Fundo (URL)</label>
                                <input type="url" id="welcome-imagem" class="form-control" placeholder="https://exemplo.com/imagem.jpg">
                            </div>
                            <button onclick="salvarBoasVindas()" class="btn btn-primary">💾 Salvar</button>
                            <div id="welcome-alert" class="alert"></div>
                        </div>
                        <div>
                            <h3>⭐ Sistema XP</h3>
                            <div class="form-group">
                                <label>Taxa de XP</label>
                                <input type="number" id="xp-taxa" class="form-control" min="1" max="10">
                            </div>
                            <div class="form-group">
                                <label>Canal de Level Up</label>
                                <select id="xp-canal" class="form-control"></select>
                            </div>
                            <button onclick="salvarXP()" class="btn btn-primary">💾 Salvar</button>
                            <div id="xp-alert" class="alert"></div>
                        </div>
                    </div>
                </div>
                
                <div class="card">
                    <h2>🛡️ Anti-Spam</h2>
                    <div class="grid-2">
                        <div class="form-group">
                            <label>Status</label>
                            <label class="switch">
                                <input type="checkbox" id="as-ativado" {'checked' if anti_spam.get('ativado', True) else ''}>
                                <span class="slider"></span>
                            </label>
                        </div>
                        <div class="form-group">
                            <label>Remover XP por Spam</label>
                            <label class="switch">
                                <input type="checkbox" id="as-remover-xp" {'checked' if anti_spam.get('remover_xp', True) else ''}>
                                <span class="slider"></span>
                            </label>
                        </div>
                        <div class="form-group">
                            <label>Deletar Mensagens</label>
                            <label class="switch">
                                <input type="checkbox" id="as-deletar" {'checked' if anti_spam.get('deletar_mensagens', True) else ''}>
                                <span class="slider"></span>
                            </label>
                        </div>
                    </div>
                    <div class="grid-3">
                        <div class="form-group">
                            <label>Limite de Mensagens</label>
                            <input type="number" id="as-limite" class="form-control" value="{anti_spam.get('limite_mensagens', 5)}" min="2" max="20">
                        </div>
                        <div class="form-group">
                            <label>Intervalo (segundos)</label>
                            <input type="number" id="as-intervalo" class="form-control" value="{anti_spam.get('intervalo_segundos', 5)}" min="2" max="30">
                        </div>
                        <div class="form-group">
                            <label>Tempo de Mute (minutos)</label>
                            <input type="number" id="as-mute" class="form-control" value="{anti_spam.get('tempo_mute_minutos', 2)}" min="1" max="60">
                        </div>
                        <div class="form-group">
                            <label>Penalidade de XP</label>
                            <input type="number" id="as-xp-penalidade" class="form-control" value="{anti_spam.get('xp_penalidade', 50)}" min="10" max="500">
                        </div>
                        <div class="form-group">
                            <label>Cargos Ignorados</label>
                            <input type="text" id="as-cargos" class="form-control" value="{','.join(anti_spam.get('cargos_ignorados', ['Administrador', 'Moderador', 'Staff', 'Dono']))}">
                        </div>
                        <div class="form-group">
                            <label>Comandos Ignorados</label>
                            <input type="text" id="as-comandos" class="form-control" value="{','.join(anti_spam.get('comandos_ignorados', ['$w','$wa','$wg','$h','$ha','$hg','$tu','$dk','$mmi','$vote','$rolls','$k','$mu']))}">
                        </div>
                    </div>
                    <button onclick="salvarAntiSpam()" class="btn btn-primary">💾 Salvar</button>
                    <div id="as-alert" class="alert"></div>
                </div>
                
                <div class="card">
                    <h2>🔗 Bloqueio de Links</h2>
                    <div class="form-group">
                        <label>Canal para bloquear links</label>
                        <select id="links-canal" class="form-control"></select>
                    </div>
                    <button onclick="alternarBloqueioLinks()" class="btn btn-danger">🔒 Alternar Bloqueio</button>
                    <div id="links-status" style="margin-top: 1rem; padding: 0.5rem; background: #1a1a1a; border-radius: 5px;"></div>
                    <div id="links-alert" class="alert"></div>
                </div>
            </div>
        </div>
        
        <!-- Modal para editar cliente -->
        <div id="modal-cliente" class="modal">
            <div class="modal-content">
                <button class="modal-close" onclick="fecharModal('modal-cliente')">×</button>
                <h2 id="modal-cliente-titulo">Editar Cliente</h2>
                <div id="modal-cliente-conteudo"></div>
            </div>
        </div>
        
        <script>
            let canais = [];
            let cargos = [];
            let membros = [];
            let configAtual = {{}};
            let botoesPrecos = {botoes_precos_json};
            let recompensas = {recompensas_json};
            let servicosLista = {servicos_json};
            let solicitacoesPendentes = {solicitacoes_pendentes};
            
            async function carregarDados() {{
                try {{
                    const [canaisRes, cargosRes, membrosRes, configBoasVindas, configXP, linksRes, antiSpamRes, configComandosRes, filaConfigRes] = await Promise.all([
                        fetch('/api/servidor/canais'),
                        fetch('/api/servidor/cargos'),
                        fetch('/api/servidor/membros'),
                        fetch('/api/config/boasvindas'),
                        fetch('/api/config/xp'),
                        fetch('/api/config/links'),
                        fetch('/api/anti_spam'),
                        fetch('/api/config/comandos'),
                        fetch('/api/fila/configuracoes')
                    ]);
                    
                    const canaisData = await canaisRes.json();
                    const cargosData = await cargosRes.json();
                    const membrosData = await membrosRes.json();
                    const configBV = await configBoasVindas.json();
                    const configXPdata = await configXP.json();
                    const linksData = await linksRes.json();
                    const antiSpamData = await antiSpamRes.json();
                    const configComandosData = await configComandosRes.json();
                    const filaConfig = await filaConfigRes.json();
                    
                    if (canaisData.sucesso) canais = canaisData.canais;
                    if (cargosData.sucesso) cargos = cargosData.cargos;
                    if (membrosData.sucesso) membros = membrosData.membros;
                    
                    popularSelects();
                    
                    if (configBV.sucesso) {{
                        document.getElementById('welcome-mensagem').value = configBV.mensagem || '';
                        document.getElementById('welcome-imagem').value = configBV.imagem || '';
                        const welcomeCanal = document.getElementById('welcome-canal');
                        if (welcomeCanal) welcomeCanal.value = configBV.canal || '';
                    }}
                    
                    if (configXPdata.sucesso) {{
                        document.getElementById('xp-taxa').value = configXPdata.taxa || 3;
                        const xpCanal = document.getElementById('xp-canal');
                        if (xpCanal) xpCanal.value = configXPdata.canal || '';
                    }}
                    
                    if (configComandosData.sucesso) {{
                        configAtual = configComandosData;
                    }}
                    
                    if (linksData.sucesso && linksData.canais) {{
                        const linksStatus = document.getElementById('links-status');
                        if (linksStatus) {{
                            const nomes = linksData.canais.map(c => {{
                                const canal = canais.find(ca => ca.id == c);
                                return canal ? '#' + canal.nome : c;
                            }}).join(', ');
                            linksStatus.innerHTML = nomes ? 'Canais bloqueados: ' + nomes : 'Nenhum canal bloqueado';
                        }}
                    }}
                    
                    if (antiSpamData.sucesso && antiSpamData.config) {{
                        document.getElementById('as-ativado').checked = antiSpamData.config.ativado;
                        document.getElementById('as-remover-xp').checked = antiSpamData.config.remover_xp;
                        document.getElementById('as-deletar').checked = antiSpamData.config.deletar_mensagens;
                        document.getElementById('as-limite').value = antiSpamData.config.limite_mensagens;
                        document.getElementById('as-intervalo').value = antiSpamData.config.intervalo_segundos;
                        document.getElementById('as-mute').value = antiSpamData.config.tempo_mute_minutos;
                        document.getElementById('as-xp-penalidade').value = antiSpamData.config.xp_penalidade;
                        document.getElementById('as-cargos').value = antiSpamData.config.cargos_ignorados;
                        document.getElementById('as-comandos').value = antiSpamData.config.comandos_ignorados;
                    }}
                    
                    if (filaConfig.sucesso && filaConfig.links) {{
                        document.getElementById('link-discord').value = filaConfig.links.discord_convite || '';
                        if (filaConfig.links.botoes_precos) {{
                            botoesPrecos = filaConfig.links.botoes_precos;
                        }}
                    }}
                    
                    await carregarFila();
                    carregarServicos();
                    carregarSolicitacoes();
                    carregarClientes();
                    carregarRecompensas();
                    carregarBotoesPrecos();
                }} catch(e) {{ console.error(e); }}
            }}
            
            function popularSelects() {{
                const selects = ['welcome-canal', 'xp-canal', 'embed-canal', 'links-canal'];
                selects.forEach(id => {{
                    const select = document.getElementById(id);
                    if (select) {{
                        select.innerHTML = '<option value="">Selecione um canal</option>';
                        canais.forEach(c => {{
                            const option = document.createElement('option');
                            option.value = c.id;
                            option.textContent = '#' + c.nome;
                            select.appendChild(option);
                        }});
                    }}
                }});
            }}
            
            function showTab(tabId) {{
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                document.getElementById(tabId).classList.add('active');
                event.target.classList.add('active');
                if (tabId === 'fila') carregarFila();
                if (tabId === 'servicos') carregarServicos();
                if (tabId === 'solicitacoes') carregarSolicitacoes();
                if (tabId === 'clientes') carregarClientes();
                if (tabId === 'fidelidade') carregarRecompensas();
            }}
            
            function showAlert(id, msg, sucesso) {{
                const el = document.getElementById(id);
                if (!el) return;
                el.textContent = msg;
                el.className = 'alert ' + (sucesso ? 'alert-success' : 'alert-error');
                el.style.display = 'block';
                setTimeout(() => el.style.display = 'none', 5000);
            }}
            
            function escapeHtml(texto) {{ if (!texto) return ''; return texto.replace(/[&<>]/g, function(m) {{ if (m === '&') return '&amp;'; if (m === '<') return '&lt;'; if (m === '>') return '&gt;'; return m; }}); }}
            
            function openModal(id) {{ document.getElementById(id).classList.add('active'); }}
            function fecharModal(id) {{ document.getElementById(id).classList.remove('active'); }}
            
            // ========================
            // FUNÇÕES DA FILA
            // ========================
            
            async function carregarFila() {{
                try {{
                    const resp = await fetch('/fila/api');
                    const data = await resp.json();
                    if (data.sucesso) {{
                        const fila = data.fila;
                        const tbody = document.getElementById('fila-tabela');
                        if (fila.entradas.length === 0) {{
                            tbody.innerHTML = '<tr><td colspan="6">📭 Ninguém na fila</td></tr>';
                        }} else {{
                            tbody.innerHTML = fila.entradas.map(e => `
                                <tr>
                                    <td><strong style="color:#ffd93d;">#${{e.posicao}}</strong></td>
                                    <td>${{escapeHtml(e.nome_usuario)}}</td>
                                    <td>${{escapeHtml(e.servico)}}</td>
                                    <td>${{escapeHtml(e.jogo || '')}}</td>
                                    <td>${{new Date(e.timestamp).toLocaleTimeString()}}</td>
                                    <td>
                                        <button onclick="moverCima('${{e.id}}')" class="btn btn-primary btn-xs">⬆️</button>
                                        <button onclick="moverBaixo('${{e.id}}')" class="btn btn-primary btn-xs">⬇️</button>
                                        <button onclick="concluir('${{e.id}}')" class="btn btn-success btn-xs">✅</button>
                                        <button onclick="remover('${{e.id}}')" class="btn btn-danger btn-xs">❌</button>
                                    </td>
                                </tr>
                            `).join('');
                        }}
                        const filaStatus = document.getElementById('fila-status');
                        if (filaStatus) {{
                            filaStatus.innerHTML = `Status: ${{fila.aberta ? '🟢 ABERTA' : '🔴 FECHADA'}} | ${{fila.contagem}}/${{fila.tamanho_maximo}}`;
                        }}
                        const toggleBtn = document.getElementById('toggle-fila-btn');
                        if (toggleBtn) {{
                            toggleBtn.className = fila.aberta ? 'btn btn-danger' : 'btn btn-success';
                            toggleBtn.textContent = fila.aberta ? '🔓 Fechar Fila' : '🔒 Abrir Fila';
                        }}
                    }}
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function remover(id) {{ if (confirm('Remover?')) {{ await fetch('/api/fila/remover', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{entrada_id:id}})}}); carregarFila(); }} }}
            async function moverCima(id) {{ await fetch('/api/fila/mover-cima', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{entrada_id:id}})}}); carregarFila(); }}
            async function moverBaixo(id) {{ await fetch('/api/fila/mover-baixo', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{entrada_id:id}})}}); carregarFila(); }}
            async function concluir(id) {{ if (confirm('Concluir serviço?')) {{ await fetch('/api/fila/concluir', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{entrada_id:id}})}}); carregarFila(); }} }}
            async function limparFila() {{ if (confirm('LIMPAR TODA A FILA?')) {{ await fetch('/api/fila/limpar', {{method:'POST'}}); carregarFila(); }} }}
            async function salvarConfigFila() {{ 
                const data = {{
                    nome: document.getElementById('fila-nome').value,
                    tamanho_maximo: parseInt(document.getElementById('fila-max').value),
                    discord_convite: document.getElementById('link-discord').value
                }};
                await fetch('/api/fila/configuracoes', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(data)}});
                carregarFila();
                showAlert('fila-status', 'Configurações salvas!', true);
            }}
            async function alternarStatusFila() {{ await fetch('/api/fila/configuracoes', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{aberta:null}})}}); carregarFila(); }}
            function atualizarFila() {{ carregarFila(); }}
            
            // ========================
            // FUNÇÕES DOS BOTÕES DE PREÇO
            // ========================
            
            function carregarBotoesPrecos() {{
                const container = document.getElementById('botoes-precos-lista');
                if (!container) return;
                
                if (botoesPrecos.length === 0) {{
                    container.innerHTML = '<div style="text-align:center;padding:20px;color:#888;">Nenhum botão de preço configurado.</div>';
                    return;
                }}
                
                let html = '';
                botoesPrecos.forEach((botao, index) => {{
                    html += `
                        <div class="botao-item">
                            <div class="botao-info">
                                <div class="botao-nome">💰 ${{escapeHtml(botao.nome)}}</div>
                                <div class="botao-url">${{escapeHtml(botao.url)}}</div>
                            </div>
                            <div class="botao-acoes">
                                <button onclick="editarBotaoPreco(${{index}})" class="btn btn-primary btn-sm">✏️ Editar</button>
                                <button onclick="removerBotaoPreco(${{index}})" class="btn btn-danger btn-sm">🗑️ Remover</button>
                            </div>
                        </div>
                    `;
                }});
                container.innerHTML = html;
            }}
            
            async function adicionarBotaoPreco() {{
                const nome = document.getElementById('novo-botao-nome').value.trim();
                const url = document.getElementById('novo-botao-url').value.trim();
                
                if (!nome || !url) {{
                    showAlert('fila-status', 'Preencha nome e URL', false);
                    return;
                }}
                
                try {{
                    const resp = await fetch('/api/fila/botoes/adicionar', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{nome, url}})
                    }});
                    const result = await resp.json();
                    if (result.sucesso) {{
                        document.getElementById('novo-botao-nome').value = '';
                        document.getElementById('novo-botao-url').value = '';
                        await carregarBotoesNovamente();
                        showAlert('fila-status', result.mensagem, true);
                    }} else {{
                        showAlert('fila-status', result.mensagem, false);
                    }}
                }} catch(e) {{
                    showAlert('fila-status', 'Erro: ' + e.message, false);
                }}
            }}
            
            async function carregarBotoesNovamente() {{
                try {{
                    const resp = await fetch('/api/fila/botoes');
                    const data = await resp.json();
                    if (data.sucesso) {{
                        botoesPrecos = data.botoes;
                        carregarBotoesPrecos();
                    }}
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function removerBotaoPreco(index) {{
                if (!confirm('Remover este botão?')) return;
                try {{
                    const resp = await fetch('/api/fila/botoes/remover', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{index}})
                    }});
                    const result = await resp.json();
                    if (result.sucesso) {{
                        await carregarBotoesNovamente();
                        showAlert('fila-status', result.mensagem, true);
                    }} else {{
                        showAlert('fila-status', result.mensagem, false);
                    }}
                }} catch(e) {{
                    showAlert('fila-status', 'Erro: ' + e.message, false);
                }}
            }}
            
            function editarBotaoPreco(index) {{
                const botao = botoesPrecos[index];
                const novoNome = prompt('Digite o novo nome do botão:', botao.nome);
                if (!novoNome) return;
                const novaUrl = prompt('Digite a nova URL:', botao.url);
                if (!novaUrl) return;
                
                fetch('/api/fila/botoes/atualizar', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{index, nome: novoNome, url: novaUrl}})
                }}).then(async (resp) => {{
                    const result = await resp.json();
                    if (result.sucesso) {{
                        await carregarBotoesNovamente();
                        showAlert('fila-status', result.mensagem, true);
                    }} else {{
                        showAlert('fila-status', result.mensagem, false);
                    }}
                }}).catch(e => showAlert('fila-status', 'Erro: ' + e.message, false));
            }}
            
            // ========================
            // FUNÇÕES DE SERVIÇOS
            // ========================
            
            async function carregarServicos() {{
                try {{
                    const resp = await fetch('/api/servicos');
                    const data = await resp.json();
                    if (data.sucesso) {{
                        servicosLista = data.servicos;
                        const tbody = document.getElementById('servicos-tabela');
                        if (servicosLista.length === 0) {{
                            tbody.innerHTML = '<tr><td colspan="6">Nenhum serviço cadastrado</td></tr>';
                        }} else {{
                            tbody.innerHTML = servicosLista.map(s => `
                                <tr>
                                    <td>${{escapeHtml(s.nome)}}</td>
                                    <td>${{escapeHtml(s.categoria || '')}}</td>
                                    <td>R$ ${{s.valor}}</td>
                                    <td>${{s.pontos}}</td>
                                    <td><span class="status-badge status-${{s.status}}">${{s.status || 'ativo'}}</span></td>
                                    <td>
                                        <button onclick="editarServico('${{s.id}}')" class="btn btn-primary btn-xs">✏️</button>
                                        <button onclick="toggleServico('${{s.id}}')" class="btn btn-warning btn-xs">${{s.status === 'ativo' ? '🔇' : '🔊'}}</button>
                                        <button onclick="excluirServico('${{s.id}}')" class="btn btn-danger btn-xs">🗑️</button>
                                    </td>
                                </tr>
                            `).join('');
                        }}
                    }}
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function criarServico() {{
                const data = {{
                    nome: document.getElementById('servico-nome').value.trim(),
                    categoria: document.getElementById('servico-categoria').value.trim(),
                    descricao: document.getElementById('servico-descricao').value.trim(),
                    valor: parseFloat(document.getElementById('servico-valor').value) || 0,
                    pontos: parseInt(document.getElementById('servico-pontos').value) || 0,
                    imagem: document.getElementById('servico-imagem').value.trim(),
                    status: document.getElementById('servico-status').value
                }};
                
                if (!data.nome) {{
                    showAlert('servico-alert', 'Nome do serviço é obrigatório', false);
                    return;
                }}
                
                try {{
                    const resp = await fetch('/api/servicos', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify(data)
                    }});
                    const result = await resp.json();
                    if (result.sucesso) {{
                        document.getElementById('servico-nome').value = '';
                        document.getElementById('servico-categoria').value = '';
                        document.getElementById('servico-descricao').value = '';
                        document.getElementById('servico-valor').value = '';
                        document.getElementById('servico-pontos').value = '';
                        document.getElementById('servico-imagem').value = '';
                        carregarServicos();
                        showAlert('servico-alert', 'Serviço criado com sucesso!', true);
                    }} else {{
                        showAlert('servico-alert', result.mensagem || 'Erro ao criar serviço', false);
                    }}
                }} catch(e) {{
                    showAlert('servico-alert', 'Erro: ' + e.message, false);
                }}
            }}
            
            function editarServico(id) {{
                const servico = servicosLista.find(s => s.id === id);
                if (!servico) return;
                
                const novoNome = prompt('Nome do serviço:', servico.nome);
                if (novoNome !== null) servico.nome = novoNome;
                
                const novaCategoria = prompt('Categoria:', servico.categoria || '');
                if (novaCategoria !== null) servico.categoria = novaCategoria;
                
                const novaDescricao = prompt('Descrição:', servico.descricao || '');
                if (novaDescricao !== null) servico.descricao = novaDescricao;
                
                const novoValor = prompt('Valor (R$):', servico.valor);
                if (novoValor !== null) servico.valor = parseFloat(novoValor) || 0;
                
                const novosPontos = prompt('Pontos:', servico.pontos);
                if (novosPontos !== null) servico.pontos = parseInt(novosPontos) || 0;
                
                fetch(`/api/servicos/${{id}}`, {{
                    method: 'PUT',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify(servico)
                }}).then(async (resp) => {{
                    const result = await resp.json();
                    if (result.sucesso) {{
                        carregarServicos();
                        showAlert('servico-alert', 'Serviço atualizado!', true);
                    }} else {{
                        showAlert('servico-alert', result.mensagem || 'Erro ao atualizar', false);
                    }}
                }}).catch(e => showAlert('servico-alert', 'Erro: ' + e.message, false));
            }}
            
            async function toggleServico(id) {{
                const servico = servicosLista.find(s => s.id === id);
                if (!servico) return;
                
                const novoStatus = servico.status === 'ativo' ? 'inativo' : 'ativo';
                servico.status = novoStatus;
                
                try {{
                    const resp = await fetch(`/api/servicos/${{id}}`, {{
                        method: 'PUT',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{status: novoStatus}})
                    }});
                    const result = await resp.json();
                    if (result.sucesso) {{
                        carregarServicos();
                        const msg = `Serviço ${novoStatus === 'ativo' ? 'ativado' : 'desativado'}!`;
                        showAlert('servico-alert', msg, true);
                    }}
                }} catch(e) {{ showAlert('servico-alert', 'Erro: ' + e.message, false); }}
            }}
            
            async function excluirServico(id) {{
                if (!confirm('Excluir este serviço?')) return;
                try {{
                    const resp = await fetch(`/api/servicos/${{id}}`, {{method: 'DELETE'}});
                    const result = await resp.json();
                    if (result.sucesso) {{
                        carregarServicos();
                        showAlert('servico-alert', 'Serviço excluído!', true);
                    }}
                }} catch(e) {{ showAlert('servico-alert', 'Erro: ' + e.message, false); }}
            }}
            
            // ========================
            // FUNÇÕES DE SOLICITAÇÕES
            // ========================
            
            async function carregarSolicitacoes() {{
                try {{
                    const resp = await fetch('/api/solicitacoes');
                    const data = await resp.json();
                    if (data.sucesso) {{
                        const solicitacoes = data.solicitacoes;
                        
                        // Pendentes
                        const pendentes = solicitacoes.filter(s => s.status === 'pendente');
                        const tbodyPendentes = document.getElementById('solicitacoes-pendentes');
                        if (pendentes.length === 0) {{
                            tbodyPendentes.innerHTML = '<tr><td colspan="6">Nenhuma solicitação pendente</td></tr>';
                        }} else {{
                            tbodyPendentes.innerHTML = pendentes.map(s => `
                                <tr>
                                    <td>${{escapeHtml(s.cliente_nome || '')}}</td>
                                    <td>${{escapeHtml(s.cliente_uid || '')}}</td>
                                    <td>${{escapeHtml(s.servico_nome || '')}}</td>
                                    <td>R$ ${{s.servico_valor || 0}}</td>
                                    <td>${{new Date(s.data_solicitacao).toLocaleDateString()}}</td>
                                    <td>
                                        <button onclick="aprovarSolicitacao('${{s.id}}')" class="btn btn-success btn-xs">✅ Aceitar</button>
                                        <button onclick="recusarSolicitacao('${{s.id}}')" class="btn btn-danger btn-xs">❌ Recusar</button>
                                    </td>
                                </tr>
                            `).join('');
                        }}
                        
                        // Histórico
                        const historico = solicitacoes.filter(s => s.status !== 'pendente');
                        const tbodyHistorico = document.getElementById('solicitacoes-historico');
                        if (historico.length === 0) {{
                            tbodyHistorico.innerHTML = '<tr><td colspan="5">Nenhum histórico</td></tr>';
                        }} else {{
                            tbodyHistorico.innerHTML = historico.slice(0, 20).map(s => `
                                <tr>
                                    <td>${{escapeHtml(s.cliente_nome || '')}}</td>
                                    <td>${{escapeHtml(s.servico_nome || '')}}</td>
                                    <td><span class="status-badge status-${{s.status}}">${{s.status}}</span></td>
                                    <td>${{new Date(s.data_solicitacao).toLocaleDateString()}}</td>
                                    <td>${{escapeHtml(s.admin_aprovacao || s.admin_recusa || s.admin_conclusao || '-')}}</td>
                                </tr>
                            `).join('');
                        }}
                    }}
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function aprovarSolicitacao(id) {{
                if (!confirm('Aprovar esta solicitação? Ela será adicionada à fila.')) return;
                try {{
                    const resp = await fetch(`/api/solicitacoes/${{id}}/aprovar`, {{method: 'POST'}});
                    const result = await resp.json();
                    showAlert('solicitacoes-pendentes', result.mensagem, result.sucesso);
                    if (result.sucesso) {{
                        carregarSolicitacoes();
                        carregarFila();
                    }}
                }} catch(e) {{ showAlert('solicitacoes-pendentes', 'Erro: ' + e.message, false); }}
            }}
            
            async function recusarSolicitacao(id) {{
                const motivo = prompt('Motivo da recusa:');
                if (motivo === null) return;
                try {{
                    const resp = await fetch(`/api/solicitacoes/${{id}}/recusar`, {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{motivo: motivo || 'Sem motivo'}})
                    }});
                    const result = await resp.json();
                    showAlert('solicitacoes-pendentes', result.mensagem, result.sucesso);
                    if (result.sucesso) carregarSolicitacoes();
                }} catch(e) {{ showAlert('solicitacoes-pendentes', 'Erro: ' + e.message, false); }}
            }}
            
            // ========================
            // FUNÇÕES DE CLIENTES
            // ========================
            
            async function carregarClientes() {{
                try {{
                    const resp = await fetch('/api/admin/clientes');
                    const data = await resp.json();
                    if (data.sucesso) {{
                        const tbody = document.getElementById('clientes-tabela');
                        if (data.clientes.length === 0) {{
                            tbody.innerHTML = '<tr><td colspan="6">Nenhum cliente cadastrado</td></tr>';
                        }} else {{
                            tbody.innerHTML = data.clientes.map(c => `
                                <tr class="cliente-row" data-nome="${{c.discord_nome || ''}}" data-uid="${{c.uid || ''}}" data-id="${{c.discord_id}}">
                                    <td>${{escapeHtml(c.discord_nome || '')}}</td>
                                    <td>${{escapeHtml(c.uid || '')}}</td>
                                    <td>${{escapeHtml(c.nick_jogo || '')}}</td>
                                    <td><strong style="color:#f59e0b;">${{c.pontos || 0}}</strong></td>
                                    <td>${{c.total_acumulado || 0}}</td>
                                    <td>
                                        <button onclick="verCliente('${{c.discord_id}}')" class="btn btn-primary btn-xs">👁️</button>
                                        <button onclick="editarCliente('${{c.discord_id}}')" class="btn btn-warning btn-xs">✏️</button>
                                    </td>
                                </tr>
                            `).join('');
                        }}
                    }}
                }} catch(e) {{ console.error(e); }}
            }}
            
            function filtrarClientes() {{
                const termo = document.getElementById('buscar-cliente').value.toLowerCase();
                const rows = document.querySelectorAll('.cliente-row');
                rows.forEach(row => {{
                    const nome = row.dataset.nome?.toLowerCase() || '';
                    const uid = row.dataset.uid?.toLowerCase() || '';
                    const id = row.dataset.id || '';
                    if (nome.includes(termo) || uid.includes(termo) || id.includes(termo)) {{
                        row.style.display = '';
                    }} else {{
                        row.style.display = 'none';
                    }}
                }});
            }}
            
            async function verCliente(discordId) {{
                try {{
                    const resp = await fetch(`/api/admin/clientes/${{discordId}}`);
                    const data = await resp.json();
                    if (data.sucesso) {{
                        const c = data.cliente;
                        document.getElementById('modal-cliente-titulo').textContent = `👤 Cliente: ${{c.discord_nome || ''}}`;
                        document.getElementById('modal-cliente-conteudo').innerHTML = `
                            <div class="grid-2">
                                <div><strong>Discord ID:</strong> ${{c.discord_id}}</div>
                                <div><strong>UID:</strong> ${{escapeHtml(c.uid || '')}}</div>
                                <div><strong>Nick do Jogo:</strong> ${{escapeHtml(c.nick_jogo || '')}}</div>
                                <div><strong>Pontos:</strong> <strong style="color:#f59e0b;">${{c.pontos || 0}}</strong></div>
                                <div><strong>Total Acumulado:</strong> ${{c.total_acumulado || 0}}</div>
                                <div><strong>Total Utilizado:</strong> ${{c.total_utilizado || 0}}</div>
                                <div><strong>Última Compra:</strong> ${{c.ultima_compra ? new Date(c.ultima_compra).toLocaleDateString() : '-'}}</div>
                                <div><strong>Último Resgate:</strong> ${{c.ultimo_resgate ? new Date(c.ultimo_resgate).toLocaleDateString() : '-'}}</div>
                            </div>
                            <div style="margin-top: 15px;">
                                <h4>Serviços em Andamento: ${{(c.servicos_andamento || []).length}}</h4>
                                ${{(c.servicos_andamento || []).map(s => `<div>• ${{escapeHtml(s.servico)}} - ${{new Date(s.data).toLocaleDateString()}}</div>`).join('') || '<div>Nenhum</div>'}}
                            </div>
                            <div style="margin-top: 15px;">
                                <h4>Últimos Históricos:</h4>
                                ${{(c.historico || []).slice(-5).reverse().map(h => `<div>• ${{h.tipo}} - ${{h.data ? new Date(h.data).toLocaleDateString() : '-'}}</div>`).join('') || '<div>Nenhum</div>'}}
                            </div>
                            <div style="margin-top: 15px; display: flex; gap: 10px; flex-wrap: wrap;">
                                <button onclick="adicionarPontos('${{c.discord_id}}')" class="btn btn-success">➕ Adicionar Pontos</button>
                                <button onclick="removerPontos('${{c.discord_id}}')" class="btn btn-danger">➖ Remover Pontos</button>
                            </div>
                        `;
                        openModal('modal-cliente');
                    }}
                }} catch(e) {{ console.error(e); }}
            }}
            
            function editarCliente(discordId) {{
                const novoNick = prompt('Novo Nick do Jogo:');
                if (novoNick !== null) {{
                    fetch(`/api/admin/clientes/${{discordId}}`, {{
                        method: 'PUT',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{nick_jogo: novoNick}})
                    }}).then(async (resp) => {{
                        const result = await resp.json();
                        if (result.sucesso) {{
                            carregarClientes();
                            showAlert('clientes', 'Nick atualizado!', true);
                        }}
                    }});
                }}
            }}
            
            async function adicionarPontos(discordId) {{
                const pontos = prompt('Quantos pontos adicionar?');
                if (!pontos || isNaN(pontos)) return;
                const motivo = prompt('Motivo:') || 'Admin';
                try {{
                    const resp = await fetch(`/api/admin/clientes/${{discordId}}/pontos`, {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{acao: 'adicionar', pontos: parseInt(pontos), motivo}})
                    }});
                    const result = await resp.json();
                    showAlert('modal-cliente-conteudo', result.mensagem, result.sucesso);
                    if (result.sucesso) {{
                        setTimeout(() => {{
                            verCliente(discordId);
                            carregarClientes();
                        }}, 1000);
                    }}
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function removerPontos(discordId) {{
                const pontos = prompt('Quantos pontos remover?');
                if (!pontos || isNaN(pontos)) return;
                const motivo = prompt('Motivo:') || 'Admin';
                try {{
                    const resp = await fetch(`/api/admin/clientes/${{discordId}}/pontos`, {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{acao: 'remover', pontos: parseInt(pontos), motivo}})
                    }});
                    const result = await resp.json();
                    showAlert('modal-cliente-conteudo', result.mensagem, result.sucesso);
                    if (result.sucesso) {{
                        setTimeout(() => {{
                            verCliente(discordId);
                            carregarClientes();
                        }}, 1000);
                    }}
                }} catch(e) {{ console.error(e); }}
            }}
            
            // ========================
            // FUNÇÕES DE FIDELIDADE
            // ========================
            
            async function carregarRecompensas() {{
                try {{
                    const resp = await fetch('/api/fidelidade/recompensas');
                    const data = await resp.json();
                    if (data.sucesso) {{
                        recompensas = data.recompensas;
                        const tbody = document.getElementById('recompensas-tabela');
                        if (recompensas.length === 0) {{
                            tbody.innerHTML = '<tr><td colspan="6">Nenhuma recompensa cadastrada</td></tr>';
                        }} else {{
                            tbody.innerHTML = recompensas.map(r => `
                                <tr>
                                    <td>${{escapeHtml(r.nome || '')}}</td>
                                    <td><strong style="color:#f59e0b;">${{r.pontos || 0}}</strong></td>
                                    <td>${{r.tipo || 'desconto'}}</td>
                                    <td>${{escapeHtml(r.valor || '')}}</td>
                                    <td><span class="status-badge status-${{r.status || 'ativo'}}">${{r.status || 'ativo'}}</span></td>
                                    <td>
                                        <button onclick="editarRecompensa('${{r.id}}')" class="btn btn-primary btn-xs">✏️</button>
                                        <button onclick="excluirRecompensa('${{r.id}}')" class="btn btn-danger btn-xs">🗑️</button>
                                    </td>
                                </tr>
                            `).join('');
                        }}
                    }}
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function adicionarRecompensa() {{
                const data = {{
                    nome: document.getElementById('recompensa-nome').value.trim(),
                    pontos: parseInt(document.getElementById('recompensa-pontos').value) || 0,
                    tipo: document.getElementById('recompensa-tipo').value,
                    valor: document.getElementById('recompensa-valor').value.trim(),
                    descricao: document.getElementById('recompensa-descricao').value.trim()
                }};
                
                if (!data.nome || data.pontos <= 0) {{
                    showAlert('recompensa-alert', 'Nome e pontos são obrigatórios', false);
                    return;
                }}
                
                try {{
                    const resp = await fetch('/api/fidelidade/recompensas', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify(data)
                    }});
                    const result = await resp.json();
                    if (result.sucesso) {{
                        document.getElementById('recompensa-nome').value = '';
                        document.getElementById('recompensa-pontos').value = '';
                        document.getElementById('recompensa-valor').value = '';
                        document.getElementById('recompensa-descricao').value = '';
                        carregarRecompensas();
                        showAlert('recompensa-alert', 'Recompensa adicionada!', true);
                    }} else {{
                        showAlert('recompensa-alert', result.mensagem || 'Erro', false);
                    }}
                }} catch(e) {{ showAlert('recompensa-alert', 'Erro: ' + e.message, false); }}
            }}
            
            function editarRecompensa(id) {{
                const recompensa = recompensas.find(r => r.id === id);
                if (!recompensa) return;
                
                const novoNome = prompt('Nome:', recompensa.nome);
                if (novoNome !== null) recompensa.nome = novoNome;
                
                const novosPontos = prompt('Pontos:', recompensa.pontos);
                if (novosPontos !== null) recompensa.pontos = parseInt(novosPontos) || 0;
                
                const novoValor = prompt('Valor:', recompensa.valor || '');
                if (novoValor !== null) recompensa.valor = novoValor;
                
                const novaDescricao = prompt('Descrição:', recompensa.descricao || '');
                if (novaDescricao !== null) recompensa.descricao = novaDescricao;
                
                fetch(`/api/fidelidade/recompensas/${{id}}`, {{
                    method: 'PUT',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify(recompensa)
                }}).then(async (resp) => {{
                    const result = await resp.json();
                    if (result.sucesso) {{
                        carregarRecompensas();
                        showAlert('recompensa-alert', 'Recompensa atualizada!', true);
                    }}
                }}).catch(e => showAlert('recompensa-alert', 'Erro: ' + e.message, false));
            }}
            
            async function excluirRecompensa(id) {{
                if (!confirm('Excluir esta recompensa?')) return;
                try {{
                    const resp = await fetch(`/api/fidelidade/recompensas?id=${{id}}`, {{method: 'DELETE'}});
                    const result = await resp.json();
                    if (result.sucesso) {{
                        carregarRecompensas();
                        showAlert('recompensa-alert', 'Recompensa excluída!', true);
                    }}
                }} catch(e) {{ showAlert('recompensa-alert', 'Erro: ' + e.message, false); }}
            }}
            
            async function salvarConfigFidelidade() {{
                const pontosPorReal = parseFloat(document.getElementById('pontos-por-real').value);
                if (!pontosPorReal || pontosPorReal <= 0) {{
                    showAlert('fidelidade-alert', 'Valor inválido', false);
                    return;
                }}
                try {{
                    const resp = await fetch('/api/fidelidade/config', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{pontos_por_real: pontosPorReal}})
                    }});
                    const result = await resp.json();
                    showAlert('fidelidade-alert', result.mensagem, result.sucesso);
                }} catch(e) {{ showAlert('fidelidade-alert', 'Erro: ' + e.message, false); }}
            }}
            
            // ========================
            // FUNÇÕES DE CONFIGURAÇÕES
            // ========================
            
            async function salvarAntiSpam() {{
                const data = {{
                    ativado: document.getElementById('as-ativado').checked,
                    remover_xp: document.getElementById('as-remover-xp').checked,
                    deletar_mensagens: document.getElementById('as-deletar').checked,
                    limite_mensagens: parseInt(document.getElementById('as-limite').value),
                    intervalo_segundos: parseInt(document.getElementById('as-intervalo').value),
                    tempo_mute_minutos: parseInt(document.getElementById('as-mute').value),
                    xp_penalidade: parseInt(document.getElementById('as-xp-penalidade').value),
                    cargos_ignorados: document.getElementById('as-cargos').value,
                    comandos_ignorados: document.getElementById('as-comandos').value
                }};
                try {{
                    const resp = await fetch('/api/anti_spam', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(data)}});
                    const result = await resp.json();
                    showAlert('as-alert', result.mensagem, result.sucesso);
                }} catch(e) {{ showAlert('as-alert', 'Erro: ' + e.message, false); }}
            }}
            
            async function salvarBoasVindas() {{
                const data = {{
                    canal_id: document.getElementById('welcome-canal').value,
                    mensagem: document.getElementById('welcome-mensagem').value,
                    imagem_url: document.getElementById('welcome-imagem').value
                }};
                try {{
                    const resp = await fetch('/api/config/boasvindas', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(data)}});
                    const result = await resp.json();
                    showAlert('welcome-alert', result.mensagem, result.sucesso);
                }} catch(e) {{ showAlert('welcome-alert', 'Erro: ' + e.message, false); }}
            }}
            
            async function salvarXP() {{
                const data = {{ taxa: parseInt(document.getElementById('xp-taxa').value), canal_id: document.getElementById('xp-canal').value }};
                try {{
                    const resp = await fetch('/api/config/xp', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(data)}});
                    const result = await resp.json();
                    showAlert('xp-alert', result.mensagem, result.sucesso);
                }} catch(e) {{ showAlert('xp-alert', 'Erro: ' + e.message, false); }}
            }}
            
            async function alternarBloqueioLinks() {{
                const canalId = document.getElementById('links-canal').value;
                if (!canalId) {{ 
                    showAlert('links-alert', 'Selecione um canal', false);
                    return; 
                }}
                try {{
                    const resp = await fetch('/api/config/links', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{canal_id: canalId}})}});
                    const result = await resp.json();
                    if (result.sucesso) {{
                        const linksRes = await fetch('/api/config/links');
                        const linksData = await linksRes.json();
                        const nomes = linksData.canais.map(c => {{
                            const canal = canais.find(ca => ca.id == c);
                            return canal ? '#' + canal.nome : c;
                        }}).join(', ');
                        document.getElementById('links-status').innerHTML = nomes ? 'Canais bloqueados: ' + nomes : 'Nenhum canal bloqueado';
                        showAlert('links-alert', result.mensagem, true);
                    }}
                }} catch(e) {{ showAlert('links-alert', 'Erro: ' + e.message, false); }}
            }}
            
            async function criarEmbed() {{
                const data = {{
                    canal_id: document.getElementById('embed-canal').value,
                    titulo: document.getElementById('embed-titulo').value,
                    corpo: document.getElementById('embed-corpo').value,
                    cor: document.getElementById('embed-cor').value,
                    url_imagem: document.getElementById('embed-imagem').value,
                    mencao: document.getElementById('embed-mencao').value
                }};
                if (!data.canal_id || !data.titulo || !data.corpo) {{
                    alert('Preencha canal, título e corpo');
                    return;
                }}
                try {{
                    const resp = await fetch('/api/comando/embed', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(data)}});
                    const result = await resp.json();
                    showAlert('embed-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) {{
                        document.getElementById('embed-titulo').value = '';
                        document.getElementById('embed-corpo').value = '';
                        document.getElementById('embed-imagem').value = '';
                    }}
                }} catch(e) {{ showAlert('embed-alert', 'Erro: ' + e.message, false); }}
            }}
            
            document.addEventListener('DOMContentLoaded', carregarDados);
        </script>
    </body>
    </html>
    '''

# ========================
# ÁREA DO CLIENTE
# ========================

@app.route("/cliente")
def cliente_area():
    if 'usuario' not in session:
        return redirect(url_for('login'))
    
    usuario = session['usuario']
    discord_id = usuario['id']
    
    cliente = obter_cliente(discord_id)
    servicos = dados.get("servicos", {})
    servicos_ativos = [s for s in servicos.values() if s.get("status") == "ativo"]
    recompensas = dados.get("fidelidade", {}).get("recompensas", [])
    recompensas_ativas = [r for r in recompensas if r.get("status") == "ativo"]
    
    # Verificar se cliente tem cadastro
    cadastrado = cliente is not None
    
    return f'''
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Área do Cliente</title>
        <style>
            :root {{ --primary: #5865F2; --primary-dark: #4752C4; --success: #10b981; --danger: #ef4444; --warning: #f59e0b; --dark: #1a1a1a; --darker: #121212; --light: #e0e0e0; --gray: #333; }}
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: 'Segoe UI', sans-serif; background: var(--darker); color: var(--light); min-height: 100vh; }}
            header {{ background: var(--dark); padding: 1rem 2rem; border-bottom: 1px solid var(--gray); }}
            .header-content {{ display: flex; justify-content: space-between; align-items: center; max-width: 1200px; margin: 0 auto; flex-wrap: wrap; gap: 10px; }}
            h1 {{ color: var(--primary); }}
            .user-info {{ display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }}
            .avatar {{ width: 40px; height: 40px; border-radius: 50%; border: 2px solid var(--primary); }}
            .btn {{ padding: 0.5rem 1rem; border: none; border-radius: 5px; cursor: pointer; font-weight: 600; text-decoration: none; display: inline-block; transition: all 0.2s; font-size: 0.9rem; }}
            .btn-primary {{ background: var(--primary); color: white; }}
            .btn-primary:hover {{ background: var(--primary-dark); }}
            .btn-success {{ background: var(--success); color: white; }}
            .btn-success:hover {{ background: #059669; }}
            .btn-danger {{ background: var(--danger); color: white; }}
            .btn-danger:hover {{ background: #dc2626; }}
            .btn-warning {{ background: var(--warning); color: white; }}
            .btn-warning:hover {{ background: #d97706; }}
            .btn-sm {{ padding: 0.25rem 0.5rem; font-size: 0.8rem; }}
            .btn-xs {{ padding: 0.15rem 0.4rem; font-size: 0.7rem; }}
            .container {{ max-width: 1200px; margin: 2rem auto; padding: 0 1rem; }}
            .card {{ background: var(--dark); border-radius: 10px; padding: 1.5rem; margin: 1rem 0; border: 1px solid var(--gray); }}
            .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 1rem; }}
            .stat-card {{ background: linear-gradient(135deg, var(--primary), var(--primary-dark)); color: white; padding: 1.2rem; border-radius: 10px; text-align: center; }}
            .stat-card h3 {{ font-size: 1.5rem; }}
            .grid-2 {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 1rem; }}
            .grid-3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; }}
            @media (max-width: 768px) {{ .grid-2, .grid-3 {{ grid-template-columns: 1fr; }} }}
            .form-group {{ margin-bottom: 1.5rem; }}
            label {{ display: block; margin-bottom: 0.5rem; font-weight: 600; color: var(--primary); }}
            .form-control {{ width: 100%; padding: 0.75rem; background: var(--darker); border: 1px solid var(--gray); border-radius: 5px; color: var(--light); }}
            .form-control:focus {{ outline: none; border-color: var(--primary); }}
            .alert {{ padding: 1rem; border-radius: 5px; margin: 1rem 0; display: none; }}
            .alert-success {{ background: #1a472a; color: #4ade80; border: 1px solid #2ecc71; }}
            .alert-error {{ background: #7f1d1d; color: #f87171; border: 1px solid #ef4444; }}
            .tab-nav {{ display: flex; gap: 0.5rem; margin-bottom: 1rem; border-bottom: 2px solid var(--gray); flex-wrap: wrap; }}
            .tab-btn {{ padding: 0.75rem 1.5rem; background: var(--gray); border: none; border-radius: 5px 5px 0 0; cursor: pointer; font-weight: 600; color: var(--light); transition: all 0.2s; }}
            .tab-btn:hover {{ background: #444; }}
            .tab-btn.active {{ background: var(--primary); color: white; }}
            .tab {{ display: none; animation: fadeIn 0.3s; }}
            .tab.active {{ display: block; }}
            @keyframes fadeIn {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
            .status-badge {{ padding: 2px 10px; border-radius: 20px; font-size: 12px; }}
            .status-ativo {{ background: #1a472a; color: #4ade80; }}
            .status-inativo {{ background: #7f1d1d; color: #f87171; }}
            .status-pendente {{ background: #7f6d1d; color: #fbbf24; }}
            .status-concluido {{ background: #1a3a6a; color: #60a5fa; }}
            .progress-bar {{ width: 100%; height: 20px; background: var(--gray); border-radius: 10px; overflow: hidden; }}
            .progress-fill {{ height: 100%; background: linear-gradient(90deg, #5865F2, #10b981); transition: width 0.5s; }}
            .info-box {{ background: #1a1a2e; border-left: 4px solid #5865F2; padding: 1rem; margin: 1rem 0; border-radius: 5px; }}
            .scroll-table {{ overflow-x: auto; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ text-align: left; padding: 10px; border-bottom: 1px solid var(--gray); }}
            th {{ background: var(--gray); }}
            .empty-state {{ text-align: center; padding: 40px; color: #888; }}
            .cupom-code {{ font-family: monospace; font-size: 1.2rem; background: #1a1a1a; padding: 4px 12px; border-radius: 4px; color: #4ade80; border: 1px solid #333; }}
        </style>
    </head>
    <body>
        <header>
            <div class="header-content">
                <h1>👤 Área do Cliente</h1>
                <div class="user-info">
                    <img src="https://cdn.discordapp.com/avatars/{usuario['id']}/{usuario.get('avatar', '')}.png" class="avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                    <span>{usuario['nome_usuario']}</span>
                    <a href="/" class="btn btn-primary">🏠 Início</a>
                    {f'<a href="/dashboard" class="btn btn-primary">🚀 Painel Admin</a>' if usuario.get('eh_admin') else ''}
                    <a href="/fila" class="btn btn-primary">📋 Fila</a>
                    <a href="/logout" class="btn btn-danger">🚪 Sair</a>
                </div>
            </div>
        </header>
        
        <div class="container">
            {'' if cadastrado else '''
            <div class="card">
                <h2>📝 Cadastro</h2>
                <p>Para usar o sistema de fidelidade, você precisa se cadastrar.</p>
                <div class="grid-2">
                    <div class="form-group">
                        <label>UID do Jogo</label>
                        <input type="text" id="cadastro-uid" class="form-control" placeholder="Seu UID">
                    </div>
                    <div class="form-group">
                        <label>Nick do Jogo</label>
                        <input type="text" id="cadastro-nick" class="form-control" placeholder="Seu nick">
                    </div>
                </div>
                <button onclick="cadastrarCliente()" class="btn btn-success">✅ Cadastrar</button>
                <div id="cadastro-alert" class="alert"></div>
            </div>
            '''}
            
            {f'''
            <div class="card">
                <h2>💰 Seus Pontos</h2>
                <div class="stats-grid">
                    <div class="stat-card"><h3>{cliente.get('pontos', 0)}</h3><p>Saldo Atual</p></div>
                    <div class="stat-card" style="background: linear-gradient(135deg, #10b981, #059669);"><h3>{cliente.get('total_acumulado', 0)}</h3><p>Total Acumulado</p></div>
                    <div class="stat-card" style="background: linear-gradient(135deg, #f59e0b, #d97706);"><h3>{cliente.get('total_utilizado', 0)}</h3><p>Total Utilizado</p></div>
                    <div class="stat-card" style="background: linear-gradient(135deg, #8b5cf6, #7c3aed);"><h3>{cliente.get('ultima_compra', 'Nunca')}</h3><p>Última Compra</p></div>
                </div>
            </div>
            
            <div class="card">
                <h2>📊 Progresso</h2>
                <p><strong>Próximo prêmio:</strong> {next(recompensas_ativas, {}).get('nome', 'Nenhum disponível')}</p>
                <div class="progress-bar">
                    <div class="progress-fill" style="width: {min(100, (cliente.get('pontos', 0) / max(next(recompensas_ativas, {}).get('pontos', 100), 1)) * 100)}%;"></div>
                </div>
                <p style="margin-top: 5px; color: #888;">{cliente.get('pontos', 0)} / {max(next(recompensas_ativas, {}).get('pontos', 100), 1)} pontos para o próximo prêmio</p>
            </div>
            
            <div class="tab-nav">
                <button class="tab-btn active" onclick="showTab('servicos')">🛒 Solicitar Serviço</button>
                <button class="tab-btn" onclick="showTab('loja')">🏪 Loja</button>
                <button class="tab-btn" onclick="showTab('historico')">📜 Histórico</button>
                <button class="tab-btn" onclick="showTab('cupons')">🎟️ Meus Cupons</button>
                <button class="tab-btn" onclick="showTab('andamento')">⏳ Em Andamento</button>
            </div>
            
            <!-- Aba Serviços -->
            <div id="servicos" class="tab active">
                <div class="card">
                    <h2>🛒 Solicitar Serviço</h2>
                    <div class="grid-2">
                        <div class="form-group">
                            <label>Serviço</label>
                            <select id="solicitar-servico" class="form-control">
                                <option value="">Selecione um serviço</option>
                                {''.join(f'<option value="{s["id"]}">{s["nome"]} - R${s["valor"]} ({s["pontos"]} pontos)</option>' for s in servicos_ativos)}
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Jogo</label>
                            <input type="text" id="solicitar-jogo" class="form-control" placeholder="Nome do jogo">
                        </div>
                    </div>
                    <div class="form-group">
                        <label>Cupom (opcional)</label>
                        <input type="text" id="solicitar-cupom" class="form-control" placeholder="Código do cupom">
                    </div>
                    <div class="form-group">
                        <label>Observações</label>
                        <textarea id="solicitar-obs" class="form-control" rows="2" placeholder="Detalhes adicionais"></textarea>
                    </div>
                    <button onclick="solicitarServico()" class="btn btn-success">📤 Enviar Solicitação</button>
                    <div id="solicitar-alert" class="alert"></div>
                </div>
            </div>
            
            <!-- Aba Loja -->
            <div id="loja" class="tab">
                <div class="card">
                    <h2>🏪 Loja de Fidelidade</h2>
                    <div class="info-box">
                        💡 Você tem <strong style="color:#f59e0b;">{cliente.get('pontos', 0)} pontos</strong> disponíveis.
                    </div>
                    <div id="recompensas-cliente">
                        {''.join(f'''
                        <div style="background: #1a1a1a; padding: 15px; border-radius: 8px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; border: 1px solid #333;">
                            <div>
                                <strong style="color: #f59e0b;">{r.get('nome', '')}</strong>
                                <div style="font-size: 0.9rem; color: #888;">{r.get('descricao', '')}</div>
                                <div>Pontos: <strong style="color: #4ade80;">{r.get('pontos', 0)}</strong> | Tipo: {r.get('tipo', 'desconto')}</div>
                            </div>
                            <button onclick="resgatarRecompensa('{r.get('id', '')}')" class="btn btn-primary" {'disabled' if cliente.get('pontos', 0) < r.get('pontos', 0) else ''}>
                                {('✅ Resgatar' if cliente.get('pontos', 0) >= r.get('pontos', 0) else f'🔒 Faltam {r.get('pontos', 0) - cliente.get('pontos', 0)} pts')}
                            </button>
                        </div>
                        ''' for r in recompensas_ativas) or '<div class="empty-state">Nenhuma recompensa disponível no momento.</div>'}
                    </div>
                </div>
            </div>
            
            <!-- Aba Histórico -->
            <div id="historico" class="tab">
                <div class="card">
                    <h2>📜 Histórico Completo</h2>
                    <div class="scroll-table">
                        <table>
                            <thead>
                                <tr><th>Data</th><th>Tipo</th><th>Detalhes</th><th>Valor</th></tr>
                            </thead>
                            <tbody id="historico-tabela">
                                <tr><td colspan="4">Carregando...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
            
            <!-- Aba Cupons -->
            <div id="cupons" class="tab">
                <div class="card">
                    <h2>🎟️ Meus Cupons</h2>
                    <div id="cupons-lista">
                        <div class="empty-state">Carregando...</div>
                    </div>
                </div>
            </div>
            
            <!-- Aba Em Andamento -->
            <div id="andamento" class="tab">
                <div class="card">
                    <h2>⏳ Serviços em Andamento</h2>
                    <div id="andamento-lista">
                        <div class="empty-state">Carregando...</div>
                    </div>
                </div>
            </div>
            ''' if cadastrado else ''}
        </div>
        
        <script>
            let recompensasCliente = {json.dumps(recompensas_ativas)};
            let servicosAtivos = {json.dumps(servicos_ativos)};
            
            function showTab(tabId) {{
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                const tab = document.getElementById(tabId);
                if (tab) tab.classList.add('active');
                const btn = document.querySelector(`.tab-btn[onclick*="${{tabId}}"]`);
                if (btn) btn.classList.add('active');
                if (tabId === 'historico') carregarHistorico();
                if (tabId === 'cupons') carregarCupons();
                if (tabId === 'andamento') carregarAndamento();
            }}
            
            function showAlert(id, msg, sucesso) {{
                const el = document.getElementById(id);
                if (!el) return;
                el.textContent = msg;
                el.className = 'alert ' + (sucesso ? 'alert-success' : 'alert-error');
                el.style.display = 'block';
                setTimeout(() => el.style.display = 'none', 5000);
            }}
            
            function escapeHtml(texto) {{ if (!texto) return ''; return texto.replace(/[&<>]/g, function(m) {{ if (m === '&') return '&amp;'; if (m === '<') return '&lt;'; if (m === '>') return '&gt;'; return m; }}); }}
            
            // ========================
            // CADASTRO
            // ========================
            
            async function cadastrarCliente() {{
                const uid = document.getElementById('cadastro-uid').value.trim();
                const nick = document.getElementById('cadastro-nick').value.trim();
                if (!uid || !nick) {{
                    showAlert('cadastro-alert', 'Preencha UID e Nick', false);
                    return;
                }}
                try {{
                    const resp = await fetch('/api/cliente/cadastrar', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{uid, nick_jogo: nick}})
                    }});
                    const result = await resp.json();
                    if (result.sucesso) {{
                        showAlert('cadastro-alert', 'Cadastro realizado com sucesso! Recarregando...', true);
                        setTimeout(() => location.reload(), 1500);
                    }} else {{
                        showAlert('cadastro-alert', result.mensagem, false);
                    }}
                }} catch(e) {{
                    showAlert('cadastro-alert', 'Erro: ' + e.message, false);
                }}
            }}
            
            // ========================
            // SOLICITAR SERVIÇO
            // ========================
            
            async function solicitarServico() {{
                const servicoId = document.getElementById('solicitar-servico').value;
                const jogo = document.getElementById('solicitar-jogo').value.trim();
                const obs = document.getElementById('solicitar-obs').value.trim();
                const cupom = document.getElementById('solicitar-cupom').value.trim();
                
                if (!servicoId) {{
                    showAlert('solicitar-alert', 'Selecione um serviço', false);
                    return;
                }}
                
                try {{
                    const resp = await fetch('/api/solicitacoes', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{servico_id: servicoId, jogo, observacoes: obs, cupom_codigo: cupom || undefined}})
                    }});
                    const result = await resp.json();
                    if (result.sucesso) {{
                        document.getElementById('solicitar-jogo').value = '';
                        document.getElementById('solicitar-obs').value = '';
                        document.getElementById('solicitar-cupom').value = '';
                        showAlert('solicitar-alert', 'Solicitação enviada com sucesso! Aguarde a aprovação.', true);
                    }} else {{
                        showAlert('solicitar-alert', result.mensagem, false);
                    }}
                }} catch(e) {{
                    showAlert('solicitar-alert', 'Erro: ' + e.message, false);
                }}
            }}
            
            // ========================
            // LOJA - RESGATE
            // ========================
            
            async function resgatarRecompensa(recompensaId) {{
                if (!confirm('Resgatar esta recompensa?')) return;
                try {{
                    const resp = await fetch('/api/fidelidade/resgatar', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{recompensa_id: recompensaId}})
                    }});
                    const result = await resp.json();
                    if (result.sucesso) {{
                        alert(`✅ Recompensa resgatada! Código: ${{result.cupom.codigo}}\\nGuarde este código para usar no seu próximo pedido.`);
                        location.reload();
                    }} else {{
                        alert('❌ ' + result.mensagem);
                    }}
                }} catch(e) {{
                    alert('Erro: ' + e.message);
                }}
            }}
            
            // ========================
            // HISTÓRICO
            // ========================
            
            async function carregarHistorico() {{
                try {{
                    const resp = await fetch('/api/cliente/historico');
                    const data = await resp.json();
                    const tbody = document.getElementById('historico-tabela');
                    if (data.sucesso && data.historico.length > 0) {{
                        tbody.innerHTML = data.historico.slice().reverse().map(h => `
                            <tr>
                                <td>${{h.data ? new Date(h.data).toLocaleDateString() : '-'}}</td>
                                <td>${{escapeHtml(h.tipo || '')}}</td>
                                <td>${{escapeHtml(h.motivo || h.servico || h.codigo || '')}}</td>
                                <td>${{h.pontos ? h.pontos + ' pts' : h.valor ? 'R$ ' + h.valor : '-'}}</td>
                            </tr>
                        `).join('');
                    }} else {{
                        tbody.innerHTML = '<tr><td colspan="4">Nenhum histórico encontrado</td></tr>';
                    }}
                }} catch(e) {{
                    document.getElementById('historico-tabela').innerHTML = '<tr><td colspan="4">Erro ao carregar histórico</td></tr>';
                }}
            }}
            
            // ========================
            // CUPONS
            // ========================
            
            async function carregarCupons() {{
                try {{
                    const resp = await fetch('/api/cliente/cupons');
                    const data = await resp.json();
                    const container = document.getElementById('cupons-lista');
                    if (data.sucesso && data.cupons.length > 0) {{
                        container.innerHTML = data.cupons.map(c => `
                            <div style="background: #1a1a1a; padding: 15px; border-radius: 8px; margin-bottom: 10px; border: 1px solid #333; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                                <div>
                                    <div class="cupom-code">${{escapeHtml(c.codigo)}}</div>
                                    <div style="font-size: 0.9rem; color: #888;">Tipo: ${{c.tipo}} | Valor: ${{escapeHtml(c.valor)}}</div>
                                    <div style="font-size: 0.8rem;">Criado: ${{new Date(c.data_criacao).toLocaleDateString()}} | Validade: ${{new Date(c.validade).toLocaleDateString()}}</div>
                                </div>
                                <span class="status-badge status-${{c.status === 'ativo' ? 'ativo' : 'inativo'}}">${{c.status === 'ativo' ? '✅ Disponível' : '❌ ' + c.status}}</span>
                            </div>
                        `).join('');
                    }} else {{
                        container.innerHTML = '<div class="empty-state">Você não possui cupons.</div>';
                    }}
                }} catch(e) {{
                    document.getElementById('cupons-lista').innerHTML = '<div class="empty-state">Erro ao carregar cupons</div>';
                }}
            }}
            
            // ========================
            // SERVIÇOS EM ANDAMENTO
            // ========================
            
            async function carregarAndamento() {{
                try {{
                    const resp = await fetch('/api/cliente/servicos_andamento');
                    const data = await resp.json();
                    const container = document.getElementById('andamento-lista');
                    if (data.sucesso && data.servicos.length > 0) {{
                        container.innerHTML = data.servicos.map(s => `
                            <div style="background: #1a1a1a; padding: 15px; border-radius: 8px; margin-bottom: 10px; border: 1px solid #333;">
                                <strong>${{escapeHtml(s.servico || '')}}</strong>
                                <div style="font-size: 0.9rem; color: #888;">Solicitado em: ${{s.data ? new Date(s.data).toLocaleDateString() : '-'}}</div>
                                <span class="status-badge status-pendente">⏳ Em andamento</span>
                            </div>
                        `).join('');
                    }} else {{
                        container.innerHTML = '<div class="empty-state">Nenhum serviço em andamento.</div>';
                    }}
                }} catch(e) {{
                    document.getElementById('andamento-lista').innerHTML = '<div class="empty-state">Erro ao carregar</div>';
                }}
            }}
            
            // Carregar dados iniciais
            {'carregarHistorico(); carregarCupons(); carregarAndamento();' if cadastrado else ''}
        </script>
    </body>
    </html>
    '''

# ========================
# PÁGINA DE REGRAS
# ========================

@app.route("/fidelidade/regras")
def fidelidade_regras():
    return '''
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Regras - Sistema de Fidelidade</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0f0c29, #302b63, #24243e); min-height: 100vh; padding: 20px; color: #fff; }
            .container { max-width: 800px; margin: 0 auto; }
            .header { text-align: center; margin-bottom: 30px; padding: 30px; background: rgba(0,0,0,0.5); border-radius: 20px; }
            h1 { background: linear-gradient(135deg, #ff6b6b, #ffd93d); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
            .card { background: rgba(0,0,0,0.4); border-radius: 15px; padding: 25px; margin: 15px 0; border: 1px solid rgba(255,255,255,0.1); }
            .card h2 { color: #ffd93d; margin-bottom: 15px; }
            .card ul { list-style: none; padding: 0; }
            .card li { padding: 12px 0; border-bottom: 1px solid rgba(255,255,255,0.05); }
            .card li:last-child { border-bottom: none; }
            .card li::before { content: "•"; color: #ffd93d; margin-right: 10px; }
            .btn { display: inline-block; background: #5865F2; color: white; padding: 12px 30px; border-radius: 8px; text-decoration: none; font-weight: bold; margin: 10px 0; transition: all 0.3s; }
            .btn:hover { background: #4752C4; transform: translateY(-2px); }
            .highlight { color: #ffd93d; font-weight: bold; }
            .footer { text-align: center; margin-top: 30px; font-size: 0.8rem; color: #888; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>📜 Sistema de Fidelidade ZankonYTB</h1>
                <p>Regras de Uso</p>
            </div>
            
            <div class="card">
                <h2>📌 1. Pontos Pessoais e Vinculados ao UID</h2>
                <ul>
                    <li>Seus pontos são <span class="highlight">pessoais, intransferíveis</span> e atrelados diretamente ao seu cadastro e ao seu UID do jogo.</li>
                    <li>Não é permitido transferir pontos para amigos ou juntar o saldo de compras de contas diferentes para resgatar prêmios.</li>
                </ul>
            </div>
            
            <div class="card">
                <h2>🎫 2. Cupons de Uso Único</h2>
                <ul>
                    <li>Ao trocar seus pontos, o sistema gera um <span class="highlight">código exclusivo</span> para você.</li>
                    <li>Esse token é de <span class="highlight">uso único</span>.</li>
                    <li>Uma vez inserido e validado no seu pedido, ele é consumido automaticamente e não poderá ser reutilizado.</li>
                </ul>
            </div>
            
            <div class="card">
                <h2>📦 3. Um Benefício por Pedido</h2>
                <ul>
                    <li>Os descontos e resgates <span class="highlight">não são cumulativos</span>.</li>
                    <li>É permitido utilizar apenas <span class="highlight">um benefício por pedido</span>.</li>
                    <li>Não é possível utilizar vários cupons juntos.</li>
                </ul>
            </div>
            
            <div class="card">
                <h2>⏰ 4. Validade</h2>
                <ul>
                    <li>Saldo de pontos expira após <span class="highlight">90 dias sem novos serviços concluídos</span>.</li>
                    <li>Cupons possuem validade de <span class="highlight">30 dias após o resgate</span>.</li>
                </ul>
            </div>
            
            <div style="text-align: center; margin-top: 30px;">
                <a href="/" class="btn">🏠 Voltar ao Início</a>
                <a href="/cliente" class="btn" style="background: #10b981;">👤 Área do Cliente</a>
            </div>
            
            <div class="footer">
                <p>Última atualização: Janeiro 2025</p>
            </div>
        </div>
    </body>
    </html>
    '''

# ========================
# API PARA MEMBRO ADVERTÊNCIAS
# ========================

@app.route("/api/membro/advertencias")
def api_membro_advertencias():
    membro_id = request.args.get('membro_id')
    if not membro_id:
        return jsonify({"sucesso": False, "advertencias": []})
    warns = dados.get("advertencias", {}).get(str(membro_id), [])
    return jsonify({"sucesso": True, "advertencias": warns})

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
# COMANDOS SLASH DO DISCORD (COM VERIFICAÇÃO DE CANAL)
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
    clientes_count = len(dados.get("clientes", {}))
    servicos_count = len(dados.get("servicos", {}))
    print(f"{'='*50}")
    print(f"✨ BOT PRONTO! Comandos: /perfil e /rank")
    print(f"🛡️ Anti-Spam: {'ATIVADO' if dados.get('anti_spam', {}).get('ativado', True) else 'DESATIVADO'}")
    print(f"🚫 Comandos da Mudae: NÃO ganham XP e NÃO contam como spam")
    print(f"👥 Clientes cadastrados: {clientes_count}")
    print(f"🛒 Serviços disponíveis: {servicos_count}")
    print(f"⭐ Sistema de Fidelidade: ATIVO")
    print(f"📢 Canal do /perfil: {config.get('canal_perfil') or 'TODOS OS CANAIS'}")
    print(f"📢 Canal do /rank: {config.get('canal_rank') or 'TODOS OS CANAIS'}")
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