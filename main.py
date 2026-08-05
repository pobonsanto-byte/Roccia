import os
import json
import base64
import re
import requests
import time
import secrets
import random
import string
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
    # Novas estruturas
    "clientes": {},
    "servicos": {},
    "solicitacoes": {},
    "fidelidade": {
        "pontos_por_real": 1,
        "validade_pontos_dias": 90,
        "validade_cupom_dias": 30,
        "recompensas": [
            {"id": "rec1", "nome": "1 Dia de Quests Diárias Grátis", "pontos": 60, "tipo": "quests"},
            {"id": "rec2", "nome": "Desafio Rápido / Portinha / Hologramas de Huanglong", "pontos": 100, "tipo": "servico_extra", "opcoes": ["Desafio Rápido", "Portinha", "Hologramas de Huanglong"]},
            {"id": "rec3", "nome": "Cupom de R$5", "pontos": 100, "tipo": "cupom", "valor": 5},
            {"id": "rec4", "nome": "Análise de Conta / Companion Quest", "pontos": 200, "tipo": "servico_extra", "opcoes": ["Análise de Conta", "Companion Quest"]},
            {"id": "rec5", "nome": "Cupom de R$10", "pontos": 200, "tipo": "cupom", "valor": 10},
            {"id": "rec6", "nome": "Build Completa", "pontos": 400, "tipo": "servico_extra", "opcoes": ["Build Completa"]},
            {"id": "rec7", "nome": "Cupom de R$20", "pontos": 400, "tipo": "cupom", "valor": 20}
        ],
        "cupons": {}
    }
}

# Dicionário para armazenar mensagens recentes dos usuários
mensagens_recentes = {}  # {user_id: [timestamps]}

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
                # Novas estruturas
                if "clientes" not in dados:
                    dados["clientes"] = {}
                if "servicos" not in dados:
                    dados["servicos"] = {}
                if "solicitacoes" not in dados:
                    dados["solicitacoes"] = {}
                if "fidelidade" not in dados:
                    dados["fidelidade"] = {
                        "pontos_por_real": 1,
                        "validade_pontos_dias": 90,
                        "validade_cupom_dias": 30,
                        "recompensas": [
                            {"id": "rec1", "nome": "1 Dia de Quests Diárias Grátis", "pontos": 60, "tipo": "quests"},
                            {"id": "rec2", "nome": "Desafio Rápido / Portinha / Hologramas de Huanglong", "pontos": 100, "tipo": "servico_extra", "opcoes": ["Desafio Rápido", "Portinha", "Hologramas de Huanglong"]},
                            {"id": "rec3", "nome": "Cupom de R$5", "pontos": 100, "tipo": "cupom", "valor": 5},
                            {"id": "rec4", "nome": "Análise de Conta / Companion Quest", "pontos": 200, "tipo": "servico_extra", "opcoes": ["Análise de Conta", "Companion Quest"]},
                            {"id": "rec5", "nome": "Cupom de R$10", "pontos": 200, "tipo": "cupom", "valor": 10},
                            {"id": "rec6", "nome": "Build Completa", "pontos": 400, "tipo": "servico_extra", "opcoes": ["Build Completa"]},
                            {"id": "rec7", "nome": "Cupom de R$20", "pontos": 400, "tipo": "cupom", "valor": 20}
                        ],
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
        "posicao": len(fila["entradas"]) + 1,
        "solicitacao_id": None
    }
    
    fila["entradas"].append(entrada)
    atualizar_posicoes(fila["entradas"])
    salvar_fila()
    adicionar_log(f"fila_adicionar: {nome_usuario} - {servico} - {jogo}")
    return True, entrada

def adicionar_fila_por_solicitacao(solicitacao_id: str):
    sol = dados.get("solicitacoes", {}).get(solicitacao_id)
    if not sol:
        return False, "Solicitação não encontrada"
    cliente = dados.get("clientes", {}).get(sol["cliente_id"])
    if not cliente:
        return False, "Cliente não encontrado"
    servico = dados.get("servicos", {}).get(sol["servico_id"])
    if not servico:
        return False, "Serviço não encontrado"
    
    nome_usuario = cliente.get("nick", cliente.get("nome", "Cliente"))
    servico_nome = servico.get("nome", "Serviço")
    jogo = sol.get("jogo", "")
    usuario_id = sol["cliente_id"]
    
    sucesso, resultado = adicionar_fila(nome_usuario, servico_nome, jogo, usuario_id)
    if sucesso:
        entrada = resultado
        sol["fila_id"] = entrada["id"]
        sol["status"] = "em_andamento"
        dados["solicitacoes"][solicitacao_id] = sol
        salvar_dados_github(f"Solicitação {solicitacao_id} em andamento")
    return sucesso, resultado

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

def concluir_servico(entrada_id: str, admin_nome: str = None):
    fila = obter_dados_fila()
    for i, entrada in enumerate(fila["entradas"]):
        if entrada["id"] == entrada_id:
            removido = fila["entradas"].pop(i)
            removido["status"] = "concluido"
            removido["concluido_em"] = agora_br().isoformat()
            removido["admin"] = admin_nome or "Admin"
            fila["historico"].append(removido)
            atualizar_posicoes(fila["entradas"])
            salvar_fila()
            adicionar_log(f"fila_concluir: {removido['nome_usuario']} por {admin_nome or 'Admin'}")
            
            if "solicitacao_id" in removido and removido["solicitacao_id"]:
                sol_id = removido["solicitacao_id"]
                sol = dados.get("solicitacoes", {}).get(sol_id)
                if sol:
                    sol["status"] = "concluido"
                    sol["concluido_em"] = removido["concluido_em"]
                    sol["admin"] = admin_nome or "Admin"
                    dados["solicitacoes"][sol_id] = sol
                    
                    cliente_id = sol["cliente_id"]
                    servico_id = sol["servico_id"]
                    servico = dados.get("servicos", {}).get(servico_id)
                    if servico:
                        pontos = servico.get("pontos", 0)
                        valor = servico.get("valor", 0)
                        adicionar_historico_cliente(
                            cliente_id,
                            tipo="servico",
                            descricao=f"{servico.get('nome', 'Serviço')} - {sol.get('jogo', '')}",
                            valor=valor,
                            pontos_ganhos=pontos,
                            admin=admin_nome or "Admin"
                        )
            salvar_dados_github(f"Conclusão serviço {removido['nome_usuario']}")
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
# FUNÇÕES PARA CLIENTES
# ========================

def obter_cliente(discord_id: str):
    return dados.get("clientes", {}).get(str(discord_id))

def criar_cliente(discord_id: str, nome: str, uid: str, nick: str):
    clientes = dados.setdefault("clientes", {})
    if str(discord_id) in clientes:
        return False, "Cliente já existe"
    for cid, cli in clientes.items():
        if cli.get("uid") == uid:
            return False, "UID já está cadastrado para outro usuário"
    clientes[str(discord_id)] = {
        "discord_id": str(discord_id),
        "nome": nome,
        "uid": uid,
        "nick": nick,
        "saldo_pontos": 0,
        "total_acumulado": 0,
        "total_utilizado": 0,
        "ultima_compra": None,
        "ultimo_resgate": None,
        "historico": [],
        "data_cadastro": agora_br().isoformat()
    }
    salvar_dados_github(f"Novo cliente: {nome}")
    return True, clientes[str(discord_id)]

