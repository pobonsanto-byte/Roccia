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
    "clientes": {},
    "servicos": {},
    "solicitacoes": {},
    "fidelidade": {
        "pontos_por_real": 1,
        "recompensas": [
            {"pontos": 60, "descricao": "1 Dia de Quests Diárias Grátis", "tipo": "quests_diarias"},
            {"pontos": 100, "descricao": "Desafio Rápido", "tipo": "desafio_rapido"},
            {"pontos": 100, "descricao": "Portinha", "tipo": "portinha"},
            {"pontos": 100, "descricao": "Hologramas de Huanglong", "tipo": "hologramas"},
            {"pontos": 100, "descricao": "Cupom de R$ 5", "tipo": "cupom_5"},
            {"pontos": 200, "descricao": "Análise de Conta", "tipo": "analise_conta"},
            {"pontos": 200, "descricao": "Companion Quest", "tipo": "companion_quest"},
            {"pontos": 200, "descricao": "Cupom de R$ 10", "tipo": "cupom_10"},
            {"pontos": 400, "descricao": "Build Completa", "tipo": "build_completa"},
            {"pontos": 400, "descricao": "Cupom de R$ 20", "tipo": "cupom_20"}
        ],
        "cupons_gerados": {}
    },
    "fidelidade_config": {
        "validade_pontos_dias": 90,
        "validade_cupom_dias": 30,
        "multiplicador_pontos": 1
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

                if "clientes" not in dados:
                    dados["clientes"] = {}
                if "servicos" not in dados:
                    dados["servicos"] = {}
                if "solicitacoes" not in dados:
                    dados["solicitacoes"] = {}
                if "fidelidade" not in dados:
                    dados["fidelidade"] = {
                        "pontos_por_real": 1,
                        "recompensas": [
                            {"pontos": 60, "descricao": "1 Dia de Quests Diárias Grátis", "tipo": "quests_diarias"},
                            {"pontos": 100, "descricao": "Desafio Rápido", "tipo": "desafio_rapido"},
                            {"pontos": 100, "descricao": "Portinha", "tipo": "portinha"},
                            {"pontos": 100, "descricao": "Hologramas de Huanglong", "tipo": "hologramas"},
                            {"pontos": 100, "descricao": "Cupom de R$ 5", "tipo": "cupom_5"},
                            {"pontos": 200, "descricao": "Análise de Conta", "tipo": "analise_conta"},
                            {"pontos": 200, "descricao": "Companion Quest", "tipo": "companion_quest"},
                            {"pontos": 200, "descricao": "Cupom de R$ 10", "tipo": "cupom_10"},
                            {"pontos": 400, "descricao": "Build Completa", "tipo": "build_completa"},
                            {"pontos": 400, "descricao": "Cupom de R$ 20", "tipo": "cupom_20"}
                        ],
                        "cupons_gerados": {}
                    }
                if "fidelidade_config" not in dados:
                    dados["fidelidade_config"] = {
                        "validade_pontos_dias": 90,
                        "validade_cupom_dias": 30,
                        "multiplicador_pontos": 1
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

def gerar_codigo_cupom():
    import string
    random_part = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    return f"ZANKON-{random_part}"

def obter_cliente(discord_id):
    return dados.get("clientes", {}).get(str(discord_id))

def criar_cliente(discord_id, game_nick, uid):
    dados.setdefault("clientes", {})
    dados["clientes"][str(discord_id)] = {
        "uid": uid,
        "game_nick": game_nick,
        "pontos_atuais": 0,
        "pontos_acumulados": 0,
        "pontos_utilizados": 0,
        "ultima_compra": None,
        "ultimo_resgate": None,
        "data_cadastro": agora_br().isoformat()
    }
    return dados["clientes"][str(discord_id)]

def atualizar_pontos_cliente(discord_id, pontos):
    cliente = obter_cliente(discord_id)
    if not cliente:
        return False
    cliente["pontos_atuais"] = max(0, cliente["pontos_atuais"] + pontos)
    if pontos > 0:
        cliente["pontos_acumulados"] += pontos
        cliente["ultima_compra"] = agora_br().isoformat()
    return True

def adicionar_servico(nome, categoria, descricao, valor_reais, pontos_gerados, status="ativo", imagem_url=""):
    servico_id = str(int(time.time() * 1000))
    dados["servicos"][servico_id] = {
        "nome": nome,
        "categoria": categoria,
        "descricao": descricao,
        "valor_reais": float(valor_reais),
        "pontos_gerados": int(pontos_gerados),
        "status": status,
        "imagem_url": imagem_url,
        "data_criacao": agora_br().isoformat()
    }
    return servico_id

def obter_servicos_ativos():
    return {k: v for k, v in dados.get("servicos", {}).items() if v.get("status") == "ativo"}

def criar_solicitacao(cliente_discord_id, servico_id, jogo, observacoes="", cupom_codigo=None):
    solicitacao_id = str(int(time.time() * 1000))
    dados["solicitacoes"][solicitacao_id] = {
        "cliente_discord_id": str(cliente_discord_id),
        "servico_id": servico_id,
        "jogo": jogo,
        "observacoes": observacoes,
        "cupom_aplicado": cupom_codigo,
        "status": "Aguardando Aprovação",
        "data_solicitacao": agora_br().isoformat(),
        "data_aprovacao": None,
        "data_conclusao": None,
        "admin_aprovacao": None,
        "admin_conclusao": None,
        "motivo_recusa": None,
        "pontos_creditados": 0
    }
    return solicitacao_id

def aprovar_solicitacao(solicitacao_id, admin_id):
    solicitacao = dados.get("solicitacoes", {}).get(solicitacao_id)
    if not solicitacao or solicitacao.get("status") != "Aguardando Aprovação":
        return False

    solicitacao["status"] = "Em Andamento"
    solicitacao["data_aprovacao"] = agora_br().isoformat()
    solicitacao["admin_aprovacao"] = str(admin_id)

    cliente = obter_cliente(solicitacao["cliente_discord_id"])
    servico = dados["servicos"].get(solicitacao["servico_id"])

    if cliente and servico:
        nome_usuario = cliente.get("game_nick", f"User_{solicitacao['cliente_discord_id']}")
        nome_servico = servico.get("nome", "Serviço")
        jogo = solicitacao.get("jogo", "")

        fila = obter_dados_fila()
        if fila["configuracoes"]["aberta"]:
            entrada = {
                "id": str(int(datetime.now().timestamp() * 1000)),
                "nome_usuario": nome_usuario,
                "servico": nome_servico,
                "jogo": jogo,
                "usuario_id": solicitacao["cliente_discord_id"],
                "timestamp": agora_br().isoformat(),
                "status": "aguardando",
                "posicao": len(fila["entradas"]) + 1,
                "solicitacao_id": solicitacao_id
            }
            fila["entradas"].append(entrada)
            atualizar_posicoes(fila["entradas"])
            salvar_dados_github(f"Solicitação aprovada: {nome_usuario} - {nome_servico}")
            return True

    return False

def recusar_solicitacao(solicitacao_id, motivo, admin_id):
    solicitacao = dados.get("solicitacoes", {}).get(solicitacao_id)
    if not solicitacao or solicitacao.get("status") != "Aguardando Aprovação":
        return False

    solicitacao["status"] = "Recusado"
    solicitacao["motivo_recusa"] = motivo
    solicitacao["data_aprovacao"] = agora_br().isoformat()
    solicitacao["admin_aprovacao"] = str(admin_id)
    return True

def concluir_solicitacao_fila(solicitacao_id, admin_id):
    solicitacao = dados.get("solicitacoes", {}).get(solicitacao_id)
    if not solicitacao or solicitacao.get("status") != "Em Andamento":
        return False

    servico = dados["servicos"].get(solicitacao["servico_id"])
    if not servico:
        return False

    pontos = servico.get("pontos_gerados", 0)
    cliente_discord_id = solicitacao["cliente_discord_id"]

    if pontos > 0:
        cliente = obter_cliente(cliente_discord_id)
        if cliente:
            cliente["pontos_atuais"] += pontos
            cliente["pontos_acumulados"] += pontos
            cliente["ultima_compra"] = agora_br().isoformat()

    solicitacao["status"] = "Concluído"
    solicitacao["data_conclusao"] = agora_br().isoformat()
    solicitacao["admin_conclusao"] = str(admin_id)
    solicitacao["pontos_creditados"] = pontos

    fila = obter_dados_fila()
    for i, entrada in enumerate(fila["entradas"]):
        if entrada.get("solicitacao_id") == solicitacao_id:
            removido = fila["entradas"].pop(i)
            removido["status"] = "concluido"
            removido["concluido_em"] = agora_br().isoformat()
            fila["historico"].append(removido)
            atualizar_posicoes(fila["entradas"])
            break

    salvar_dados_github(f"Solicitação concluída: {solicitacao_id}")
    return True

def resgatar_recompensa(discord_id, recompensa_tipo):
    cliente = obter_cliente(discord_id)
    if not cliente:
        return False, "Cliente não encontrado"

    recompensa = None
    for r in dados.get("fidelidade", {}).get("recompensas", []):
        if r.get("tipo") == recompensa_tipo:
            recompensa = r
            break

    if not recompensa:
        return False, "Recompensa não encontrada"

    pontos_necessarios = recompensa.get("pontos", 0)
    if cliente["pontos_atuais"] < pontos_necessarios:
        return False, f"Pontos insuficientes. Você tem {cliente['pontos_atuais']} pontos, precisa de {pontos_necessarios}"

    cliente["pontos_atuais"] -= pontos_necessarios
    cliente["pontos_utilizados"] += pontos_necessarios
    cliente["ultimo_resgate"] = agora_br().isoformat()

    codigo = gerar_codigo_cupom()
    validade = (agora_br() + timedelta(days=30)).isoformat()

    dados["fidelidade"].setdefault("cupons_gerados", {})[codigo] = {
        "discord_id": str(discord_id),
        "tipo_recompensa": recompensa_tipo,
        "descricao": recompensa.get("descricao", ""),
        "validade": validade,
        "data_resgate": agora_br().isoformat(),
        "status": "ativo",
        "utilizado_em": None
    }

    salvar_dados_github(f"Cupom resgatado: {codigo} para {discord_id}")
    return True, codigo

def aplicar_cupom(codigo, discord_id):
    cupons = dados.get("fidelidade", {}).get("cupons_gerados", {})
    cupom = cupons.get(codigo)

    if not cupom:
        return False, "Cupom não encontrado"

    if cupom.get("discord_id") != str(discord_id):
        return False, "Este cupom não pertence a você"

    if cupom.get("status") != "ativo":
        return False, "Cupom já foi utilizado ou está expirado"

    validade = cupom.get("validade")
    if validade:
        try:
            validade_date = datetime.fromisoformat(validade)
            if validade_date < agora_br():
                cupom["status"] = "expirado"
                return False, "Cupom expirado"
        except:
            pass

    return True, "Cupom válido"

def usar_cupom(codigo):
    cupons = dados.get("fidelidade", {}).get("cupons_gerados", {})
    cupom = cupons.get(codigo)
    if cupom:
        cupom["status"] = "utilizado"
        cupom["utilizado_em"] = agora_br().isoformat()
        return True
    return False

# ========================
# FUNÇÕES ANTI-SPAM E IGNORADOS
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

def limpar_mensagens_antigas(user_id: int):
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
            .btn-cliente {{ background: #f59e0b; }}
            .btn-cliente:hover {{ background: #d97706; }}
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
                    <li>Sistema de Fidelidade e Pontos</li>
                </ul>
            </div>
            <div>
                <a href="/regras-fidelidade" class="btn" style="background:#7c3aed;">📜 Regras</a>
                <a href="/login" class="btn">🔐 Login com Discord</a>
            </div>
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

        is_admin = False
        for guild in guilds:
            if str(guild['id']) == GUILD_ID and (guild['permissions'] & 0x8):
                is_admin = True
                break

        session['usuario'] = {
            'id': user_data['id'],
            'nome_usuario': user_data['username'],
            'avatar': user_data.get('avatar'),
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

@app.route("/regras-fidelidade")
def regras_fidelidade():
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Regras - Sistema de Fidelidade</title>
        <style>
            * { margin:0; padding:0; box-sizing:border-box; }
            body { font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0a0a0a, #1a1a1a); min-height:100vh; padding:40px 20px; color:#e0e0e0; }
            .container { max-width:800px; margin:0 auto; background:#121212; border-radius:20px; padding:40px; border:1px solid #333; }
            h1 { color: #f59e0b; text-align:center; margin-bottom:10px; font-size:2.5rem; }
            h2 { color: #5865F2; margin-top:30px; margin-bottom:15px; }
            .subtitle { text-align:center; color:#888; margin-bottom:30px; }
            .rule { background:#1a1a1a; padding:20px; border-radius:10px; margin-bottom:20px; border-left:4px solid #f59e0b; }
            .rule h3 { color:#f59e0b; margin-bottom:10px; }
            .rule p { color:#ccc; line-height:1.6; }
            .btn { display:inline-block; background:#5865F2; color:white; padding:12px 30px; border-radius:8px; text-decoration:none; font-weight:bold; margin-top:20px; transition:all 0.3s; }
            .btn:hover { background:#4752C4; transform:translateY(-2px); }
            .footer { text-align:center; margin-top:30px; border-top:1px solid #333; padding-top:20px; color:#666; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📜 Regras de Uso</h1>
            <div class="subtitle">Sistema de Fidelidade ZankonYTB</div>

            <div class="rule">
                <h3>1. Pontos Pessoais e Vinculados ao UID</h3>
                <p>Seus pontos são pessoais, intransferíveis e atrelados diretamente ao seu cadastro e ao seu UID do jogo. Não é permitido transferir pontos para amigos ou juntar o saldo de compras de contas diferentes para resgatar prêmios.</p>
            </div>

            <div class="rule">
                <h3>2. Cupons de Uso Único</h3>
                <p>Ao trocar seus pontos, o sistema gera um código exclusivo para você. Esse token é de uso único. Uma vez inserido e validado no seu pedido, ele é consumido automaticamente e não poderá ser reutilizado.</p>
            </div>

            <div class="rule">
                <h3>3. Um Benefício por Pedido</h3>
                <p>Os descontos e resgates não são cumulativos. É permitido utilizar apenas um benefício por pedido. Não é possível utilizar vários cupons juntos.</p>
            </div>

            <div class="rule">
                <h3>4. Validade</h3>
                <p>Saldo de pontos expira após 90 dias sem novos serviços concluídos. Cupons possuem validade de 30 dias após o resgate.</p>
            </div>

            <div style="text-align:center;">
                <a href="/" class="btn">🏠 Voltar ao Início</a>
                <a href="/cliente" class="btn" style="background:#f59e0b; margin-left:10px;">👤 Área do Cliente</a>
            </div>

            <div class="footer">
                <p>© 2024 ZankonYTB - Todos os direitos reservados</p>
            </div>
        </div>
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
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False}), 401
    sucesso, _ = remover_fila(request.json.get("entrada_id"))
    return jsonify({"sucesso": sucesso})

@app.route("/api/fila/mover-cima", methods=["POST"])
def api_fila_mover_cima():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False}), 401
    sucesso, _ = mover_cima(request.json.get("entrada_id"))
    return jsonify({"sucesso": sucesso})

@app.route("/api/fila/mover-baixo", methods=["POST"])
def api_fila_mover_baixo():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False}), 401
    sucesso, _ = mover_baixo(request.json.get("entrada_id"))
    return jsonify({"sucesso": sucesso})

@app.route("/api/fila/concluir", methods=["POST"])
def api_fila_concluir():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False}), 401
    entrada_id = request.json.get("entrada_id")
    sucesso, removido = concluir_servico(entrada_id)

    if sucesso and removido and removido.get("solicitacao_id"):
        solicitacao_id = removido["solicitacao_id"]
        concluir_solicitacao_fila(solicitacao_id, session['usuario']['id'])

    return jsonify({"sucesso": sucesso})

@app.route("/api/fila/limpar", methods=["POST"])
def api_fila_limpar():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False}), 401
    limpar_fila()
    return jsonify({"sucesso": True})

@app.route("/api/fila/configuracoes", methods=["GET", "POST"])
def api_fila_configuracoes():
    if request.method == "GET":
        fila = obter_dados_fila()
        links = obter_links_fila()
        return jsonify({"sucesso": True, "configuracoes": fila["configuracoes"], "nome": fila["nome"], "links": links})
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
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
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
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
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False}), 401
    index = request.json.get("index")
    if index is None:
        return jsonify({"sucesso": False, "mensagem": "Índice não informado"})
    remover_botao_preco(int(index))
    return jsonify({"sucesso": True, "mensagem": "Botão removido!"})

@app.route("/api/fila/botoes/atualizar", methods=["POST"])
def api_fila_botoes_atualizar():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
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
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
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
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
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
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
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
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
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
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
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
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
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
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False}), 401
    req = request.json
    sucesso = executar_acao_bot("criar_embed", **req)
    return jsonify({"sucesso": sucesso, "mensagem": "✅ Embed criada!" if sucesso else "❌ Falha"})

@app.route("/api/comando/advertir", methods=["POST"])
def api_comando_advertir():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False}), 401
    req = request.json
    sucesso = executar_acao_bot("advertir_membro", membro_id=req.get('membro_id'), motivo=req.get('motivo'), admin=session['usuario']['nome_usuario'])
    return jsonify({"sucesso": sucesso, "mensagem": "✅ Advertência aplicada!" if sucesso else "❌ Falha"})

@app.route("/api/comando/limpar_advertencias", methods=["POST"])
def api_comando_limpar_advertencias():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False}), 401
    membro_id = str(request.json.get('membro_id'))
    if membro_id in dados.get("advertencias", {}):
        dados["advertencias"].pop(membro_id)
        salvar_dados_github(f"Advertências limpas: {membro_id}")
        return jsonify({"sucesso": True, "mensagem": "✅ Advertências removidas!"})
    return jsonify({"sucesso": False, "mensagem": "❌ Membro sem advertências"})

