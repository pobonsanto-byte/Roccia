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
    # NOVAS ESTRUTURAS
    "clientes": {},
    "servicos": {},
    "solicitacoes": {},
    "fidelidade": {
        "pontos_por_real": 1,
        "validade_pontos_dias": 90,
        "validade_cupom_dias": 30,
        "recompensas": [
            {
                "id": "rec_60_1",
                "pontos": 60,
                "titulo": "1 Dia de Quests Diárias Grátis",
                "opcoes": ["1 Dia de Quests Diárias Grátis"]
            },
            {
                "id": "rec_100_1",
                "pontos": 100,
                "titulo": "Opção Especial - 100 Pontos",
                "opcoes": ["Desafio Rápido", "Portinha", "Hologramas de Huanglong", "Cupom de R$5"]
            },
            {
                "id": "rec_200_1",
                "pontos": 200,
                "titulo": "Opção Avançada - 200 Pontos",
                "opcoes": ["Análise de Conta", "Companion Quest", "Cupom de R$10"]
            },
            {
                "id": "rec_400_1",
                "pontos": 400,
                "titulo": "Opção Premium - 400 Pontos",
                "opcoes": ["Build Completa", "Cupom de R$20"]
            }
        ],
        "cupons": {}
    }
}

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
                
                # Garantir estruturas padrão
                dados.setdefault("fila", {"nome": "Fila de Serviços", "configuracoes": {"tamanho_maximo": 50, "aberta": True}, "entradas": [], "historico": []})
                dados.setdefault("botoes_cargos", {})
                dados.setdefault("cargos_nivel", {})
                dados.setdefault("canais_links_bloqueados", [])
                dados.setdefault("links_fila", {"discord_convite": "", "botoes_precos": []})
                dados.setdefault("clientes", {})
                dados.setdefault("servicos", {})
                dados.setdefault("solicitacoes", {})
                dados.setdefault("fidelidade", {
                    "pontos_por_real": 1,
                    "validade_pontos_dias": 90,
                    "validade_cupom_dias": 30,
                    "recompensas": [
                        {"id": "rec_60_1", "pontos": 60, "titulo": "1 Dia de Quests Diárias Grátis", "opcoes": ["1 Dia de Quests Diárias Grátis"]},
                        {"id": "rec_100_1", "pontos": 100, "titulo": "Opção Especial - 100 Pontos", "opcoes": ["Desafio Rápido", "Portinha", "Hologramas de Huanglong", "Cupom de R$5"]},
                        {"id": "rec_200_1", "pontos": 200, "titulo": "Opção Avançada - 200 Pontos", "opcoes": ["Análise de Conta", "Companion Quest", "Cupom de R$10"]},
                        {"id": "rec_400_1", "pontos": 400, "titulo": "Opção Premium - 400 Pontos", "opcoes": ["Build Completa", "Cupom de R$20"]}
                    ],
                    "cupons": {}
                })
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
    return (str(texto)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )

def gerar_codigo_cupom():
    sufixo = secrets.token_hex(4).upper()
    return f"ZANKON-{sufixo}"

# ========================
# DECORADORES DE ACESSO
# ========================
def cliente_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario' not in session or not session['usuario'].get('eh_admin'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ========================
# FUNÇÕES DO SISTEMA DE SERVIÇOS & CLIENTES
# ========================
def obter_cliente(discord_id):
    clientes = dados.setdefault("clientes", {})
    if str(discord_id) not in clientes:
        return None
    c = clientes[str(discord_id)]
    c.setdefault("pontos_atuais", 0)
    c.setdefault("pontos_acumulados", 0)
    c.setdefault("pontos_utilizados", 0)
    c.setdefault("historico", [])
    c.setdefault("cupons", {})
    c.setdefault("ultima_compra", None)
    c.setdefault("ultimo_resgate", None)
    return c

def cadastrar_cliente(discord_id, username, avatar, uid, nick):
    clientes = dados.setdefault("clientes", {})
    str_id = str(discord_id)
    
    # Verificar se UID já está em uso por outro Discord
    for cid, cdata in clientes.items():
        if cid != str_id and cdata.get("uid") == uid:
            return False, "Este UID já está cadastrado em outra conta do Discord."
    
    if str_id in clientes:
        clientes[str_id]["uid"] = uid
        clientes[str_id]["nick"] = nick
        clientes[str_id]["username"] = username
        clientes[str_id]["avatar"] = avatar
    else:
        clientes[str_id] = {
            "discord_id": str_id,
            "username": username,
            "avatar": avatar,
            "uid": uid,
            "nick": nick,
            "pontos_atuais": 0,
            "pontos_acumulados": 0,
            "pontos_utilizados": 0,
            "historico": [],
            "cupons": {},
            "criado_em": agora_br().isoformat(),
            "ultima_compra": None,
            "ultimo_resgate": None
        }
    salvar_dados_github(f"Cliente cadastrado/atualizado: {username} (UID: {uid})")
    return True, "Cadastro realizado com sucesso!"

def obter_servicos_ativos():
    servicos = dados.setdefault("servicos", {})
    return {k: v for k, v in servicos.items() if v.get("ativo", True)}

# ========================
# FUNÇÕES ANTI-SPAM E IGNORADOS
# ========================
def verificar_comando_ignorado(conteudo: str) -> bool:
    conteudo_lower = conteudo.lower().strip()
    comandos_ignorados = dados.get("anti_spam", {}).get("comandos_ignorados", [])
    for comando in comandos_ignorados:
        if conteudo_lower.startswith(comando.lower()) or conteudo_lower == comando.lower():
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

# ========================
# SISTEMA DE FILA E SOLICITAÇÕES
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