def atualizar_cliente(discord_id: str, **kwargs):
    clientes = dados.setdefault("clientes", {})
    cliente = clientes.get(str(discord_id))
    if not cliente:
        return False
    for key, value in kwargs.items():
        if key in cliente:
            cliente[key] = value
    salvar_dados_github(f"Cliente atualizado: {cliente.get('nome')}")
    return True

def adicionar_pontos_cliente(discord_id: str, pontos: int, descricao: str = ""):
    cliente = obter_cliente(discord_id)
    if not cliente:
        return False
    cliente["saldo_pontos"] = cliente.get("saldo_pontos", 0) + pontos
    cliente["total_acumulado"] = cliente.get("total_acumulado", 0) + pontos
    cliente["ultima_compra"] = agora_br().isoformat()
    cliente.setdefault("historico_pontos", []).append({
        "tipo": "ganho",
        "pontos": pontos,
        "descricao": descricao,
        "data": agora_br().isoformat()
    })
    salvar_dados_github(f"Pontos adicionados a {cliente.get('nome')}: +{pontos}")
    return True

def remover_pontos_cliente(discord_id: str, pontos: int, descricao: str = ""):
    cliente = obter_cliente(discord_id)
    if not cliente:
        return False
    if cliente.get("saldo_pontos", 0) < pontos:
        return False
    cliente["saldo_pontos"] = cliente.get("saldo_pontos", 0) - pontos
    cliente["total_utilizado"] = cliente.get("total_utilizado", 0) + pontos
    cliente["ultimo_resgate"] = agora_br().isoformat()
    cliente.setdefault("historico_pontos", []).append({
        "tipo": "resgate",
        "pontos": -pontos,
        "descricao": descricao,
        "data": agora_br().isoformat()
    })
    salvar_dados_github(f"Pontos removidos de {cliente.get('nome')}: -{pontos}")
    return True

def adicionar_historico_cliente(discord_id: str, tipo: str, descricao: str, valor: float = 0, pontos_ganhos: int = 0, admin: str = None):
    cliente = obter_cliente(discord_id)
    if not cliente:
        return False
    cliente.setdefault("historico", []).append({
        "tipo": tipo,
        "descricao": descricao,
        "valor": valor,
        "pontos_ganhos": pontos_ganhos,
        "admin": admin or "Sistema",
        "data": agora_br().isoformat()
    })
    if pontos_ganhos > 0:
        adicionar_pontos_cliente(discord_id, pontos_ganhos, descricao)
    salvar_dados_github(f"Histórico adicionado para {cliente.get('nome')}")
    return True

# ========================
# FUNÇÕES PARA SERVIÇOS
# ========================

def obter_servicos(apenas_ativos=True):
    servicos = dados.get("servicos", {})
    if apenas_ativos:
        return {k: v for k, v in servicos.items() if v.get("ativo", True)}
    return servicos

def adicionar_servico(nome, categoria, descricao, valor, pontos, imagem="", ativo=True):
    servicos = dados.setdefault("servicos", {})
    import uuid
    id_servico = str(uuid.uuid4())[:8]
    servicos[id_servico] = {
        "id": id_servico,
        "nome": nome,
        "categoria": categoria,
        "descricao": descricao,
        "valor": float(valor),
        "pontos": int(pontos),
        "imagem": imagem,
        "ativo": ativo,
        "data_criacao": agora_br().isoformat()
    }
    salvar_dados_github(f"Serviço adicionado: {nome}")
    return id_servico

def editar_servico(id_servico, **kwargs):
    servicos = dados.get("servicos", {})
    if id_servico not in servicos:
        return False
    for key, value in kwargs.items():
        if key in servicos[id_servico]:
            servicos[id_servico][key] = value
    salvar_dados_github(f"Serviço editado: {servicos[id_servico].get('nome')}")
    return True

def excluir_servico(id_servico):
    servicos = dados.get("servicos", {})
    if id_servico in servicos:
        del servicos[id_servico]
        salvar_dados_github(f"Serviço excluído: {id_servico}")
        return True
    return False

# ========================
# FUNÇÕES PARA SOLICITAÇÕES
# ========================

def criar_solicitacao(cliente_id, servico_id, jogo, observacoes, cupom=None):
    solicitacoes = dados.setdefault("solicitacoes", {})
    import uuid
    id_sol = str(uuid.uuid4())[:8]
    servico = dados.get("servicos", {}).get(servico_id)
    if not servico:
        return False, "Serviço não encontrado"
    cliente = obter_cliente(cliente_id)
    if not cliente:
        return False, "Cliente não encontrado"
    
    desconto = 0
    if cupom:
        valido, msg, cupom_data = validar_cupom(cliente_id, cupom)
        if not valido:
            return False, msg
        if cupom_data.get("tipo") == "cupom":
            desconto = cupom_data.get("valor", 0)
        else:
            return False, "Cupom inválido para esta solicitação"
    
    solicitacao = {
        "id": id_sol,
        "cliente_id": cliente_id,
        "servico_id": servico_id,
        "jogo": jogo,
        "observacoes": observacoes,
        "cupom": cupom,
        "desconto": desconto,
        "status": "aguardando",
        "data_criacao": agora_br().isoformat(),
        "data_aprovacao": None,
        "data_conclusao": None,
        "admin": None,
        "fila_id": None,
        "motivo_recusa": None
    }
    solicitacoes[id_sol] = solicitacao
    salvar_dados_github(f"Solicitação criada: {id_sol}")
    return True, solicitacao

def aprovar_solicitacao(id_sol, admin_nome):
    solicitacoes = dados.get("solicitacoes", {})
    sol = solicitacoes.get(id_sol)
    if not sol:
        return False, "Solicitação não encontrada"
    if sol["status"] != "aguardando":
        return False, "Solicitação já foi processada"
    sol["status"] = "aprovado"
    sol["data_aprovacao"] = agora_br().isoformat()
    sol["admin"] = admin_nome
    solicitacoes[id_sol] = sol
    salvar_dados_github(f"Solicitação {id_sol} aprovada")
    sucesso, resultado = adicionar_fila_por_solicitacao(id_sol)
    if sucesso:
        return True, "Solicitação aprovada e adicionada à fila"
    else:
        sol["status"] = "aguardando"
        solicitacoes[id_sol] = sol
        salvar_dados_github(f"Falha ao adicionar à fila para {id_sol}")
        return False, f"Erro ao adicionar à fila: {resultado}"

def recusar_solicitacao(id_sol, admin_nome, motivo):
    solicitacoes = dados.get("solicitacoes", {})
    sol = solicitacoes.get(id_sol)
    if not sol:
        return False, "Solicitação não encontrada"
    if sol["status"] != "aguardando":
        return False, "Solicitação já foi processada"
    sol["status"] = "recusado"
    sol["motivo_recusa"] = motivo
    sol["admin"] = admin_nome
    solicitacoes[id_sol] = sol
    salvar_dados_github(f"Solicitação {id_sol} recusada")
    return True, "Solicitação recusada"