@app.route("/api/reacao_cargo/criar", methods=["POST"])
def api_reacao_cargo_criar():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False}), 401
    req = request.json
    sucesso = executar_acao_bot("criar_reacao_cargo", **req)
    return jsonify({"sucesso": sucesso, "mensagem": "✅ Reaction role criada!" if sucesso else "❌ Falha"})

@app.route("/api/botoes_cargo/criar", methods=["POST"])
def api_botoes_cargo_criar():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False}), 401
    req = request.json
    sucesso = executar_acao_bot("criar_botoes_cargo", **req)
    return jsonify({"sucesso": sucesso, "mensagem": "✅ Botões criados!" if sucesso else "❌ Falha"})

# ========================
# NOVAS APIs - SISTEMA DE FIDELIDADE
# ========================

@app.route("/api/cliente/verificar", methods=["GET"])
def api_cliente_verificar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401

    discord_id = session['usuario']['id']
    cliente = obter_cliente(discord_id)

    if cliente:
        return jsonify({
            "sucesso": True,
            "cadastrado": True,
            "cliente": cliente
        })
    else:
        return jsonify({
            "sucesso": True,
            "cadastrado": False,
            "cliente": None
        })

@app.route("/api/cliente/cadastrar", methods=["POST"])
def api_cliente_cadastrar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401

    req = request.json
    discord_id = session['usuario']['id']
    uid = req.get("uid", "").strip()
    game_nick = req.get("game_nick", "").strip()

    if not uid or not game_nick:
        return jsonify({"sucesso": False, "mensagem": "UID e Nick são obrigatórios"})

    if obter_cliente(discord_id):
        return jsonify({"sucesso": False, "mensagem": "Você já está cadastrado"})

    for d_id, cliente in dados.get("clientes", {}).items():
        if cliente.get("uid") == uid and d_id != str(discord_id):
            return jsonify({"sucesso": False, "mensagem": "Este UID já está cadastrado para outra conta"})

    criar_cliente(discord_id, game_nick, uid)
    salvar_dados_github(f"Novo cliente cadastrado: {game_nick} ({uid})")

    return jsonify({
        "sucesso": True,
        "mensagem": "Cadastro realizado com sucesso!",
        "cliente": obter_cliente(discord_id)
    })

@app.route("/api/cliente/pontos", methods=["GET"])
def api_cliente_pontos():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401

    discord_id = session['usuario']['id']
    cliente = obter_cliente(discord_id)

    if not cliente:
        return jsonify({"sucesso": False, "mensagem": "Cliente não encontrado"})

    return jsonify({
        "sucesso": True,
        "pontos_atuais": cliente.get("pontos_atuais", 0),
        "pontos_acumulados": cliente.get("pontos_acumulados", 0),
        "pontos_utilizados": cliente.get("pontos_utilizados", 0),
        "ultima_compra": cliente.get("ultima_compra"),
        "ultimo_resgate": cliente.get("ultimo_resgate")
    })

@app.route("/api/cliente/solicitacoes", methods=["GET"])
def api_cliente_solicitacoes():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401

    discord_id = session['usuario']['id']
    solicitacoes = []

    for s_id, s in dados.get("solicitacoes", {}).items():
        if s.get("cliente_discord_id") == str(discord_id):
            servico = dados.get("servicos", {}).get(s.get("servico_id"), {})
            s_data = s.copy()
            s_data["servico_nome"] = servico.get("nome", "Serviço não encontrado")
            s_data["id"] = s_id
            solicitacoes.append(s_data)

    solicitacoes.sort(key=lambda x: x.get("data_solicitacao", ""), reverse=True)

    return jsonify({
        "sucesso": True,
        "solicitacoes": solicitacoes
    })

@app.route("/api/cliente/cupons", methods=["GET"])
def api_cliente_cupons():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401

    discord_id = session['usuario']['id']
    cupons = []

    for codigo, cupom in dados.get("fidelidade", {}).get("cupons_gerados", {}).items():
        if cupom.get("discord_id") == str(discord_id):
            cupom_data = cupom.copy()
            cupom_data["codigo"] = codigo
            cupons.append(cupom_data)

    agora = agora_br()
    for cupom in cupons:
        if cupom.get("status") == "ativo":
            validade = cupom.get("validade")
            if validade:
                try:
                    validade_date = datetime.fromisoformat(validade)
                    if validade_date < agora:
                        cupom["status"] = "expirado"
                except:
                    pass

    return jsonify({
        "sucesso": True,
        "cupons": cupons
    })

@app.route("/api/cliente/recompensas", methods=["GET"])
def api_cliente_recompensas():
    recompensas = dados.get("fidelidade", {}).get("recompensas", [])
    return jsonify({
        "sucesso": True,
        "recompensas": recompensas
    })

@app.route("/api/cliente/resgatar", methods=["POST"])
def api_cliente_resgatar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401

    req = request.json
    discord_id = session['usuario']['id']
    tipo = req.get("tipo")

    if not tipo:
        return jsonify({"sucesso": False, "mensagem": "Tipo de recompensa não informado"})

    sucesso, resultado = resgatar_recompensa(discord_id, tipo)

    if sucesso:
        salvar_dados_github(f"Cupom resgatado: {resultado} para {discord_id}")
        return jsonify({
            "sucesso": True,
            "mensagem": "Recompensa resgatada com sucesso!",
            "codigo": resultado
        })
    else:
        return jsonify({
            "sucesso": False,
            "mensagem": resultado
        })

@app.route("/api/cliente/solicitar", methods=["POST"])
def api_cliente_solicitar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401

    req = request.json
    discord_id = session['usuario']['id']
    servico_id = req.get("servico_id")
    jogo = req.get("jogo", "")
    observacoes = req.get("observacoes", "")
    cupom_codigo = req.get("cupom", "").strip()

    if not servico_id:
        return jsonify({"sucesso": False, "mensagem": "Serviço não selecionado"})

    servico = dados.get("servicos", {}).get(servico_id)
    if not servico or servico.get("status") != "ativo":
        return jsonify({"sucesso": False, "mensagem": "Serviço não disponível"})

    if cupom_codigo:
        valido, mensagem = aplicar_cupom(cupom_codigo, discord_id)
        if not valido:
            return jsonify({"sucesso": False, "mensagem": mensagem})

    solicitacao_id = criar_solicitacao(discord_id, servico_id, jogo, observacoes, cupom_codigo if cupom_codigo else None)

    if cupom_codigo:
        usar_cupom(cupom_codigo)

    salvar_dados_github(f"Nova solicitação: {solicitacao_id} para {discord_id}")

    return jsonify({
        "sucesso": True,
        "mensagem": "Solicitação enviada com sucesso! Aguarde a aprovação.",
        "solicitacao_id": solicitacao_id
    })

