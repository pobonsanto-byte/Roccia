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
    # ===== NOVAS ESTRUTURAS PARA SISTEMA DE SERVIÇOS =====
    "clientes": {},  # {discord_id: {uid, nome, servicos_contratados, total_gasto, pontos_fidelidade, data_cadastro, pedidos: []}}
    "pedidos": {},   # {pedido_id: {cliente_id, servico, jogo, valor, status, data_criacao, data_aprovacao, data_conclusao, observacoes, cupom_usado}}
    "fila_servicos": {  # Fila separada para serviços WW
        "nome": "Fila de Serviços Wuthering Waves",
        "configuracoes": {"tamanho_maximo": 50, "aberta": True},
        "entradas": [],
        "historico": []
    },
    "cupons": {},    # {codigo: {desconto, tipo, valor, limite_usos, usos, expiracao, criado_por}}
    "fidelidade": {  # Sistema de fidelidade
        "niveis": {
            "bronze": {"min_gasto": 0, "desconto": 0},
            "prata": {"min_gasto": 100, "desconto": 5},
            "ouro": {"min_gasto": 300, "desconto": 10},
            "diamante": {"min_gasto": 600, "desconto": 15}
        },
        "pontos_por_real": 10,
        "pontos_necessarios_cupom": 1000
    },
    "servicos_config": {
        "categorias": ["Diárias", "Semanais", "Mensais", "Eventos", "Rolamento"],
        "servicos_disponiveis": [
            {"nome": "Diárias", "valor": 10.00, "categoria": "Diárias"},
            {"nome": "Semanais", "valor": 25.00, "categoria": "Semanais"},
            {"nome": "Mensais", "valor": 80.00, "categoria": "Mensais"},
            {"nome": "Eventos", "valor": 50.00, "categoria": "Eventos"},
            {"nome": "Rolamento", "valor": 15.00, "categoria": "Rolamento"},
            {"nome": "Exploração", "valor": 30.00, "categoria": "Eventos"},
            {"nome": "Bosses", "valor": 20.00, "categoria": "Eventos"}
        ],
        "canal_pedidos": None,
        "canal_fila": None
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
                # Garantir estruturas novas
                if "clientes" not in dados:
                    dados["clientes"] = {}
                if "pedidos" not in dados:
                    dados["pedidos"] = {}
                if "fila_servicos" not in dados:
                    dados["fila_servicos"] = {
                        "nome": "Fila de Serviços Wuthering Waves",
                        "configuracoes": {"tamanho_maximo": 50, "aberta": True},
                        "entradas": [],
                        "historico": []
                    }
                if "cupons" not in dados:
                    dados["cupons"] = {}
                if "fidelidade" not in dados:
                    dados["fidelidade"] = {
                        "niveis": {
                            "bronze": {"min_gasto": 0, "desconto": 0},
                            "prata": {"min_gasto": 100, "desconto": 5},
                            "ouro": {"min_gasto": 300, "desconto": 10},
                            "diamante": {"min_gasto": 600, "desconto": 15}
                        },
                        "pontos_por_real": 10,
                        "pontos_necessarios_cupom": 1000
                    }
                if "servicos_config" not in dados:
                    dados["servicos_config"] = {
                        "categorias": ["Diárias", "Semanais", "Mensais", "Eventos", "Rolamento"],
                        "servicos_disponiveis": [
                            {"nome": "Diárias", "valor": 10.00, "categoria": "Diárias"},
                            {"nome": "Semanais", "valor": 25.00, "categoria": "Semanais"},
                            {"nome": "Mensais", "valor": 80.00, "categoria": "Mensais"},
                            {"nome": "Eventos", "valor": 50.00, "categoria": "Eventos"},
                            {"nome": "Rolamento", "valor": 15.00, "categoria": "Rolamento"},
                            {"nome": "Exploração", "valor": 30.00, "categoria": "Eventos"},
                            {"nome": "Bosses", "valor": 20.00, "categoria": "Eventos"}
                        ],
                        "canal_pedidos": None,
                        "canal_fila": None
                    }
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
# SISTEMA DE FILA (ORIGINAL)
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
# SISTEMA DE SERVIÇOS WUTHERING WAVES
# ========================

def gerar_id_pedido():
    """Gera um ID único para pedidos"""
    import random
    import string
    prefixo = "WW"
    numeros = ''.join(random.choices(string.digits, k=6))
    return f"{prefixo}{numeros}"

def gerar_codigo_cupom():
    """Gera um código de cupom único"""
    import random
    import string
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

def obter_nivel_fidelidade(total_gasto: float) -> str:
    """Retorna o nível de fidelidade baseado no gasto total"""
    niveis = dados.get("fidelidade", {}).get("niveis", {})
    nivel_atual = "bronze"
    for nivel, config in niveis.items():
        if total_gasto >= config.get("min_gasto", 0):
            nivel_atual = nivel
    return nivel_atual

def obter_desconto_fidelidade(total_gasto: float) -> float:
    """Retorna o percentual de desconto baseado no nível de fidelidade"""
    nivel = obter_nivel_fidelidade(total_gasto)
    niveis = dados.get("fidelidade", {}).get("niveis", {})
    return niveis.get(nivel, {}).get("desconto", 0)

def obter_servicos_disponiveis():
    """Retorna a lista de serviços disponíveis"""
    return dados.get("servicos_config", {}).get("servicos_disponiveis", [])

def obter_categorias_servicos():
    """Retorna as categorias de serviços"""
    return dados.get("servicos_config", {}).get("categorias", [])

def obter_fila_servicos():
    """Obtém a fila de serviços Wuthering Waves"""
    dados.setdefault("fila_servicos", {
        "nome": "Fila de Serviços Wuthering Waves",
        "configuracoes": {"tamanho_maximo": 50, "aberta": True},
        "entradas": [],
        "historico": []
    })
    return dados["fila_servicos"]

def adicionar_fila_servicos(nome_usuario: str, servico: str, jogo: str = "Wuthering Waves", usuario_id: str = None, pedido_id: str = None):
    """Adiciona uma entrada na fila de serviços WW"""
    fila = obter_fila_servicos()
    
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
        "pedido_id": pedido_id,
        "timestamp": agora_br().isoformat(),
        "status": "aguardando",
        "posicao": len(fila["entradas"]) + 1
    }
    
    fila["entradas"].append(entrada)
    atualizar_posicoes_fila_servicos(fila["entradas"])
    salvar_dados_github("Fila serviços WW atualizada")
    adicionar_log(f"fila_servicos_adicionar: {nome_usuario} - {servico}")
    return True, entrada

def atualizar_posicoes_fila_servicos(entradas):
    for i, entrada in enumerate(entradas):
        entrada["posicao"] = i + 1
        entrada["status"] = "aguardando"

def remover_fila_servicos(entrada_id: str):
    """Remove uma entrada da fila de serviços WW"""
    fila = obter_fila_servicos()
    
    for i, entrada in enumerate(fila["entradas"]):
        if entrada["id"] == entrada_id:
            removido = fila["entradas"].pop(i)
            removido["removido_em"] = agora_br().isoformat()
            fila["historico"].append(removido)
            if len(fila["historico"]) > 100:
                fila["historico"] = fila["historico"][-100:]
            atualizar_posicoes_fila_servicos(fila["entradas"])
            salvar_dados_github("Fila serviços WW atualizada")
            adicionar_log(f"fila_servicos_remover: {removido['nome_usuario']}")
            return True, removido
    return False, None

def concluir_fila_servicos(entrada_id: str):
    """Conclui um serviço na fila WW"""
    fila = obter_fila_servicos()
    for i, entrada in enumerate(fila["entradas"]):
        if entrada["id"] == entrada_id:
            removido = fila["entradas"].pop(i)
            removido["status"] = "concluido"
            removido["concluido_em"] = agora_br().isoformat()
            fila["historico"].append(removido)
            atualizar_posicoes_fila_servicos(fila["entradas"])
            salvar_dados_github("Fila serviços WW atualizada")
            adicionar_log(f"fila_servicos_concluir: {removido['nome_usuario']}")
            return True, removido
    return False, None

# ========================
# FUNÇÕES DE CADASTRO DE CLIENTES
# ========================

def cadastrar_cliente(discord_id: str, discord_nome: str, uid: str, nome_ingame: str = None):
    """Cadastra um novo cliente no sistema"""
    clientes = dados.get("clientes", {})
    
    if discord_id in clientes:
        return False, "Cliente já cadastrado"
    
    clientes[discord_id] = {
        "discord_id": discord_id,
        "discord_nome": discord_nome,
        "uid": uid,
        "nome_ingame": nome_ingame or discord_nome,
        "data_cadastro": agora_br().isoformat(),
        "total_gasto": 0.0,
        "pontos_fidelidade": 0,
        "pedidos": [],
        "ultimo_pedido": None,
        "nivel_fidelidade": "bronze"
    }
    
    dados["clientes"] = clientes
    salvar_dados_github(f"Cliente cadastrado: {discord_nome} (UID: {uid})")
    adicionar_log(f"cliente_cadastrado: {discord_nome} - UID: {uid}")
    return True, clientes[discord_id]

def obter_cliente(discord_id: str):
    """Obtém os dados de um cliente"""
    return dados.get("clientes", {}).get(str(discord_id))

def atualizar_cliente(discord_id: str, dados_cliente: dict):
    """Atualiza os dados de um cliente"""
    if str(discord_id) not in dados.get("clientes", {}):
        return False
    
    dados["clientes"][str(discord_id)].update(dados_cliente)
    salvar_dados_github(f"Cliente atualizado: {discord_id}")
    return True

# ========================
# FUNÇÕES DE PEDIDOS
# ========================

def criar_pedido(discord_id: str, servico: str, valor: float, jogo: str = "Wuthering Waves", observacoes: str = "", cupom_codigo: str = None):
    """Cria um novo pedido para um cliente"""
    cliente = obter_cliente(discord_id)
    if not cliente:
        return False, "Cliente não encontrado. Use /cadastrar primeiro."
    
    pedido_id = gerar_id_pedido()
    
    valor_final = valor
    cupom_usado = None
    desconto_aplicado = 0
    
    # Aplicar desconto de fidelidade
    desconto_fidelidade = obter_desconto_fidelidade(cliente.get("total_gasto", 0))
    if desconto_fidelidade > 0:
        desconto_aplicado = (valor * desconto_fidelidade) / 100
        valor_final = valor - desconto_aplicado
    
    # Aplicar cupom se fornecido
    if cupom_codigo:
        cupom = dados.get("cupons", {}).get(cupom_codigo.upper())
        if cupom:
            if cupom.get("usos", 0) >= cupom.get("limite_usos", 1):
                return False, "Cupom atingiu limite de usos"
            
            if cupom.get("expiracao") and datetime.fromisoformat(cupom["expiracao"]) < agora_br():
                return False, "Cupom expirado"
            
            if cupom.get("tipo") == "percentual":
                desconto_cupom = (valor * cupom.get("valor", 0)) / 100
            else:
                desconto_cupom = cupom.get("valor", 0)
            
            desconto_aplicado += desconto_cupom
            valor_final = max(0, valor - desconto_aplicado)
            cupom_usado = cupom_codigo.upper()
            
            # Registrar uso do cupom
            cupom["usos"] = cupom.get("usos", 0) + 1
            dados["cupons"][cupom_codigo.upper()] = cupom
    
    # Criar o pedido
    pedido = {
        "id": pedido_id,
        "cliente_id": str(discord_id),
        "cliente_nome": cliente.get("discord_nome", "Desconhecido"),
        "uid": cliente.get("uid", ""),
        "servico": servico,
        "jogo": jogo,
        "valor": valor,
        "valor_final": round(valor_final, 2),
        "desconto_aplicado": round(desconto_aplicado, 2),
        "desconto_fidelidade": desconto_fidelidade,
        "cupom_usado": cupom_usado,
        "status": "pendente",
        "observacoes": observacoes,
        "data_criacao": agora_br().isoformat(),
        "data_aprovacao": None,
        "data_conclusao": None,
        "aprovado_por": None
    }
    
    dados.setdefault("pedidos", {})[pedido_id] = pedido
    
    # Adicionar ao histórico do cliente
    cliente["pedidos"].append(pedido_id)
    cliente["ultimo_pedido"] = agora_br().isoformat()
    
    salvar_dados_github(f"Pedido criado: {pedido_id} - {servico} - {cliente['discord_nome']}")
    adicionar_log(f"pedido_criado: {pedido_id} - {servico} - {cliente['discord_nome']}")
    
    return True, pedido