def adicionar_fila(nome_usuario: str, servico: str, jogo: str = "", usuario_id: str = None, solicitacao_id: str = None):
    fila = obter_dados_fila()
    
    if not fila["configuracoes"]["aberta"]:
        return False, "Fila está fechada no momento"
    
    if len(fila["entradas"]) >= fila["configuracoes"]["tamanho_maximo"]:
        return False, "Fila está cheia"
    
    entrada = {
        "id": str(int(datetime.now().timestamp() * 1000)),
        "nome_usuario": nome_usuario,
        "servico": servico,
        "jogo": jogo,
        "usuario_id": usuario_id or nome_usuario,
        "solicitacao_id": solicitacao_id,
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

def concluir_servico(entrada_id: str, admin_nome: str = "Admin"):
    fila = obter_dados_fila()
    solicitacoes = dados.setdefault("solicitacoes", {})
    clientes = dados.setdefault("clientes", {})
    fidelidade_config = dados.setdefault("fidelidade", {})
    mult = fidelidade_config.get("pontos_por_real", 1)

    for i, entrada in enumerate(fila["entradas"]):
        if entrada["id"] == entrada_id:
            removido = fila["entradas"].pop(i)
            removido["status"] = "concluido"
            removido["concluido_em"] = agora_br().isoformat()
            fila["historico"].append(removido)
            atualizar_posicoes(fila["entradas"])

            # Se vinculada a uma solicitação do cliente
            sol_id = removido.get("solicitacao_id")
            if sol_id and sol_id in solicitacoes:
                sol = solicitacoes[sol_id]
                sol["status"] = "Concluído"
                sol["concluido_em"] = agora_br().isoformat()
                sol["admin_responsavel"] = admin_nome

                cid = sol.get("discord_id")
                if cid and cid in clientes:
                    cli = clientes[cid]
                    valor_pago = float(sol.get("valor", 0))
                    # Calcular pontos ganhos com multiplicador
                    pontos_ganhos = sol.get("pontos_gerados", int(valor_pago * mult))
                    
                    cli["pontos_atuais"] = cli.get("pontos_atuais", 0) + pontos_ganhos
                    cli["pontos_acumulados"] = cli.get("pontos_acumulados", 0) + pontos_ganhos
                    cli["ultima_compra"] = agora_br().isoformat()

                    hist_entry = {
                        "servico": sol.get("servico_nome"),
                        "valor": valor_pago,
                        "pontos_ganhos": pontos_ganhos,
                        "cupom_utilizado": sol.get("cupom"),
                        "admin_responsavel": admin_nome,
                        "data_conclusao": agora_br().isoformat()
                    }
                    cli.setdefault("historico", []).append(hist_entry)

            salvar_fila()
            adicionar_log(f"fila_concluir: {removido['nome_usuario']} por {admin_nome}")
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

# ========================
# EXECUÇÃO DE AÇÕES DO BOT
# ========================
def executar_acao_bot(tipo_acao, **kwargs):
    acoes_fila_bot.append({
        "tipo": tipo_acao,
        "dados": kwargs,
        "timestamp": agora_br().isoformat()
    })
    return True

async def executar_acao_bot_interno(acao):
    tipo_acao = acao["tipo"]
    dados_acao = acao["dados"]
    if not bot.is_ready():
        return False
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID else None
    if not guild:
        return False
    try:
        if tipo_acao == "criar_embed":
            canal = guild.get_channel(int(dados_acao["canal_id"]))
            if not canal: return False
            cor = discord.Color.blue()
            if dados_acao.get('cor'):
                try: cor = discord.Color(int(dados_acao['cor'].replace('#', ''), 16))
                except: pass
            embed = discord.Embed(title=dados_acao["titulo"], description=dados_acao["corpo"], color=cor)
            if dados_acao.get('url_imagem'): embed.set_image(url=dados_acao['url_imagem'])
            mencao = "@everyone" if dados_acao.get('mencao') == 'everyone' else ("@here" if dados_acao.get('mencao') == 'here' else "")
            await canal.send(content=mencao, embed=embed)
            return True
    except Exception as e:
        print(f"Erro acao bot: {e}")
    return False

async def processar_acoes_bot_continuo():
    global processador_acoes_rodando
    processador_acoes_rodando = True
    if not bot.is_ready():
        await bot.wait_until_ready()
    while processador_acoes_rodando and not bot.is_closed():
        try:
            if acoes_fila_bot:
                acao = acoes_fila_bot.pop(0)
                await executar_acao_bot_interno(acao)
            await asyncio.sleep(1)
        except Exception:
            await asyncio.sleep(5)

# ========================
# ROTAS DO SITE: LOGIN & NAVEGAÇÃO
# ========================
@app.route("/", methods=["GET"])
def home():
    status_bot = "✅ Bot Online" if bot.is_ready() else "❌ Bot Offline"
    classe_bot = "online" if bot.is_ready() else "offline"
    user_sess = session.get('usuario')
    
    return f'''
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>ZankonYTB - Painel & Cliente</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; background: #0a0a0a; color: #e0e0e0; margin: 0; padding: 0; display: flex; align-items: center; justify-content: center; min-height: 100vh; }}
            .container {{ background: #121212; border-radius: 20px; padding: 40px; text-align: center; max-width: 500px; width: 90%; border: 1px solid #333; box-shadow: 0 10px 30px rgba(0,0,0,0.8); }}
            h1 {{ color: #5865F2; margin-bottom: 10px; }}
            .status {{ padding: 10px; border-radius: 10px; margin: 20px 0; font-weight: bold; }}
            .online {{ background: #1a472a; color: #4ade80; border: 1px solid #2ecc71; }}
            .offline {{ background: #7f1d1d; color: #f87171; border: 1px solid #ef4444; }}
            .btn {{ display: inline-block; background: #5865F2; color: white; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: bold; margin: 8px; transition: 0.3s; }}
            .btn:hover {{ background: #4752C4; transform: translateY(-2px); }}
            .btn-sec {{ background: #2f3136; color: #fff; }}
            .btn-sec:hover {{ background: #393c43; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎮 Portal ZankonYTB</h1>
            <div class="status {classe_bot}">{status_bot}</div>
            <p style="margin-bottom: 25px; color: #aaa;">Serviços, Fila de Espera e Loja de Fidelidade</p>
            {f"""
            <p>Olá, <strong>{user_sess['nome_usuario']}</strong>!</p>
            {"<a href='/dashboard' class='btn'>🚀 Painel Admin</a>" if user_sess.get('eh_admin') else ""}
            <a href="/cliente" class="btn">👤 Área do Cliente</a>
            <a href="/fila" class="btn btn-sec">📋 Ver Fila</a>
            <a href="/regras" class="btn btn-sec">📜 Regras de Fidelidade</a>
            <br><br><a href="/logout" style="color: #ef4444; text-decoration: none; font-size: 0.9rem;">🚪 Sair</a>
            """ if user_sess else """
            <a href="/login" class="btn">🔐 Login com Discord</a>
            <a href="/fila" class="btn btn-sec">📋 Ver Fila Pública</a>
            <a href="/regras" class="btn btn-sec">📜 Regras Fidelidade</a>
            """}
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
            return "Erro ao obter informações do usuário", 400
        
        user_data = user_r.json()
        
        guilds_r = requests.get('https://discord.com/api/users/@me/guilds', headers={'Authorization': f'Bearer {access_token}'})
        guilds = guilds_r.json() if guilds_r.status_code == 200 else []
        
        no_servidor = False
        is_admin = False
        
        for guild in guilds:
            if str(guild['id']) == GUILD_ID:
                no_servidor = True
                if (guild['permissions'] & 0x8): # Permissão de Administrador
                    is_admin = True
                break
        
        if not no_servidor and not is_admin:
            return "<h2>⚠️ Acesso Negado</h2><p>Você precisa fazer parte do servidor do Discord para acessar!</p><a href='/'>Voltar</a>", 403
        
        session['usuario'] = {
            'id': user_data['id'],
            'nome_usuario': user_data['username'],
            'avatar': user_data.get('avatar'),
            'eh_admin': is_admin
        }
        
        if is_admin:
            return redirect(url_for('dashboard'))
        return redirect(url_for('area_cliente'))
        
    except Exception as e:
        return f"Erro interno: {str(e)}", 500

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route("/regras")
def regras_fidelidade():
    return '''
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Regras - Sistema de Fidelidade</title>
        <style>
            body { font-family: 'Segoe UI', sans-serif; background: #0f0c29; color: #fff; padding: 20px; line-height: 1.6; }
            .container { max-width: 800px; margin: 0 auto; background: rgba(0,0,0,0.6); padding: 30px; border-radius: 15px; border: 1px solid #333; }
            h1 { color: #ffd93d; border-bottom: 2px solid #ffd93d; padding-bottom: 10px; }
            h2 { color: #5865F2; margin-top: 20px; }
            p { color: #ddd; margin-bottom: 15px; }
            .btn { display: inline-block; background: #5865F2; color: #fff; padding: 10px 20px; border-radius: 5px; text-decoration: none; font-weight: bold; margin-top: 20px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📜 Regras de Uso - Sistema de Fidelidade ZankonYTB</h1>

            <h2>1. Pontos Pessoais e Vinculados ao UID</h2>
            <p>Seus pontos são pessoais, intransferíveis e atrelados diretamente ao seu cadastro e ao seu UID do jogo.</p>
            <p>Não é permitido transferir pontos para amigos ou juntar o saldo de compras de contas diferentes para resgatar prêmios.</p>

            <h2>2. Cupons de Uso Único</h2>
            <p>Ao trocar seus pontos, o sistema gera um código exclusivo para você.</p>
            <p>Esse token é de uso único.</p>
            <p>Uma vez inserido e validado no seu pedido, ele é consumido automaticamente e não poderá ser reutilizado.</p>

            <h2>3. Um Benefício por Pedido</h2>
            <p>Os descontos e resgates não são cumulativos.</p>
            <p>É permitido utilizar apenas um benefício por pedido.</p>
            <p>Não é possível utilizar vários cupons juntos.</p>

            <h2>4. Validade</h2>
            <p>Saldo de pontos expira após 90 dias sem novos serviços concluídos.</p>
            <p>Cupons possuem validade de 30 dias após o resgate.</p>

            <a href="/" class="btn">🏠 Voltar ao Início</a>
        </div>
    </body>
    </html>
    '''

# ========================
# ÁREA DO CLIENTE & CADASTRO
# ========================
@app.route("/cliente")
@cliente_required
def area_cliente():
    u = session['usuario']
    cli = obter_cliente(u['id'])
    
    # Se ainda não cadastrou UID / Nick
    if not cli or not cli.get("uid"):
        return f'''
        <!DOCTYPE html>
        <html lang="pt-BR">
        <head>
            <meta charset="UTF-8"><title>Cadastro do Cliente</title>
            <style>
                body {{ font-family:'Segoe UI', sans-serif; background:#0a0a0a; color:#fff; display:flex; align-items:center; justify-content:center; min-height:100vh; }}
                .box {{ background:#121212; border:1px solid #333; padding:30px; border-radius:15px; max-width:400px; width:90%; }}
                input {{ width:100%; padding:10px; margin:10px 0 20px; background:#1a1a1a; border:1px solid #444; color:#fff; border-radius:5px; box-sizing:border-box; }}
                button {{ width:100%; padding:12px; background:#5865F2; color:#fff; border:none; border-radius:5px; font-weight:bold; cursor:pointer; }}
            </style>
        </head>
        <body>
            <div class="box">
                <h2>📝 Primeiro Acesso</h2>
                <p>Por favor, cadastre seu UID e Nick do jogo para continuar.</p>
                <form action="/cliente/cadastrar" method="POST">
                    <label>UID do Jogo:</label>
                    <input type="text" name="uid" required placeholder="Ex: 900123456">
                    <label>Nick do Jogo:</label>
                    <input type="text" name="nick" required placeholder="Ex: Player123">
                    <button type="submit">Salvar Cadastro</button>
                </form>
            </div>
        </body>
        </html>
        '''

    # Dados formatados
    servicos_ativos = obter_servicos_ativos()
    solicitacoes = [v for k, v in dados.get("solicitacoes", {}).items() if str(v.get("discord_id")) == str(u['id'])]
    solicitacoes_pendentes = [s for s in solicitacoes if s.get("status") == "Aguardando Aprovação"]
    
    # Próximo prêmio
    fidelidade = dados.get("fidelidade", {})
    recompensas = fidelidade.get("recompensas", [])
    pts = cli.get("pontos_atuais", 0)
    
    proximo_premio = None
    for r in sorted(recompensas, key=lambda x: x["pontos"]):
        if r["pontos"] > pts:
            proximo_premio = r
            break
            
    pct_progresso = 100
    if proximo_premio:
        pct_progresso = min(100, int((pts / proximo_premio["pontos"]) * 100))

    # Renderizar Cupons e Histórico
    cupons = cli.get("cupons", {})
    agora_iso = agora_br().isoformat()

    return f'''
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Área do Cliente</title>
        <style>
            :root {{ --primary:#5865F2; --bg:#0f0c29; --card:#121212; --gray:#2a2a2a; }}
            * {{ margin:0; padding:0; box-sizing:border-box; }}
            body {{ font-family:'Segoe UI', sans-serif; background:var(--bg); color:#fff; padding:20px; }}
            .container {{ max-width:1100px; margin:0 auto; }}
            .profile-card {{ background:var(--card); padding:20px; border-radius:15px; border:1px solid #333; display:flex; align-items:center; gap:20px; flex-wrap:wrap; margin-bottom:20px; }}
            .avatar {{ width:80px; height:80px; border-radius:50%; border:3px solid var(--primary); }}
            .stats-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:15px; margin-bottom:20px; }}
            .stat-box {{ background:var(--card); padding:15px; border-radius:10px; border:1px solid #333; text-align:center; }}
            .stat-box h3 {{ color:var(--primary); font-size:1.8rem; }}
            .card {{ background:var(--card); padding:20px; border-radius:15px; border:1px solid #333; margin-bottom:20px; }}
            .progress-bar {{ background:#222; height:20px; border-radius:10px; overflow:hidden; margin:10px 0; }}
            .progress-fill {{ background:linear-gradient(90deg, #ff6b6b, #ffd93d); height:100%; transition:width 0.3s; }}
            .btn {{ padding:10px 20px; background:var(--primary); color:#fff; border:none; border-radius:5px; text-decoration:none; font-weight:bold; cursor:pointer; display:inline-block; }}
            .btn-sm {{ padding:5px 10px; font-size:0.8rem; }}
            .btn-success {{ background:#10b981; }}
            table {{ width:100%; border-collapse:collapse; margin-top:10px; }}
            th, td {{ padding:10px; text-align:left; border-bottom:1px solid #222; }}
            th {{ background:#1a1a1a; }}
            .badge {{ padding:3px 8px; border-radius:5px; font-size:0.8rem; }}
            .badge-p {{ background:#f59e0b; color:#000; }}
            .badge-a {{ background:#10b981; color:#fff; }}
            .badge-r {{ background:#ef4444; color:#fff; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;">
                <h1>👤 Área do Cliente</h1>
                <div>
                    <a href="/regras" class="btn" style="background:#4b5563;">📜 Regras</a>
                    <a href="/" class="btn" style="background:#2a2a2a;">🏠 Início</a>
                    <a href="/logout" class="btn" style="background:#ef4444;">🚪 Sair</a>
                </div>
            </div>

            <!-- Perfil -->
            <div class="profile-card">
                <img src="https://cdn.discordapp.com/avatars/{u['id']}/{u.get('avatar','')}.png" class="avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                <div>
                    <h2>{escape_html(u['nome_usuario'])}</h2>
                    <p><strong>UID:</strong> {escape_html(cli.get('uid'))} | <strong>Nick:</strong> {escape_html(cli.get('nick'))}</p>
                </div>
            </div>

            <!-- Stats de Pontos -->
            <div class="stats-grid">
                <div class="stat-box"><h3>{cli.get('pontos_atuais',0)}</h3><p>Pontos Atuais</p></div>
                <div class="stat-box"><h3>{cli.get('pontos_acumulados',0)}</h3><p>Total Acumulado</p></div>
                <div class="stat-box"><h3>{cli.get('pontos_utilizados',0)}</h3><p>Total Utilizado</p></div>
            </div>

            <!-- Barra de Progresso Fidelidade -->
            <div class="card">
                <h3>⭐ Progresso de Recompensa</h3>
                {f'<p>Próximo prêmio: <strong>{escape_html(proximo_premio["titulo"])}</strong> ({proximo_premio["pontos"]} Pts)</p>' if proximo_premio else '<p>🎉 Você atingiu o nível máximo de recompensas!</p>'}
                <div class="progress-bar"><div class="progress-fill" style="width: {pct_progresso}%;"></div></div>
                <small>{cli.get('pontos_atuais',0)} / {proximo_premio['pontos'] if proximo_premio else cli.get('pontos_atuais',0)} Pontos</small>
            </div>

            <!-- Solicitacao de Servicos -->
            <div class="card">
                <h3>🚀 Solicitar Serviço</h3>
                <form action="/cliente/solicitar" method="POST" style="margin-top:15px;">
                    <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap:10px;">
                        <div>
                            <label>Serviço:</label>
                            <select name="servico_id" class="btn" style="width:100%; background:#1a1a1a; text-align:left;" required>
                                {''.join(f'<option value="{k}">{escape_html(v["nome"])} - R${v["valor"]} ({v.get("pontos",v["valor"])} pts)</option>' for k,v in servicos_ativos.items()) or '<option value="">Nenhum serviço disponível</option>'}
                            </select>
                        </div>
                        <div>
                            <label>Jogo:</label>
                            <input type="text" name="jogo" placeholder="Ex: Wuthering Waves" required style="width:100%; padding:10px; background:#1a1a1a; border:1px solid #333; color:#fff; border-radius:5px;">
                        </div>
                        <div>
                            <label>Cupom de Desconto (Opcional):</label>
                            <input type="text" name="cupom" placeholder="Código do cupom" style="width:100%; padding:10px; background:#1a1a1a; border:1px solid #333; color:#fff; border-radius:5px;">
                        </div>
                    </div>
                    <div style="margin-top:10px;">
                        <label>Observações:</label>
                        <textarea name="observacoes" rows="2" style="width:100%; padding:10px; background:#1a1a1a; border:1px solid #333; color:#fff; border-radius:5px;" placeholder="Detalhes da conta, horário, etc."></textarea>
                    </div>
                    <button type="submit" class="btn btn-success" style="margin-top:10px;">📩 Enviar Solicitação</button>
                </form>
            </div>

            <!-- Loja de Fidelidade -->
            <div class="card">
                <h3>🛍️ Loja de Fidelidade</h3>
                <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap:15px; margin-top:15px;">
                    {''.join(f'''
                    <div style="background:#1a1a1a; padding:15px; border-radius:10px; border:1px solid #333;">
                        <h4>{escape_html(r["titulo"])}</h4>
                        <p style="color:#ffd93d; font-weight:bold; margin:5px 0;">{r["pontos"]} Pontos</p>
                        <form action="/cliente/resgatar" method="POST">
                            <input type="hidden" name="rec_id" value="{r["id"]}">
                            <select name="opcao" style="width:100%; padding:5px; background:#2a2a2a; color:#fff; margin-bottom:10px; border-radius:5px;">
                                {''.join(f'<option value="{escape_html(op)}">{escape_html(op)}</option>' for op in r["opcoes"])}
                            </select>
                            <button type="submit" class="btn btn-sm" {'disabled' if cli.get('pontos_atuais',0) < r['pontos'] else ''}>Resgatar</button>
                        </form>
                    </div>
                    ''' for r in recompensas)}
                </div>
            </div>

            <!-- Cupons Disponíveis -->
            <div class="card">
                <h3>🎟️ Seus Cupons</h3>
                <table>
                    <thead><tr><th>Código</th><th>Opção Resgatada</th><th>Validade</th><th>Status</th></tr></thead>
                    <tbody>
                        {''.join(f'''
                        <tr>
                            <td><strong>{escape_html(c_code)}</strong></td>
                            <td>{escape_html(c_data["tipo"])}</td>
                            <td>{c_data["validade"][:10]}</td>
                            <td><span class="badge {'badge-a' if c_data['status']=='Disponível' and c_data['validade'] > agora_iso else 'badge-r'}">{c_data['status'] if c_data['validade'] > agora_iso else 'Expirado'}</span></td>
                        </tr>
                        ''' for c_code, c_data in cupons.items()) or '<tr><td colspan="4">Nenhum cupom gerado.</td></tr>'}
                    </tbody>
                </table>
            </div>

            <!-- Historico / Solicitacoes -->
            <div class="card">
                <h3>📜 Suas Solicitações & Histórico</h3>
                <table>
                    <thead><tr><th>Data</th><th>Serviço</th><th>Status</th><th>Valor</th><th>Pontos</th></tr></thead>
                    <tbody>
                        {''.join(f'''
                        <tr>
                            <td>{s.get("data", "")[:10]}</td>
                            <td>{escape_html(s.get("servico_nome"))}</td>
                            <td><span class="badge {'badge-p' if s.get('status')=='Aguardando Aprovação' else ('badge-a' if s.get('status') in ['Aceito','Concluído'] else 'badge-r')}">{s.get('status')}</span></td>
                            <td>R${s.get("valor")}</td>
                            <td>+{s.get("pontos_gerados",0)}</td>
                        </tr>
                        ''' for s in solicitacoes) or '<tr><td colspan="5">Nenhuma solicitação encontrada.</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>
    </body>
    </html>
    '''

@app.route("/cliente/cadastrar", methods=["POST"])
@cliente_required
def cliente_cadastrar():
    u = session['usuario']
    uid = request.form.get("uid", "").strip()
    nick = request.form.get("nick", "").strip()
    
    if not uid or not nick:
        return "Preencha todos os campos!", 400
        
    sucesso, msg = cadastrar_cliente(u['id'], u['nome_usuario'], u.get('avatar'), uid, nick)
    if not sucesso:
        return f"<h2>Erro: {msg}</h2><a href='/cliente'>Voltar</a>", 400
    return redirect(url_for('area_cliente'))

@app.route("/cliente/solicitar", methods=["POST"])
@cliente_required
def cliente_solicitar():
    u = session['usuario']
    cli = obter_cliente(u['id'])
    if not cli or not cli.get("uid"):
        return redirect(url_for('area_cliente'))

    servico_id = request.form.get("servico_id")
    jogo = request.form.get("jogo", "").strip()
    cupom_code = request.form.get("cupom", "").strip()
    obs = request.form.get("observacoes", "").strip()

    servicos = dados.get("servicos", {})
    if servico_id not in servicos:
        return "Serviço inválido", 400

    srv = servicos[servico_id]
    mult = dados.get("fidelidade", {}).get("pontos_por_real", 1)
    
    # Validar cupom se informado
    cupom_aplicado = None
    if cupom_code:
        cupons_cli = cli.get("cupons", {})
        if cupom_code in cupons_cli:
            cdata = cupons_cli[cupom_code]
            if cdata["status"] == "Disponível" and cdata["validade"] >= agora_br().isoformat():
                cupom_aplicado = cupom_code
                cdata["status"] = "Utilizado" # Consumir
            else:
                return "Cupom inválido, expirado ou já utilizado!", 400
        else:
            return "Cupom não pertence a este usuário!", 400

    sol_id = str(int(datetime.now().timestamp() * 1000))
    solicitacao = {
        "id": sol_id,
        "discord_id": str(u['id']),
        "cliente_nome": u['nome_usuario'],
        "uid": cli['uid'],
        "nick": cli['nick'],
        "servico_id": servico_id,
        "servico_nome": srv['nome'],
        "valor": srv['valor'],
        "pontos_gerados": srv.get("pontos", int(srv['valor'] * mult)),
        "jogo": jogo,
        "cupom": cupom_aplicado,
        "observacoes": obs,
        "data": agora_br().isoformat(),
        "status": "Aguardando Aprovação"
    }

    dados.setdefault("solicitacoes", {})[sol_id] = solicitacao
    salvar_dados_github(f"Nova solicitação de serviço por {u['nome_usuario']}")
    return redirect(url_for('area_cliente'))

@app.route("/cliente/resgatar", methods=["POST"])
@cliente_required
def cliente_resgatar():
    u = session['usuario']
    cli = obter_cliente(u['id'])
    rec_id = request.form.get("rec_id")
    opcao = request.form.get("opcao")

    fidelidade = dados.get("fidelidade", {})
    recompensas = fidelidade.get("recompensas", [])
    rec = next((r for r in recompensas if r["id"] == rec_id), None)

    if not rec:
        return "Recompensa não encontrada", 400

    pts_necessarios = rec["pontos"]
    if cli.get("pontos_atuais", 0) < pts_necessarios:
        return "Pontos insuficientes!", 400

    # Descontar pontos
    cli["pontos_atuais"] -= pts_necessarios
    cli["pontos_utilizados"] = cli.get("pontos_utilizados", 0) + pts_necessarios
    cli["ultimo_resgate"] = agora_br().isoformat()

    # Gerar Cupom
    codigo = gerar_codigo_cupom()
    validade_dias = fidelidade.get("validade_cupom_dias", 30)
    data_validade = (agora_br() + timedelta(dias=validade_dias)).isoformat()

    cupom_data = {
        "codigo": codigo,
        "usuario_id": str(u['id']),
        "uid": cli['uid'],
        "data_resgate": agora_br().isoformat(),
        "tipo": opcao,
        "validade": data_validade,
        "status": "Disponível"
    }

    cli.setdefault("cupons", {})[codigo] = cupom_data
    fidelidade.setdefault("cupons", {})[codigo] = cupom_data

    salvar_dados_github(f"Resgate efetuado: {codigo} por {u['nome_usuario']}")
    return redirect(url_for('area_cliente'))

# ========================
# DASHBOARD ADMINISTRATIVO & EXPANSÕES
# ========================
@app.route("/dashboard")
@admin_required
def dashboard():
    usuario = session['usuario']
    fila = obter_dados_fila()
    anti_spam = dados.get("anti_spam", {})
    links = obter_links_fila()
    
    servicos = dados.get("servicos", {})
    solicitacoes = dados.get("solicitacoes", {})
    clientes = dados.get("clientes", {})
    fidelidade = dados.get("fidelidade", {})

    return f'''
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Painel Administrativo Completo</title>
        <style>
            :root {{ --primary: #5865F2; --dark: #121212; --card: #1a1a1a; --gray: #333; }}
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: 'Segoe UI', sans-serif; background: var(--dark); color: #e0e0e0; }}
            header {{ background: #0a0a0a; padding: 1rem 2rem; border-bottom: 1px solid var(--gray); display:flex; justify-content:space-between; align-items:center; }}
            .container {{ max-width: 1400px; margin: 2rem auto; padding: 0 1rem; }}
            .nav-tabs {{ display: flex; gap: 0.5rem; margin-bottom: 1rem; border-bottom: 2px solid var(--gray); flex-wrap: wrap; }}
            .tab-btn {{ padding: 0.75rem 1.2rem; background: var(--gray); border: none; border-radius: 5px 5px 0 0; cursor: pointer; color: #fff; font-weight: bold; }}
            .tab-btn.active {{ background: var(--primary); }}
            .tab-content {{ display: none; background: var(--card); padding: 20px; border-radius: 0 0 10px 10px; border: 1px solid var(--gray); }}
            .tab-content.active {{ display: block; }}
            .btn {{ padding: 8px 16px; background: var(--primary); color: white; border: none; border-radius: 5px; cursor: pointer; text-decoration: none; display: inline-block; font-weight: bold; }}
            .btn-danger {{ background: #ef4444; }}
            .btn-success {{ background: #10b981; }}
            .btn-warning {{ background: #f59e0b; }}
            .form-group {{ margin-bottom: 1rem; }}
            label {{ display: block; margin-bottom: 0.3rem; color: var(--primary); font-weight: bold; }}
            input, select, textarea {{ width: 100%; padding: 8px; background: #0a0a0a; border: 1px solid var(--gray); color: #fff; border-radius: 5px; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
            th, td {{ padding: 10px; border-bottom: 1px solid var(--gray); text-align: left; }}
            th {{ background: #0a0a0a; }}
            .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
        </style>
    </head>
    <body>
        <header>
            <h2>🛡️ Painel Admin - ZankonYTB</h2>
            <div>
                <a href="/cliente" class="btn">👤 Área Cliente</a>
                <a href="/fila" class="btn" style="background:#4b5563;">📋 Ver Fila</a>
                <a href="/logout" class="btn btn-danger">🚪 Sair</a>
            </div>
        </header>

        <div class="container">
            <div class="nav-tabs">
                <button class="tab-btn active" onclick="openTab('solicitacoes')">📩 Solicitações ({len([s for s in solicitacoes.values() if s.get('status')=='Aguardando Aprovação'])})</button>
                <button class="tab-btn" onclick="openTab('servicos')">🛠️ Serviços</button>
                <button class="tab-btn" onclick="openTab('clientes')">👥 Clientes</button>
                <button class="tab-btn" onclick="openTab('fidelidade')">🎁 Fidelidade & Config</button>
                <button class="tab-btn" onclick="openTab('fila')">📋 Fila do Bot</button>
            </div>

            <!-- Aba Solicitações -->
            <div id="solicitacoes" class="tab-content active">
                <h3>📩 Solicitações Pendentes de Aprovação</h3>
                <table>
                    <thead><tr><th>Cliente</th><th>UID</th><th>Serviço</th><th>Valor</th><th>Jogo</th><th>Obs</th><th>Data</th><th>Ações</th></tr></thead>
                    <tbody>
                        {''.join(f'''
                        <tr>
                            <td>{escape_html(s.get("cliente_nome"))}</td>
                            <td>{escape_html(s.get("uid"))}</td>
                            <td>{escape_html(s.get("servico_nome"))}</td>
                            <td>R${s.get("valor")}</td>
                            <td>{escape_html(s.get("jogo"))}</td>
                            <td>{escape_html(s.get("observacoes"))}</td>
                            <td>{s.get("data")[:10]}</td>
                            <td>
                                <form action="/admin/solicitacao/aceitar" method="POST" style="display:inline;">
                                    <input type="hidden" name="sol_id" value="{s["id"]}">
                                    <button class="btn btn-success">Aceitar</button>
                                </form>
                                <form action="/admin/solicitacao/recusar" method="POST" style="display:inline;">
                                    <input type="hidden" name="sol_id" value="{s["id"]}">
                                    <input type="text" name="motivo" placeholder="Motivo" style="width:100px; display:inline;">
                                    <button class="btn btn-danger">Recusar</button>
                                </form>
                            </td>
                        </tr>
                        ''' for s in solicitacoes.values() if s.get("status") == "Aguardando Aprovação") or '<tr><td colspan="8">Nenhuma solicitação pendente.</td></tr>'}
                    </tbody>
                </table>
            </div>

            <!-- Aba Serviços -->
            <div id="servicos" class="tab-content">
                <h3>🛠️ Gerenciar Serviços</h3>
                <div class="grid-2">
                    <div>
                        <h4>Criar Novo Serviço</h4>
                        <form action="/admin/servico/salvar" method="POST">
                            <div class="form-group"><label>Nome:</label><input type="text" name="nome" required></div>
                            <div class="form-group"><label>Categoria:</label><input type="text" name="categoria" required></div>
                            <div class="form-group"><label>Descrição:</label><textarea name="descricao" rows="2"></textarea></div>
                            <div class="form-group"><label>Valor (R$):</label><input type="number" step="0.01" name="valor" required></div>
                            <div class="form-group"><label>Pontos Gerados:</label><input type="number" name="pontos" required></div>
                            <button type="submit" class="btn btn-success">➕ Salvar Serviço</button>
                        </form>
                    </div>
                    <div>
                        <h4>Serviços Cadastrados</h4>
                        <table>
                            <thead><tr><th>Nome</th><th>Valor</th><th>Pontos</th><th>Status</th><th>Ação</th></tr></thead>
                            <tbody>
                                {''.join(f'''
                                <tr>
                                    <td>{escape_html(s.get("nome"))}</td>
                                    <td>R${s.get("valor")}</td>
                                    <td>{s.get("pontos")}</td>
                                    <td>{"🟢 Ativo" if s.get("ativo", True) else "🔴 Inativo"}</td>
                                    <td>
                                        <a href="/admin/servico/toggle/{k}" class="btn btn-warning">Mudar Status</a>
                                    </td>
                                </tr>
                                ''' for k, s in servicos.items()) or '<tr><td colspan="5">Nenhum serviço criado.</td></tr>'}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- Aba Clientes -->
            <div id="clientes" class="tab-content">
                <h3>👥 Gerenciamento de Clientes</h3>
                <table>
                    <thead><tr><th>Cliente</th><th>UID</th><th>Nick</th><th>Pontos Atuais</th><th>Acumulados</th><th>Ações</th></tr></thead>
                    <tbody>
                        {''.join(f'''
                        <tr>
                            <td>{escape_html(c.get("username"))}</td>
                            <td>{escape_html(c.get("uid"))}</td>
                            <td>{escape_html(c.get("nick"))}</td>
                            <td>{c.get("pontos_atuais", 0)}</td>
                            <td>{c.get("pontos_acumulados", 0)}</td>
                            <td>
                                <form action="/admin/cliente/pontos" method="POST" style="display:inline;">
                                    <input type="hidden" name="discord_id" value="{c["discord_id"]}">
                                    <input type="number" name="qtd" style="width:70px;" placeholder="+/- Pts">
                                    <button class="btn">Ajustar</button>
                                </form>
                            </td>
                        </tr>
                        ''' for c in clientes.values()) or '<tr><td colspan="6">Nenhum cliente cadastrado.</td></tr>'}
                    </tbody>
                </table>
            </div>

            <!-- Aba Fidelidade & Config -->
            <div id="fidelidade" class="tab-content">
                <h3>🎁 Configurações do Sistema de Fidelidade</h3>
                <form action="/admin/fidelidade/config" method="POST" style="max-width:400px;">
                    <div class="form-group">
                        <label>Multiplicador de Pontos (R$ 1.00 = X Pontos):</label>
                        <input type="number" name="pontos_por_real" value="{fidelidade.get('pontos_por_real', 1)}" min="1">
                    </div>
                    <button type="submit" class="btn btn-success">💾 Salvar Multiplicador</button>
                </form>
            </div>

            <!-- Aba Fila do Bot -->
            <div id="fila" class="tab-content">
                <h3>📋 Entradas na Fila Atual</h3>
                <table>
                    <thead><tr><th>Posição</th><th>Jogador</th><th>Serviço</th><th>Ação</th></tr></thead>
                    <tbody>
                        {''.join(f'''
                        <tr>
                            <td>#{e["posicao"]}</td>
                            <td>{escape_html(e["nome_usuario"])}</td>
                            <td>{escape_html(e["servico"])}</td>
                            <td>
                                <form action="/admin/fila/concluir" method="POST" style="display:inline;">
                                    <input type="hidden" name="entrada_id" value="{e["id"]}">
                                    <button class="btn btn-success">Concluir Serviço</button>
                                </form>
                            </td>
                        </tr>
                        ''' for e in fila["entradas"]) or '<tr><td colspan="4">Fila vazia.</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>

        <script>
            function openTab(tabId) {{
                document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
                document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
                document.getElementById(tabId).classList.add('active');
                event.currentTarget.classList.add('active');
            }}
        </script>
    </body>
    </html>
    '''

# ========================
# ROTAS ADMIN DE AÇÃO
# ========================
@app.route("/admin/solicitacao/aceitar", methods=["POST"])
@admin_required
def admin_solicitacao_aceitar():
    sol_id = request.form.get("sol_id")
    solicitacoes = dados.setdefault("solicitacoes", {})
    
    if sol_id in solicitacoes:
        sol = solicitacoes[sol_id]
        sol["status"] = "Em andamento"
        
        # Adicionar automaticamente na fila existente
        adicionar_fila(
            nome_usuario=sol["cliente_nome"],
            servico=sol["servico_nome"],
            jogo=sol.get("jogo", ""),
            usuario_id=sol["discord_id"],
            solicitacao_id=sol_id
        )
        salvar_dados_github(f"Solicitação aceita: {sol_id}")
    return redirect(url_for('dashboard'))

@app.route("/admin/solicitacao/recusar", methods=["POST"])
@admin_required
def admin_solicitacao_recusar():
    sol_id = request.form.get("sol_id")
    motivo = request.form.get("motivo", "Não informado")
    solicitacoes = dados.setdefault("solicitacoes", {})
    
    if sol_id in solicitacoes:
        sol = solicitacoes[sol_id]
        sol["status"] = f"Recusado: {motivo}"
        sol["motivo_recusa"] = motivo
        salvar_dados_github(f"Solicitação recusada: {sol_id}")
    return redirect(url_for('dashboard'))

@app.route("/admin/servico/salvar", methods=["POST"])
@admin_required
def admin_servico_salvar():
    servicos = dados.setdefault("servicos", {})
    sid = str(int(datetime.now().timestamp() * 1000))
    
    servicos[sid] = {
        "id": sid,
        "nome": request.form.get("nome"),
        "categoria": request.form.get("categoria"),
        "descricao": request.form.get("descricao"),
        "valor": float(request.form.get("valor", 0)),
        "pontos": int(request.form.get("pontos", 0)),
        "ativo": True
    }
    salvar_dados_github("Serviço salvo/criado via painel")
    return redirect(url_for('dashboard'))

@app.route("/admin/servico/toggle/<sid>")
@admin_required
def admin_servico_toggle(sid):
    servicos = dados.get("servicos", {})
    if sid in servicos:
        servicos[sid]["ativo"] = not servicos[sid].get("ativo", True)
        salvar_dados_github(f"Status do serviço {sid} alterado")
    return redirect(url_for('dashboard'))

@app.route("/admin/cliente/pontos", methods=["POST"])
@admin_required
def admin_cliente_pontos():
    cid = request.form.get("discord_id")
    qtd = int(request.form.get("qtd", 0))
    cli = obter_cliente(cid)
    if cli:
        cli["pontos_atuais"] = max(0, cli.get("pontos_atuais", 0) + qtd)
        if qtd > 0:
            cli["pontos_acumulados"] = cli.get("pontos_acumulados", 0) + qtd
        salvar_dados_github(f"Pontos do cliente {cid} alterados por admin")
    return redirect(url_for('dashboard'))

@app.route("/admin/fidelidade/config", methods=["POST"])
@admin_required
def admin_fidelidade_config():
    fidelidade = dados.setdefault("fidelidade", {})
    fidelidade["pontos_por_real"] = int(request.form.get("pontos_por_real", 1))
    salvar_dados_github("Configuração de fidelidade atualizada")
    return redirect(url_for('dashboard'))

@app.route("/admin/fila/concluir", methods=["POST"])
@admin_required
def admin_fila_concluir():
    eid = request.form.get("entrada_id")
    concluir_servico(eid, admin_nome=session['usuario']['nome_usuario'])
    return redirect(url_for('dashboard'))

# ========================
# APIS E ROTAS PÚBLICAS DA FILA
# ========================
@app.route("/fila")
def fila_publica():
    fila = obter_dados_fila()
    links = obter_links_fila()
    botoes_precos = links.get("botoes_precos", [])
    
    botoes_html = "".join([f'<a href="{escape_html(b["url"])}" target="_blank" class="btn-link btn-link-precos">💰 {escape_html(b["nome"])}</a>' for b in botoes_precos])
    
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
            .status {{ display:inline-block; padding:5px 15px; border-radius:20px; }}
            .status-aberta {{ background:#00b894; }}
            .status-fechada {{ background:#d63031; }}
            .links-container {{ display: flex; justify-content: center; gap: 20px; margin: 20px 0; flex-wrap: wrap; }}
            .btn-link {{ display: inline-flex; align-items: center; gap: 10px; padding: 12px 24px; border-radius: 30px; text-decoration: none; font-weight: bold; transition: all 0.3s; }}
            .btn-link-discord {{ background: #5865F2; color: white; }}
            .btn-link-precos {{ background: #f59e0b; color: white; }}
            .lista-fila {{ background:rgba(0,0,0,0.4); border-radius:20px; overflow:hidden; }}
            .cabecalho-fila, .item-fila {{ display:grid; grid-template-columns:60px 1fr 1fr 1fr 80px; padding:12px 15px; border-bottom:1px solid rgba(255,255,255,0.1); }}
            .posicao {{ font-weight:bold; color:#ffd93d; }}
            .servico {{ color:#a8e6cf; }}
            .jogo {{ color:#ffb347; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>📋 {escape_html(fila["nome"])}</h1>
                <span class="status status-{'aberta' if fila['configuracoes']['aberta'] else 'fechada'}">{'🟢 ABERTA' if fila['configuracoes']['aberta'] else '🔴 FECHADA'}</span>
                <div style="margin-top:10px;">📊 {len(fila["entradas"])} / {fila["configuracoes"]["tamanho_maximo"]} pessoas na fila</div>
            </div>
            
            <div class="links-container">
                {'<a href="' + escape_html(links["discord_convite"]) + '" target="_blank" class="btn-link btn-link-discord">💬 Convite Discord</a>' if links.get("discord_convite") else ''}
                {botoes_html}
            </div>
            
            <div class="lista-fila">
                <div class="cabecalho-fila"><span>#</span><span>Jogador</span><span>Serviço</span><span>Jogo</span><span>Status</span></div>
                {''.join(f'<div class="item-fila"><span class="posicao">#{e["posicao"]}</span><span>{escape_html(e["nome_usuario"])}</span><span class="servico">{escape_html(e["servico"])}</span><span class="jogo">{escape_html(e.get("jogo", ""))}</span><span>⏳</span></div>' for e in fila["entradas"]) or '<div style="text-align:center; padding:30px;">✨ Fila vazia no momento</div>'}
            </div>
        </div>
    </body>
    </html>
    '''

# ========================
# EVENTOS DO DISCORD BOT
# ========================
@bot.event
async def on_ready():
    print(f"🤖 Bot online como {bot.user}")
    iniciar_processador_acoes()

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    # Anti-Spam e Ignorados
    if verificar_comando_ignorado(message.content):
        return

    if not verificar_cargo_ignorado(message.author):
        cnt = registrar_mensagem(message.author.id)
        lim = dados.get("anti_spam", {}).get("limite_mensagens", 5)
        if cnt > lim:
            await aplicar_mute(message.author, dados.get("anti_spam", {}).get("tempo_mute_minutos", 2))
            await remover_xp_por_spam(message.author)
            return

    # Ganho de XP padrão
    uid = str(message.author.id)
    xp_ganho = xp_por_mensagem()
    dados.setdefault("xp", {})[uid] = dados.get("xp", {}).get(uid, 0) + xp_ganho
    novo_nv = xp_para_nivel(dados["xp"][uid])
    dados.setdefault("nivel", {})[uid] = novo_nv
    
    await bot.process_commands(message)

# ========================
# INICIALIZAÇÃO DA APLICAÇÃO
# ========================
def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    carregar_dados_github()
    
    # Iniciar Flask em uma thread paralela
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    
    # Iniciar Bot Discord
    bot.run(BOT_TOKEN)