@app.route("/api/cliente/servicos", methods=["GET"])
def api_cliente_servicos():
    servicos = obter_servicos_ativos()
    return jsonify({
        "sucesso": True,
        "servicos": servicos
    })

@app.route("/api/cliente/jogos", methods=["GET"])
def api_cliente_jogos():
    jogos = [
        "Genshin Impact",
        "Honkai: Star Rail",
        "Wuthering Waves",
        "Zenless Zone Zero",
        "Arknights",
        "Blue Archive",
        "Fate/Grand Order",
        "Other"
    ]
    return jsonify({
        "sucesso": True,
        "jogos": jogos
    })

# ========================
# APIs ADMIN - FIDELIDADE
# ========================

@app.route("/api/admin/servicos", methods=["GET", "POST", "PUT", "DELETE"])
def api_admin_servicos():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401

    if request.method == "GET":
        return jsonify({
            "sucesso": True,
            "servicos": dados.get("servicos", {})
        })

    elif request.method == "POST":
        req = request.json
        nome = req.get("nome", "").strip()
        categoria = req.get("categoria", "").strip()
        descricao = req.get("descricao", "").strip()
        valor_reais = float(req.get("valor_reais", 0))
        pontos_gerados = int(req.get("pontos_gerados", 0))
        status = req.get("status", "ativo")
        imagem_url = req.get("imagem_url", "")

        if not nome or not categoria:
            return jsonify({"sucesso": False, "mensagem": "Nome e categoria são obrigatórios"})

        servico_id = adicionar_servico(nome, categoria, descricao, valor_reais, pontos_gerados, status, imagem_url)
        salvar_dados_github(f"Serviço criado: {nome}")

        return jsonify({
            "sucesso": True,
            "mensagem": "Serviço criado com sucesso!",
            "servico_id": servico_id
        })

    elif request.method == "PUT":
        req = request.json
        servico_id = req.get("servico_id")
        if not servico_id or servico_id not in dados.get("servicos", {}):
            return jsonify({"sucesso": False, "mensagem": "Serviço não encontrado"})

        servico = dados["servicos"][servico_id]
        servico["nome"] = req.get("nome", servico["nome"])
        servico["categoria"] = req.get("categoria", servico["categoria"])
        servico["descricao"] = req.get("descricao", servico["descricao"])
        servico["valor_reais"] = float(req.get("valor_reais", servico["valor_reais"]))
        servico["pontos_gerados"] = int(req.get("pontos_gerados", servico["pontos_gerados"]))
        servico["status"] = req.get("status", servico["status"])
        servico["imagem_url"] = req.get("imagem_url", servico.get("imagem_url", ""))

        salvar_dados_github(f"Serviço atualizado: {servico['nome']}")
        return jsonify({
            "sucesso": True,
            "mensagem": "Serviço atualizado com sucesso!"
        })

    elif request.method == "DELETE":
        servico_id = request.args.get("servico_id")
        if not servico_id or servico_id not in dados.get("servicos", {}):
            return jsonify({"sucesso": False, "mensagem": "Serviço não encontrado"})

        nome = dados["servicos"][servico_id].get("nome", "Serviço")
        del dados["servicos"][servico_id]
        salvar_dados_github(f"Serviço removido: {nome}")
        return jsonify({
            "sucesso": True,
            "mensagem": "Serviço removido com sucesso!"
        })

@app.route("/api/admin/solicitacoes", methods=["GET"])
def api_admin_solicitacoes():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401

    status_filter = request.args.get("status")
    solicitacoes = []

    for s_id, s in dados.get("solicitacoes", {}).items():
        if status_filter and s.get("status") != status_filter:
            continue
        servico = dados.get("servicos", {}).get(s.get("servico_id"), {})
        cliente = obter_cliente(s.get("cliente_discord_id"))
        s_data = s.copy()
        s_data["id"] = s_id
        s_data["servico_nome"] = servico.get("nome", "Serviço não encontrado")
        s_data["cliente_nome"] = cliente.get("game_nick", "Cliente não encontrado") if cliente else "Cliente não encontrado"
        solicitacoes.append(s_data)

    solicitacoes.sort(key=lambda x: x.get("data_solicitacao", ""))

    return jsonify({
        "sucesso": True,
        "solicitacoes": solicitacoes
    })

@app.route("/api/admin/solicitacao/aprovar", methods=["POST"])
def api_admin_solicitacao_aprovar():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401

    req = request.json
    solicitacao_id = req.get("solicitacao_id")

    if not solicitacao_id or solicitacao_id not in dados.get("solicitacoes", {}):
        return jsonify({"sucesso": False, "mensagem": "Solicitação não encontrada"})

    sucesso = aprovar_solicitacao(solicitacao_id, session['usuario']['id'])

    if sucesso:
        salvar_dados_github(f"Solicitação aprovada: {solicitacao_id}")
        return jsonify({
            "sucesso": True,
            "mensagem": "Solicitação aprovada e adicionada à fila!"
        })
    else:
        return jsonify({
            "sucesso": False,
            "mensagem": "Erro ao aprovar solicitação"
        })

@app.route("/api/admin/solicitacao/recusar", methods=["POST"])
def api_admin_solicitacao_recusar():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401

    req = request.json
    solicitacao_id = req.get("solicitacao_id")
    motivo = req.get("motivo", "Não especificado")

    if not solicitacao_id or solicitacao_id not in dados.get("solicitacoes", {}):
        return jsonify({"sucesso": False, "mensagem": "Solicitação não encontrada"})

    sucesso = recusar_solicitacao(solicitacao_id, motivo, session['usuario']['id'])

    if sucesso:
        salvar_dados_github(f"Solicitação recusada: {solicitacao_id}")
        return jsonify({
            "sucesso": True,
            "mensagem": "Solicitação recusada com sucesso!"
        })
    else:
        return jsonify({
            "sucesso": False,
            "mensagem": "Erro ao recusar solicitação"
        })

@app.route("/api/admin/clientes", methods=["GET"])
def api_admin_clientes():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401

    clientes = []
    for discord_id, cliente in dados.get("clientes", {}).items():
        cliente_data = cliente.copy()
        cliente_data["discord_id"] = discord_id
        clientes.append(cliente_data)

    return jsonify({
        "sucesso": True,
        "clientes": clientes
    })

@app.route("/api/admin/cliente/editar", methods=["POST"])
def api_admin_cliente_editar():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401

    req = request.json
    discord_id = req.get("discord_id")
    if not discord_id:
        return jsonify({"sucesso": False, "mensagem": "ID do cliente não informado"})

    cliente = obter_cliente(discord_id)
    if not cliente:
        return jsonify({"sucesso": False, "mensagem": "Cliente não encontrado"})

    if "uid" in req:
        cliente["uid"] = req["uid"]
    if "game_nick" in req:
        cliente["game_nick"] = req["game_nick"]
    if "pontos_atuais" in req:
        cliente["pontos_atuais"] = max(0, int(req["pontos_atuais"]))
    if "pontos_acumulados" in req:
        cliente["pontos_acumulados"] = max(0, int(req["pontos_acumulados"]))
    if "pontos_utilizados" in req:
        cliente["pontos_utilizados"] = max(0, int(req["pontos_utilizados"]))

    salvar_dados_github(f"Cliente editado: {discord_id}")
    return jsonify({
        "sucesso": True,
        "mensagem": "Cliente atualizado com sucesso!",
        "cliente": cliente
    })

@app.route("/api/admin/fidelidade/config", methods=["GET", "POST"])
def api_admin_fidelidade_config():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401

    if request.method == "GET":
        return jsonify({
            "sucesso": True,
            "config": dados.get("fidelidade_config", {})
        })

    req = request.json
    config = dados.setdefault("fidelidade_config", {})

    if "multiplicador_pontos" in req:
        config["multiplicador_pontos"] = max(1, int(req["multiplicador_pontos"]))
    if "validade_pontos_dias" in req:
        config["validade_pontos_dias"] = max(1, int(req["validade_pontos_dias"]))
    if "validade_cupom_dias" in req:
        config["validade_cupom_dias"] = max(1, int(req["validade_cupom_dias"]))

    salvar_dados_github("Configurações de fidelidade atualizadas")
    return jsonify({
        "sucesso": True,
        "mensagem": "Configurações atualizadas com sucesso!",
        "config": config
    })

@app.route("/api/admin/fidelidade/recompensas", methods=["GET", "POST", "DELETE"])
def api_admin_fidelidade_recompensas():
    if 'usuario' not in session or not session['usuario'].get('eh_admin', False):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 401

    if request.method == "GET":
        return jsonify({
            "sucesso": True,
            "recompensas": dados.get("fidelidade", {}).get("recompensas", [])
        })

    elif request.method == "POST":
        req = request.json
        pontos = int(req.get("pontos", 0))
        descricao = req.get("descricao", "").strip()
        tipo = req.get("tipo", "").strip()

        if not descricao or not tipo:
            return jsonify({"sucesso": False, "mensagem": "Descrição e tipo são obrigatórios"})

        recompensas = dados["fidelidade"].setdefault("recompensas", [])
        recompensas.append({
            "pontos": pontos,
            "descricao": descricao,
            "tipo": tipo
        })

        salvar_dados_github(f"Recompensa adicionada: {descricao}")
        return jsonify({
            "sucesso": True,
            "mensagem": "Recompensa adicionada com sucesso!",
            "recompensas": recompensas
        })

    elif request.method == "DELETE":
        index = request.args.get("index")
        if index is None:
            return jsonify({"sucesso": False, "mensagem": "Índice não informado"})

        index = int(index)
        recompensas = dados.get("fidelidade", {}).get("recompensas", [])
        if index < 0 or index >= len(recompensas):
            return jsonify({"sucesso": False, "mensagem": "Recompensa não encontrada"})

        removida = recompensas.pop(index)
        salvar_dados_github(f"Recompensa removida: {removida.get('descricao')}")
        return jsonify({
            "sucesso": True,
            "mensagem": "Recompensa removida com sucesso!",
            "recompensas": recompensas
        })

# ========================
# ROTA DO CLIENTE
# ========================