def aprovar_pedido(pedido_id: str, aprovado_por: str):
    """Aprova um pedido e adiciona à fila"""
    pedido = dados.get("pedidos", {}).get(pedido_id)
    if not pedido:
        return False, "Pedido não encontrado"
    
    if pedido["status"] != "pendente":
        return False, f"Pedido já está {pedido['status']}"
    
    pedido["status"] = "aprovado"
    pedido["data_aprovacao"] = agora_br().isoformat()
    pedido["aprovado_por"] = aprovado_por
    
    # Adicionar à fila de serviços WW
    nome_cliente = pedido.get("cliente_nome", "Desconhecido")
    servico = pedido.get("servico", "Serviço")
    
    sucesso, _ = adicionar_fila_servicos(
        nome_usuario=nome_cliente,
        servico=servico,
        jogo=pedido.get("jogo", "Wuthering Waves"),
        usuario_id=pedido["cliente_id"],
        pedido_id=pedido_id
    )
    
    salvar_dados_github(f"Pedido aprovado: {pedido_id}")
    adicionar_log(f"pedido_aprovado: {pedido_id} - {servico}")
    
    return True, pedido

def concluir_pedido(pedido_id: str):
    """Conclui um pedido e atualiza pontos de fidelidade"""
    pedido = dados.get("pedidos", {}).get(pedido_id)
    if not pedido:
        return False, "Pedido não encontrado"
    
    if pedido["status"] not in ["aprovado", "em_andamento"]:
        return False, f"Pedido não pode ser concluído (status: {pedido['status']})"
    
    pedido["status"] = "concluido"
    pedido["data_conclusao"] = agora_br().isoformat()
    
    # Atualizar cliente com pontos de fidelidade
    cliente = obter_cliente(pedido["cliente_id"])
    if cliente:
        valor = pedido.get("valor_final", pedido.get("valor", 0))
        pontos_por_real = dados.get("fidelidade", {}).get("pontos_por_real", 10)
        pontos = int(valor * pontos_por_real)
        
        cliente["total_gasto"] = cliente.get("total_gasto", 0) + valor
        cliente["pontos_fidelidade"] = cliente.get("pontos_fidelidade", 0) + pontos
        cliente["nivel_fidelidade"] = obter_nivel_fidelidade(cliente["total_gasto"])
        
        dados["clientes"][pedido["cliente_id"]] = cliente
    
    # Remover da fila se estiver lá
    fila = obter_fila_servicos()
    for i, entrada in enumerate(fila["entradas"]):
        if entrada.get("pedido_id") == pedido_id:
            fila["entradas"].pop(i)
            atualizar_posicoes_fila_servicos(fila["entradas"])
            break
    
    salvar_dados_github(f"Pedido concluído: {pedido_id}")
    adicionar_log(f"pedido_concluido: {pedido_id}")
    
    return True, pedido

def cancelar_pedido(pedido_id: str, motivo: str = ""):
    """Cancela um pedido"""
    pedido = dados.get("pedidos", {}).get(pedido_id)
    if not pedido:
        return False, "Pedido não encontrado"
    
    pedido["status"] = "cancelado"
    pedido["motivo_cancelamento"] = motivo
    pedido["data_cancelamento"] = agora_br().isoformat()
    
    # Remover da fila se estiver lá
    fila = obter_fila_servicos()
    for i, entrada in enumerate(fila["entradas"]):
        if entrada.get("pedido_id") == pedido_id:
            fila["entradas"].pop(i)
            atualizar_posicoes_fila_servicos(fila["entradas"])
            break
    
    salvar_dados_github(f"Pedido cancelado: {pedido_id}")
    adicionar_log(f"pedido_cancelado: {pedido_id} - {motivo}")
    
    return True, pedido

def obter_pedidos_cliente(discord_id: str):
    """Obtém todos os pedidos de um cliente"""
    cliente = obter_cliente(discord_id)
    if not cliente:
        return []
    
    pedidos = []
    for pedido_id in cliente.get("pedidos", []):
        pedido = dados.get("pedidos", {}).get(pedido_id)
        if pedido:
            pedidos.append(pedido)
    
    return sorted(pedidos, key=lambda x: x.get("data_criacao", ""), reverse=True)

def obter_pedidos_pendentes():
    """Obtém todos os pedidos pendentes"""
    return [p for p in dados.get("pedidos", {}).values() if p.get("status") == "pendente"]

def obter_pedidos_aprovados():
    """Obtém todos os pedidos aprovados"""
    return [p for p in dados.get("pedidos", {}).values() if p.get("status") == "aprovado"]

# ========================
# FUNÇÕES DE CUPONS
# ========================

def criar_cupom(codigo: str, desconto: float, tipo: str = "percentual", limite_usos: int = 1, expiracao_dias: int = 30, criado_por: str = "admin"):
    """Cria um novo cupom de desconto"""
    codigo = codigo.upper()
    
    if codigo in dados.get("cupons", {}):
        return False, "Cupom já existe"
    
    expiracao = (agora_br() + timedelta(days=expiracao_dias)).isoformat()
    
    dados.setdefault("cupons", {})[codigo] = {
        "codigo": codigo,
        "desconto": desconto,
        "tipo": tipo,  # "percentual" ou "fixo"
        "limite_usos": limite_usos,
        "usos": 0,
        "expiracao": expiracao,
        "criado_por": criado_por,
        "data_criacao": agora_br().isoformat()
    }
    
    salvar_dados_github(f"Cupom criado: {codigo}")
    adicionar_log(f"cupom_criado: {codigo} - {desconto}% {tipo}")
    return True, dados["cupons"][codigo]

def obter_cupom(codigo: str):
    """Obtém os dados de um cupom"""
    return dados.get("cupons", {}).get(codigo.upper())

def listar_cupons():
    """Lista todos os cupons"""
    return list(dados.get("cupons", {}).values())

def validar_cupom(codigo: str):
    """Valida se um cupom é válido"""
    cupom = obter_cupom(codigo)
    if not cupom:
        return False, "Cupom não encontrado"
    
    if cupom.get("usos", 0) >= cupom.get("limite_usos", 1):
        return False, "Cupom atingiu limite de usos"
    
    if cupom.get("expiracao"):
        expiracao = datetime.fromisoformat(cupom["expiracao"])
        if expiracao < agora_br():
            return False, "Cupom expirado"
    
    return True, cupom

# ========================
# FUNÇÕES DE FIDELIDADE
# ========================

def resgatar_pontos_para_cupom(discord_id: str):
    """Permite que o cliente troque pontos por um cupom de desconto"""
    cliente = obter_cliente(discord_id)
    if not cliente:
        return False, "Cliente não encontrado"
    
    pontos_necessarios = dados.get("fidelidade", {}).get("pontos_necessarios_cupom", 1000)
    
    if cliente.get("pontos_fidelidade", 0) < pontos_necessarios:
        return False, f"Pontos insuficientes. Você precisa de {pontos_necessarios} pontos. Você tem {cliente.get('pontos_fidelidade', 0)}"
    
    # Gerar cupom
    codigo = gerar_codigo_cupom()
    sucesso, cupom = criar_cupom(
        codigo=codigo,
        desconto=10,
        tipo="percentual",
        limite_usos=1,
        expiracao_dias=30,
        criado_por=cliente.get("discord_nome", "cliente")
    )
    
    if sucesso:
        cliente["pontos_fidelidade"] = cliente.get("pontos_fidelidade", 0) - pontos_necessarios
        dados["clientes"][discord_id] = cliente
        salvar_dados_github(f"Pontos resgatados por {discord_id}")
        adicionar_log(f"pontos_resgatados: {discord_id} - Cupom {codigo}")
        return True, cupom
    
    return False, "Erro ao gerar cupom"

# ========================
# ROTAS DO SITE (PÁGINAS EXISTENTES)
# ========================

@app.route("/", methods=["GET"])
def home():
    status_bot = "✅ Bot Online" if bot.is_ready() else "❌ Bot Offline"
    classe_bot = "online" if bot.is_ready() else "offline"
    
    # Contagem de pedidos pendentes
    pedidos_pendentes = len(obter_pedidos_pendentes())
    
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
            <div style="margin: 10px 0; padding: 10px; background: #1a1a1a; border-radius: 8px;">
                <span style="color: #ffd93d;">📋 Pedidos Pendentes: {pedidos_pendentes}</span>
            </div>
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
                    <li>Sistema de Serviços Wuthering Waves</li>
                    <li>Cadastro de Clientes com UID</li>
                    <li>Pedidos e Fila Automática</li>
                    <li>Sistema de Fidelidade e Cupons</li>
                </ul>
            </div>
            {"<a href='/login' class='btn'>🔐 Login com Discord</a>" if 'usuario' not in session else f'<p>Olá, {session["usuario"]["nome_usuario"]}!</p><a href="/dashboard" class="btn">🚀 Painel</a><a href="/fila" class="btn">📋 Fila</a><a href="/logout" class="btn">🚪 Sair</a>'}
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
        
        access_token = r.json()['access_token']
        
        user_r = requests.get('https://discord.com/api/users/@me', headers={'Authorization': f'Bearer {access_token}'})
        if user_r.status_code != 200:
            return "Erro ao obter informações", 400
        
        user_data = user_r.json()
        
        guilds_r = requests.get('https://discord.com/api/users/@me/guilds', headers={'Authorization': f'Bearer {access_token}'})
        guilds = guilds_r.json() if guilds_r.status_code == 200 else []
        
        is_admin = False
        for guild in guilds:
            if str(guild['id']) == GUILD_ID and (guild['permissions'] & 0x8):
                is_admin = True
                break
        
        if not is_admin:
            return "<h2>⚠️ Acesso Restrito</h2><p>Apenas administradores podem acessar.</p><a href='/'>Voltar</a>", 403
        
        session['usuario'] = {
            'id': user_data['id'],
            'nome_usuario': user_data['username'],
            'avatar': user_data.get('avatar'),
            'eh_admin': True
        }
        
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        return f"Erro interno: {str(e)}", 500

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('home'))

# ========================
# ROTAS DA FILA (EXISTENTES)
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
# APIs DA FILA (EXISTENTES)
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

# ========================
# APIs DOS BOTÕES DE PREÇO (EXISTENTES)
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
# APIs DE CONFIGURAÇÃO (EXISTENTES)
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
# APIs DE COMANDOS (EXISTENTES)
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
# NOVAS APIS PARA SISTEMA DE SERVIÇOS WW
# ========================

@app.route("/api/clientes", methods=["GET", "POST"])
def api_clientes():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    
    if request.method == "GET":
        return jsonify({"sucesso": True, "clientes": dados.get("clientes", {})})
    
    req = request.json
    discord_id = req.get("discord_id")
    uid = req.get("uid")
    nome_ingame = req.get("nome_ingame")
    
    if not discord_id or not uid:
        return jsonify({"sucesso": False, "mensagem": "Discord ID e UID são obrigatórios"})
    
    # Buscar nome do Discord
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID else None
    nome_discord = "Desconhecido"
    if guild:
        member = guild.get_member(int(discord_id))
        if member:
            nome_discord = member.display_name
    
    sucesso, cliente = cadastrar_cliente(discord_id, nome_discord, uid, nome_ingame)
    return jsonify({"sucesso": sucesso, "mensagem": "Cliente cadastrado!" if sucesso else cliente, "cliente": cliente if sucesso else None})

@app.route("/api/clientes/<discord_id>")
def api_cliente(discord_id):
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    cliente = obter_cliente(discord_id)
    if not cliente:
        return jsonify({"sucesso": False, "mensagem": "Cliente não encontrado"})
    return jsonify({"sucesso": True, "cliente": cliente})

@app.route("/api/pedidos", methods=["GET", "POST"])
def api_pedidos():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    
    if request.method == "GET":
        status = request.args.get("status")
        pedidos = list(dados.get("pedidos", {}).values())
        if status:
            pedidos = [p for p in pedidos if p.get("status") == status]
        return jsonify({"sucesso": True, "pedidos": pedidos})
    
    req = request.json
    discord_id = req.get("discord_id")
    servico = req.get("servico")
    valor = req.get("valor")
    jogo = req.get("jogo", "Wuthering Waves")
    observacoes = req.get("observacoes", "")
    cupom = req.get("cupom")
    
    if not discord_id or not servico or not valor:
        return jsonify({"sucesso": False, "mensagem": "Dados incompletos"})
    
    sucesso, pedido = criar_pedido(discord_id, servico, float(valor), jogo, observacoes, cupom)
    return jsonify({
        "sucesso": sucesso,
        "mensagem": "Pedido criado!" if sucesso else pedido,
        "pedido": pedido if sucesso else None
    })

@app.route("/api/pedidos/<pedido_id>/aprovar", methods=["POST"])
def api_aprovar_pedido(pedido_id):
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    aprovado_por = session['usuario']['nome_usuario']
    sucesso, pedido = aprovar_pedido(pedido_id, aprovado_por)
    return jsonify({
        "sucesso": sucesso,
        "mensagem": "Pedido aprovado e adicionado à fila!" if sucesso else pedido,
        "pedido": pedido if sucesso else None
    })