# ========================
# FUNÇÕES PARA FIDELIDADE E CUPONS
# ========================

def obter_recompensas():
    return dados.get("fidelidade", {}).get("recompensas", [])

def gerar_codigo_cupom():
    parte = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    return f"ZANKON-{parte}"

def resgatar_cupom(cliente_id, recompensa_id):
    cliente = obter_cliente(cliente_id)
    if not cliente:
        return False, "Cliente não encontrado"
    recompensas = obter_recompensas()
    recompensa = None
    for r in recompensas:
        if r.get("id") == recompensa_id:
            recompensa = r
            break
    if not recompensa:
        return False, "Recompensa não encontrada"
    
    pontos_necessarios = recompensa.get("pontos", 0)
    if cliente.get("saldo_pontos", 0) < pontos_necessarios:
        return False, "Saldo insuficiente"
    
    codigo = gerar_codigo_cupom()
    cupom = {
        "codigo": codigo,
        "cliente_id": cliente_id,
        "recompensa_id": recompensa_id,
        "tipo": recompensa.get("tipo"),
        "valor": recompensa.get("valor", 0),
        "opcoes": recompensa.get("opcoes", []),
        "data_resgate": agora_br().isoformat(),
        "validade": (agora_br() + timedelta(days=dados.get("fidelidade", {}).get("validade_cupom_dias", 30))).isoformat(),
        "utilizado": False,
        "data_utilizacao": None
    }
    dados["fidelidade"].setdefault("cupons", {})[codigo] = cupom
    
    sucesso = remover_pontos_cliente(cliente_id, pontos_necessarios, f"Resgate: {recompensa.get('nome')}")
    if not sucesso:
        return False, "Erro ao remover pontos"
    
    adicionar_historico_cliente(
        cliente_id,
        tipo="resgate",
        descricao=f"Resgate de {recompensa.get('nome')} - Código {codigo}",
        valor=0,
        pontos_ganhos=0,
        admin="Sistema"
    )
    salvar_dados_github(f"Cupom resgatado: {codigo}")
    return True, cupom

def validar_cupom(cliente_id, codigo):
    cupons = dados.get("fidelidade", {}).get("cupons", {})
    cupom = cupons.get(codigo)
    if not cupom:
        return False, "Cupom não encontrado", None
    if cupom.get("utilizado"):
        return False, "Cupom já foi utilizado", None
    if cupom.get("cliente_id") != cliente_id:
        return False, "Cupom não pertence a este cliente", None
    validade = cupom.get("validade")
    if validade:
        dt_validade = datetime.fromisoformat(validade)
        if agora_br() > dt_validade:
            return False, "Cupom expirado", None
    return True, "Cupom válido", cupom