@app.route("/cliente")
def cliente_area():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    usuario = session['usuario']
    discord_id = usuario['id']
    cliente = obter_cliente(discord_id)

    if not cliente:
        return f'''
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Cadastro - Área do Cliente</title>
            <style>
                * {{ margin:0; padding:0; box-sizing:border-box; }}
                body {{ font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0a0a0a, #1a1a1a); min-height:100vh; padding:40px 20px; color:#e0e0e0; display:flex; align-items:center; justify-content:center; }}
                .container {{ max-width:500px; width:100%; background:#121212; border-radius:20px; padding:40px; border:1px solid #333; }}
                h1 {{ color:#f59e0b; text-align:center; margin-bottom:10px; }}
                .subtitle {{ text-align:center; color:#888; margin-bottom:30px; }}
                .form-group {{ margin-bottom:20px; }}
                label {{ display:block; margin-bottom:8px; font-weight:600; color:#5865F2; }}
                .form-control {{ width:100%; padding:12px; background:#1a1a1a; border:1px solid #333; border-radius:8px; color:#fff; font-size:16px; }}
                .form-control:focus {{ outline:none; border-color:#5865F2; }}
                .btn {{ display:block; width:100%; padding:14px; background:#f59e0b; color:#121212; border:none; border-radius:8px; font-weight:bold; font-size:16px; cursor:pointer; transition:all 0.3s; }}
                .btn:hover {{ background:#d97706; transform:translateY(-2px); }}
                .btn-secondary {{ background:#333; color:#fff; margin-top:10px; }}
                .btn-secondary:hover {{ background:#444; }}
                .alert {{ padding:12px; border-radius:8px; margin-bottom:20px; display:none; }}
                .alert-success {{ background:#1a472a; color:#4ade80; border:1px solid #2ecc71; }}
                .alert-error {{ background:#7f1d1d; color:#f87171; border:1px solid #ef4444; }}
                .info-box {{ background:#1a1a2e; border-left:4px solid #f59e0b; padding:15px; border-radius:5px; margin-bottom:20px; }}
                .info-box p {{ color:#ccc; line-height:1.6; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>👤 Cadastro</h1>
                <div class="subtitle">Complete seu cadastro para acessar a área do cliente</div>

                <div class="info-box">
                    <p>⚠️ <strong>Importante:</strong> Seu UID será vinculado permanentemente à sua conta do Discord.</p>
                </div>

                <div id="cadastro-alert" class="alert"></div>

                <div class="form-group">
                    <label>Seu UID do Jogo</label>
                    <input type="text" id="cadastro-uid" class="form-control" placeholder="Digite seu UID">
                </div>

                <div class="form-group">
                    <label>Seu Nick no Jogo</label>
                    <input type="text" id="cadastro-nick" class="form-control" placeholder="Digite seu nick">
                </div>

                <button onclick="realizarCadastro()" class="btn">✅ Cadastrar</button>
                <a href="/" class="btn btn-secondary">🏠 Voltar ao Início</a>
            </div>

            <script>
                async function realizarCadastro() {{
                    const uid = document.getElementById('cadastro-uid').value.trim();
                    const game_nick = document.getElementById('cadastro-nick').value.trim();
                    const alertEl = document.getElementById('cadastro-alert');

                    if (!uid || !game_nick) {{
                        alertEl.className = 'alert alert-error';
                        alertEl.textContent = '⚠️ Preencha todos os campos';
                        alertEl.style.display = 'block';
                        return;
                    }}

                    try {{
                        const resp = await fetch('/api/cliente/cadastrar', {{
                            method: 'POST',
                            headers: {{'Content-Type': 'application/json'}},
                            body: JSON.stringify({{uid, game_nick}})
                        }});
                        const result = await resp.json();

                        if (result.sucesso) {{
                            alertEl.className = 'alert alert-success';
                            alertEl.textContent = '✅ ' + result.mensagem;
                            alertEl.style.display = 'block';
                            setTimeout(() => window.location.reload(), 1500);
                        }} else {{
                            alertEl.className = 'alert alert-error';
                            alertEl.textContent = '❌ ' + result.mensagem;
                            alertEl.style.display = 'block';
                        }}
                    }} catch(e) {{
                        alertEl.className = 'alert alert-error';
                        alertEl.textContent = '❌ Erro: ' + e.message;
                        alertEl.style.display = 'block';
                    }}
                }}
            </script>
        </body>
        </html>
        '''

    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Área do Cliente</title>
        <style>
            * {{ margin:0; padding:0; box-sizing:border-box; }}
            body {{ font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0a0a0a, #1a1a1a); min-height:100vh; padding:20px; color:#e0e0e0; }}
            .container {{ max-width:1200px; margin:0 auto; }}
            header {{ background:#121212; padding:15px 20px; border-radius:15px; display:flex; justify-content:space-between; align-items:center; border:1px solid #333; margin-bottom:20px; }}
            .user-info {{ display:flex; align-items:center; gap:15px; }}
            .avatar {{ width:40px; height:40px; border-radius:50%; border:2px solid #f59e0b; }}
            h1 {{ color:#f59e0b; font-size:1.5rem; }}
            .btn {{ padding:8px 16px; border:none; border-radius:8px; cursor:pointer; font-weight:600; text-decoration:none; display:inline-block; transition:all 0.2s; }}
            .btn-primary {{ background:#5865F2; color:white; }}
            .btn-primary:hover {{ background:#4752C4; }}
            .btn-warning {{ background:#f59e0b; color:#121212; }}
            .btn-warning:hover {{ background:#d97706; }}
            .btn-danger {{ background:#ef4444; color:white; }}
            .btn-danger:hover {{ background:#dc2626; }}
            .btn-success {{ background:#10b981; color:white; }}
            .btn-success:hover {{ background:#059669; }}
            .btn-sm {{ padding:4px 12px; font-size:0.85rem; }}
            .grid-2 {{ display:grid; grid-template-columns:repeat(2,1fr); gap:20px; }}
            .grid-3 {{ display:grid; grid-template-columns:repeat(3,1fr); gap:20px; }}
            @media (max-width:768px) {{ .grid-2, .grid-3 {{ grid-template-columns:1fr; }} }}
            .card {{ background:#121212; border-radius:15px; padding:20px; border:1px solid #333; }}
            .card h2 {{ color:#5865F2; margin-bottom:15px; font-size:1.2rem; }}
            .card h3 {{ color:#f59e0b; margin-bottom:10px; font-size:1rem; }}
            .stat-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:10px; }}
            .stat-item {{ background:#1a1a1a; padding:15px; border-radius:10px; text-align:center; }}
            .stat-value {{ font-size:1.8rem; font-weight:bold; color:#f59e0b; }}
            .stat-label {{ color:#888; font-size:0.8rem; }}
            .progress-bar {{ width:100%; height:20px; background:#1a1a1a; border-radius:10px; overflow:hidden; margin-top:10px; }}
            .progress-fill {{ height:100%; background:linear-gradient(90deg,#f59e0b,#d97706); border-radius:10px; transition:width 0.5s; }}
            .tab-nav {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:20px; border-bottom:2px solid #333; padding-bottom:10px; }}
            .tab-btn {{ padding:10px 20px; background:#1a1a1a; border:none; border-radius:8px 8px 0 0; cursor:pointer; font-weight:600; color:#888; transition:all 0.3s; }}
            .tab-btn:hover {{ background:#2a2a2a; color:#fff; }}
            .tab-btn.active {{ background:#f59e0b; color:#121212; }}
            .tab {{ display:none; animation:fadeIn 0.3s; }}
            .tab.active {{ display:block; }}
            @keyframes fadeIn {{ from{{opacity:0;}} to{{opacity:1;}} }}
            .table-container {{ overflow-x:auto; }}
            table {{ width:100%; border-collapse:collapse; }}
            th, td {{ padding:12px; text-align:left; border-bottom:1px solid #333; }}
            th {{ background:#1a1a1a; color:#f59e0b; }}
            .badge {{ display:inline-block; padding:3px 10px; border-radius:12px; font-size:0.75rem; font-weight:600; }}
            .badge-pending {{ background:#7c3aed; color:#fff; }}
            .badge-approved {{ background:#10b981; color:#fff; }}
            .badge-completed {{ background:#5865F2; color:#fff; }}
            .badge-refused {{ background:#ef4444; color:#fff; }}
            .badge-active {{ background:#10b981; color:#fff; }}
            .badge-used {{ background:#6b7280; color:#fff; }}
            .badge-expired {{ background:#ef4444; color:#fff; }}
            .modal {{ display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.7); align-items:center; justify-content:center; z-index:1000; }}
            .modal-content {{ background:#121212; padding:30px; border-radius:15px; max-width:500px; width:90%; border:1px solid #333; max-height:80vh; overflow-y:auto; }}
            .modal-content h2 {{ color:#f59e0b; margin-bottom:20px; }}
            .modal-content .form-group {{ margin-bottom:15px; }}
            .modal-content label {{ display:block; margin-bottom:5px; font-weight:600; color:#5865F2; }}
            .modal-content .form-control {{ width:100%; padding:10px; background:#1a1a1a; border:1px solid #333; border-radius:8px; color:#fff; }}
            .modal-content .form-control:focus {{ outline:none; border-color:#5865F2; }}
            .modal-actions {{ display:flex; gap:10px; margin-top:20px; }}
            .cupom-card {{ background:#1a1a1a; padding:15px; border-radius:10px; border-left:4px solid #f59e0b; margin-bottom:10px; }}
            .cupom-card .codigo {{ font-family:monospace; font-size:1.2rem; color:#f59e0b; font-weight:bold; }}
            .servico-card {{ background:#1a1a1a; padding:15px; border-radius:10px; border-left:4px solid #5865F2; margin-bottom:10px; }}
            .servico-card .preco {{ color:#4ade80; font-weight:bold; }}
            .servico-card .pontos {{ color:#f59e0b; }}
            .alert {{ padding:10px; border-radius:8px; margin-bottom:15px; display:none; }}
            .alert-success {{ background:#1a472a; color:#4ade80; border:1px solid #2ecc71; }}
            .alert-error {{ background:#7f1d1d; color:#f87171; border:1px solid #ef4444; }}
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <div class="user-info">
                    <img src="https://cdn.discordapp.com/avatars/{usuario['id']}/{usuario.get('avatar', '')}.png" class="avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                    <div>
                        <strong>{usuario['nome_usuario']}</strong>
                        <span style="color:#888;font-size:0.8rem;display:block;">UID: {cliente.get('uid', 'N/A')} | Nick: {cliente.get('game_nick', 'N/A')}</span>
                    </div>
                </div>
                <div>
                    <a href="/" class="btn btn-primary btn-sm">🏠 Início</a>
                    <a href="/logout" class="btn btn-danger btn-sm">🚪 Sair</a>
                </div>
            </header>

            <div class="tab-nav">
                <button class="tab-btn active" onclick="showTab('painel')">📊 Painel</button>
                <button class="tab-btn" onclick="showTab('servicos')">🎮 Serviços</button>
                <button class="tab-btn" onclick="showTab('solicitacoes')">📋 Solicitações</button>
                <button class="tab-btn" onclick="showTab('cupons')">🎫 Meus Cupons</button>
                <button class="tab-btn" onclick="showTab('loja')">🏪 Loja</button>
            </div>

            <div id="painel" class="tab active">
                <div class="grid-2">
                    <div class="card">
                        <h2>📊 Seus Pontos</h2>
                        <div class="stat-grid">
                            <div class="stat-item"><div class="stat-value" id="pontos-atuais">0</div><div class="stat-label">Pontos Atuais</div></div>
                            <div class="stat-item"><div class="stat-value" id="pontos-acumulados">0</div><div class="stat-label">Total Acumulado</div></div>
                            <div class="stat-item"><div class="stat-value" id="pontos-utilizados">0</div><div class="stat-label">Total Utilizado</div></div>
                        </div>
                        <div style="margin-top:15px;">
                            <div style="display:flex;justify-content:space-between;color:#888;font-size:0.85rem;">
                                <span>Progresso para próximo prêmio</span>
                                <span id="progresso-texto">0 / 60</span>
                            </div>
                            <div class="progress-bar">
                                <div class="progress-fill" id="progresso-bar" style="width:0%;"></div>
                            </div>
                        </div>
                        <div style="margin-top:10px;font-size:0.8rem;color:#666;">
                            Última compra: <span id="ultima-compra">N/A</span> | Último resgate: <span id="ultimo-resgate">N/A</span>
                        </div>
                    </div>

                    <div class="card">
                        <h2>👤 Seu Perfil</h2>
                        <p><strong>Discord:</strong> {usuario['nome_usuario']}</p>
                        <p><strong>UID:</strong> {cliente.get('uid', 'N/A')}</p>
                        <p><strong>Nick:</strong> {cliente.get('game_nick', 'N/A')}</p>
                        <p><strong>Cadastro:</strong> {cliente.get('data_cadastro', 'N/A')}</p>
                        <button onclick="abrirSolicitarServico()" class="btn btn-warning" style="margin-top:15px;width:100%;">📝 Solicitar Serviço</button>
                    </div>
                </div>
            </div>

            <div id="servicos" class="tab">
                <div class="card">
                    <h2>🎮 Serviços Disponíveis</h2>
                    <div id="lista-servicos"></div>
                </div>
            </div>

            <div id="solicitacoes" class="tab">
                <div class="card">
                    <h2>📋 Suas Solicitações</h2>
                    <div class="table-container">
                        <table>
                            <thead>
                                <tr><th>Serviço</th><th>Jogo</th><th>Status</th><th>Data</th><th>Pontos</th></tr>
                            </thead>
                            <tbody id="tabela-solicitacoes"></tbody>
                        </table>
                    </div>
                    <button onclick="carregarSolicitacoes()" class="btn btn-primary btn-sm" style="margin-top:10px;">🔄 Atualizar</button>
                </div>
            </div>

            <div id="cupons" class="tab">
                <div class="card">
                    <h2>🎫 Meus Cupons</h2>
                    <div id="lista-cupons"></div>
                    <button onclick="carregarCupons()" class="btn btn-primary btn-sm" style="margin-top:10px;">🔄 Atualizar</button>
                </div>
            </div>

            <div id="loja" class="tab">
                <div class="card">
                    <h2>🏪 Loja de Fidelidade</h2>
                    <div id="lista-recompensas"></div>
                </div>
            </div>
        </div>

        <div id="modal-solicitar" class="modal">
            <div class="modal-content">
                <h2>📝 Solicitar Serviço</h2>
                <div id="solicitar-alert" class="alert"></div>
                <div class="form-group">
                    <label>Serviço</label>
                    <select id="solicitar-servico" class="form-control"></select>
                </div>
                <div class="form-group">
                    <label>Jogo</label>
                    <select id="solicitar-jogo" class="form-control"></select>
                </div>
                <div class="form-group">
                    <label>Observações</label>
                    <textarea id="solicitar-obs" class="form-control" rows="3" placeholder="Detalhes adicionais..."></textarea>
                </div>
                <div class="form-group">
                    <label>Cupom (opcional)</label>
                    <input type="text" id="solicitar-cupom" class="form-control" placeholder="ZANKON-XXXXXX">
                </div>
                <div class="modal-actions">
                    <button onclick="enviarSolicitacao()" class="btn btn-warning" style="flex:1;">📤 Enviar</button>
                    <button onclick="fecharModal('modal-solicitar')" class="btn btn-secondary" style="flex:1;background:#333;color:#fff;">Cancelar</button>
                </div>
            </div>
        </div>

        <script>
            let dadosCliente = {{}};

            async function carregarDadosCliente() {{
                try {{
                    const [pontosRes, servicosRes, jogosRes, recompensasRes] = await Promise.all([
                        fetch('/api/cliente/pontos'),
                        fetch('/api/cliente/servicos'),
                        fetch('/api/cliente/jogos'),
                        fetch('/api/cliente/recompensas')
                    ]);

                    const pontos = await pontosRes.json();
                    const servicos = await servicosRes.json();
                    const jogos = await jogosRes.json();
                    const recompensas = await recompensasRes.json();

                    if (pontos.sucesso) {{
                        document.getElementById('pontos-atuais').textContent = pontos.pontos_atuais;
                        document.getElementById('pontos-acumulados').textContent = pontos.pontos_acumulados;
                        document.getElementById('pontos-utilizados').textContent = pontos.pontos_utilizados;
                        document.getElementById('ultima-compra').textContent = pontos.ultima_compra ? new Date(pontos.ultima_compra).toLocaleDateString() : 'N/A';
                        document.getElementById('ultimo-resgate').textContent = pontos.ultimo_resgate ? new Date(pontos.ultimo_resgate).toLocaleDateString() : 'N/A';

                        const pontosAtuais = pontos.pontos_atuais || 0;
                        const recompensasList = recompensas.sucesso ? recompensas.recompensas : [];
                        let proximo = null;
                        for (const r of recompensasList.sort((a,b) => a.pontos - b.pontos)) {{
                            if (r.pontos > pontosAtuais) {{
                                proximo = r;
                                break;
                            }}
                        }}
                        if (proximo) {{
                            const progresso = Math.min(100, (pontosAtuais / proximo.pontos) * 100);
                            document.getElementById('progresso-texto').textContent = `${{pontosAtuais}} / ${{proximo.pontos}}`;
                            document.getElementById('progresso-bar').style.width = progresso + '%';
                        }} else {{
                            document.getElementById('progresso-texto').textContent = `${{pontosAtuais}} / Máximo`;
                            document.getElementById('progresso-bar').style.width = '100%';
                        }}
                    }}

                    if (servicos.sucesso) {{
                        const container = document.getElementById('lista-servicos');
                        const servicosList = Object.values(servicos.servicos);
                        if (servicosList.length === 0) {{
                            container.innerHTML = '<p style="color:#888;">Nenhum serviço disponível no momento.</p>';
                        }} else {{
                            container.innerHTML = servicosList.map(s => `
                                <div class="servico-card">
                                    <h3>${{escapeHtml(s.nome)}}</h3>
                                    <p style="color:#ccc;">${{escapeHtml(s.descricao)}}</p>
                                    <p><span class="preco">R$ ${{s.valor_reais.toFixed(2)}}</span> | <span class="pontos">+${{s.pontos_gerados}} pontos</span></p>
                                    <button onclick="abrirSolicitarComServico('${{Object.keys(servicos.servicos).find(k => servicos.servicos[k].nome === s.nome)}}')" class="btn btn-warning btn-sm">📝 Solicitar</button>
                                </div>
                            `).join('');
                        }}
                    }}

                    if (jogos.sucesso) {{
                        const select = document.getElementById('solicitar-jogo');
                        select.innerHTML = jogos.jogos.map(j => `<option value="${{escapeHtml(j)}}">${{escapeHtml(j)}}</option>`).join('');
                    }}

                    if (recompensas.sucesso) {{
                        const container = document.getElementById('lista-recompensas');
                        const recompensasList = recompensas.recompensas;
                        if (recompensasList.length === 0) {{
                            container.innerHTML = '<p style="color:#888;">Nenhuma recompensa disponível.</p>';
                        }} else {{
                            container.innerHTML = recompensasList.map(r => `
                                <div class="servico-card" style="border-left-color:#f59e0b;">
                                    <h3>🎯 ${{escapeHtml(r.descricao)}}</h3>
                                    <p><span class="pontos">${{r.pontos}} pontos</span></p>
                                    <button onclick="resgatarRecompensa('${{r.tipo}}')" class="btn btn-success btn-sm">🔄 Resgatar</button>
                                </div>
                            `).join('');
                        }}
                    }}

                    carregarSolicitacoes();
                    carregarCupons();
                    carregarServicosSelect();
                }} catch(e) {{ console.error(e); }}
            }}

            async function carregarServicosSelect() {{
                try {{
                    const resp = await fetch('/api/cliente/servicos');
                    const data = await resp.json();
                    if (data.sucesso) {{
                        const select = document.getElementById('solicitar-servico');
                        const servicos = Object.values(data.servicos);
                        select.innerHTML = servicos.map(s => `<option value="${{Object.keys(data.servicos).find(k => data.servicos[k].nome === s.nome)}}">${{escapeHtml(s.nome)}} (R$ ${{s.valor_reais.toFixed(2)}})</option>`).join('');
                    }}
                }} catch(e) {{ console.error(e); }}
            }}

            async function carregarSolicitacoes() {{
                try {{
                    const resp = await fetch('/api/cliente/solicitacoes');
                    const data = await resp.json();
                    if (data.sucesso) {{
                        const tbody = document.getElementById('tabela-solicitacoes');
                        if (data.solicitacoes.length === 0) {{
                            tbody.innerHTML = '<tr><td colspan="5" style="color:#888;">Nenhuma solicitação encontrada.</td></tr>';
                        }} else {{
                            tbody.innerHTML = data.solicitacoes.map(s => `
                                <tr>
                                    <td>${{escapeHtml(s.servico_nome)}}</td>
                                    <td>${{escapeHtml(s.jogo || 'N/A')}}</td>
                                    <td><span class="badge badge-${{s.status === 'Aguardando Aprovação' ? 'pending' : s.status === 'Em Andamento' ? 'approved' : s.status === 'Concluído' ? 'completed' : 'refused'}}">${{s.status}}</span></td>
                                    <td>${{new Date(s.data_solicitacao).toLocaleDateString()}}</td>
                                    <td>${{s.pontos_creditados || 0}}</td>
                                </tr>
                            `).join('');
                        }}
                    }}
                }} catch(e) {{ console.error(e); }}
            }}

            async function carregarCupons() {{
                try {{
                    const resp = await fetch('/api/cliente/cupons');
                    const data = await resp.json();
                    if (data.sucesso) {{
                        const container = document.getElementById('lista-cupons');
                        if (data.cupons.length === 0) {{
                            container.innerHTML = '<p style="color:#888;">Nenhum cupom encontrado.</p>';
                        }} else {{
                            container.innerHTML = data.cupons.map(c => `
                                <div class="cupom-card">
                                    <div class="codigo">${{c.codigo}}</div>
                                    <div style="color:#ccc;">${{escapeHtml(c.descricao)}}</div>
                                    <div style="font-size:0.8rem;color:#888;">
                                        Válido até: ${{c.validade ? new Date(c.validade).toLocaleDateString() : 'N/A'}}
                                        <span class="badge badge-${{c.status}}">${{c.status}}</span>
                                    </div>
                                </div>
                            `).join('');
                        }}
                    }}
                }} catch(e) {{ console.error(e); }}
            }}

            function abrirSolicitarServico() {{
                document.getElementById('modal-solicitar').style.display = 'flex';
                document.getElementById('solicitar-alert').style.display = 'none';
                document.getElementById('solicitar-obs').value = '';
                document.getElementById('solicitar-cupom').value = '';
                carregarServicosSelect();
            }}

            function abrirSolicitarComServico(servicoId) {{
                document.getElementById('modal-solicitar').style.display = 'flex';
                document.getElementById('solicitar-servico').value = servicoId;
                document.getElementById('solicitar-alert').style.display = 'none';
                document.getElementById('solicitar-obs').value = '';
                document.getElementById('solicitar-cupom').value = '';
            }}

            async function enviarSolicitacao() {{
                const servicoId = document.getElementById('solicitar-servico').value;
                const jogo = document.getElementById('solicitar-jogo').value;
                const observacoes = document.getElementById('solicitar-obs').value.trim();
                const cupom = document.getElementById('solicitar-cupom').value.trim();
                const alertEl = document.getElementById('solicitar-alert');

                if (!servicoId) {{
                    alertEl.className = 'alert alert-error';
                    alertEl.textContent = '❌ Selecione um serviço';
                    alertEl.style.display = 'block';
                    return;
                }}

                try {{
                    const resp = await fetch('/api/cliente/solicitar', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{servico_id: servicoId, jogo, observacoes, cupom}})
                    }});
                    const result = await resp.json();

                    if (result.sucesso) {{
                        alertEl.className = 'alert alert-success';
                        alertEl.textContent = '✅ ' + result.mensagem;
                        alertEl.style.display = 'block';
                        setTimeout(() => {{
                            fecharModal('modal-solicitar');
                            carregarSolicitacoes();
                        }}, 1500);
                    }} else {{
                        alertEl.className = 'alert alert-error';
                        alertEl.textContent = '❌ ' + result.mensagem;
                        alertEl.style.display = 'block';
                    }}
                }} catch(e) {{
                    alertEl.className = 'alert alert-error';
                    alertEl.textContent = '❌ Erro: ' + e.message;
                    alertEl.style.display = 'block';
                }}
            }}

            async function resgatarRecompensa(tipo) {{
                if (!confirm('Confirmar resgate desta recompensa?')) return;

                try {{
                    const resp = await fetch('/api/cliente/resgatar', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{tipo}})
                    }});
                    const result = await resp.json();

                    if (result.sucesso) {{
                        alert('✅ Recompensa resgatada! Código: ' + result.codigo);
                        carregarDadosCliente();
                    }} else {{
                        alert('❌ ' + result.mensagem);
                    }}
                }} catch(e) {{
                    alert('❌ Erro: ' + e.message);
                }}
            }}

            function fecharModal(id) {{
                document.getElementById(id).style.display = 'none';
            }}

            function showTab(tabId) {{
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                document.getElementById(tabId).classList.add('active');
                if (event && event.target) event.target.classList.add('active');
                if (tabId === 'solicitacoes') carregarSolicitacoes();
                if (tabId === 'cupons') carregarCupons();
            }}

            function escapeHtml(texto) {{ if (!texto) return ''; return texto.replace(/[&<>]/g, function(m) {{ if (m === '&') return '&amp;'; if (m === '<') return '&lt;'; if (m === '>') return '&gt;'; return m; }}); }}

            window.onclick = function(event) {{
                if (event.target.classList.contains('modal')) {{
                    event.target.style.display = 'none';
                }}
            }}

            document.addEventListener('DOMContentLoaded', carregarDadosCliente);
        </script>
    </body>
    </html>
    '''

# ========================
# DASHBOARD PRINCIPAL (ADMIN)
# ========================

@app.route("/dashboard")
def dashboard():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    if not session['usuario'].get('eh_admin', False):
        return redirect(url_for('cliente_area'))

    usuario = session['usuario']
    config = dados.get("config", {})
    fila = obter_dados_fila()
    anti_spam = dados.get("anti_spam", {})
    links = obter_links_fila()
    botoes_precos = links.get("botoes_precos", [])

    botoes_precos_json = json.dumps(botoes_precos)

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
            .badge {{ display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; }}
            .badge-pending {{ background: #7c3aed; color: #fff; }}
            .badge-approved {{ background: #10b981; color: #fff; }}
            .badge-completed {{ background: #5865F2; color: #fff; }}
            .badge-refused {{ background: #ef4444; color: #fff; }}
            .modal {{ display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); align-items: center; justify-content: center; z-index: 1000; }}
            .modal-content {{ background: #121212; padding: 30px; border-radius: 15px; max-width: 500px; width: 90%; border: 1px solid #333; max-height: 80vh; overflow-y: auto; }}
            .modal-content h2 {{ color: #f59e0b; margin-bottom: 20px; }}
            .modal-actions {{ display: flex; gap: 10px; margin-top: 20px; }}
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
                <h1> Painel Admin</h1>
                <div class="user-info">
                    <img src="https://cdn.discordapp.com/avatars/{usuario['id']}/{usuario.get('avatar', '')}.png" class="avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                    <span>{usuario['nome_usuario']}</span>
                    <a href="/" class="btn btn-primary">🏠 Início</a>
                    <a href="/cliente" class="btn btn-warning">👤 Cliente</a>
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
                <button class="tab-btn" onclick="showTab('servicos')">🎮 Serviços</button>
                <button class="tab-btn" onclick="showTab('clientes')">👤 Clientes</button>
                <button class="tab-btn" onclick="showTab('solicitacoes')">📋 Solicitações</button>
                <button class="tab-btn" onclick="showTab('fidelidade')">🏪 Fidelidade</button>
                <button class="tab-btn" onclick="showTab('comandos')">⚡ Comandos</button>
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
                            <div class="stat-card"><h3>{len(dados.get("servicos", {}))}</h3><p>Serviços</p></div>
                        </div>
                    </div>
                    <div class="card">
                        <h2>⚡ Status</h2>
                        <p><strong>Bot:</strong> {'✅ Online' if bot.is_ready() else '❌ Offline'}</p>
                        <p><strong>Processador:</strong> {'✅ Ativo' if processador_acoes_rodando else '❌ Inativo'}</p>
                        <p><strong>Ações na fila:</strong> {len(acoes_fila_bot)}</p>
                        <p><strong>Anti-Spam:</strong> {'✅ Ativo' if anti_spam.get('ativado', True) else '❌ Desativado'}</p>
                        <p><strong>Multiplicador de Pontos:</strong> {dados.get('fidelidade_config', {}).get('multiplicador_pontos', 1)}</p>
                    </div>
                </div>
            </div>

            <div id="comandos_canais" class="tab">
                <div class="card">
                    <h2>📢 Configurar Canais dos Comandos</h2>
                    <div class="info-box">
                        💡 <strong>Como funciona:</strong><br>
                        • Selecione um canal → O comando só funcionará naquele canal<br>
                        • Selecione o <strong>mesmo canal novamente</strong> → Remove a restrição
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
                    <button onclick="salvarConfigComandos()" class="btn btn-primary">💾 Salvar</button>
                    <div id="comandos-alert" class="alert"></div>
                </div>
            </div>

            <div id="antispam" class="tab">
                <div class="card">
                    <h2>🛡️ Configuração Anti-Spam</h2>
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
                            <label>Cargos Ignorados (separar por vírgula)</label>
                            <input type="text" id="as-cargos" class="form-control" value="{','.join(anti_spam.get('cargos_ignorados', ['Administrador', 'Moderador', 'Staff', 'Dono']))}">
                        </div>
                        <div class="form-group">
                            <label>Comandos Ignorados (separar por vírgula)</label>
                            <input type="text" id="as-comandos" class="form-control" value="{','.join(anti_spam.get('comandos_ignorados', ['$w','$wa','$wg','$h','$ha','$hg','$tu','$dk','$mmi','$vote','$rolls','$k','$mu']))}">
                        </div>
                    </div>
                    <button onclick="salvarAntiSpam()" class="btn btn-primary">💾 Salvar</button>
                    <div id="as-alert" class="alert"></div>
                </div>
            </div>

            <div id="boasvindas" class="tab">
                <div class="card">
                    <h2>👋 Configurar Boas-vindas</h2>
                    <div class="form-group">
                        <label>Canal</label>
                        <select id="welcome-canal" class="form-control"></select>
                    </div>
                    <div class="form-group">
                        <label>Mensagem</label>
                        <textarea id="welcome-mensagem" class="form-control" rows="3"></textarea>
                        <small>Use {{member}} para mencionar</small>
                    </div>
                    <div class="form-group">
                        <label>Imagem de Fundo (URL)</label>
                        <input type="url" id="welcome-imagem" class="form-control" placeholder="https://exemplo.com/imagem.jpg">
                    </div>
                    <button onclick="salvarBoasVindas()" class="btn btn-primary">💾 Salvar</button>
                    <div id="welcome-alert" class="alert"></div>
                </div>
            </div>

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
                        <div style="display: flex; gap: 1rem;">
                            <input type="number" id="novo-nivel" class="form-control" placeholder="Nível" min="1" style="width: 100px;">
                            <select id="novo-cargo" class="form-control" style="flex:1;"></select>
                            <button onclick="adicionarCargoNivel()" class="btn btn-primary">➕ Adicionar</button>
                        </div>
                    </div>
                </div>
            </div>

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
                            <textarea id="rr-conteudo" class="form-control" rows="3"></textarea>
                        </div>
                        <div class="form-group">
                            <label>Emoji:Cargo (separar por vírgula)</label>
                            <input type="text" id="rr-pares" class="form-control" placeholder="✅:Verificado,👍:Aprovado">
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
                            <textarea id="btn-conteudo" class="form-control" rows="3"></textarea>
                        </div>
                        <div class="form-group">
                            <label>Botão:Cargo (separar por vírgula)</label>
                            <input type="text" id="btn-pares" class="form-control" placeholder="Notícias:Notícias,Eventos:Eventos">
                        </div>
                        <button onclick="criarBotoesCargo()" class="btn btn-success">🔄 Criar</button>
                        <div id="btn-alert" class="alert"></div>
                    </div>
                </div>
            </div>

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
                            <input type="text" id="warn-motivo" class="form-control" placeholder="Motivo">
                        </div>
                        <button onclick="aplicarAdvertencia()" class="btn btn-warning">⚠️ Advertir</button>
                        <button onclick="limparAdvertencias()" class="btn btn-danger">🧹 Limpar</button>
                        <div id="warn-alert" class="alert"></div>
                    </div>

                    <div class="card">
                        <h2>🔗 Bloqueio de Links</h2>
                        <div class="form-group">
                            <label>Canal</label>
                            <select id="links-canal" class="form-control"></select>
                        </div>
                        <button onclick="alternarBloqueioLinks()" class="btn btn-danger">🔒 Alternar</button>
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

            <div id="fila" class="tab">
                <div class="card">
                    <h2>📋 Configurações da Fila</h2>
                    <div class="grid-2">
                        <div><label>Nome</label><input type="text" id="fila-nome" class="form-control" value="{escape_html(fila['nome'])}"></div>
                        <div><label>Tamanho Máximo</label><input type="number" id="fila-max" class="form-control" value="{fila['configuracoes']['tamanho_maximo']}" min="1" max="100"></div>
                    </div>

                    <h3 style="margin-top: 20px;">🔗 Links</h3>
                    <div class="form-group">
                        <label>Link do Discord (convite)</label>
                        <input type="url" id="link-discord" class="form-control" value="{escape_html(links.get('discord_convite', ''))}">
                    </div>

                    <h3 style="margin-top: 20px;">💰 Botões de Preço</h3>
                    <div class="form-group">
                        <label>Novo Botão - Nome</label>
                        <input type="text" id="novo-botao-nome" class="form-control" placeholder="Ex: Tabela de Preços">
                    </div>
                    <div class="form-group">
                        <label>Novo Botão - URL</label>
                        <input type="url" id="novo-botao-url" class="form-control" placeholder="https://docs.google.com/...">
                    </div>
                    <button onclick="adicionarBotaoPreco()" class="btn btn-success">➕ Adicionar</button>

                    <div id="botoes-precos-lista" class="botoes-lista" style="margin-top: 20px;"></div>

                    <div style="display: flex; gap: 1rem; margin-top: 1rem;">
                        <button onclick="salvarConfigFila()" class="btn btn-primary">💾 Salvar</button>
                        <button onclick="alternarStatusFila()" id="toggle-fila-btn" class="btn {'btn-success' if fila['configuracoes']['aberta'] else 'btn-danger'}">{'🔓 Fechar' if fila['configuracoes']['aberta'] else '🔒 Abrir'}</button>
                        <button onclick="limparFila()" class="btn btn-danger">🗑️ Limpar</button>
                    </div>
                    <div id="fila-status" style="margin-top: 1rem; padding: 0.5rem; background: #1a1a1a; border-radius: 5px;">Status: {'🟢 ABERTA' if fila['configuracoes']['aberta'] else '🔴 FECHADA'} | {len(fila['entradas'])}/{fila['configuracoes']['tamanho_maximo']}</div>
                </div>

                <div class="card">
                    <h2>➕ Adicionar à Fila</h2>
                    <div style="display: flex; gap: 1rem; flex-wrap: wrap;">
                        <input type="text" id="add-nome" class="form-control" placeholder="Nome" style="flex:1;">
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

            <div id="servicos" class="tab">
                <div class="card">
                    <h2>🎮 Gerenciar Serviços</h2>
                    <div id="servicos-lista" style="margin-bottom:20px;"></div>

                    <h3>➕ Novo Serviço</h3>
                    <div class="grid-2">
                        <div class="form-group">
                            <label>Nome</label>
                            <input type="text" id="servico-nome" class="form-control" placeholder="Build Completa">
                        </div>
                        <div class="form-group">
                            <label>Categoria</label>
                            <input type="text" id="servico-categoria" class="form-control" placeholder="Builds">
                        </div>
                        <div class="form-group">
                            <label>Descrição</label>
                            <textarea id="servico-descricao" class="form-control" rows="2"></textarea>
                        </div>
                        <div class="form-group">
                            <label>Imagem (URL)</label>
                            <input type="url" id="servico-imagem" class="form-control" placeholder="https://...">
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
                            <label>Status</label>
                            <select id="servico-status" class="form-control">
                                <option value="ativo">Ativo</option>
                                <option value="inativo">Inativo</option>
                            </select>
                        </div>
                    </div>
                    <button onclick="criarServico()" class="btn btn-success">➕ Criar Serviço</button>
                    <div id="servico-alert" class="alert"></div>
                </div>
            </div>

            <div id="clientes" class="tab">
                <div class="card">
                    <h2>👤 Gerenciar Clientes</h2>
                    <div id="clientes-lista"></div>
                </div>
                <div class="card">
                    <h2>✏️ Editar Cliente</h2>
                    <div class="form-group">
                        <label>Selecione o Cliente</label>
                        <select id="editar-cliente-select" class="form-control" onchange="carregarClienteEdicao()"></select>
                    </div>
                    <div class="grid-2">
                        <div class="form-group">
                            <label>UID</label>
                            <input type="text" id="editar-uid" class="form-control">
                        </div>
                        <div class="form-group">
                            <label>Nick</label>
                            <input type="text" id="editar-nick" class="form-control">
                        </div>
                        <div class="form-group">
                            <label>Pontos Atuais</label>
                            <input type="number" id="editar-pontos" class="form-control">
                        </div>
                        <div class="form-group">
                            <label>Pontos Acumulados</label>
                            <input type="number" id="editar-acumulados" class="form-control">
                        </div>
                    </div>
                    <button onclick="salvarEdicaoCliente()" class="btn btn-primary">💾 Salvar</button>
                    <div id="editar-cliente-alert" class="alert"></div>
                </div>
            </div>

            <div id="solicitacoes" class="tab">
                <div class="card">
                    <h2>📋 Gerenciar Solicitações</h2>
                    <div style="display:flex; gap:10px; margin-bottom:15px; flex-wrap:wrap;">
                        <button onclick="carregarSolicitacoesAdmin('Aguardando Aprovação')" class="btn btn-warning btn-sm">⏳ Pendentes</button>
                        <button onclick="carregarSolicitacoesAdmin('Em Andamento')" class="btn btn-success btn-sm">✅ Aceitas</button>
                        <button onclick="carregarSolicitacoesAdmin('Concluído')" class="btn btn-primary btn-sm">✔️ Concluídas</button>
                        <button onclick="carregarSolicitacoesAdmin('Recusado')" class="btn btn-danger btn-sm">❌ Recusadas</button>
                        <button onclick="carregarSolicitacoesAdmin('')" class="btn btn-sm" style="background:#333;">📋 Todas</button>
                    </div>
                    <div id="solicitacoes-admin-lista"></div>
                    <div id="solicitacoes-admin-alert" class="alert"></div>
                </div>
            </div>

            <div id="fidelidade" class="tab">
                <div class="card">
                    <h2>🏪 Configurações de Fidelidade</h2>
                    <div class="grid-3">
                        <div class="form-group">
                            <label>Multiplicador de Pontos (1 real = X pontos)</label>
                            <input type="number" id="fid-multiplicador" class="form-control" value="{dados.get('fidelidade_config', {}).get('multiplicador_pontos', 1)}" min="1">
                        </div>
                        <div class="form-group">
                            <label>Validade dos Pontos (dias)</label>
                            <input type="number" id="fid-validade-pontos" class="form-control" value="{dados.get('fidelidade_config', {}).get('validade_pontos_dias', 90)}" min="1">
                        </div>
                        <div class="form-group">
                            <label>Validade dos Cupons (dias)</label>
                            <input type="number" id="fid-validade-cupom" class="form-control" value="{dados.get('fidelidade_config', {}).get('validade_cupom_dias', 30)}" min="1">
                        </div>
                    </div>
                    <button onclick="salvarConfigFidelidade()" class="btn btn-primary">💾 Salvar</button>
                    <div id="fid-alert" class="alert"></div>
                </div>

                <div class="card">
                    <h2>🏪 Recompensas da Loja</h2>
                    <div id="recompensas-lista"></div>
                    <h3>➕ Nova Recompensa</h3>
                    <div class="grid-3">
                        <div class="form-group">
                            <label>Pontos Necessários</label>
                            <input type="number" id="recompensa-pontos" class="form-control" placeholder="60">
                        </div>
                        <div class="form-group">
                            <label>Descrição</label>
                            <input type="text" id="recompensa-descricao" class="form-control" placeholder="1 Dia de Quests Diárias">
                        </div>
                        <div class="form-group">
                            <label>Tipo (identificador único)</label>
                            <input type="text" id="recompensa-tipo" class="form-control" placeholder="quests_diarias">
                        </div>
                    </div>
                    <button onclick="criarRecompensa()" class="btn btn-success">➕ Adicionar</button>
                    <div id="recompensa-alert" class="alert"></div>
                </div>
            </div>

            <div id="comandos" class="tab">
                <div class="card">
                    <h2>📝 Criar Embed Personalizada</h2>
                    <div class="form-group">
                        <label>Canal</label>
                        <select id="embed-canal" class="form-control"></select>
                    </div>
                    <div class="form-group">
                        <label>Título</label>
                        <input type="text" id="embed-titulo" class="form-control">
                    </div>
                    <div class="form-group">
                        <label>Corpo</label>
                        <textarea id="embed-corpo" class="form-control" rows="3"></textarea>
                    </div>
                    <div class="form-group">
                        <label>Cor (hexadecimal)</label>
                        <input type="text" id="embed-cor" class="form-control" value="#5865F2">
                    </div>
                    <div class="form-group">
                        <label>Imagem (URL)</label>
                        <input type="url" id="embed-imagem" class="form-control" placeholder="https://...">
                    </div>
                    <div class="form-group">
                        <label>Menção</label>
                        <select id="embed-mencao" class="form-control"><option value="">Nenhuma</option><option value="everyone">@everyone</option><option value="here">@here</option></select>
                    </div>
                    <button onclick="criarEmbed()" class="btn btn-primary">📝 Criar</button>
                    <div id="embed-alert" class="alert"></div>
                </div>
            </div>
        </div>

        <div id="modal-solicitacao" class="modal">
            <div class="modal-content">
                <h2 id="modal-solicitacao-titulo">Aprovar Solicitação</h2>
                <div id="modal-solicitacao-info"></div>
                <div class="form-group" id="modal-motivo-group" style="display:none;">
                    <label>Motivo da Recusa</label>
                    <textarea id="modal-motivo" class="form-control" rows="3" placeholder="Informe o motivo da recusa..."></textarea>
                </div>
                <div class="modal-actions">
                    <button onclick="aprovarSolicitacaoModal()" id="modal-btn-aprovar" class="btn btn-success" style="flex:1;">✅ Aprovar</button>
                    <button onclick="recusarSolicitacaoModal()" id="modal-btn-recusar" class="btn btn-danger" style="flex:1;">❌ Recusar</button>
                    <button onclick="fecharModal('modal-solicitacao')" class="btn btn-secondary" style="flex:1;background:#333;color:#fff;">Fechar</button>
                </div>
            </div>
        </div>

        <script>
            let canais = [];
            let cargos = [];
            let membros = [];
            let configAtual = {{}};
            let botoesPrecos = {botoes_precos_json};
            let solicitacaoAtual = null;

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
                    carregarServicos();
                    carregarClientes();
                    carregarRecompensas();
                }} catch(e) {{ console.error(e); }}
            }}

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
                                <button onclick="editarBotaoPreco(${{index}})" class="btn btn-primary btn-sm">✏️</button>
                                <button onclick="removerBotaoPreco(${{index}})" class="btn btn-danger btn-sm">🗑️</button>
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
                const novoNome = prompt('Digite o novo nome:', botao.nome);
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
                }});
            }}

            function atualizarStatusPerfil(canalId) {{
                const div = document.getElementById('perfil-status');
                if (!canalId) {{
                    div.innerHTML = '<span class="config-badge" style="background:#00b894;">🔓 Funciona em TODOS</span>';
                }} else {{
                    const canal = canais.find(c => c.id == canalId);
                    div.innerHTML = `<span class="config-badge">📢 /perfil em #${{canal ? canal.nome : canalId}}</span>`;
                }}
            }}

            function atualizarStatusRank(canalId) {{
                const div = document.getElementById('rank-status');
                if (!canalId) {{
                    div.innerHTML = '<span class="config-badge" style="background:#00b894;">🔓 Funciona em TODOS</span>';
                }} else {{
                    const canal = canais.find(c => c.id == canalId);
                    div.innerHTML = `<span class="config-badge">📢 /rank em #${{canal ? canal.nome : canalId}}</span>`;
                }}
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
                    cargoSelect.innerHTML = '<option value="">Selecione</option>';
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
                        select.innerHTML = '<option value="">Selecione</option>';
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
                if (event && event.target) event.target.classList.add('active');
                if (tabId === 'fila') carregarFila();
                if (tabId === 'moderacao') carregarAdvertencias();
                if (tabId === 'servicos') carregarServicos();
                if (tabId === 'clientes') carregarClientes();
                if (tabId === 'solicitacoes') carregarSolicitacoesAdmin('');
                if (tabId === 'fidelidade') carregarRecompensas();
            }}

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
                            toggleBtn.textContent = fila.aberta ? '🔓 Fechar' : '🔒 Abrir';
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
            async function concluir(id) {{
                if (!confirm('Concluir serviço?')) return;
                try {{
                    const resp = await fetch('/api/fila/concluir', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{entrada_id:id}})}});
                    const result = await resp.json();
                    if (result.sucesso) {{
                        carregarFila();
                        showAlert('fila-status', '✅ Serviço concluído!', true);
                    }} else {{
                        showAlert('fila-status', '❌ Erro ao concluir', false);
                    }}
                }} catch(e) {{
                    showAlert('fila-status', '❌ Erro: ' + e.message, false);
                }}
            }}
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

            async function carregarServicos() {{
                try {{
                    const resp = await fetch('/api/admin/servicos');
                    const data = await resp.json();
                    if (data.sucesso) {{
                        const container = document.getElementById('servicos-lista');
                        const servicos = Object.entries(data.servicos);
                        if (servicos.length === 0) {{
                            container.innerHTML = '<p style="color:#888;">Nenhum serviço cadastrado.</p>';
                        }} else {{
                            container.innerHTML = servicos.map(([id, s]) => `
                                <div style="background:#1a1a1a;padding:15px;border-radius:10px;margin-bottom:10px;border-left:4px solid ${{s.status === 'ativo' ? '#10b981' : '#ef4444'}};">
                                    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;">
                                        <div>
                                            <strong>${{escapeHtml(s.nome)}}</strong>
                                            <span style="color:#888;font-size:0.8rem;">${{escapeHtml(s.categoria)}}</span>
                                            <span class="badge badge-${{s.status === 'ativo' ? 'approved' : 'refused'}}">${{s.status}}</span>
                                        </div>
                                        <div>
                                            <span style="color:#4ade80;">R$ ${{s.valor_reais.toFixed(2)}}</span>
                                            <span style="color:#f59e0b;margin-left:10px;">+${{s.pontos_gerados}} pts</span>
                                        </div>
                                        <div>
                                            <button onclick="editarServico('${{id}}')" class="btn btn-primary btn-sm">✏️</button>
                                            <button onclick="excluirServico('${{id}}')" class="btn btn-danger btn-sm">🗑️</button>
                                        </div>
                                    </div>
                                    <div style="color:#ccc;font-size:0.9rem;margin-top:5px;">${{escapeHtml(s.descricao)}}</div>
                                </div>
                            `).join('');
                        }}
                    }}
                }} catch(e) {{ console.error(e); }}
            }}

            function editarServico(id) {{
                const servicos = dados.servicos || {{}};
                const s = servicos[id];
                if (!s) return;
                document.getElementById('servico-nome').value = s.nome;
                document.getElementById('servico-categoria').value = s.categoria;
                document.getElementById('servico-descricao').value = s.descricao;
                document.getElementById('servico-imagem').value = s.imagem_url || '';
                document.getElementById('servico-valor').value = s.valor_reais;
                document.getElementById('servico-pontos').value = s.pontos_gerados;
                document.getElementById('servico-status').value = s.status;
                window.servicoEditando = id;
                document.querySelector('button[onclick="criarServico()"]').textContent = '💾 Atualizar Serviço';
            }}

            async function criarServico() {{
                const data = {{
                    nome: document.getElementById('servico-nome').value.trim(),
                    categoria: document.getElementById('servico-categoria').value.trim(),
                    descricao: document.getElementById('servico-descricao').value.trim(),
                    imagem_url: document.getElementById('servico-imagem').value.trim(),
                    valor_reais: parseFloat(document.getElementById('servico-valor').value),
                    pontos_gerados: parseInt(document.getElementById('servico-pontos').value),
                    status: document.getElementById('servico-status').value
                }};

                if (!data.nome || !data.categoria) {{
                    showAlert('servico-alert', 'Nome e categoria são obrigatórios', false);
                    return;
                }}

                try {{
                    let url = '/api/admin/servicos';
                    let method = 'POST';
                    if (window.servicoEditando) {{
                        url = '/api/admin/servicos';
                        method = 'PUT';
                        data.servico_id = window.servicoEditando;
                    }}

                    const resp = await fetch(url, {{method: method, headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(data)}});
                    const result = await resp.json();
                    showAlert('servico-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) {{
                        document.getElementById('servico-nome').value = '';
                        document.getElementById('servico-categoria').value = '';
                        document.getElementById('servico-descricao').value = '';
                        document.getElementById('servico-imagem').value = '';
                        document.getElementById('servico-valor').value = '';
                        document.getElementById('servico-pontos').value = '';
                        window.servicoEditando = null;
                        document.querySelector('button[onclick="criarServico()"]').textContent = '➕ Criar Serviço';
                        carregarServicos();
                    }}
                }} catch(e) {{
                    showAlert('servico-alert', 'Erro: ' + e.message, false);
                }}
            }}

            async function excluirServico(id) {{
                if (!confirm('Excluir este serviço?')) return;
                try {{
                    const resp = await fetch(`/api/admin/servicos?servico_id=${{id}}`, {{method: 'DELETE'}});
                    const result = await resp.json();
                    showAlert('servico-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) carregarServicos();
                }} catch(e) {{
                    showAlert('servico-alert', 'Erro: ' + e.message, false);
                }}
            }}

            async function carregarClientes() {{
                try {{
                    const resp = await fetch('/api/admin/clientes');
                    const data = await resp.json();
                    if (data.sucesso) {{
                        const container = document.getElementById('clientes-lista');
                        if (data.clientes.length === 0) {{
                            container.innerHTML = '<p style="color:#888;">Nenhum cliente cadastrado.</p>';
                        }} else {{
                            container.innerHTML = `
                                <div class="table-container">
                                    <table>
                                        <thead>
                                            <tr><th>Discord</th><th>Nick</th><th>UID</th><th>Pontos</th><th>Acumulado</th><th>Última Compra</th></tr>
                                        </thead>
                                        <tbody>
                                            ${{data.clientes.map(c => `
                                                <tr>
                                                    <td>${{escapeHtml(c.discord_id)}}</td>
                                                    <td>${{escapeHtml(c.game_nick)}}</td>
                                                    <td>${{escapeHtml(c.uid)}}</td>
                                                    <td style="color:#f59e0b;">${{c.pontos_atuais}}</td>
                                                    <td style="color:#4ade80;">${{c.pontos_acumulados}}</td>
                                                    <td>${{c.ultima_compra ? new Date(c.ultima_compra).toLocaleDateString() : 'N/A'}}</td>
                                                </tr>
                                            `).join('')}}
                                        </tbody>
                                    </table>
                                </div>
                            `;
                        }}

                        const select = document.getElementById('editar-cliente-select');
                        select.innerHTML = data.clientes.map(c => `<option value="${{c.discord_id}}">${{escapeHtml(c.game_nick)}} (${{c.uid}})</option>`).join('');
                    }}
                }} catch(e) {{ console.error(e); }}
            }}

            function carregarClienteEdicao() {{
                const discordId = document.getElementById('editar-cliente-select').value;
                if (!discordId) return;
                fetch('/api/admin/clientes')
                    .then(r => r.json())
                    .then(data => {{
                        if (data.sucesso) {{
                            const cliente = data.clientes.find(c => c.discord_id == discordId);
                            if (cliente) {{
                                document.getElementById('editar-uid').value = cliente.uid || '';
                                document.getElementById('editar-nick').value = cliente.game_nick || '';
                                document.getElementById('editar-pontos').value = cliente.pontos_atuais || 0;
                                document.getElementById('editar-acumulados').value = cliente.pontos_acumulados || 0;
                            }}
                        }}
                    }});
            }}

            async function salvarEdicaoCliente() {{
                const discordId = document.getElementById('editar-cliente-select').value;
                if (!discordId) {{
                    showAlert('editar-cliente-alert', 'Selecione um cliente', false);
                    return;
                }}

                const data = {{
                    discord_id: discordId,
                    uid: document.getElementById('editar-uid').value.trim(),
                    game_nick: document.getElementById('editar-nick').value.trim(),
                    pontos_atuais: parseInt(document.getElementById('editar-pontos').value) || 0,
                    pontos_acumulados: parseInt(document.getElementById('editar-acumulados').value) || 0
                }};

                try {{
                    const resp = await fetch('/api/admin/cliente/editar', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(data)}});
                    const result = await resp.json();
                    showAlert('editar-cliente-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) carregarClientes();
                }} catch(e) {{
                    showAlert('editar-cliente-alert', 'Erro: ' + e.message, false);
                }}
            }}

            async function carregarSolicitacoesAdmin(status) {{
                try {{
                    const url = status ? `/api/admin/solicitacoes?status=${{encodeURIComponent(status)}}` : '/api/admin/solicitacoes';
                    const resp = await fetch(url);
                    const data = await resp.json();
                    if (data.sucesso) {{
                        const container = document.getElementById('solicitacoes-admin-lista');
                        if (data.solicitacoes.length === 0) {{
                            container.innerHTML = '<p style="color:#888;">Nenhuma solicitação encontrada.</p>';
                        }} else {{
                            container.innerHTML = `
                                <div class="table-container">
                                    <table>
                                        <thead>
                                            <tr><th>Cliente</th><th>Serviço</th><th>Jogo</th><th>Status</th><th>Data</th><th>Ações</th></tr>
                                        </thead>
                                        <tbody>
                                            ${{data.solicitacoes.map(s => `
                                                <tr>
                                                    <td>${{escapeHtml(s.cliente_nome)}}</td>
                                                    <td>${{escapeHtml(s.servico_nome)}}</td>
                                                    <td>${{escapeHtml(s.jogo || 'N/A')}}</td>
                                                    <td><span class="badge badge-${{s.status === 'Aguardando Aprovação' ? 'pending' : s.status === 'Em Andamento' ? 'approved' : s.status === 'Concluído' ? 'completed' : 'refused'}}">${{s.status}}</span></td>
                                                    <td>${{new Date(s.data_solicitacao).toLocaleDateString()}}</td>
                                                    <td>
                                                        ${{s.status === 'Aguardando Aprovação' ? `
                                                            <button onclick="abrirModalSolicitacao('${{s.id}}', 'aprovar')" class="btn btn-success btn-sm">✅</button>
                                                            <button onclick="abrirModalSolicitacao('${{s.id}}', 'recusar')" class="btn btn-danger btn-sm">❌</button>
                                                        ` : s.status === 'Em Andamento' ? `
                                                            <button onclick="concluirSolicitacaoAdmin('${{s.id}}')" class="btn btn-success btn-sm">✔️ Concluir</button>
                                                        ` : ''}
                                                    </td>
                                                </tr>
                                            `).join('')}}
                                        </tbody>
                                    </table>
                                </div>
                            `;
                        }}
                    }}
                }} catch(e) {{ console.error(e); }}
            }}

            function abrirModalSolicitacao(id, tipo) {{
                solicitacaoAtual = id;
                document.getElementById('modal-solicitacao').style.display = 'flex';
                document.getElementById('modal-solicitacao-titulo').textContent = tipo === 'aprovar' ? '✅ Aprovar Solicitação' : '❌ Recusar Solicitação';
                document.getElementById('modal-motivo-group').style.display = tipo === 'recusar' ? 'block' : 'none';
                document.getElementById('modal-btn-aprovar').style.display = tipo === 'aprovar' ? 'block' : 'none';
                document.getElementById('modal-btn-recusar').style.display = tipo === 'recusar' ? 'block' : 'none';

                fetch('/api/admin/solicitacoes')
                    .then(r => r.json())
                    .then(data => {{
                        if (data.sucesso) {{
                            const s = data.solicitacoes.find(x => x.id == id);
                            if (s) {{
                                document.getElementById('modal-solicitacao-info').innerHTML = `
                                    <p><strong>Cliente:</strong> ${{escapeHtml(s.cliente_nome)}}</p>
                                    <p><strong>Serviço:</strong> ${{escapeHtml(s.servico_nome)}}</p>
                                    <p><strong>Jogo:</strong> ${{escapeHtml(s.jogo || 'N/A')}}</p>
                                    <p><strong>Observações:</strong> ${{escapeHtml(s.observacoes || 'N/A')}}</p>
                                    ${s.cupom_aplicado ? `<p><strong>Cupom:</strong> ${escapeHtml(s.cupom_aplicado)}</p>` : ''}
                                `;
                            }}
                        }}
                    }});
            }}

            function fecharModal(id) {{
                document.getElementById(id).style.display = 'none';
                solicitacaoAtual = null;
            }}

            async function aprovarSolicitacaoModal() {{
                if (!solicitacaoAtual) return;
                try {{
                    const resp = await fetch('/api/admin/solicitacao/aprovar', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{solicitacao_id: solicitacaoAtual}})
                    }});
                    const result = await resp.json();
                    showAlert('solicitacoes-admin-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) {{
                        fecharModal('modal-solicitacao');
                        carregarSolicitacoesAdmin('');
                        carregarFila();
                    }}
                }} catch(e) {{
                    showAlert('solicitacoes-admin-alert', 'Erro: ' + e.message, false);
                }}
            }}

            async function recusarSolicitacaoModal() {{
                if (!solicitacaoAtual) return;
                const motivo = document.getElementById('modal-motivo').value.trim();
                if (!motivo) {{
                    alert('Informe o motivo da recusa');
                    return;
                }}
                try {{
                    const resp = await fetch('/api/admin/solicitacao/recusar', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{solicitacao_id: solicitacaoAtual, motivo}})
                    }});
                    const result = await resp.json();
                    showAlert('solicitacoes-admin-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) {{
                        fecharModal('modal-solicitacao');
                        carregarSolicitacoesAdmin('');
                    }}
                }} catch(e) {{
                    showAlert('solicitacoes-admin-alert', 'Erro: ' + e.message, false);
                }}
            }}

            async function concluirSolicitacaoAdmin(solicitacaoId) {{
                if (!confirm('Concluir este serviço?')) return;
                try {{
                    const resp = await fetch('/api/fila/concluir', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{entrada_id: null, solicitacao_id: solicitacaoId}})
                    }});
                    const result = await resp.json();
                    showAlert('solicitacoes-admin-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) {{
                        carregarSolicitacoesAdmin('');
                        carregarFila();
                    }}
                }} catch(e) {{
                    showAlert('solicitacoes-admin-alert', 'Erro: ' + e.message, false);
                }}
            }}

            async function salvarConfigFidelidade() {{
                const data = {{
                    multiplicador_pontos: parseInt(document.getElementById('fid-multiplicador').value) || 1,
                    validade_pontos_dias: parseInt(document.getElementById('fid-validade-pontos').value) || 90,
                    validade_cupom_dias: parseInt(document.getElementById('fid-validade-cupom').value) || 30
                }};
                try {{
                    const resp = await fetch('/api/admin/fidelidade/config', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(data)}});
                    const result = await resp.json();
                    showAlert('fid-alert', result.mensagem, result.sucesso);
                }} catch(e) {{
                    showAlert('fid-alert', 'Erro: ' + e.message, false);
                }}
            }}

            async function carregarRecompensas() {{
                try {{
                    const resp = await fetch('/api/admin/fidelidade/recompensas');
                    const data = await resp.json();
                    if (data.sucesso) {{
                        const container = document.getElementById('recompensas-lista');
                        if (data.recompensas.length === 0) {{
                            container.innerHTML = '<p style="color:#888;">Nenhuma recompensa cadastrada.</p>';
                        }} else {{
                            container.innerHTML = data.recompensas.map((r, index) => `
                                <div style="background:#1a1a1a;padding:10px;border-radius:8px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;border-left:4px solid #f59e0b;">
                                    <div>
                                        <strong style="color:#f59e0b;">${{r.pontos}} pts</strong>
                                        <span style="color:#ccc;">${{escapeHtml(r.descricao)}}</span>
                                        <span style="color:#888;font-size:0.8rem;">(${{escapeHtml(r.tipo)}})</span>
                                    </div>
                                    <button onclick="removerRecompensa(${{index}})" class="btn btn-danger btn-sm">🗑️</button>
                                </div>
                            `).join('');
                        }}
                    }}
                }} catch(e) {{ console.error(e); }}
            }}

            async function criarRecompensa() {{
                const data = {{
                    pontos: parseInt(document.getElementById('recompensa-pontos').value) || 0,
                    descricao: document.getElementById('recompensa-descricao').value.trim(),
                    tipo: document.getElementById('recompensa-tipo').value.trim()
                }};
                if (!data.descricao || !data.tipo) {{
                    showAlert('recompensa-alert', 'Descrição e tipo são obrigatórios', false);
                    return;
                }}
                try {{
                    const resp = await fetch('/api/admin/fidelidade/recompensas', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(data)}});
                    const result = await resp.json();
                    showAlert('recompensa-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) {{
                        document.getElementById('recompensa-pontos').value = '';
                        document.getElementById('recompensa-descricao').value = '';
                        document.getElementById('recompensa-tipo').value = '';
                        carregarRecompensas();
                    }}
                }} catch(e) {{
                    showAlert('recompensa-alert', 'Erro: ' + e.message, false);
                }}
            }}

            async function removerRecompensa(index) {{
                if (!confirm('Remover esta recompensa?')) return;
                try {{
                    const resp = await fetch(`/api/admin/fidelidade/recompensas?index=${{index}}`, {{method: 'DELETE'}});
                    const result = await resp.json();
                    showAlert('recompensa-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) carregarRecompensas();
                }} catch(e) {{
                    showAlert('recompensa-alert', 'Erro: ' + e.message, false);
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

            window.onclick = function(event) {{
                if (event.target.classList.contains('modal')) {{
                    event.target.style.display = 'none';
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
    print(f"👤 Clientes cadastrados: {len(dados.get('clientes', {}))}")
    print(f"🎮 Serviços disponíveis: {len(obter_servicos_ativos())}")
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