@app.route("/api/pedidos/<pedido_id>/concluir", methods=["POST"])
def api_concluir_pedido(pedido_id):
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    sucesso, pedido = concluir_pedido(pedido_id)
    return jsonify({
        "sucesso": sucesso,
        "mensagem": "Pedido concluído!" if sucesso else pedido,
        "pedido": pedido if sucesso else None
    })

@app.route("/api/pedidos/<pedido_id>/cancelar", methods=["POST"])
def api_cancelar_pedido(pedido_id):
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    motivo = request.json.get("motivo", "")
    sucesso, pedido = cancelar_pedido(pedido_id, motivo)
    return jsonify({
        "sucesso": sucesso,
        "mensagem": "Pedido cancelado!" if sucesso else pedido,
        "pedido": pedido if sucesso else None
    })

@app.route("/api/pedidos/cliente/<discord_id>")
def api_pedidos_cliente(discord_id):
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    pedidos = obter_pedidos_cliente(discord_id)
    return jsonify({"sucesso": True, "pedidos": pedidos})

@app.route("/api/cupons", methods=["GET", "POST"])
def api_cupons():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    
    if request.method == "GET":
        return jsonify({"sucesso": True, "cupons": listar_cupons()})
    
    req = request.json
    codigo = req.get("codigo")
    desconto = req.get("desconto")
    tipo = req.get("tipo", "percentual")
    limite_usos = req.get("limite_usos", 1)
    expiracao_dias = req.get("expiracao_dias", 30)
    
    if not codigo or not desconto:
        return jsonify({"sucesso": False, "mensagem": "Código e desconto são obrigatórios"})
    
    sucesso, cupom = criar_cupom(codigo, float(desconto), tipo, int(limite_usos), int(expiracao_dias), session['usuario']['nome_usuario'])
    return jsonify({
        "sucesso": sucesso,
        "mensagem": "Cupom criado!" if sucesso else cupom,
        "cupom": cupom if sucesso else None
    })

@app.route("/api/cupons/<codigo>/validar")
def api_validar_cupom(codigo):
    sucesso, resultado = validar_cupom(codigo)
    return jsonify({"sucesso": sucesso, "mensagem": "Cupom válido!" if sucesso else resultado, "cupom": resultado if sucesso else None})

@app.route("/api/fidelidade/<discord_id>")
def api_fidelidade(discord_id):
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    cliente = obter_cliente(discord_id)
    if not cliente:
        return jsonify({"sucesso": False, "mensagem": "Cliente não encontrado"})
    
    return jsonify({
        "sucesso": True,
        "fidelidade": {
            "nivel": cliente.get("nivel_fidelidade", "bronze"),
            "pontos": cliente.get("pontos_fidelidade", 0),
            "total_gasto": cliente.get("total_gasto", 0),
            "desconto": obter_desconto_fidelidade(cliente.get("total_gasto", 0)),
            "pontos_para_cupom": dados.get("fidelidade", {}).get("pontos_necessarios_cupom", 1000)
        }
    })

@app.route("/api/fidelidade/resgatar", methods=["POST"])
def api_resgatar_cupom():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    discord_id = request.json.get("discord_id")
    if not discord_id:
        return jsonify({"sucesso": False, "mensagem": "Discord ID é obrigatório"})
    
    sucesso, resultado = resgatar_pontos_para_cupom(discord_id)
    return jsonify({
        "sucesso": sucesso,
        "mensagem": "Cupom resgatado!" if sucesso else resultado,
        "cupom": resultado if sucesso else None
    })

@app.route("/api/servicos/disponiveis")
def api_servicos_disponiveis():
    return jsonify({"sucesso": True, "servicos": obter_servicos_disponiveis()})

@app.route("/api/servicos/categorias")
def api_servicos_categorias():
    return jsonify({"sucesso": True, "categorias": obter_categorias_servicos()})

@app.route("/api/fila_servicos")
def api_fila_servicos():
    fila = obter_fila_servicos()
    return jsonify({
        "sucesso": True,
        "fila": {
            "nome": fila["nome"],
            "aberta": fila["configuracoes"]["aberta"],
            "tamanho_maximo": fila["configuracoes"]["tamanho_maximo"],
            "contagem": len(fila["entradas"]),
            "entradas": [{"posicao": e["posicao"], "nome_usuario": e["nome_usuario"], "servico": e["servico"], "jogo": e.get("jogo", ""), "pedido_id": e.get("pedido_id"), "timestamp": e["timestamp"], "id": e["id"]} for e in fila["entradas"]]
        }
    })

@app.route("/api/fila_servicos/adicionar", methods=["POST"])
def api_fila_servicos_adicionar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    nome = req.get("nome_usuario", "").strip()
    servico = req.get("servico", "").strip()
    jogo = req.get("jogo", "Wuthering Waves")
    pedido_id = req.get("pedido_id")
    
    if not nome or not servico:
        return jsonify({"sucesso": False, "mensagem": "Nome e serviço são obrigatórios"})
    
    sucesso, entrada = adicionar_fila_servicos(nome, servico, jogo, nome, pedido_id)
    return jsonify({"sucesso": sucesso, "mensagem": f"{nome} adicionado!" if sucesso else entrada})

@app.route("/api/fila_servicos/remover", methods=["POST"])
def api_fila_servicos_remover():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    entrada_id = request.json.get("entrada_id")
    if not entrada_id:
        return jsonify({"sucesso": False, "mensagem": "ID da entrada é obrigatório"})
    sucesso, _ = remover_fila_servicos(entrada_id)
    return jsonify({"sucesso": sucesso})

@app.route("/api/fila_servicos/concluir", methods=["POST"])
def api_fila_servicos_concluir():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    entrada_id = request.json.get("entrada_id")
    if not entrada_id:
        return jsonify({"sucesso": False, "mensagem": "ID da entrada é obrigatório"})
    sucesso, entrada = concluir_fila_servicos(entrada_id)
    return jsonify({"sucesso": sucesso, "mensagem": "Serviço concluído!" if sucesso else entrada})

@app.route("/api/fila_servicos/configuracoes", methods=["POST"])
def api_fila_servicos_configuracoes():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    fila = obter_fila_servicos()
    if "aberta" in req:
        fila["configuracoes"]["aberta"] = req["aberta"]
    if "tamanho_maximo" in req:
        fila["configuracoes"]["tamanho_maximo"] = max(1, min(int(req["tamanho_maximo"]), 100))
    if "nome" in req:
        fila["nome"] = req["nome"][:50]
    salvar_dados_github("Config fila serviços WW atualizada")
    return jsonify({"sucesso": True})

# ========================
# DASHBOARD PRINCIPAL (ATUALIZADO)
# ========================