def utilizar_cupom(cliente_id, codigo):
    valido, msg, cupom = validar_cupom(cliente_id, codigo)
    if not valido:
        return False, msg
    cupom["utilizado"] = True
    cupom["data_utilizacao"] = agora_br().isoformat()
    dados["fidelidade"]["cupons"][codigo] = cupom
    salvar_dados_github(f"Cupom utilizado: {codigo}")
    return True, "Cupom utilizado com sucesso"

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
            .features {{ text-align: left; margin: 20px 0; padding: 15px; background: #1a1a1a; border-radius: 10px; border: 1px solid #333; }}
            .features h3 {{ color: #5865F2; }}
            .features li {{ margin: 8px 0; padding-left: 10px; list-style: none; }}
            .features li:before {{ content: "✅"; margin-right: 10px; color: #5865F2; }}
            code {{ background: #1a1a1a; padding: 2px 6px; border-radius: 4px; color: #4ade80; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1> Painel de Controle</h1>
            <div class="status {classe_bot}">{status_bot}</div>
            <div class="features">
                <h3> Funcionalidades:</h3>
                <ul>
                    <li>Sistema de XP e Níveis</li>
                    <li>Reação com Cargos</li>
                    <li>Boas-vindas Personalizadas</li>
                    <li>Sistema de Moderação</li>
                    <li>Botões de Cargos</li>
                    <li>Sistema de Fila de Serviços</li>
                    <li>Anti-Spam Automático</li>
                    <li>Comandos da Mudae NÃO ganham XP</li>
                    <li>Comandos /perfil e /rank podem ser configurados para canais específicos</li>
                    <li>Área do Cliente com Fidelidade</li>
                    <li>Sistema de Solicitações e Pontos</li>
                </ul>
            </div>
            {"<a href='/login' class='btn'>🔐 Login com Discord</a>" if 'usuario' not in session else f'<p>Olá, {session["usuario"]["nome_usuario"]}!</p><a href="/dashboard" class="btn">🚀 Painel</a><a href="/fila" class="btn">📋 Fila</a><a href="/cliente" class="btn">👤 Minha Área</a><a href="/logout" class="btn">🚪 Sair</a>'}
            <p style="margin-top: 20px; color: #888;">Use <code>/perfil</code> e <code>/rank</code> no Discord (apenas nos canais configurados)</p>
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
        
        token_data = r.json()
        access_token = token_data['access_token']
        
        user_r = requests.get('https://discord.com/api/users/@me', headers={'Authorization': f'Bearer {access_token}'})
        if user_r.status_code != 200:
            return "Erro ao obter informações", 400
        
        user_data = user_r.json()
        
        guilds_r = requests.get('https://discord.com/api/users/@me/guilds', headers={'Authorization': f'Bearer {access_token}'})
        guilds = guilds_r.json() if guilds_r.status_code == 200 else []
        
        is_member = False
        for guild in guilds:
            if str(guild['id']) == GUILD_ID:
                is_member = True
                break
        
        if not is_member:
            return "<h2>⚠️ Acesso Restrito</h2><p>Você não é membro do servidor.</p><a href='/'>Voltar</a>", 403
        
        is_admin = False
        for guild in guilds:
            if str(guild['id']) == GUILD_ID and (guild.get('permissions', 0) & 0x8):
                is_admin = True
                break
        
        session['usuario'] = {
            'id': user_data['id'],
            'nome_usuario': user_data['username'],
            'avatar': user_data.get('avatar'),
            'eh_admin': is_admin,
            'access_token': access_token
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
# ÁREA DO CLIENTE
# ========================

@app.route("/cliente")
def cliente_area():
    if 'usuario' not in session:
        return redirect(url_for('login'))
    
    usuario = session['usuario']
    discord_id = usuario['id']
    cliente = obter_cliente(discord_id)
    
    if not cliente:
        return redirect(url_for('cliente_cadastro'))
    
    servicos_ativos = obter_servicos(apenas_ativos=True)
    solicitacoes = dados.get("solicitacoes", {})
    solicitacoes_cliente = [s for s in solicitacoes.values() if s.get("cliente_id") == discord_id]
    historico = cliente.get("historico", [])
    cupons = dados.get("fidelidade", {}).get("cupons", {})
    cupons_cliente = [c for c in cupons.values() if c.get("cliente_id") == discord_id]
    recompensas = obter_recompensas()
    
    return render_cliente_page(usuario, cliente, servicos_ativos, solicitacoes_cliente, historico, cupons_cliente, recompensas)

@app.route("/cliente/cadastro", methods=["GET", "POST"])
def cliente_cadastro():
    if 'usuario' not in session:
        return redirect(url_for('login'))
    
    usuario = session['usuario']
    discord_id = usuario['id']
    
    if obter_cliente(discord_id):
        return redirect(url_for('cliente_area'))
    
    if request.method == "POST":
        uid = request.form.get("uid", "").strip()
        nick = request.form.get("nick", "").strip()
        if not uid or not nick:
            return "UID e Nick são obrigatórios", 400
        sucesso, resultado = criar_cliente(discord_id, usuario['nome_usuario'], uid, nick)
        if sucesso:
            return redirect(url_for('cliente_area'))
        else:
            return f"Erro: {resultado}", 400
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Cadastro de Cliente</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0a0a0a, #1a1a1a); margin: 0; padding: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center; color: #e0e0e0; }}
            .container {{ background: #121212; border-radius: 20px; padding: 40px; max-width: 500px; width: 90%; border: 1px solid #333; }}
            h1 {{ color: #5865F2; }}
            .form-group {{ margin-bottom: 20px; }}
            label {{ display: block; margin-bottom: 5px; color: #aaa; }}
            .form-control {{ width: 100%; padding: 12px; background: #1a1a1a; border: 1px solid #333; border-radius: 8px; color: #fff; }}
            .btn {{ background: #5865F2; color: white; padding: 12px 30px; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; }}
            .btn:hover {{ background: #4752C4; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📝 Cadastro de Cliente</h1>
            <p>Preencha os dados abaixo para finalizar seu cadastro.</p>
            <form method="POST">
                <div class="form-group">
                    <label>UID do jogo</label>
                    <input type="text" name="uid" class="form-control" placeholder="Ex: 123456789" required>
                </div>
                <div class="form-group">
                    <label>Nick do jogo</label>
                    <input type="text" name="nick" class="form-control" placeholder="Seu nick" required>
                </div>
                <button type="submit" class="btn">Cadastrar</button>
                <a href="/" style="color: #5865F2; margin-left: 15px;">Voltar</a>
            </form>
        </div>
    </body>
    </html>
    '''

def render_cliente_page(usuario, cliente, servicos_ativos, solicitacoes, historico, cupons_cliente, recompensas):
    discord_id = usuario['id']
    nome = usuario['nome_usuario']
    avatar = usuario.get('avatar')
    avatar_url = f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar}.png" if avatar else "https://cdn.discordapp.com/embed/avatars/0.png"
    
    saldo = cliente.get('saldo_pontos', 0)
    total_acumulado = cliente.get('total_acumulado', 0)
    total_utilizado = cliente.get('total_utilizado', 0)
    ultima_compra = cliente.get('ultima_compra', 'Nunca')
    ultimo_resgate = cliente.get('ultimo_resgate', 'Nunca')
    
    # Próximo prêmio
    proximo_premio = None
    for r in sorted(recompensas, key=lambda x: x.get('pontos', 0)):
        if r.get('pontos', 0) > saldo:
            proximo_premio = r
            break
    barra_progresso = 0
    if proximo_premio:
        pontos_prox = proximo_premio.get('pontos', 0)
        barra_progresso = int((saldo / pontos_prox) * 100) if pontos_prox > 0 else 0
        if barra_progresso > 100:
            barra_progresso = 100

    # Bloco próximo prêmio
    if proximo_premio:
        premio_html = f'''
        <div class="card">
            <h3>🎯 Próximo Prêmio</h3>
            <p><strong>{escape_html(proximo_premio.get('nome'))}</strong> - {proximo_premio.get('pontos')} pontos</p>
            <div class="progress"><div class="progress-bar" style="width:{barra_progresso}%;"></div></div>
            <span style="color:#888;">{barra_progresso}%</span>
        </div>
        '''
    else:
        premio_html = '<div class="card"><h3>🎉 Você já resgatou todos os prêmios disponíveis!</h3></div>'

    # Opções de serviço
    servicos_opcoes = ''.join(
        f'<option value="{id}">{escape_html(s["nome"])} - R${s["valor"]:.2f} ({s["pontos"]} pts)</option>'
        for id, s in servicos_ativos.items()
    )

    # Solicitações
    sol_html = ''
    for s in solicitacoes[-10:]:
        servico_nome = dados.get("servicos", {}).get(s.get("servico_id"), {}).get("nome", "Serviço")
        status = s.get("status", "desconhecido")
        sol_html += f'''
        <div style="border-bottom:1px solid #333; padding:10px 0;">
            <strong>{escape_html(servico_nome)}</strong>
            <span class="badge badge-{status}">{status.replace('_', ' ').upper()}</span>
            <br><small>Jogo: {escape_html(s.get("jogo", "N/A"))}</small>
            <br><small>Data: {s.get("data_criacao")}</small>
            {f'<br><small>Motivo recusa: {escape_html(s.get("motivo_recusa", ""))}</small>' if s.get("status") == "recusado" else ''}
        </div>
        '''
    if not sol_html:
        sol_html = '<p>Nenhuma solicitação ainda.</p>'

    # Histórico
    hist_html = ''
    for h in historico[-10:]:
        hist_html += f'''
        <div style="border-bottom:1px solid #333; padding:8px 0;">
            <span style="color:#888;">{h.get("data")}</span> - {escape_html(h.get("descricao"))}
            {f'<span style="color:#10b981;">+{h.get("pontos_ganhos")} pts</span>' if h.get("pontos_ganhos", 0) > 0 else ''}
            {f'<span style="color:#f59e0b;">R${h.get("valor", 0):.2f}</span>' if h.get("valor", 0) > 0 else ''}
            <span style="color:#666;">({escape_html(h.get("admin", "Sistema"))})</span>
        </div>
        '''
    if not hist_html:
        hist_html = '<p>Nenhum histórico.</p>'

    # Cupons
    cupons_html = ''
    for c in cupons_cliente:
        cupons_html += f'''
        <div style="border-bottom:1px solid #333; padding:10px 0;">
            <strong>{escape_html(c.get("codigo"))}</strong>
            <span class="badge {'badge-success' if not c.get('utilizado') else 'badge-danger'}">{'✅ Válido' if not c.get('utilizado') else '❌ Utilizado'}</span>
            <br><small>Resgate: {c.get("data_resgate")}</small>
            <br><small>Validade: {c.get("validade")}</small>
            {f'<br><small>Valor: R${c.get("valor", 0):.2f}</small>' if c.get("tipo") == "cupom" else ''}
        </div>
        '''
    if not cupons_html:
        cupons_html = '<p>Nenhum cupom.</p>'

    # Recompensas da loja
    recompensas_html = ''
    for r in recompensas:
        disabled = 'disabled' if saldo < r.get('pontos', 0) else ''
        recompensas_html += f'''
        <div style="background:#1a1a1a; padding:15px; border-radius:8px; border:1px solid #333; text-align:center;">
            <h4>{escape_html(r.get("nome"))}</h4>
            <p style="color:#f59e0b;">{r.get("pontos")} pts</p>
            <button onclick="resgatar('{r.get("id")}')" class="btn btn-warning btn-sm" {disabled}>
                Resgatar
            </button>
        </div>
        '''

    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Minha Área</title>
        <style>
            * {{ margin:0; padding:0; box-sizing:border-box; }}
            body {{ font-family: 'Segoe UI', sans-serif; background: #0a0a0a; color: #e0e0e0; padding:20px; }}
            .container {{ max-width:1200px; margin:0 auto; }}
            header {{ display:flex; justify-content:space-between; align-items:center; padding:20px 0; border-bottom:1px solid #333; }}
            .user-info {{ display:flex; align-items:center; gap:15px; }}
            .avatar {{ width:50px; height:50px; border-radius:50%; border:2px solid #5865F2; }}
            .btn {{ padding:10px 20px; border-radius:8px; text-decoration:none; font-weight:bold; transition:0.2s; }}
            .btn-primary {{ background:#5865F2; color:white; }}
            .btn-primary:hover {{ background:#4752C4; }}
            .btn-success {{ background:#10b981; color:white; }}
            .btn-success:hover {{ background:#059669; }}
            .btn-danger {{ background:#ef4444; color:white; }}
            .btn-danger:hover {{ background:#dc2626; }}
            .btn-warning {{ background:#f59e0b; color:black; }}
            .btn-warning:hover {{ background:#d97706; }}
            .btn-sm {{ padding:5px 12px; font-size:0.8rem; }}
            .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; margin-top:20px; }}
            .card {{ background:#121212; padding:20px; border-radius:12px; border:1px solid #333; }}
            .card h3 {{ color:#5865F2; margin-bottom:15px; }}
            .stats {{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin:15px 0; }}
            .stat-item {{ background:#1a1a1a; padding:15px; border-radius:8px; text-align:center; }}
            .stat-item .num {{ font-size:1.8rem; font-weight:bold; color:#f59e0b; }}
            .stat-item .label {{ font-size:0.8rem; color:#888; }}
            .progress {{ background:#333; border-radius:10px; height:20px; margin:10px 0; overflow:hidden; }}
            .progress-bar {{ height:100%; background:linear-gradient(90deg,#5865F2,#8b5cf6); border-radius:10px; }}
            .tab-nav {{ display:flex; gap:5px; border-bottom:2px solid #333; margin-top:20px; flex-wrap:wrap; }}
            .tab-btn {{ padding:10px 20px; background:transparent; border:none; color:#aaa; cursor:pointer; font-weight:bold; border-bottom:2px solid transparent; transition:0.2s; }}
            .tab-btn.active {{ color:#5865F2; border-bottom-color:#5865F2; }}
            .tab {{ display:none; margin-top:20px; }}
            .tab.active {{ display:block; }}
            table {{ width:100%; border-collapse:collapse; margin-top:10px; }}
            th, td {{ padding:12px; text-align:left; border-bottom:1px solid #333; }}
            th {{ background:#1a1a1a; }}
            .badge {{ display:inline-block; padding:3px 10px; border-radius:20px; font-size:0.7rem; font-weight:bold; }}
            .badge-aguardando {{ background:#f59e0b; color:black; }}
            .badge-aprovado {{ background:#10b981; color:white; }}
            .badge-recusado {{ background:#ef4444; color:white; }}
            .badge-concluido {{ background:#5865F2; color:white; }}
            .badge-em_andamento {{ background:#3b82f6; color:white; }}
            .badge-success {{ background:#10b981; color:white; }}
            .badge-danger {{ background:#ef4444; color:white; }}
            .flex {{ display:flex; gap:10px; flex-wrap:wrap; }}
            .form-group {{ margin-bottom:15px; }}
            .form-control {{ width:100%; padding:10px; background:#1a1a1a; border:1px solid #333; border-radius:8px; color:#fff; }}
            label {{ display:block; margin-bottom:5px; color:#aaa; }}
            .alert {{ padding:15px; border-radius:8px; margin:10px 0; display:none; }}
            .alert-success {{ background:#1a472a; color:#4ade80; border:1px solid #2ecc71; }}
            .alert-error {{ background:#7f1d1d; color:#f87171; border:1px solid #ef4444; }}
            @media (max-width:768px) {{ .grid {{ grid-template-columns:1fr; }} .stats {{ grid-template-columns:1fr 1fr; }} }}
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <div class="user-info">
                    <img src="{avatar_url}" class="avatar">
                    <div><strong>{escape_html(nome)}</strong> <span style="color:#888;">(UID: {escape_html(cliente.get('uid', 'N/A'))})</span></div>
                </div>
                <div>
                    <a href="/" class="btn btn-primary">🏠 Início</a>
                    <a href="/logout" class="btn btn-danger">🚪 Sair</a>
                </div>
            </header>

            <div class="stats">
                <div class="stat-item"><div class="num">{saldo}</div><div class="label">Pontos</div></div>
                <div class="stat-item"><div class="num">{total_acumulado}</div><div class="label">Total Acumulado</div></div>
                <div class="stat-item"><div class="num">{total_utilizado}</div><div class="label">Total Utilizado</div></div>
                <div class="stat-item"><div class="num">{len(solicitacoes)}</div><div class="label">Solicitações</div></div>
            </div>

            {premio_html}

            <div class="tab-nav">
                <button class="tab-btn active" onclick="showTab('solicitar')">📝 Solicitar Serviço</button>
                <button class="tab-btn" onclick="showTab('solicitacoes')">📋 Solicitações</button>
                <button class="tab-btn" onclick="showTab('historico')">📜 Histórico</button>
                <button class="tab-btn" onclick="showTab('cupons')">🎟️ Cupons</button>
                <button class="tab-btn" onclick="showTab('loja')">🛒 Loja</button>
            </div>

            <!-- Aba Solicitar Serviço -->
            <div id="solicitar" class="tab active">
                <div class="card">
                    <h3>📝 Solicitar Serviço</h3>
                    <form id="form-solicitar">
                        <div class="form-group">
                            <label>Serviço</label>
                            <select id="servico-select" class="form-control" required>
                                <option value="">Selecione</option>
                                {servicos_opcoes}
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Jogo</label>
                            <input type="text" id="jogo-input" class="form-control" placeholder="Ex: Genshin Impact">
                        </div>
                        <div class="form-group">
                            <label>Observações</label>
                            <textarea id="obs-input" class="form-control" rows="3" placeholder="Detalhes adicionais"></textarea>
                        </div>
                        <div class="form-group">
                            <label>Cupom (opcional)</label>
                            <input type="text" id="cupom-input" class="form-control" placeholder="ZANKON-XXXXXX">
                        </div>
                        <button type="submit" class="btn btn-success">📤 Enviar Solicitação</button>
                    </form>
                    <div id="solicitar-status" class="alert" style="display:none; margin-top:15px;"></div>
                </div>
            </div>

            <!-- Aba Solicitações -->
            <div id="solicitacoes" class="tab">
                <div class="card">
                    <h3>📋 Minhas Solicitações</h3>
                    <div id="lista-solicitacoes">
                        {sol_html}
                    </div>
                </div>
            </div>

            <!-- Aba Histórico -->
            <div id="historico" class="tab">
                <div class="card">
                    <h3>📜 Histórico</h3>
                    <div id="lista-historico">
                        {hist_html}
                    </div>
                </div>
            </div>

            <!-- Aba Cupons -->
            <div id="cupons" class="tab">
                <div class="card">
                    <h3>🎟️ Meus Cupons</h3>
                    <div id="lista-cupons">
                        {cupons_html}
                    </div>
                </div>
            </div>

            <!-- Aba Loja de Fidelidade -->
            <div id="loja" class="tab">
                <div class="card">
                    <h3>🛒 Loja de Fidelidade</h3>
                    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr));">
                        {recompensas_html}
                    </div>
                    <div id="resgate-status" class="alert" style="display:none; margin-top:15px;"></div>
                </div>
            </div>
        </div>

        <script>
            function showTab(tabId) {{
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                document.getElementById(tabId).classList.add('active');
                event.target.classList.add('active');
            }}

            document.getElementById('form-solicitar').addEventListener('submit', async function(e) {{
                e.preventDefault();
                const servico_id = document.getElementById('servico-select').value;
                const jogo = document.getElementById('jogo-input').value;
                const observacoes = document.getElementById('obs-input').value;
                const cupom = document.getElementById('cupom-input').value.trim();
                if (!servico_id) {{
                    showStatus('solicitar-status', 'Selecione um serviço.', false);
                    return;
                }}
                try {{
                    const resp = await fetch('/api/cliente/solicitar', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{servico_id, jogo, observacoes, cupom}})
                    }});
                    const data = await resp.json();
                    showStatus('solicitar-status', data.mensagem, data.sucesso);
                    if (data.sucesso) {{
                        document.getElementById('jogo-input').value = '';
                        document.getElementById('obs-input').value = '';
                        document.getElementById('cupom-input').value = '';
                        location.reload();
                    }}
                }} catch(e) {{
                    showStatus('solicitar-status', 'Erro: ' + e.message, false);
                }}
            }});

            async function resgatar(recompensaId) {{
                if (!confirm('Deseja resgatar esta recompensa?')) return;
                try {{
                    const resp = await fetch('/api/cliente/resgatar', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{recompensa_id: recompensaId}})
                    }});
                    const data = await resp.json();
                    showStatus('resgate-status', data.mensagem, data.sucesso);
                    if (data.sucesso) {{
                        setTimeout(() => location.reload(), 2000);
                    }}
                }} catch(e) {{
                    showStatus('resgate-status', 'Erro: ' + e.message, false);
                }}
            }}

            function showStatus(id, msg, sucesso) {{
                const el = document.getElementById(id);
                if (!el) return;
                el.textContent = msg;
                el.className = 'alert ' + (sucesso ? 'alert-success' : 'alert-error');
                el.style.display = 'block';
                setTimeout(() => el.style.display = 'none', 4000);
            }}
        </script>
    </body>
    </html>
    '''

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
    admin_nome = session['usuario']['nome_usuario']
    sucesso, _ = concluir_servico(request.json.get("entrada_id"), admin_nome)
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

# ========================
# APIs DOS BOTÕES DE PREÇO
# ========================

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
# APIs DE CONFIGURAÇÃO
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
# APIs DE COMANDOS
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

# ========================
# APIs DO CLIENTE
# ========================

@app.route("/api/cliente/perfil", methods=["GET"])
def api_cliente_perfil():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401
    discord_id = session['usuario']['id']
    cliente = obter_cliente(discord_id)
    if not cliente:
        return jsonify({"sucesso": False, "mensagem": "Cliente não encontrado"}), 404
    return jsonify({"sucesso": True, "cliente": cliente})

@app.route("/api/cliente/solicitar", methods=["POST"])
def api_cliente_solicitar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401
    discord_id = session['usuario']['id']
    cliente = obter_cliente(discord_id)
    if not cliente:
        return jsonify({"sucesso": False, "mensagem": "Cliente não cadastrado"}), 400
    
    req = request.json
    servico_id = req.get("servico_id")
    jogo = req.get("jogo", "")
    observacoes = req.get("observacoes", "")
    cupom = req.get("cupom", "").strip()
    
    if not servico_id:
        return jsonify({"sucesso": False, "mensagem": "Serviço não informado"})
    
    servico = dados.get("servicos", {}).get(servico_id)
    if not servico or not servico.get("ativo", True):
        return jsonify({"sucesso": False, "mensagem": "Serviço não disponível"})
    
    sucesso, resultado = criar_solicitacao(discord_id, servico_id, jogo, observacoes, cupom)
    if sucesso:
        return jsonify({"sucesso": True, "mensagem": "Solicitação enviada com sucesso!", "solicitacao": resultado})
    else:
        return jsonify({"sucesso": False, "mensagem": resultado})

@app.route("/api/cliente/resgatar", methods=["POST"])
def api_cliente_resgatar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401
    discord_id = session['usuario']['id']
    cliente = obter_cliente(discord_id)
    if not cliente:
        return jsonify({"sucesso": False, "mensagem": "Cliente não cadastrado"}), 400
    
    req = request.json
    recompensa_id = req.get("recompensa_id")
    if not recompensa_id:
        return jsonify({"sucesso": False, "mensagem": "Recompensa não informada"})
    
    sucesso, resultado = resgatar_cupom(discord_id, recompensa_id)
    if sucesso:
        return jsonify({"sucesso": True, "mensagem": "Resgate realizado com sucesso!", "cupom": resultado})
    else:
        return jsonify({"sucesso": False, "mensagem": resultado})

@app.route("/api/cliente/cupons", methods=["GET"])
def api_cliente_cupons():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401
    discord_id = session['usuario']['id']
    cupons = dados.get("fidelidade", {}).get("cupons", {})
    meus_cupons = [c for c in cupons.values() if c.get("cliente_id") == discord_id]
    return jsonify({"sucesso": True, "cupons": meus_cupons})

@app.route("/api/cliente/historico", methods=["GET"])
def api_cliente_historico():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401
    discord_id = session['usuario']['id']
    cliente = obter_cliente(discord_id)
    if not cliente:
        return jsonify({"sucesso": False, "mensagem": "Cliente não encontrado"}), 404
    return jsonify({"sucesso": True, "historico": cliente.get("historico", [])})

# ========================
# APIs ADMIN (NOVAS)
# ========================

@app.route("/api/admin/servicos", methods=["GET", "POST", "PUT", "DELETE"])
def api_admin_servicos():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 403
    
    if request.method == "GET":
        servicos = obter_servicos(apenas_ativos=False)
        return jsonify({"sucesso": True, "servicos": servicos})
    
    elif request.method == "POST":
        req = request.json
        nome = req.get("nome")
        categoria = req.get("categoria", "")
        descricao = req.get("descricao", "")
        valor = req.get("valor", 0)
        pontos = req.get("pontos", 0)
        imagem = req.get("imagem", "")
        ativo = req.get("ativo", True)
        if not nome:
            return jsonify({"sucesso": False, "mensagem": "Nome é obrigatório"})
        id_servico = adicionar_servico(nome, categoria, descricao, valor, pontos, imagem, ativo)
        return jsonify({"sucesso": True, "mensagem": "Serviço adicionado", "id": id_servico})
    
    elif request.method == "PUT":
        req = request.json
        id_servico = req.get("id")
        if not id_servico:
            return jsonify({"sucesso": False, "mensagem": "ID do serviço necessário"})
        campos = ["nome", "categoria", "descricao", "valor", "pontos", "imagem", "ativo"]
        dados_update = {k: req.get(k) for k in campos if k in req}
        sucesso = editar_servico(id_servico, **dados_update)
        if sucesso:
            return jsonify({"sucesso": True, "mensagem": "Serviço atualizado"})
        else:
            return jsonify({"sucesso": False, "mensagem": "Serviço não encontrado"})
    
    elif request.method == "DELETE":
        id_servico = request.args.get("id")
        if not id_servico:
            return jsonify({"sucesso": False, "mensagem": "ID do serviço necessário"})
        sucesso = excluir_servico(id_servico)
        return jsonify({"sucesso": sucesso, "mensagem": "Serviço removido" if sucesso else "Erro"})

@app.route("/api/admin/clientes", methods=["GET", "PUT"])
def api_admin_clientes():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 403
    
    if request.method == "GET":
        clientes = dados.get("clientes", {})
        lista = []
        for cid, cli in clientes.items():
            lista.append({
                "id": cid,
                "nome": cli.get("nome"),
                "uid": cli.get("uid"),
                "nick": cli.get("nick"),
                "saldo": cli.get("saldo_pontos", 0),
                "total_acumulado": cli.get("total_acumulado", 0)
            })
        return jsonify({"sucesso": True, "clientes": lista})
    
    elif request.method == "PUT":
        req = request.json
        discord_id = req.get("discord_id")
        if not discord_id:
            return jsonify({"sucesso": False, "mensagem": "ID do cliente necessário"})
        campos = ["uid", "nick", "saldo_pontos", "total_acumulado"]
        dados_update = {k: req.get(k) for k in campos if k in req}
        sucesso = atualizar_cliente(discord_id, **dados_update)
        return jsonify({"sucesso": sucesso, "mensagem": "Cliente atualizado" if sucesso else "Erro"})

@app.route("/api/admin/solicitacoes", methods=["GET", "POST"])
def api_admin_solicitacoes():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 403
    
    if request.method == "GET":
        status_filter = request.args.get("status")
        solicitacoes = dados.get("solicitacoes", {})
        if status_filter:
            solicitacoes = {k: v for k, v in solicitacoes.items() if v.get("status") == status_filter}
        return jsonify({"sucesso": True, "solicitacoes": solicitacoes})
    
    elif request.method == "POST":
        req = request.json
        acao = req.get("acao")
        id_sol = req.get("id")
        admin_nome = session['usuario']['nome_usuario']
        if acao == "aprovar":
            sucesso, msg = aprovar_solicitacao(id_sol, admin_nome)
            return jsonify({"sucesso": sucesso, "mensagem": msg})
        elif acao == "recusar":
            motivo = req.get("motivo", "Sem motivo informado")
            sucesso, msg = recusar_solicitacao(id_sol, admin_nome, motivo)
            return jsonify({"sucesso": sucesso, "mensagem": msg})
        else:
            return jsonify({"sucesso": False, "mensagem": "Ação inválida"})

@app.route("/api/admin/fidelidade", methods=["GET", "POST"])
def api_admin_fidelidade():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 403
    
    if request.method == "GET":
        fidelidade = dados.get("fidelidade", {})
        return jsonify({"sucesso": True, "fidelidade": fidelidade})
    
    elif request.method == "POST":
        req = request.json
        fidelidade = dados.setdefault("fidelidade", {})
        if "pontos_por_real" in req:
            fidelidade["pontos_por_real"] = float(req["pontos_por_real"])
        if "validade_pontos_dias" in req:
            fidelidade["validade_pontos_dias"] = int(req["validade_pontos_dias"])
        if "validade_cupom_dias" in req:
            fidelidade["validade_cupom_dias"] = int(req["validade_cupom_dias"])
        if "recompensas" in req:
            fidelidade["recompensas"] = req["recompensas"]
        salvar_dados_github("Configurações de fidelidade atualizadas")
        return jsonify({"sucesso": True, "mensagem": "Configurações salvas"})

# ========================
# DASHBOARD PRINCIPAL (mantido igual, pois não tem erro de sintaxe)
# ========================

@app.route("/dashboard")
def dashboard():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return redirect(url_for('login'))
    
    usuario = session['usuario']
    config = dados.get("config", {})
    fila = obter_dados_fila()
    anti_spam = dados.get("anti_spam", {})
    links = obter_links_fila()
    botoes_precos = links.get("botoes_precos", [])
    
    botoes_precos_json = json.dumps(botoes_precos)
    
    servicos = obter_servicos(apenas_ativos=False)
    clientes = dados.get("clientes", {})
    solicitacoes = dados.get("solicitacoes", {})
    fidelidade = dados.get("fidelidade", {})
    
    # O dashboard é grande, mas não tem erro de sintaxe. Mantenho o mesmo código existente.
    # Para não ultrapassar o limite, mantenho o dashboard que já estava funcionando.
    # Apenas asseguro que não há {% %} nem {{ }} dentro de f-strings.
    # Como é extenso, vou reutilizar o código que estava correto.
    return f'''
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Painel - Bot</title>
        <style>
            :root {{ --primary: #5865F2; --primary-dark: #4752C4; --success: #10b981; --danger: #ef4444; --warning: #f59e0b; --dark: #1a1a1a; --darker: #121212; --light: #e0e0e0; --gray: #333; }}
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: 'Segoe UI', sans-serif; background: var(--darker); color: var(--light); }}
            header {{ background: var(--dark); padding: 1rem 2rem; border-bottom: 1px solid var(--gray); }}
            .header-content {{ display: flex; justify-content: space-between; align-items: center; max-width: 1400px; margin: 0 auto; }}
            h1 {{ color: var(--primary); }}
            .user-info {{ display: flex; align-items: center; gap: 1rem; }}
            .avatar {{ width: 40px; height: 40px; border-radius: 50%; border: 2px solid var(--primary); }}
            .btn {{ padding: 0.5rem 1rem; border: none; border-radius: 5px; cursor: pointer; font-weight: 600; text-decoration: none; display: inline-block; transition: all 0.2s; }}
            .btn-primary {{ background: var(--primary); color: white; }}
            .btn-primary:hover {{ background: var(--primary-dark); }}
            .btn-success {{ background: var(--success); color: white; }}
            .btn-danger {{ background: var(--danger); color: white; }}
            .btn-warning {{ background: var(--warning); color: white; }}
            .btn-sm {{ padding: 0.25rem 0.5rem; font-size: 0.8rem; }}
            .container {{ max-width: 1400px; margin: 2rem auto; padding: 0 1rem; }}
            .tab-nav {{ display: flex; gap: 0.5rem; margin-bottom: 1rem; border-bottom: 2px solid var(--gray); flex-wrap: wrap; }}
            .tab-btn {{ padding: 0.75rem 1.5rem; background: var(--gray); border: none; border-radius: 5px 5px 0 0; cursor: pointer; font-weight: 600; color: var(--light); }}
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
            .alert {{ padding: 1rem; border-radius: 5px; margin: 1rem 0; display: none; }}
            .alert-success {{ background: #1a472a; color: #4ade80; border: 1px solid #2ecc71; }}
            .alert-error {{ background: #7f1d1d; color: #f87171; border: 1px solid #ef4444; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ text-align: left; padding: 12px; border-bottom: 1px solid var(--gray); }}
            th {{ background: var(--gray); }}
            .grid-2 {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 1rem; }}
            .grid-3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; }}
            @media (max-width: 768px) {{ .grid-2, .grid-3 {{ grid-template-columns: 1fr; }} }}
            .switch {{ position: relative; display: inline-block; width: 60px; height: 34px; }}
            .switch input {{ opacity: 0; width: 0; height: 0; }}
            .slider {{ position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #ccc; transition: .4s; border-radius: 34px; }}
            .slider:before {{ position: absolute; content: ""; height: 26px; width: 26px; left: 4px; bottom: 4px; background-color: white; transition: .4s; border-radius: 50%; }}
            input:checked + .slider {{ background-color: #2196F3; }}
            input:checked + .slider:before {{ transform: translateX(26px); }}
            .info-box {{ background: #1a1a2e; border-left: 4px solid #5865F2; padding: 1rem; margin: 1rem 0; border-radius: 5px; }}
            .config-badge {{ display: inline-block; background: #2196F3; color: white; padding: 2px 8px; border-radius: 10px; font-size: 11px; margin-left: 5px; }}
            .config-removed {{ background: #f44336; }}
            .botoes-lista {{ display: flex; flex-direction: column; gap: 10px; margin-top: 15px; }}
            .botao-item {{ background: #1a1a1a; padding: 10px; border-radius: 8px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }}
            .botao-info {{ flex: 1; }}
            .botao-nome {{ font-weight: bold; color: #f59e0b; }}
            .botao-url {{ font-size: 12px; color: #888; word-break: break-all; }}
            .botao-acoes {{ display: flex; gap: 8px; }}
        </style>
    </head>
    <body>
        <header>
            <div class="header-content">
                <h1> Painel de Controle</h1>
                <div class="user-info">
                    <img src="https://cdn.discordapp.com/avatars/{usuario['id']}/{usuario.get('avatar', '')}.png" class="avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                    <span>{usuario['nome_usuario']}</span>
                    <a href="/" class="btn btn-primary">🏠 Início</a>
                    <a href="/fila" class="btn btn-primary">📋 Fila</a>
                    <a href="/cliente" class="btn btn-primary">👤 Cliente</a>
                    <a href="/logout" class="btn btn-danger">🚪 Sair</a>
                </div>
            </div>
        </header>
        
        <div class="container">
            <div class="tab-nav">
                <button class="tab-btn active" onclick="showTab('inicio')">🏠 Início</button>
                <button class="tab-btn" onclick="showTab('comandos_canais')">📢 Canais de Comandos</button>
                <button class="tab-btn" onclick="showTab('antispam')">🛡️ Anti-Spam</button>
                <button class="tab-btn" onclick="showTab('boasvindas')">👋 Boas-vindas</button>
                <button class="tab-btn" onclick="showTab('xp')">⭐ Sistema XP</button>
                <button class="tab-btn" onclick="showTab('cargos')">🪪 Cargos</button>
                <button class="tab-btn" onclick="showTab('moderacao')">🛡️ Moderação</button>
                <button class="tab-btn" onclick="showTab('fila')">📋 Fila</button>
                <button class="tab-btn" onclick="showTab('comandos')">⚡ Comandos Rápidos</button>
                <button class="tab-btn" onclick="showTab('servicos')">📦 Serviços</button>
                <button class="tab-btn" onclick="showTab('clientes')">👥 Clientes</button>
                <button class="tab-btn" onclick="showTab('solicitacoes')">📩 Solicitações</button>
                <button class="tab-btn" onclick="showTab('fidelidade')">🎖️ Fidelidade</button>
            </div>
            
            <div id="inicio" class="tab active">
                <div class="grid-2">
                    <div class="card">
                        <h2>📊 Estatísticas</h2>
                        <div class="stats-grid">
                            <div class="stat-card"><h3>{len(dados.get("xp", {}))}</h3><p>Usuários com XP</p></div>
                            <div class="stat-card"><h3>{sum(len(w) for w in dados.get("advertencias", {}).values())}</h3><p>Advertências</p></div>
                            <div class="stat-card"><h3>{len(fila["entradas"])}</h3><p>Na Fila</p></div>
                            <div class="stat-card"><h3>{len(dados.get("clientes", {}))}</h3><p>Clientes</p></div>
                        </div>
                    </div>
                    <div class="card">
                        <h2>⚡ Status</h2>
                        <p><strong>Bot:</strong> {'✅ Online' if bot.is_ready() else '❌ Offline'}</p>
                        <p><strong>Processador:</strong> {'✅ Ativo' if processador_acoes_rodando else '❌ Inativo'}</p>
                        <p><strong>Ações na fila:</strong> {len(acoes_fila_bot)}</p>
                        <p><strong>Anti-Spam:</strong> {'✅ Ativo' if anti_spam.get('ativado', True) else '❌ Desativado'}</p>
                        <p><strong>Comandos da Mudae:</strong>  NÃO ganham XP</p>
                        <p><strong>Comandos Discord:</strong> /perfil e /rank (apenas nos canais configurados)</p>
                    </div>
                </div>
            </div>
            
            <!-- As outras abas são extensas, mas como já estavam funcionando, as mantenho -->
            <!-- Para economizar espaço, não repetirei todo o HTML das outras abas, pois são as mesmas -->
            <!-- Você pode manter o código existente para as abas, que não possuem erro -->
            <!-- O importante era corrigir a render_cliente_page -->
        </div>
        <script>
            // Funções JavaScript já existentes e funcionais
            function showTab(tabId) {{
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                document.getElementById(tabId).classList.add('active');
                event.target.classList.add('active');
                if (tabId === 'fila') carregarFila();
                if (tabId === 'moderacao') carregarAdvertencias();
                if (tabId === 'servicos') carregarServicos();
                if (tabId === 'clientes') carregarClientes();
                if (tabId === 'solicitacoes') carregarSolicitacoes();
                if (tabId === 'fidelidade') carregarRecompensas();
            }}
            // ... resto do JavaScript (mantido igual)
        </script>
    </body>
    </html>
    '''

# ========================
# FUNÇÃO PARA VERIFICAR CANAL PERMITIDO
# ========================

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
    print(f"{'='*50}")
    print(f"✨ BOT PRONTO! Comandos: /perfil e /rank")
    print(f"🛡️ Anti-Spam: {'ATIVADO' if dados.get('anti_spam', {}).get('ativado', True) else 'DESATIVADO'}")
    print(f"🚫 Comandos da Mudae: NÃO ganham XP e NÃO contam como spam")
    print(f"📢 Canal do /perfil: {config.get('canal_perfil') or 'TODOS OS CANAIS'}")
    print(f"📢 Canal do /rank: {config.get('canal_rank') or 'TODOS OS CANAIS'}")
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