@app.route("/dashboard")
def dashboard():
    if 'usuario' not in session:
        return redirect(url_for('login'))
    
    usuario = session['usuario']
    config = dados.get("config", {})
    fila = obter_dados_fila()
    anti_spam = dados.get("anti_spam", {})
    links = obter_links_fila()
    botoes_precos = links.get("botoes_precos", [])
    fila_ww = obter_fila_servicos()
    pedidos_pendentes = len(obter_pedidos_pendentes())
    total_clientes = len(dados.get("clientes", {}))
    
    botoes_precos_json = json.dumps(botoes_precos)
    
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
            .header-content {{ display: flex; justify-content: space-between; align-items: center; max-width: 1400px; margin: 0 auto; flex-wrap: wrap; gap: 10px; }}
            h1 {{ color: var(--primary); }}
            .user-info {{ display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }}
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
            .stat-card .label {{ font-size: 0.8rem; opacity: 0.8; }}
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
            .status-badge {{ padding: 2px 10px; border-radius: 10px; font-size: 12px; }}
            .status-pendente {{ background: #f59e0b; color: #000; }}
            .status-aprovado {{ background: #3b82f6; color: #fff; }}
            .status-concluido {{ background: #10b981; color: #fff; }}
            .status-cancelado {{ background: #ef4444; color: #fff; }}
            .botoes-lista {{ display: flex; flex-direction: column; gap: 10px; margin-top: 15px; }}
            .botao-item {{ background: #1a1a1a; padding: 10px; border-radius: 8px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }}
            .botao-info {{ flex: 1; }}
            .botao-nome {{ font-weight: bold; color: #f59e0b; }}
            .botao-url {{ font-size: 12px; color: #888; word-break: break-all; }}
            .botao-acoes {{ display: flex; gap: 8px; }}
            .status-dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 5px; }}
            .dot-aberta {{ background: #10b981; }}
            .dot-fechada {{ background: #ef4444; }}
            .fila-ww-container {{ max-height: 400px; overflow-y: auto; }}
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
                    <a href="/logout" class="btn btn-danger">🚪 Sair</a>
                </div>
            </div>
        </header>
        
        <div class="container">
            <div class="tab-nav">
                <button class="tab-btn active" onclick="showTab('inicio')">🏠 Início</button>
                <button class="tab-btn" onclick="showTab('comandos_canais')">📢 Canais</button>
                <button class="tab-btn" onclick="showTab('antispam')">🛡️ Anti-Spam</button>
                <button class="tab-btn" onclick="showTab('boasvindas')">👋 Boas-vindas</button>
                <button class="tab-btn" onclick="showTab('xp')">⭐ XP</button>
                <button class="tab-btn" onclick="showTab('cargos')">🪪 Cargos</button>
                <button class="tab-btn" onclick="showTab('moderacao')">🛡️ Moderação</button>
                <button class="tab-btn" onclick="showTab('fila')">📋 Fila</button>
                <button class="tab-btn" onclick="showTab('comandos')">⚡ Comandos</button>
                <button class="tab-btn" onclick="showTab('servicos')">🎮 Serviços WW</button>
                <button class="tab-btn" onclick="showTab('clientes')">👤 Clientes</button>
                <button class="tab-btn" onclick="showTab('pedidos')">📦 Pedidos</button>
                <button class="tab-btn" onclick="showTab('cupons')">🏷️ Cupons</button>
                <button class="tab-btn" onclick="showTab('fidelidade')">⭐ Fidelidade</button>
            </div>
            
            <!-- Aba Início -->
            <div id="inicio" class="tab active">
                <div class="grid-2">
                    <div class="card">
                        <h2>📊 Estatísticas</h2>
                        <div class="stats-grid">
                            <div class="stat-card"><h3>{len(dados.get("xp", {}))}</h3><span class="label">Usuários com XP</span></div>
                            <div class="stat-card"><h3>{sum(len(w) for w in dados.get("advertencias", {}).values())}</h3><span class="label">Advertências</span></div>
                            <div class="stat-card"><h3>{len(fila["entradas"])}</h3><span class="label">Na Fila</span></div>
                            <div class="stat-card"><h3>{total_clientes}</h3><span class="label">Clientes</span></div>
                            <div class="stat-card"><h3>{pedidos_pendentes}</h3><span class="label">Pedidos Pendentes</span></div>
                            <div class="stat-card"><h3>{len(fila_ww["entradas"])}</h3><span class="label">Fila WW</span></div>
                        </div>
                    </div>
                    <div class="card">
                        <h2>⚡ Status</h2>
                        <p><strong>Bot:</strong> {'✅ Online' if bot.is_ready() else '❌ Offline'}</p>
                        <p><strong>Processador:</strong> {'✅ Ativo' if processador_acoes_rodando else '❌ Inativo'}</p>
                        <p><strong>Ações na fila:</strong> {len(acoes_fila_bot)}</p>
                        <p><strong>Anti-Spam:</strong> {'✅ Ativo' if anti_spam.get('ativado', True) else '❌ Desativado'}</p>
                        <p><strong>Fila WW:</strong> {'🟢 Aberta' if fila_ww['configuracoes']['aberta'] else '🔴 Fechada'}</p>
                        <p><strong>Comandos Discord:</strong> /perfil e /rank (apenas nos canais configurados)</p>
                    </div>
                </div>
            </div>
            
            <!-- Aba Canais de Comandos -->
            <div id="comandos_canais" class="tab">
                <div class="card">
                    <h2>📢 Configurar Canais dos Comandos</h2>
                    <div class="info-box">
                        💡 <strong>Como funciona:</strong><br>
                        • Selecione um canal → O comando só funcionará naquele canal<br>
                        • Selecione o <strong>mesmo canal novamente</strong> → Remove a restrição (volta a funcionar em todos os canais)<br>
                        • Deixe em "🔓 Todos os canais" → O comando funciona em qualquer lugar
                    </div>
                    <div class="grid-2">
                        <div class="form-group">
                            <label>Canal para o comando /perfil</label>
                            <select id="canal-perfil" class="form-control">
                                <option value="">🔓 Todos os canais</option>
                            </select>
                            <div id="perfil-status" style="margin-top: 8px;"></div>
                        </div>
                        <div class="form-group">
                            <label>Canal para o comando /rank</label>
                            <select id="canal-rank" class="form-control">
                                <option value="">🔓 Todos os canais</option>
                            </select>
                            <div id="rank-status" style="margin-top: 8px;"></div>
                        </div>
                    </div>
                    <button onclick="salvarConfigComandos()" class="btn btn-primary">💾 Salvar Configurações</button>
                    <div id="comandos-alert" class="alert"></div>
                </div>
                <div class="card">
                    <h2>ℹ️ Informações</h2>
                    <p>Comandos disponíveis no Discord:</p>
                    <ul style="margin-left: 20px; margin-top: 10px;">
                        <li><code>/perfil [membro]</code> - Mostra o perfil com XP e nível</li>
                        <li><code>/rank</code> - Mostra o ranking de XP do servidor</li>
                        <li><code>/cadastrar [uid] [nome_ingame]</code> - Cadastra no sistema WW</li>
                        <li><code>/pedido [servico] [valor] [observacoes]</code> - Cria um pedido WW</li>
                        <li><code>/meuperfil</code> - Mostra seu perfil WW com pedidos</li>
                        <li><code>/fila_ww</code> - Mostra a fila de serviços WW</li>
                        <li><code>/cupom [codigo]</code> - Valida um cupom</li>
                    </ul>
                </div>
            </div>
            
            <!-- Aba Anti-Spam (mesma de antes) -->
            <div id="antispam" class="tab">
                <div class="card">
                    <h2>🛡️ Configuração Anti-Spam</h2>
                    <div class="info-box">💡 <strong>Comandos da Mudae são ignorados automaticamente</strong> - Eles NÃO contam como spam e NÃO ganham XP!</div>
                    <div class="grid-2">
                        <div class="form-group">
                            <label>Status do Anti-Spam</label>
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
                            <label>Deletar Mensagens de Spam</label>
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
                            <label>Cargos Ignorados (separar por vírgula)</label>
                            <input type="text" id="as-cargos" class="form-control" value="{','.join(anti_spam.get('cargos_ignorados', ['Administrador', 'Moderador', 'Staff', 'Dono']))}">
                        </div>
                        <div class="form-group">
                            <label>Comandos Ignorados (separar por vírgula)</label>
                            <input type="text" id="as-comandos" class="form-control" value="{','.join(anti_spam.get('comandos_ignorados', ['$w','$wa','$wg','$h','$ha','$hg','$tu','$dk','$mmi','$vote','$rolls','$k','$mu']))}">
                        </div>
                    </div>
                    <button onclick="salvarAntiSpam()" class="btn btn-primary">💾 Salvar Configurações</button>
                    <div id="as-alert" class="alert"></div>
                </div>
                <div class="card">
                    <h2>📋 Comandos Ignorados</h2>
                    <div id="lista-comandos" style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px;"></div>
                </div>
            </div>
            
            <!-- Aba Boas-vindas (mesma de antes) -->
            <div id="boasvindas" class="tab">
                <div class="card">
                    <h2>👋 Configurar Boas-vindas</h2>
                    <div class="form-group">
                        <label>Canal de Boas-vindas</label>
                        <select id="welcome-canal" class="form-control"></select>
                    </div>
                    <div class="form-group">
                        <label>Mensagem de Boas-vindas</label>
                        <textarea id="welcome-mensagem" class="form-control" rows="3"></textarea>
                        <small>Use {{member}} para mencionar o membro</small>
                    </div>
                    <div class="form-group">
                        <label>Imagem de Fundo (URL)</label>
                        <input type="url" id="welcome-imagem" class="form-control" placeholder="https://exemplo.com/imagem.jpg">
                    </div>
                    <button onclick="salvarBoasVindas()" class="btn btn-primary">💾 Salvar</button>
                    <div id="welcome-alert" class="alert"></div>
                </div>
            </div>
            
            <!-- Aba XP (mesma de antes) -->
            <div id="xp" class="tab">
                <div class="card">
                    <h2>⭐ Sistema de XP</h2>
                    <div class="form-group">
                        <label>Taxa de XP (1=fácil, 10=difícil)</label>
                        <input type="number" id="xp-taxa" class="form-control" min="1" max="10">
                    </div>
                    <div class="form-group">
                        <label>Canal de Level Up</label>
                        <select id="xp-canal" class="form-control"></select>
                    </div>
                    <button onclick="salvarXP()" class="btn btn-primary">💾 Salvar</button>
                    <div id="xp-alert" class="alert"></div>
                </div>
                <div class="card">
                    <h2>🪪 Cargos por Nível</h2>
                    <div id="cargos-nivel-lista"></div>
                    <div class="form-group">
                        <label>Adicionar Cargo por Nível</label>
                        <div style="display: flex; gap: 1rem; flex-wrap: wrap;">
                            <input type="number" id="novo-nivel" class="form-control" placeholder="Nível" min="1" style="width: 100px;">
                            <select id="novo-cargo" class="form-control" style="flex:1;"></select>
                            <button onclick="adicionarCargoNivel()" class="btn btn-primary">➕ Adicionar</button>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Aba Cargos (mesma de antes) -->
            <div id="cargos" class="tab">
                <div class="grid-2">
                    <div class="card">
                        <h2>🪪 Reação com Cargo</h2>
                        <div class="form-group">
                            <label>Canal</label>
                            <select id="rr-canal" class="form-control"></select>
                        </div>
                        <div class="form-group">
                            <label>Mensagem</label>
                            <textarea id="rr-conteudo" class="form-control" rows="3" placeholder="Reaja para receber cargos!"></textarea>
                        </div>
                        <div class="form-group">
                            <label>Emoji:Cargo (separar por vírgula)</label>
                            <input type="text" id="rr-pares" class="form-control" placeholder="✅:Verificado,👍:Aprovado,⭐:VIP">
                            <small>Ex: ✅:Verificado, 👍:Aprovado, &lt;:custom:123456789&gt;:VIP</small>
                        </div>
                        <button onclick="criarReacaoCargo()" class="btn btn-primary">✨ Criar</button>
                        <div id="rr-alert" class="alert"></div>
                    </div>
                    <div class="card">
                        <h2>🔄 Botões de Cargos</h2>
                        <div class="form-group">
                            <label>Canal</label>
                            <select id="btn-canal" class="form-control"></select>
                        </div>
                        <div class="form-group">
                            <label>Mensagem</label>
                            <textarea id="btn-conteudo" class="form-control" rows="3" placeholder="Clique nos botões para receber cargos!"></textarea>
                        </div>
                        <div class="form-group">
                            <label>Botão:Cargo (separar por vírgula)</label>
                            <input type="text" id="btn-pares" class="form-control" placeholder="Notícias:Notícias,Eventos:Eventos,VIP:VIP">
                        </div>
                        <button onclick="criarBotoesCargo()" class="btn btn-success">🔄 Criar Botões</button>
                        <div id="btn-alert" class="alert"></div>
                    </div>
                </div>
            </div>
            
            <!-- Aba Moderação (mesma de antes) -->
            <div id="moderacao" class="tab">
                <div class="grid-2">
                    <div class="card">
                        <h2>🛡️ Advertências</h2>
                        <div class="form-group">
                            <label>Membro</label>
                            <select id="warn-membro" class="form-control"></select>
                        </div>
                        <div class="form-group">
                            <label>Motivo</label>
                            <input type="text" id="warn-motivo" class="form-control" placeholder="Motivo da advertência">
                        </div>
                        <button onclick="aplicarAdvertencia()" class="btn btn-warning">⚠️ Advertir</button>
                        <button onclick="limparAdvertencias()" class="btn btn-danger">🧹 Limpar Advertências</button>
                        <div id="warn-alert" class="alert"></div>
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
                <div class="card">
                    <h2>📋 Lista de Advertências</h2>
                    <div class="form-group">
                        <label>Ver advertências de</label>
                        <select id="ver-warns" class="form-control" onchange="carregarAdvertencias()"></select>
                    </div>
                    <div id="lista-warns" style="margin-top: 1rem; padding: 1rem; background: #1a1a1a; border-radius: 5px; border: 1px solid var(--gray);"></div>
                </div>
            </div>
            
            <!-- Aba Fila (mesma de antes) -->
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
                    <div class="info-box">💡 <strong>Adicione quantos botões quiser!</strong> Cada botão terá um nome personalizado e um link diferente.</div>
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
                    <h2>➕ Adicionar à Fila</h2>
                    <div style="display: flex; gap: 1rem; flex-wrap: wrap;">
                        <input type="text" id="add-nome" class="form-control" placeholder="Nome do jogador" style="flex:1;">
                        <input type="text" id="add-servico" class="form-control" placeholder="Serviço" style="flex:1;">
                        <input type="text" id="add-jogo" class="form-control" placeholder="Jogo" style="flex:1;">
                        <button onclick="adicionarFila()" class="btn btn-primary">➕ Adicionar</button>
                    </div>
                    <div id="add-result" class="alert" style="margin-top: 10px; display: none;"></div>
                </div>
                <div class="card">
                    <h2>📋 Lista de Espera</h2>
                    <div style="overflow-x: auto;">
                        <table style="width:100%">
                            <thead>
                                <tr><th>#</th><th>Jogador</th><th>Serviço</th><th>Jogo</th><th>Entrada</th><th>Ações</th></tr>
                            </thead>
                            <tbody id="fila-tabela"><tr><td colspan="6">Carregando...</td></tr></tbody>
                        </table>
                    </div>
                    <div style="margin-top: 10px;"><button onclick="atualizarFila()" class="btn btn-primary">🔄 Atualizar</button></div>
                </div>
            </div>
            
            <!-- Aba Comandos Rápidos (mesma de antes) -->
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
            
            <!-- Aba Serviços WW -->
            <div id="servicos" class="tab">
                <div class="card">
                    <h2>🎮 Gerenciamento de Serviços Wuthering Waves</h2>
                    <div class="grid-2">
                        <div>
                            <h3>📋 Fila de Serviços WW</h3>
                            <p>Status: <span class="status-dot {'dot-aberta' if fila_ww['configuracoes']['aberta'] else 'dot-fechada'}"></span> {'Aberta' if fila_ww['configuracoes']['aberta'] else 'Fechada'}</p>
                            <p>Na fila: {len(fila_ww['entradas'])} / {fila_ww['configuracoes']['tamanho_maximo']}</p>
                            <div class="fila-ww-container" style="margin-top: 10px;">
                                {''.join(f'<div style="display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid #333;"><span style="color:#ffd93d;">#{e["posicao"]}</span><span>{escape_html(e["nome_usuario"])}</span><span style="color:#a8e6cf;">{escape_html(e["servico"])}</span><span style="font-size:12px;color:#888;">{e.get("pedido_id", "")}</span></div>' for e in fila_ww['entradas'][:10]) or '<div style="text-align:center;padding:20px;color:#888;">📭 Ninguém na fila</div>'}
                            </div>
                            <button onclick="carregarFilaWW()" class="btn btn-primary btn-sm" style="margin-top:10px;">🔄 Atualizar</button>
                        </div>
                        <div>
                            <h3>⚙️ Configurações</h3>
                            <div class="form-group">
                                <label>Nome da Fila WW</label>
                                <input type="text" id="fila-ww-nome" class="form-control" value="{escape_html(fila_ww['nome'])}">
                            </div>
                            <div class="form-group">
                                <label>Tamanho Máximo</label>
                                <input type="number" id="fila-ww-max" class="form-control" value="{fila_ww['configuracoes']['tamanho_maximo']}" min="1" max="100">
                            </div>
                            <div style="display:flex; gap:10px; flex-wrap:wrap;">
                                <button onclick="salvarConfigFilaWW()" class="btn btn-primary">💾 Salvar</button>
                                <button onclick="alternarFilaWW()" class="btn {'btn-success' if fila_ww['configuracoes']['aberta'] else 'btn-danger'}">{'🔓 Fechar' if fila_ww['configuracoes']['aberta'] else '🔒 Abrir'}</button>
                            </div>
                            <div id="fila-ww-alert" class="alert" style="margin-top:10px;"></div>
                        </div>
                    </div>
                </div>
                
                <div class="card">
                    <h2>📦 Adicionar à Fila WW</h2>
                    <div style="display:flex; gap:10px; flex-wrap:wrap;">
                        <input type="text" id="ww-add-nome" class="form-control" placeholder="Nome do jogador" style="flex:1;">
                        <input type="text" id="ww-add-servico" class="form-control" placeholder="Serviço" style="flex:1;">
                        <input type="text" id="ww-add-pedido" class="form-control" placeholder="ID do Pedido (opcional)" style="flex:0.7;">
                        <button onclick="adicionarFilaWW()" class="btn btn-success">➕ Adicionar</button>
                    </div>
                    <div id="ww-add-result" class="alert" style="margin-top:10px; display:none;"></div>
                </div>
            </div>
            
            <!-- Aba Clientes -->
            <div id="clientes" class="tab">
                <div class="card">
                    <h2>👤 Cadastrar Cliente</h2>
                    <div class="grid-2">
                        <div class="form-group">
                            <label>Discord ID</label>
                            <input type="text" id="cli-discord-id" class="form-control" placeholder="ID do Discord">
                        </div>
                        <div class="form-group">
                            <label>UID do Jogo</label>
                            <input type="text" id="cli-uid" class="form-control" placeholder="UID Wuthering Waves">
                        </div>
                        <div class="form-group">
                            <label>Nome In-Game (opcional)</label>
                            <input type="text" id="cli-nome" class="form-control" placeholder="Nome no jogo">
                        </div>
                    </div>
                    <button onclick="cadastrarCliente()" class="btn btn-success">📝 Cadastrar Cliente</button>
                    <div id="cli-alert" class="alert" style="margin-top:10px;"></div>
                </div>
                
                <div class="card">
                    <h2>📋 Lista de Clientes</h2>
                    <div style="overflow-x:auto;">
                        <table>
                            <thead><tr><th>Discord</th><th>UID</th><th>Nome In-Game</th><th>Nível</th><th>Gasto</th><th>Pontos</th><th>Pedidos</th></tr></thead>
                            <tbody id="clientes-tabela"><tr><td colspan="7">Carregando...</td></tr></tbody>
                        </table>
                    </div>
                    <button onclick="carregarClientes()" class="btn btn-primary" style="margin-top:10px;">🔄 Atualizar</button>
                </div>
            </div>
            
            <!-- Aba Pedidos -->
            <div id="pedidos" class="tab">
                <div class="card">
                    <h2>📦 Criar Pedido</h2>
                    <div class="grid-2">
                        <div class="form-group">
                            <label>Discord ID do Cliente</label>
                            <input type="text" id="ped-discord-id" class="form-control" placeholder="ID do Discord">
                        </div>
                        <div class="form-group">
                            <label>Serviço</label>
                            <select id="ped-servico" class="form-control">
                                <option value="">Selecione um serviço</option>
                                {''.join(f'<option value="{s["nome"]}">{s["nome"]} - R$ {s["valor"]:.2f}</option>' for s in obter_servicos_disponiveis())}
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Valor (R$)</label>
                            <input type="number" id="ped-valor" class="form-control" placeholder="0.00" step="0.01">
                        </div>
                        <div class="form-group">
                            <label>Cupom (opcional)</label>
                            <input type="text" id="ped-cupom" class="form-control" placeholder="Código do cupom">
                        </div>
                        <div class="form-group" style="grid-column: span 2;">
                            <label>Observações</label>
                            <textarea id="ped-obs" class="form-control" rows="2" placeholder="Observações sobre o pedido"></textarea>
                        </div>
                    </div>
                    <button onclick="criarPedido()" class="btn btn-success">📦 Criar Pedido</button>
                    <div id="ped-alert" class="alert" style="margin-top:10px;"></div>
                </div>
                
                <div class="card">
                    <h2>📋 Gerenciar Pedidos</h2>
                    <div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:10px;">
                        <select id="filtro-pedidos" class="form-control" style="width:auto;" onchange="carregarPedidos()">
                            <option value="todos">Todos</option>
                            <option value="pendente">Pendentes</option>
                            <option value="aprovado">Aprovados</option>
                            <option value="concluido">Concluídos</option>
                            <option value="cancelado">Cancelados</option>
                        </select>
                        <button onclick="carregarPedidos()" class="btn btn-primary">🔄 Atualizar</button>
                    </div>
                    <div style="overflow-x:auto;">
                        <table>
                            <thead><tr><th>ID</th><th>Cliente</th><th>Serviço</th><th>Valor</th><th>Desconto</th><th>Status</th><th>Ações</th></tr></thead>
                            <tbody id="pedidos-tabela"><tr><td colspan="7">Carregando...</td></tr></tbody>
                        </table>
                    </div>
                </div>
            </div>
            
            <!-- Aba Cupons -->
            <div id="cupons" class="tab">
                <div class="card">
                    <h2>🏷️ Criar Cupom</h2>
                    <div class="grid-2">
                        <div class="form-group">
                            <label>Código do Cupom</label>
                            <input type="text" id="cup-codigo" class="form-control" placeholder="Ex: WW10">
                        </div>
                        <div class="form-group">
                            <label>Desconto</label>
                            <input type="number" id="cup-desconto" class="form-control" placeholder="10" step="0.01">
                        </div>
                        <div class="form-group">
                            <label>Tipo</label>
                            <select id="cup-tipo" class="form-control">
                                <option value="percentual">Percentual (%)</option>
                                <option value="fixo">Valor Fixo (R$)</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Limite de Usos</label>
                            <input type="number" id="cup-limite" class="form-control" value="1" min="1">
                        </div>
                        <div class="form-group">
                            <label>Dias até Expirar</label>
                            <input type="number" id="cup-dias" class="form-control" value="30" min="1">
                        </div>
                    </div>
                    <button onclick="criarCupom()" class="btn btn-success">🏷️ Criar Cupom</button>
                    <div id="cup-alert" class="alert" style="margin-top:10px;"></div>
                </div>
                
                <div class="card">
                    <h2>📋 Lista de Cupons</h2>
                    <div style="overflow-x:auto;">
                        <table>
                            <thead><tr><th>Código</th><th>Desconto</th><th>Tipo</th><th>Usos</th><th>Limite</th><th>Expiração</th><th>Status</th></tr></thead>
                            <tbody id="cupons-tabela"><tr><td colspan="7">Carregando...</td></tr></tbody>
                        </table>
                    </div>
                    <button onclick="carregarCupons()" class="btn btn-primary" style="margin-top:10px;">🔄 Atualizar</button>
                </div>
            </div>
            
            <!-- Aba Fidelidade -->
            <div id="fidelidade" class="tab">
                <div class="card">
                    <h2>⭐ Sistema de Fidelidade</h2>
                    <div class="grid-4">
                        <div class="stat-card" style="background: #cd7f32;"><h3>Bronze</h3><span class="label">Gasto: R$ 0+ | 0% desconto</span></div>
                        <div class="stat-card" style="background: #c0c0c0; color:#000;"><h3>Prata</h3><span class="label">Gasto: R$ 100+ | 5% desconto</span></div>
                        <div class="stat-card" style="background: #ffd700; color:#000;"><h3>Ouro</h3><span class="label">Gasto: R$ 300+ | 10% desconto</span></div>
                        <div class="stat-card" style="background: #b9f2ff; color:#000;"><h3>Diamante</h3><span class="label">Gasto: R$ 600+ | 15% desconto</span></div>
                    </div>
                    <div class="info-box">
                        💡 <strong>Como funciona:</strong> Cada R$ 1,00 gasto = {dados.get('fidelidade', {}).get('pontos_por_real', 10)} pontos. 
                        Com {dados.get('fidelidade', {}).get('pontos_necessarios_cupom', 1000)} pontos você pode resgatar um cupom de 10% de desconto!
                    </div>
                </div>
                
                <div class="card">
                    <h2>💰 Consultar Fidelidade</h2>
                    <div style="display:flex; gap:10px; flex-wrap:wrap;">
                        <input type="text" id="fid-discord-id" class="form-control" placeholder="Discord ID do cliente" style="flex:1;">
                        <button onclick="consultarFidelidade()" class="btn btn-primary">🔍 Consultar</button>
                    </div>
                    <div id="fid-resultado" style="margin-top: 15px; padding: 15px; background: #1a1a1a; border-radius: 8px; display:none;"></div>
                </div>
                
                <div class="card">
                    <h2>🎁 Resgatar Cupom com Pontos</h2>
                    <div style="display:flex; gap:10px; flex-wrap:wrap;">
                        <input type="text" id="fid-resgatar-id" class="form-control" placeholder="Discord ID do cliente" style="flex:1;">
                        <button onclick="resgatarCupomFidelidade()" class="btn btn-warning">🎁 Resgatar Cupom</button>
                    </div>
                    <div id="fid-resgatar-alert" class="alert" style="margin-top:10px;"></div>
                </div>
            </div>
        </div>
        
        <script>
            let canais = [];
            let cargos = [];
            let membros = [];
            let configAtual = {{}};
            let botoesPrecos = {botoes_precos_json};
            
            // ========== FUNÇÕES EXISTENTES ==========
            
            async function carregarDados() {{
                try {{
                    const [canaisRes, cargosRes, membrosRes, configBV, configXP, linksRes, antiSpamRes, configComandosRes, filaConfigRes] = await Promise.all([
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
                    const configBVdata = await configBV.json();
                    const configXPdata = await configXP.json();
                    const linksData = await linksRes.json();
                    const antiSpamData = await antiSpamRes.json();
                    const configComandosData = await configComandosRes.json();
                    const filaConfig = await filaConfigRes.json();
                    
                    if (canaisData.sucesso) canais = canaisData.canais;
                    if (cargosData.sucesso) cargos = cargosData.cargos;
                    if (membrosData.sucesso) membros = membrosData.membros;
                    
                    popularSelects();
                    
                    if (configBVdata.sucesso) {{
                        document.getElementById('welcome-mensagem').value = configBVdata.mensagem || '';
                        document.getElementById('welcome-imagem').value = configBVdata.imagem || '';
                        const welcomeCanal = document.getElementById('welcome-canal');
                        if (welcomeCanal) welcomeCanal.value = configBVdata.canal || '';
                    }}
                    
                    if (configXPdata.sucesso) {{
                        document.getElementById('xp-taxa').value = configXPdata.taxa || 3;
                        const xpCanal = document.getElementById('xp-canal');
                        if (xpCanal) xpCanal.value = configXPdata.canal || '';
                    }}
                    
                    if (configComandosData.sucesso) {{
                        configAtual = configComandosData;
                        const canalPerfil = document.getElementById('canal-perfil');
                        const canalRank = document.getElementById('canal-rank');
                        if (canalPerfil) {{
                            canalPerfil.value = configComandosData.canal_perfil || '';
                            atualizarStatusPerfil(configComandosData.canal_perfil);
                        }}
                        if (canalRank) {{
                            canalRank.value = configComandosData.canal_rank || '';
                            atualizarStatusRank(configComandosData.canal_rank);
                        }}
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
                        
                        const listaDiv = document.getElementById('lista-comandos');
                        const comandos = antiSpamData.config.comandos_ignorados.split(',');
                        listaDiv.innerHTML = comandos.map(c => `<span style="background:#333; padding:4px 12px; border-radius:20px;">${{c.trim()}}</span>`).join('');
                    }}
                    
                    if (filaConfig.sucesso && filaConfig.links) {{
                        document.getElementById('link-discord').value = filaConfig.links.discord_convite || '';
                        if (filaConfig.links.botoes_precos) {{
                            botoesPrecos = filaConfig.links.botoes_precos;
                        }}
                    }}
                    
                    carregarCargosNivel();
                    carregarFila();
                    carregarBotoesPrecos();
                    carregarClientes();
                    carregarPedidos();
                    carregarCupons();
                    carregarFilaWW();
                }} catch(e) {{ console.error(e); }}
            }}
            
            function popularSelects() {{
                const selects = ['welcome-canal', 'xp-canal', 'rr-canal', 'btn-canal', 'embed-canal', 'links-canal', 'canal-perfil', 'canal-rank'];
                selects.forEach(id => {{
                    const select = document.getElementById(id);
                    if (select) {{
                        select.innerHTML = '<option value="">🔓 Todos os canais</option>';
                        canais.forEach(c => {{
                            const option = document.createElement('option');
                            option.value = c.id;
                            option.textContent = '#' + c.nome;
                            select.appendChild(option);
                        }});
                    }}
                }});
                
                const cargoSelect = document.getElementById('novo-cargo');
                if (cargoSelect) {{
                    cargoSelect.innerHTML = '<option value="">Selecione um cargo</option>';
                    cargos.forEach(c => {{
                        const option = document.createElement('option');
                        option.value = c.id;
                        option.textContent = c.nome;
                        cargoSelect.appendChild(option);
                    }});
                }}
                
                const membroSelects = ['warn-membro', 'ver-warns'];
                membroSelects.forEach(id => {{
                    const select = document.getElementById(id);
                    if (select) {{
                        select.innerHTML = '<option value="">Selecione um membro</option>';
                        membros.forEach(m => {{
                            const option = document.createElement('option');
                            option.value = m.id;
                            option.textContent = m.nome;
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
                if (tabId === 'moderacao') carregarAdvertencias();
                if (tabId === 'clientes') carregarClientes();
                if (tabId === 'pedidos') carregarPedidos();
                if (tabId === 'cupons') carregarCupons();
                if (tabId === 'servicos') carregarFilaWW();
            }}
            
            function atualizarStatusPerfil(canalId) {{
                const div = document.getElementById('perfil-status');
                if (!canalId) {{
                    div.innerHTML = '<span class="config-badge" style="background:#00b894;">🔓 Funciona em TODOS os canais</span>';
                }} else {{
                    const canal = canais.find(c => c.id == canalId);
                    div.innerHTML = `<span class="config-badge">📢 /perfil funciona apenas em <strong>#${{canal ? canal.nome : canalId}}</strong></span> <span style="color:#ffd93d;">(Clique novamente para remover)</span>`;
                }}
            }}
            
            function atualizarStatusRank(canalId) {{
                const div = document.getElementById('rank-status');
                if (!canalId) {{
                    div.innerHTML = '<span class="config-badge" style="background:#00b894;">🔓 Funciona em TODOS os canais</span>';
                }} else {{
                    const canal = canais.find(c => c.id == canalId);
                    div.innerHTML = `<span class="config-badge">📢 /rank funciona apenas em <strong>#${{canal ? canal.nome : canalId}}</strong></span> <span style="color:#ffd93d;">(Clique novamente para remover)</span>`;
                }}
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
            
            // ========== FUNÇÕES EXISTENTES (Anti-Spam, Boas-vindas, XP, etc) ==========
            
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
                    if (result.sucesso) {{
                        const comandos = data.comandos_ignorados.split(',');
                        document.getElementById('lista-comandos').innerHTML = comandos.map(c => `<span style="background:#333; padding:4px 12px; border-radius:20px;">${{c.trim()}}</span>`).join('');
                    }}
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
            
            async function salvarConfigComandos() {{
                const canalPerfil = document.getElementById('canal-perfil').value;
                const canalRank = document.getElementById('canal-rank').value;
                
                let perfilFinal = canalPerfil;
                let rankFinal = canalRank;
                
                if (canalPerfil && configAtual.canal_perfil === canalPerfil) {{
                    perfilFinal = '';
                }}
                if (canalRank && configAtual.canal_rank === canalRank) {{
                    rankFinal = '';
                }}
                
                const data = {{
                    canal_perfil: perfilFinal,
                    canal_rank: rankFinal
                }};
                try {{
                    const resp = await fetch('/api/config/comandos', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(data)}});
                    const result = await resp.json();
                    showAlert('comandos-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) {{
                        if (perfilFinal !== canalPerfil) {{
                            document.getElementById('canal-perfil').value = '';
                            atualizarStatusPerfil('');
                            configAtual.canal_perfil = '';
                        }} else {{
                            atualizarStatusPerfil(perfilFinal);
                            configAtual.canal_perfil = perfilFinal;
                        }}
                        if (rankFinal !== canalRank) {{
                            document.getElementById('canal-rank').value = '';
                            atualizarStatusRank('');
                            configAtual.canal_rank = '';
                        }} else {{
                            atualizarStatusRank(rankFinal);
                            configAtual.canal_rank = rankFinal;
                        }}
                    }}
                }} catch(e) {{ showAlert('comandos-alert', 'Erro: ' + e.message, false); }}
            }}
            
            async function carregarCargosNivel() {{
                try {{
                    const resp = await fetch('/api/cargos/nivel');
                    const data = await resp.json();
                    const container = document.getElementById('cargos-nivel-lista');
                    if (data.sucesso && Object.keys(data.cargos).length > 0) {{
                        let html = '<div style="display: flex; flex-wrap: wrap; gap: 0.5rem;">';
                        for (const [nivel, cargoId] of Object.entries(data.cargos)) {{
                            const cargo = cargos.find(c => c.id == cargoId);
                            html += `<div style="background: #333; padding: 0.5rem 1rem; border-radius: 5px;">Nível ${{nivel}}: ${{cargo ? cargo.nome : 'Cargo não encontrado'}} <button onclick="removerCargoNivel(${{nivel}})" style="background:#dc3545;color:white;border:none;border-radius:3px;padding:0.25rem 0.5rem;cursor:pointer;">×</button></div>`;
                        }}
                        html += '</div>';
                        container.innerHTML = html;
                    }} else {{
                        container.innerHTML = '<p>Nenhum cargo por nível configurado.</p>';
                    }}
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function adicionarCargoNivel() {{
                const nivel = document.getElementById('novo-nivel').value;
                const cargoId = document.getElementById('novo-cargo').value;
                if (!nivel || !cargoId) {{
                    showAlert('xp-alert', 'Preencha nível e cargo', false);
                    return;
                }}
                try {{
                    const resp = await fetch('/api/cargos/nivel', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{nivel, cargo_id: cargoId}})}});
                    const result = await resp.json();
                    showAlert('xp-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) {{
                        document.getElementById('novo-nivel').value = '';
                        carregarCargosNivel();
                    }}
                }} catch(e) {{ showAlert('xp-alert', 'Erro: ' + e.message, false); }}
            }}
            
            async function removerCargoNivel(nivel) {{
                if (!confirm('Remover cargo do nível ' + nivel + '?')) return;
                try {{
                    const resp = await fetch(`/api/cargos/nivel?nivel=${{nivel}}`, {{method: 'DELETE'}});
                    const result = await resp.json();
                    showAlert('xp-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) carregarCargosNivel();
                }} catch(e) {{ showAlert('xp-alert', 'Erro: ' + e.message, false); }}
            }}
            
            async function criarReacaoCargo() {{
                const data = {{
                    canal_id: document.getElementById('rr-canal').value,
                    conteudo: document.getElementById('rr-conteudo').value,
                    emoji_cargo: document.getElementById('rr-pares').value
                }};
                if (!data.canal_id || !data.conteudo || !data.emoji_cargo) {{
                    showAlert('rr-alert', 'Preencha todos os campos', false);
                    return;
                }}
                try {{
                    const resp = await fetch('/api/reacao_cargo/criar', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(data)}});
                    const result = await resp.json();
                    showAlert('rr-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) {{
                        document.getElementById('rr-conteudo').value = '';
                        document.getElementById('rr-pares').value = '';
                    }}
                }} catch(e) {{ showAlert('rr-alert', 'Erro: ' + e.message, false); }}
            }}
            
            async function criarBotoesCargo() {{
                const data = {{
                    canal_id: document.getElementById('btn-canal').value,
                    conteudo: document.getElementById('btn-conteudo').value,
                    cargos: document.getElementById('btn-pares').value
                }};
                if (!data.canal_id || !data.conteudo || !data.cargos) {{
                    showAlert('btn-alert', 'Preencha todos os campos', false);
                    return;
                }}
                try {{
                    const resp = await fetch('/api/botoes_cargo/criar', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(data)}});
                    const result = await resp.json();
                    showAlert('btn-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) {{
                        document.getElementById('btn-conteudo').value = '';
                        document.getElementById('btn-pares').value = '';
                    }}
                }} catch(e) {{ showAlert('btn-alert', 'Erro: ' + e.message, false); }}
            }}
            
            async function aplicarAdvertencia() {{
                const membroId = document.getElementById('warn-membro').value;
                const motivo = document.getElementById('warn-motivo').value;
                if (!membroId || !motivo) {{
                    alert('Selecione um membro e digite um motivo');
                    return;
                }}
                try {{
                    const resp = await fetch('/api/comando/advertir', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{membro_id: membroId, motivo}})}});
                    const result = await resp.json();
                    alert(result.mensagem);
                    if (result.sucesso) document.getElementById('warn-motivo').value = '';
                }} catch(e) {{ alert('Erro: ' + e.message); }}
            }}
            
            async function limparAdvertencias() {{
                const membroId = document.getElementById('warn-membro').value;
                if (!membroId) {{ alert('Selecione um membro'); return; }}
                if (!confirm('Tem certeza?')) return;
                try {{
                    const resp = await fetch('/api/comando/limpar_advertencias', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{membro_id: membroId}})}});
                    const result = await resp.json();
                    alert(result.mensagem);
                }} catch(e) {{ alert('Erro: ' + e.message); }}
            }}
            
            async function carregarAdvertencias() {{
                const membroId = document.getElementById('ver-warns').value;
                if (!membroId) {{
                    document.getElementById('lista-warns').innerHTML = '<p>Selecione um membro</p>';
                    return;
                }}
                try {{
                    const resp = await fetch(`/api/membro/advertencias?membro_id=${{membroId}}`);
                    const data = await resp.json();
                    if (data.sucesso && data.advertencias.length > 0) {{
                        let html = '<h4>Advertências:</h4><ul>';
                        data.advertencias.forEach(w => {{
                            html += `<li><strong>${{w.motivo}}</strong> - ${{w.ts}} (por ${{w.admin || w.por}})</li>`;
                        }});
                        html += '</ul>';
                        document.getElementById('lista-warns').innerHTML = html;
                    }} else {{
                        document.getElementById('lista-warns').innerHTML = '<p>Nenhuma advertência encontrada.</p>';
                    }}
                }} catch(e) {{ console.error(e); }}
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
                    }} else {{
                        showAlert('links-alert', 'Erro ao alternar bloqueio', false);
                    }}
                }} catch(e) {{ 
                    showAlert('links-alert', 'Erro: ' + e.message, false);
                }}
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
            
            // ========== FUNÇÕES DA FILA (EXISTENTES) ==========
            
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
                                    <td style="color:#a8e6cf;">${{escapeHtml(e.servico)}}</td>
                                    <td style="color:#ffb347;">${{escapeHtml(e.jogo || '')}}</td>
                                    <td>${{new Date(e.timestamp).toLocaleTimeString()}}</td>
                                    <td>
                                        <button onclick="moverCima('${{e.id}}')" class="btn btn-primary btn-sm">⬆️</button>
                                        <button onclick="moverBaixo('${{e.id}}')" class="btn btn-primary btn-sm">⬇️</button>
                                        <button onclick="concluir('${{e.id}}')" class="btn btn-success btn-sm">✅</button>
                                        <button onclick="remover('${{e.id}}')" class="btn btn-danger btn-sm">❌</button>
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
            
            async function adicionarFila() {{
                const nome = document.getElementById('add-nome').value.trim();
                const servico = document.getElementById('add-servico').value.trim();
                const jogo = document.getElementById('add-jogo').value.trim();
                if (!nome || !servico) {{
                    showAlert('add-result', 'Preencha nome e serviço', false);
                    return;
                }}
                try {{
                    const resp = await fetch('/api/fila/adicionar', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{nome_usuario: nome, servico, jogo}})}});
                    const data = await resp.json();
                    showAlert('add-result', data.mensagem, data.sucesso);
                    if (data.sucesso) {{
                        document.getElementById('add-nome').value = '';
                        document.getElementById('add-servico').value = '';
                        document.getElementById('add-jogo').value = '';
                        carregarFila();
                    }}
                }} catch(e) {{ showAlert('add-result', 'Erro: ' + e.message, false); }}
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
            
            // ========== FUNÇÕES BOTÕES PREÇO ==========
            
            function carregarBotoesPrecos() {{
                const container = document.getElementById('botoes-precos-lista');
                if (!container) return;
                
                if (botoesPrecos.length === 0) {{
                    container.innerHTML = '<div style="text-align:center;padding:20px;color:#888;">Nenhum botão de preço configurado. Adicione um acima!</div>';
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
                    showAlert('fila-status', 'Preencha nome e URL do botão', false);
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
                }} catch(e) {{
                    console.error(e);
                }}
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
            
            // ========== NOVAS FUNÇÕES: SERVIÇOS WW ==========
            
            async function carregarFilaWW() {{
                try {{
                    const resp = await fetch('/api/fila_servicos');
                    const data = await resp.json();
                    if (data.sucesso) {{
                        const fila = data.fila;
                        // Atualizar visualização da fila WW na aba Serviços
                        const container = document.querySelector('.fila-ww-container');
                        if (container) {{
                            if (fila.entradas.length === 0) {{
                                container.innerHTML = '<div style="text-align:center;padding:20px;color:#888;">📭 Ninguém na fila</div>';
                            }} else {{
                                container.innerHTML = fila.entradas.map(e => `
                                    <div style="display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid #333;">
                                        <span style="color:#ffd93d;">#${{e.posicao}}</span>
                                        <span>${{escapeHtml(e.nome_usuario)}}</span>
                                        <span style="color:#a8e6cf;">${{escapeHtml(e.servico)}}</span>
                                        <span style="font-size:12px;color:#888;">${{e.pedido_id || ''}}</span>
                                    </div>
                                `).join('');
                            }}
                        }}
                        // Atualizar status
                        const statusText = document.querySelector('#servicos .stat-dot');
                        if (statusText) {{
                            const dot = document.querySelector('#servicos .status-dot');
                            if (dot) {{
                                dot.className = 'status-dot ' + (fila.aberta ? 'dot-aberta' : 'dot-fechada');
                            }}
                        }}
                        // Atualizar campos de configuração
                        document.getElementById('fila-ww-nome').value = fila.nome || '';
                        document.getElementById('fila-ww-max').value = fila.tamanho_maximo || 50;
                    }}
                }} catch(e) {{
                    console.error(e);
                }}
            }}
            
            async function adicionarFilaWW() {{
                const nome = document.getElementById('ww-add-nome').value.trim();
                const servico = document.getElementById('ww-add-servico').value.trim();
                const pedidoId = document.getElementById('ww-add-pedido').value.trim();
                
                if (!nome || !servico) {{
                    showAlert('ww-add-result', 'Preencha nome e serviço', false);
                    return;
                }}
                
                try {{
                    const resp = await fetch('/api/fila_servicos/adicionar', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{nome_usuario: nome, servico, pedido_id: pedidoId}})
                    }});
                    const data = await resp.json();
                    showAlert('ww-add-result', data.mensagem, data.sucesso);
                    if (data.sucesso) {{
                        document.getElementById('ww-add-nome').value = '';
                        document.getElementById('ww-add-servico').value = '';
                        document.getElementById('ww-add-pedido').value = '';
                        carregarFilaWW();
                    }}
                }} catch(e) {{
                    showAlert('ww-add-result', 'Erro: ' + e.message, false);
                }}
            }}
            
            async function salvarConfigFilaWW() {{
                const data = {{
                    nome: document.getElementById('fila-ww-nome').value,
                    tamanho_maximo: parseInt(document.getElementById('fila-ww-max').value)
                }};
                try {{
                    const resp = await fetch('/api/fila_servicos/configuracoes', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify(data)
                    }});
                    const result = await resp.json();
                    showAlert('fila-ww-alert', 'Configurações salvas!', true);
                    carregarFilaWW();
                }} catch(e) {{
                    showAlert('fila-ww-alert', 'Erro: ' + e.message, false);
                }}
            }}
            
            async function alternarFilaWW() {{
                const fila = await fetch('/api/fila_servicos').then(r => r.json());
                const novoStatus = !fila.fila.aberta;
                try {{
                    await fetch('/api/fila_servicos/configuracoes', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{aberta: novoStatus}})
                    }});
                    carregarFilaWW();
                    showAlert('fila-ww-alert', 'Fila ' + (novoStatus ? 'aberta' : 'fechada') + '!', true);
                }} catch(e) {{
                    showAlert('fila-ww-alert', 'Erro: ' + e.message, false);
                }}
            }}
            
            // ========== FUNÇÕES CLIENTES ==========
            
            async function carregarClientes() {{
                try {{
                    const resp = await fetch('/api/clientes');
                    const data = await resp.json();
                    const tbody = document.getElementById('clientes-tabela');
                    if (data.sucesso && Object.keys(data.clientes).length > 0) {{
                        tbody.innerHTML = Object.values(data.clientes).map(c => `
                            <tr>
                                <td>${{escapeHtml(c.discord_nome || c.discord_id)}}</td>
                                <td><code>${{escapeHtml(c.uid || '')}}</code></td>
                                <td>${{escapeHtml(c.nome_ingame || '')}}</td>
                                <td><span class="status-badge" style="background:${{c.nivel_fidelidade === 'diamante' ? '#b9f2ff' : c.nivel_fidelidade === 'ouro' ? '#ffd700' : c.nivel_fidelidade === 'prata' ? '#c0c0c0' : '#cd7f32'}};">${{(c.nivel_fidelidade || 'bronze').toUpperCase()}}</span></td>
                                <td>R$ ${{(c.total_gasto || 0).toFixed(2)}}</td>
                                <td>${{c.pontos_fidelidade || 0}}</td>
                                <td>${{(c.pedidos || []).length}}</td>
                            </tr>
                        `).join('');
                    }} else {{
                        tbody.innerHTML = '<tr><td colspan="7">Nenhum cliente cadastrado</td></tr>';
                    }}
                }} catch(e) {{
                    console.error(e);
                }}
            }}
            
            async function cadastrarCliente() {{
                const discordId = document.getElementById('cli-discord-id').value.trim();
                const uid = document.getElementById('cli-uid').value.trim();
                const nomeIngame = document.getElementById('cli-nome').value.trim();
                
                if (!discordId || !uid) {{
                    showAlert('cli-alert', 'Discord ID e UID são obrigatórios', false);
                    return;
                }}
                
                try {{
                    const resp = await fetch('/api/clientes', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{discord_id: discordId, uid, nome_ingame: nomeIngame}})
                    }});
                    const data = await resp.json();
                    showAlert('cli-alert', data.mensagem, data.sucesso);
                    if (data.sucesso) {{
                        document.getElementById('cli-discord-id').value = '';
                        document.getElementById('cli-uid').value = '';
                        document.getElementById('cli-nome').value = '';
                        carregarClientes();
                    }}
                }} catch(e) {{
                    showAlert('cli-alert', 'Erro: ' + e.message, false);
                }}
            }}
            
            // ========== FUNÇÕES PEDIDOS ==========
            
            async function carregarPedidos() {{
                const status = document.getElementById('filtro-pedidos').value;
                const url = status === 'todos' ? '/api/pedidos' : `/api/pedidos?status=${status}`;
                try {{
                    const resp = await fetch(url);
                    const data = await resp.json();
                    const tbody = document.getElementById('pedidos-tabela');
                    if (data.sucesso && data.pedidos.length > 0) {{
                        tbody.innerHTML = data.pedidos.map(p => `
                            <tr>
                                <td><code>${{p.id}}</code></td>
                                <td>${{escapeHtml(p.cliente_nome || p.cliente_id)}}</td>
                                <td>${{escapeHtml(p.servico)}}</td>
                                <td>R$ ${{p.valor_final.toFixed(2)}}</td>
                                <td>${{p.desconto_aplicado > 0 ? 'R$ ' + p.desconto_aplicado.toFixed(2) : '-'}}</td>
                                <td><span class="status-badge status-${{p.status}}">${{(p.status || 'pendente').toUpperCase()}}</span></td>
                                <td>
                                    ${{p.status === 'pendente' ? `<button onclick="aprovarPedido('${{p.id}}')" class="btn btn-success btn-sm">✅ Aprovar</button>` : ''}}
                                    ${{p.status === 'aprovado' ? `<button onclick="concluirPedido('${{p.id}}')" class="btn btn-primary btn-sm">📦 Concluir</button>` : ''}}
                                    ${{p.status === 'pendente' || p.status === 'aprovado' ? `<button onclick="cancelarPedido('${{p.id}}')" class="btn btn-danger btn-sm">❌ Cancelar</button>` : ''}}
                                </td>
                            </tr>
                        `).join('');
                    }} else {{
                        tbody.innerHTML = '<tr><td colspan="7">Nenhum pedido encontrado</td></tr>';
                    }}
                }} catch(e) {{
                    console.error(e);
                }}
            }}
            
            async function criarPedido() {{
                const discordId = document.getElementById('ped-discord-id').value.trim();
                const servico = document.getElementById('ped-servico').value;
                const valor = document.getElementById('ped-valor').value;
                const cupom = document.getElementById('ped-cupom').value.trim();
                const observacoes = document.getElementById('ped-obs').value.trim();
                
                if (!discordId || !servico || !valor) {{
                    showAlert('ped-alert', 'Preencha Discord ID, serviço e valor', false);
                    return;
                }}
                
                try {{
                    const resp = await fetch('/api/pedidos', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{discord_id: discordId, servico, valor: parseFloat(valor), observacoes, cupom}})
                    }});
                    const data = await resp.json();
                    showAlert('ped-alert', data.mensagem, data.sucesso);
                    if (data.sucesso) {{
                        document.getElementById('ped-discord-id').value = '';
                        document.getElementById('ped-valor').value = '';
                        document.getElementById('ped-cupom').value = '';
                        document.getElementById('ped-obs').value = '';
                        carregarPedidos();
                    }}
                }} catch(e) {{
                    showAlert('ped-alert', 'Erro: ' + e.message, false);
                }}
            }}
            
            async function aprovarPedido(id) {{
                if (!confirm('Aprovar pedido ' + id + '?')) return;
                try {{
                    const resp = await fetch(`/api/pedidos/${id}/aprovar`, {{method: 'POST'}});
                    const data = await resp.json();
                    showAlert('ped-alert', data.mensagem, data.sucesso);
                    if (data.sucesso) carregarPedidos();
                }} catch(e) {{
                    showAlert('ped-alert', 'Erro: ' + e.message, false);
                }}
            }}
            
            async function concluirPedido(id) {{
                if (!confirm('Concluir pedido ' + id + '?')) return;
                try {{
                    const resp = await fetch(`/api/pedidos/${id}/concluir`, {{method: 'POST'}});
                    const data = await resp.json();
                    showAlert('ped-alert', data.mensagem, data.sucesso);
                    if (data.sucesso) carregarPedidos();
                }} catch(e) {{
                    showAlert('ped-alert', 'Erro: ' + e.message, false);
                }}
            }}
            
            async function cancelarPedido(id) {{
                const motivo = prompt('Motivo do cancelamento:');
                if (motivo === null) return;
                try {{
                    const resp = await fetch(`/api/pedidos/${id}/cancelar`, {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{motivo}})
                    }});
                    const data = await resp.json();
                    showAlert('ped-alert', data.mensagem, data.sucesso);
                    if (data.sucesso) carregarPedidos();
                }} catch(e) {{
                    showAlert('ped-alert', 'Erro: ' + e.message, false);
                }}
            }}
            
            // ========== FUNÇÕES CUPONS ==========
            
            async function carregarCupons() {{
                try {{
                    const resp = await fetch('/api/cupons');
                    const data = await resp.json();
                    const tbody = document.getElementById('cupons-tabela');
                    if (data.sucesso && data.cupons.length > 0) {{
                        const agora = new Date();
                        tbody.innerHTML = data.cupons.map(c => {{
                            const expiracao = new Date(c.expiracao);
                            const valido = expiracao > agora && (c.usos || 0) < (c.limite_usos || 1);
                            return `
                                <tr>
                                    <td><code>${{c.codigo}}</code></td>
                                    <td>${{c.desconto}}${c.tipo === 'percentual' ? '%' : ' R$'}</td>
                                    <td>${{c.tipo}}</td>
                                    <td>${{c.usos || 0}}/${{c.limite_usos || 1}}</td>
                                    <td>${{new Date(c.expiracao).toLocaleDateString()}}</td>
                                    <td><span class="status-badge" style="background:${{valido ? '#10b981' : '#ef4444'}};">${{valido ? '✅ Válido' : '❌ Expirado'}}</span></td>
                                </tr>
                            `;
                        }}).join('');
                    }} else {{
                        tbody.innerHTML = '<tr><td colspan="6">Nenhum cupom criado</td></tr>';
                    }}
                }} catch(e) {{
                    console.error(e);
                }}
            }}
            
            async function criarCupom() {{
                const codigo = document.getElementById('cup-codigo').value.trim().toUpperCase();
                const desconto = document.getElementById('cup-desconto').value;
                const tipo = document.getElementById('cup-tipo').value;
                const limite = document.getElementById('cup-limite').value;
                const dias = document.getElementById('cup-dias').value;
                
                if (!codigo || !desconto) {{
                    showAlert('cup-alert', 'Preencha código e desconto', false);
                    return;
                }}
                
                try {{
                    const resp = await fetch('/api/cupons', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{codigo, desconto: parseFloat(desconto), tipo, limite_usos: parseInt(limite), expiracao_dias: parseInt(dias)}})
                    }});
                    const data = await resp.json();
                    showAlert('cup-alert', data.mensagem, data.sucesso);
                    if (data.sucesso) {{
                        document.getElementById('cup-codigo').value = '';
                        document.getElementById('cup-desconto').value = '';
                        carregarCupons();
                    }}
                }} catch(e) {{
                    showAlert('cup-alert', 'Erro: ' + e.message, false);
                }}
            }}
            
            // ========== FUNÇÕES FIDELIDADE ==========
            
            async function consultarFidelidade() {{
                const discordId = document.getElementById('fid-discord-id').value.trim();
                if (!discordId) {{
                    showAlert('fid-resultado', 'Digite um Discord ID', false);
                    return;
                }}
                
                try {{
                    const resp = await fetch(`/api/fidelidade/${discordId}`);
                    const data = await resp.json();
                    const div = document.getElementById('fid-resultado');
                    if (data.sucesso) {{
                        const f = data.fidelidade;
                        div.style.display = 'block';
                        div.innerHTML = `
                            <h4>⭐ Fidelidade</h4>
                            <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap:10px;">
                                <div style="background:#333; padding:10px; border-radius:5px; text-align:center;">
                                    <div style="font-size:24px;">🏅</div>
                                    <div><strong>${f.nivel.toUpperCase()}</strong></div>
                                </div>
                                <div style="background:#333; padding:10px; border-radius:5px; text-align:center;">
                                    <div style="font-size:24px;">💰</div>
                                    <div>R$ ${f.total_gasto.toFixed(2)}</div>
                                </div>
                                <div style="background:#333; padding:10px; border-radius:5px; text-align:center;">
                                    <div style="font-size:24px;">⭐</div>
                                    <div>${f.pontos} pontos</div>
                                </div>
                                <div style="background:#333; padding:10px; border-radius:5px; text-align:center;">
                                    <div style="font-size:24px;">🎯</div>
                                    <div>${f.desconto}% desconto</div>
                                </div>
                                <div style="background:#333; padding:10px; border-radius:5px; text-align:center;">
                                    <div style="font-size:24px;">🎁</div>
                                    <div>${f.pontos_para_cupom} pts para cupom</div>
                                </div>
                            </div>
                        `;
                    }} else {{
                        div.style.display = 'block';
                        div.innerHTML = `<p style="color:#f87171;">${data.mensagem}</p>`;
                    }}
                }} catch(e) {{
                    showAlert('fid-resultado', 'Erro: ' + e.message, false);
                }}
            }}
            
            async function resgatarCupomFidelidade() {{
                const discordId = document.getElementById('fid-resgatar-id').value.trim();
                if (!discordId) {{
                    showAlert('fid-resgatar-alert', 'Digite um Discord ID', false);
                    return;
                }}
                
                try {{
                    const resp = await fetch('/api/fidelidade/resgatar', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{discord_id: discordId}})
                    }});
                    const data = await resp.json();
                    if (data.sucesso) {{
                        showAlert('fid-resgatar-alert', `✅ Cupom resgatado! Código: ${data.cupom.codigo} - ${data.cupom.desconto}% de desconto`, true);
                        document.getElementById('fid-resgatar-id').value = '';
                    }} else {{
                        showAlert('fid-resgatar-alert', data.mensagem, false);
                    }}
                }} catch(e) {{
                    showAlert('fid-resgatar-alert', 'Erro: ' + e.message, false);
                }}
            }}
            
            document.addEventListener('DOMContentLoaded', carregarDados);
        </script>
    </body>
    </html>
    '''

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
# NOVOS COMANDOS SLASH PARA SERVIÇOS WW
# ========================

@tree.command(name="cadastrar", description="Cadastra você no sistema de serviços Wuthering Waves")
@app_commands.describe(uid="Seu UID do Wuthering Waves", nome_ingame="Seu nome no jogo (opcional)")
async def slash_cadastrar(interaction: discord.Interaction, uid: str, nome_ingame: str = None):
    """Cadastra um cliente no sistema WW"""
    discord_id = str(interaction.user.id)
    
    # Verificar se já está cadastrado
    if obter_cliente(discord_id):
        await interaction.response.send_message("❌ Você já está cadastrado!", ephemeral=True)
        return
    
    sucesso, resultado = cadastrar_cliente(
        discord_id=discord_id,
        discord_nome=interaction.user.display_name,
        uid=uid,
        nome_ingame=nome_ingame or interaction.user.display_name
    )
    
    if sucesso:
        embed = discord.Embed(
            title="✅ Cadastro Realizado!",
            description=f"Você foi cadastrado com sucesso no sistema de serviços!\n\n"
                       f"**UID:** {uid}\n"
                       f"**Nome In-Game:** {nome_ingame or interaction.user.display_name}\n\n"
                       f"Use `/pedido` para solicitar um serviço.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ {resultado}", ephemeral=True)

@tree.command(name="pedido", description="Cria um pedido de serviço Wuthering Waves")
@app_commands.describe(servico="Nome do serviço desejado", valor="Valor do serviço em R$", observacoes="Observações sobre o pedido", cupom="Código do cupom de desconto (opcional)")
async def slash_pedido(interaction: discord.Interaction, servico: str, valor: str, observacoes: str = None, cupom: str = None):
    """Cria um pedido de serviço WW"""
    discord_id = str(interaction.user.id)
    
    # Verificar se está cadastrado
    cliente = obter_cliente(discord_id)
    if not cliente:
        await interaction.response.send_message(
            "❌ Você não está cadastrado! Use `/cadastrar` primeiro.",
            ephemeral=True
        )
        return
    
    try:
        valor_float = float(valor.replace(',', '.'))
        if valor_float <= 0:
            raise ValueError("Valor deve ser positivo")
    except:
        await interaction.response.send_message("❌ Valor inválido. Use um número positivo (ex: 10.50)", ephemeral=True)
        return
    
    sucesso, resultado = criar_pedido(
        discord_id=discord_id,
        servico=servico,
        valor=valor_float,
        jogo="Wuthering Waves",
        observacoes=observacoes or "",
        cupom_codigo=cupom
    )
    
    if sucesso:
        embed = discord.Embed(
            title="📦 Pedido Criado!",
            description=f"Seu pedido foi criado e aguarda aprovação.\n\n"
                       f"**ID:** {resultado['id']}\n"
                       f"**Serviço:** {servico}\n"
                       f"**Valor:** R$ {valor_float:.2f}\n"
                       f"**Valor Final:** R$ {resultado['valor_final']:.2f}\n"
                       f"**Desconto:** R$ {resultado['desconto_aplicado']:.2f}\n"
                       f"**Status:** ⏳ Pendente\n\n"
                       f"Use `/meuperfil` para acompanhar seus pedidos.",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed)
        
        # Notificar canal de pedidos se configurado
        canal_pedidos_id = dados.get("servicos_config", {}).get("canal_pedidos")
        if canal_pedidos_id:
            canal = interaction.guild.get_channel(int(canal_pedidos_id))
            if canal:
                embed_admin = discord.Embed(
                    title=f"📦 Novo Pedido #{resultado['id']}",
                    description=f"**Cliente:** {interaction.user.mention}\n"
                               f"**Serviço:** {servico}\n"
                               f"**Valor:** R$ {valor_float:.2f}\n"
                               f"**Valor Final:** R$ {resultado['valor_final']:.2f}\n"
                               f"**Observações:** {observacoes or 'Nenhuma'}\n"
                               f"**Cupom:** {cupom or 'Nenhum'}\n\n"
                               f"Use o painel para aprovar este pedido.",
                    color=discord.Color.gold()
                )
                await canal.send(embed=embed_admin)
    else:
        await interaction.response.send_message(f"❌ {resultado}", ephemeral=True)

@tree.command(name="meuperfil", description="Mostra seu perfil no sistema Wuthering Waves")
async def slash_meuperfil(interaction: discord.Interaction):
    """Mostra o perfil do cliente com pedidos"""
    discord_id = str(interaction.user.id)
    
    cliente = obter_cliente(discord_id)
    if not cliente:
        await interaction.response.send_message(
            "❌ Você não está cadastrado! Use `/cadastrar` primeiro.",
            ephemeral=True
        )
        return
    
    pedidos = obter_pedidos_cliente(discord_id)[:5]
    nivel_fidelidade = cliente.get("nivel_fidelidade", "bronze")
    desconto = obter_desconto_fidelidade(cliente.get("total_gasto", 0))
    
    # Emojis para níveis
    emojis = {"bronze": "🥉", "prata": "🥈", "ouro": "🥇", "diamante": "💎"}
    emoji = emojis.get(nivel_fidelidade, "⭐")
    
    embed = discord.Embed(
        title=f"📊 Perfil de {interaction.user.display_name}",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="📋 Informações",
        value=f"**UID:** {cliente.get('uid', 'N/A')}\n"
              f"**Nome In-Game:** {cliente.get('nome_ingame', 'N/A')}\n"
              f"**Cadastro:** {cliente.get('data_cadastro', 'N/A')[:10]}",
        inline=False
    )
    
    embed.add_field(
        name="⭐ Fidelidade",
        value=f"**Nível:** {emoji} {nivel_fidelidade.upper()}\n"
              f"**Desconto:** {desconto}%\n"
              f"**Total Gasto:** R$ {cliente.get('total_gasto', 0):.2f}\n"
              f"**Pontos:** {cliente.get('pontos_fidelidade', 0)}",
        inline=False
    )
    
    if pedidos:
        pedidos_texto = ""
        for p in pedidos[:3]:
            status_emoji = {
                "pendente": "⏳",
                "aprovado": "✅",
                "concluido": "📦",
                "cancelado": "❌"
            }.get(p.get("status", "pendente"), "❓")
            pedidos_texto += f"{status_emoji} **{p.get('id', 'N/A')}** - {p.get('servico', 'N/A')} - R$ {p.get('valor_final', 0):.2f}\n"
        
        if len(pedidos) > 3:
            pedidos_texto += f"\n... e mais {len(pedidos) - 3} pedidos"
        
        embed.add_field(
            name="📦 Últimos Pedidos",
            value=pedidos_texto,
            inline=False
        )
    else:
        embed.add_field(
            name="📦 Pedidos",
            value="Nenhum pedido ainda. Use `/pedido` para começar!",
            inline=False
        )
    
    await interaction.response.send_message(embed=embed)

@tree.command(name="fila_ww", description="Mostra a fila de serviços Wuthering Waves")
async def slash_fila_ww(interaction: discord.Interaction):
    """Mostra a fila de serviços WW"""
    fila = obter_fila_servicos()
    
    if not fila["entradas"]:
        embed = discord.Embed(
            title="📋 Fila de Serviços WW",
            description="📭 Ninguém na fila no momento!",
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Status: {'🟢 Aberta' if fila['configuracoes']['aberta'] else '🔴 Fechada'}")
        await interaction.response.send_message(embed=embed)
        return
    
    texto = ""
    for i, entrada in enumerate(fila["entradas"][:15], 1):
        texto += f"`#{i}` **{entrada.get('nome_usuario', 'Desconhecido')}** - {entrada.get('servico', 'N/A')}\n"
    
    if len(fila["entradas"]) > 15:
        texto += f"\n... e mais {len(fila['entradas']) - 15} pessoas na fila"
    
    embed = discord.Embed(
        title=f"📋 Fila de Serviços WW",
        description=texto,
        color=discord.Color.blue()
    )
    embed.set_footer(
        text=f"Total: {len(fila['entradas'])} | {'🟢 Aberta' if fila['configuracoes']['aberta'] else '🔴 Fechada'} | Máximo: {fila['configuracoes']['tamanho_maximo']}"
    )
    await interaction.response.send_message(embed=embed)

@tree.command(name="cupom", description="Valida um cupom de desconto")
@app_commands.describe(codigo="Código do cupom para validar")
async def slash_cupom(interaction: discord.Interaction, codigo: str):
    """Valida um cupom de desconto"""
    sucesso, resultado = validar_cupom(codigo)
    
    if sucesso:
        cupom = resultado
        embed = discord.Embed(
            title="🏷️ Cupom Válido!",
            description=f"**Código:** {cupom['codigo']}\n"
                       f"**Desconto:** {cupom['desconto']}{'%' if cupom['tipo'] == 'percentual' else ' R$'}\n"
                       f"**Tipo:** {'Percentual' if cupom['tipo'] == 'percentual' else 'Valor Fixo'}\n"
                       f"**Usos:** {cupom.get('usos', 0)}/{cupom.get('limite_usos', 1)}\n"
                       f"**Expiração:** {cupom.get('expiracao', 'N/A')[:10]}",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)
    else:
        embed = discord.Embed(
            title="❌ Cupom Inválido",
            description=resultado,
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
    total_clientes = len(dados.get("clientes", {}))
    pedidos_pendentes = len(obter_pedidos_pendentes())
    
    print(f"{'='*50}")
    print(f"✨ BOT PRONTO!")
    print(f"📢 Comandos: /perfil, /rank, /cadastrar, /pedido, /meuperfil, /fila_ww, /cupom")
    print(f"🛡️ Anti-Spam: {'ATIVADO' if dados.get('anti_spam', {}).get('ativado', True) else 'DESATIVADO'}")
    print(f"🚫 Comandos da Mudae: NÃO ganham XP e NÃO contam como spam")
    print(f"📢 Canal do /perfil: {config.get('canal_perfil') or 'TODOS OS CANAIS'}")
    print(f"📢 Canal do /rank: {config.get('canal_rank') or 'TODOS OS CANAIS'}")
    print(f"👤 Clientes cadastrados: {total_clientes}")
    print(f"📦 Pedidos pendentes: {pedidos_pendentes}")
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
