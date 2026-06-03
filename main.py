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
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://roccia.onrender.com/callback")
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
    "config": {"canal_boas_vindas": None},
    "logs": [],
    "fila": {
        "nome": "Fila de Serviços",
        "configuracoes": {"tamanho_maximo": 50, "aberta": True},
        "entradas": [],
        "historico": []
    }
}

# ========================
# FUNÇÕES UTILITÁRIAS
# ========================
def agora_br():
    """Retorna a data/hora atual no fuso horário de Brasília (UTC-3)"""
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-3)))

UTC_MENOS_3 = timezone(timedelta(hours=-3))

def agora_br_alternativo():
    """Alternativa usando timezone fixo UTC-3"""
    return datetime.now(UTC_MENOS_3)

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
                if "fila" not in dados:
                    dados["fila"] = {
                        "nome": "Fila de Serviços",
                        "configuracoes": {"tamanho_maximo": 50, "aberta": True},
                        "entradas": [],
                        "historico": []
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
            "mensagem": f"{mensagem} @ {agora_br().isoformat()}",
            "conteudo": base64.b64encode(conteudo).decode("utf-8"),
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

EMOJI_RE = re.compile(r"<a?:([a-zA-Z0-9_]+):([0-9]+)>")
EMOJI_NOME_RE = re.compile(r":([a-zA-Z0-9_]+):")

def processar_emoji_str(emoji_str, guild: discord.Guild = None):
    if not emoji_str:
        return None
    
    emoji_str = emoji_str.strip()
    
    print(f"[DEBUG EMOJI] Processando: '{emoji_str}'")
    
    m = EMOJI_RE.match(emoji_str)
    if m:
        nome, id_str = m.groups()
        try:
            eid = int(id_str)
            animado = emoji_str.startswith('<a:')
            print(f"[DEBUG EMOJI] É emoji personalizado: nome={nome}, id={eid}, animado={animado}")
            
            if guild:
                e = discord.utils.get(guild.emojis, id=eid)
                if e:
                    print(f"[DEBUG EMOJI] Encontrado no servidor: {e.name}")
                    return e
            
            print(f"[DEBUG EMOJI] Criando PartialEmoji")
            return discord.PartialEmoji(name=nome, id=eid, animated=animado)
        except Exception as e:
            print(f"[DEBUG EMOJI] Erro ao processar emoji personalizado: {e}")
            pass
    
    m2 = EMOJI_NOME_RE.match(emoji_str)
    if m2:
        nome_emoji = m2.group(1)
        print(f"[DEBUG EMOJI] É formato :nome:: {nome_emoji}")
        
        if guild:
            emoji = discord.utils.get(guild.emojis, name=nome_emoji)
            if emoji:
                print(f"[DEBUG EMOJI] Encontrado no servidor por nome: {emoji.name}")
                return emoji
        
        emojis_padrao = {
            "thumbsup": "👍", "thumbsdown": "👎", "check": "✅", "x": "❌",
            "warning": "⚠️", "exclamation": "❗", "question": "❓", "star": "⭐",
            "heart": "❤️", "fire": "🔥", "rocket": "🚀", "tada": "🎉",
            "eyes": "👀", "smile": "😄", "sunglasses": "😎", "thinking": "🤔",
            "partying_face": "🥳", "ok_hand": "👌", "clap": "👏", "muscle": "💪",
            "pray": "🙏", "100": "💯", "poop": "💩", "skull": "💀"
        }
        
        nome_emoji_lower = nome_emoji.lower()
        if nome_emoji_lower in emojis_padrao:
            resultado = emojis_padrao[nome_emoji_lower]
            print(f"[DEBUG EMOJI] Mapeado para emoji padrão: {resultado}")
            return resultado
        
        print(f"[DEBUG EMOJI] Retornando como string: {emoji_str}")
        return emoji_str
    
    if len(emoji_str) <= 10:
        import unicodedata
        tem_caractere_emoji = any('EMOJI' in unicodedata.name(c, '') for c in emoji_str)
        
        if tem_caractere_emoji or any(c in emoji_str for c in ['👍', '👎', '✅', '❌', '⚠️', '❗', '❓', '⭐', '❤️', '🔥', '🚀', '🎉']):
            print(f"[DEBUG EMOJI] É emoji Unicode: {emoji_str}")
            return emoji_str
    
    print(f"[DEBUG EMOJI] Retornando string original: {emoji_str}")
    return emoji_str

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

def adicionar_fila(nome_usuario: str, servico: str, usuario_id: str = None):
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
        "usuario_id": usuario_id or nome_usuario,
        "timestamp": agora_br().isoformat(),
        "status": "aguardando",
        "posicao": len(fila["entradas"]) + 1
    }
    
    fila["entradas"].append(entrada)
    atualizar_posicoes(fila["entradas"])
    salvar_fila()
    
    adicionar_log(f"fila_adicionar: {nome_usuario} - {servico}")
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
            adicionar_log(f"fila_remover: {removido['nome_usuario']} - {removido['servico']}")
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
            adicionar_log(f"fila_mover_cima: {entrada['nome_usuario']}")
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
            adicionar_log(f"fila_mover_baixo: {entrada['nome_usuario']}")
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
    adicionar_log(f"fila_alternar: {'aberta' if fila['configuracoes']['aberta'] else 'fechada'}")
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
# DIAGNÓSTICO DE CONEXÃO
# ========================
async def verificar_conexao_bot():
    await bot.wait_until_ready()
    
    print("\n" + "="*60)
    print("🔍 DIAGNÓSTICO DE CONEXÃO BOT-SITE")
    print("="*60)
    
    if GUILD_ID:
        guild = bot.get_guild(int(GUILD_ID))
        if guild:
            print(f"✅ Servidor encontrado: {guild.name} (ID: {guild.id})")
            print(f"   👥 Membros: {len(guild.members)}")
            print(f"   📝 Canais: {len(guild.text_channels)}")
            
            print(f"   📋 Canais disponíveis:")
            for channel in guild.text_channels[:10]:
                print(f"      #{channel.name} (ID: {channel.id})")
            
            if len(guild.text_channels) > 10:
                print(f"      ... e mais {len(guild.text_channels) - 10} canais")
        else:
            print(f"❌ Servidor NÃO encontrado! ID: {GUILD_ID}")
            print(f"   Servidores disponíveis: {[f'{g.name} ({g.id})' for g in bot.guilds]}")
    else:
        print("⚠️ GUILD_ID não configurado")
    
    if GUILD_ID and bot.get_guild(int(GUILD_ID)):
        guild = bot.get_guild(int(GUILD_ID))
        bot_member = guild.get_member(bot.user.id)
        if bot_member:
            permissoes = bot_member.guild_permissions
            print(f"🔑 Permissões do bot em {guild.name}:")
            print(f"   📝 Enviar mensagens: {'✅' if permissoes.send_messages else '❌'}")
            print(f"   📋 Gerenciar mensagens: {'✅' if permissoes.manage_messages else '❌'}")
            print(f"   🎭 Gerenciar cargos: {'✅' if permissoes.manage_roles else '❌'}")
            print(f"   📢 Menções @everyone: {'✅' if permissoes.mention_everyone else '❌'}")
            print(f"   🔗 Embed links: {'✅' if permissoes.embed_links else '❌'}")
            print(f"   🎨 Adicionar reações: {'✅' if permissoes.add_reactions else '❌'}")
    
    print("="*60 + "\n")

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
    print(f"   📊 Dados: {kwargs}")
    return True

async def executar_acao_bot_interno(acao):
    tipo_acao = acao["tipo"]
    dados_acao = acao["dados"]
    
    print(f"\n{'='*50}")
    print(f"🤖 EXECUTANDO AÇÃO DO SITE: {tipo_acao}")
    print(f"📊 Dados: {dados_acao}")
    print(f"⏰ Timestamp: {acao.get('timestamp')}")
    print(f"{'='*50}")
    
    if not bot.is_ready():
        print("❌ Bot não está pronto ainda!")
        return False
    
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID else None
    if not guild:
        print(f"❌ Servidor {GUILD_ID} não encontrado!")
        print(f"   Servidores disponíveis: {[g.id for g in bot.guilds]}")
        return False
    
    print(f"✅ Servidor: {guild.name}")
    
    try:
        if tipo_acao == "criar_embed":
            try:
                canal_id = int(dados_acao["canal_id"])
                print(f"🔍 Procurando canal ID: {canal_id} ({type(canal_id)})")
                
                canal = guild.get_channel(canal_id)
                
                if not canal:
                    print(f"⚠️ Canal {canal_id} não encontrado via get_channel")
                    for c in guild.text_channels:
                        if c.id == canal_id:
                            canal = c
                            print(f"✅ Encontrado na iteração: #{c.name}")
                            break
                    
                    if not canal:
                        print("❌ Canal realmente não encontrado após iteração completa")
                        print("📋 Canais disponíveis:")
                        for c in guild.text_channels[:20]:
                            print(f"   {c.id}: #{c.name}")
                        return False
                
                print(f"✅ Canal encontrado: #{canal.name} ({canal.id})")
                print(f"📝 Título: {dados_acao['titulo'][:50]}...")
                print(f"📄 Corpo: {dados_acao['corpo'][:100]}...")
                
                bot_member = guild.get_member(bot.user.id)
                if bot_member:
                    permissoes = canal.permissions_for(bot_member)
                    if not permissoes.send_messages:
                        print("❌ Bot não tem permissão para enviar mensagens neste canal!")
                        return False
                    if not permissoes.embed_links:
                        print("❌ Bot não tem permissão para enviar embeds neste canal!")
                        return False
                
                cor = discord.Color.blue()
                if dados_acao.get('cor'):
                    try:
                        cor_hex = dados_acao['cor'].replace('#', '')
                        cor = discord.Color(int(cor_hex, 16))
                    except:
                        print(f"⚠️ Cor inválida: {dados_acao.get('cor')}, usando padrão")
                
                embed = discord.Embed(
                    title=dados_acao["titulo"],
                    description=dados_acao["corpo"],
                    color=cor
                )
                
                if dados_acao.get('url_imagem'):
                    embed.set_image(url=dados_acao['url_imagem'])
                
                texto_menção = ""
                if dados_acao.get('menção') == 'everyone':
                    texto_menção = "@everyone"
                elif dados_acao.get('menção') == 'here':
                    texto_menção = "@here"
                
                print("📤 Enviando embed...")
                await canal.send(content=texto_menção, embed=embed)
                print(f"✅ Embed enviada com sucesso para #{canal.name}")
                
                canal_logs_id = dados.get("config", {}).get("canal_logs")
                if canal_logs_id:
                    canal_logs = guild.get_channel(int(canal_logs_id))
                    if canal_logs:
                        await canal_logs.send(
                            f"📝 Embed criada por {dados_acao.get('admin', 'Admin do Site')} em #{canal.name}\n"
                            f"Título: {dados_acao['titulo'][:100]}"
                        )
                
                return True
                
            except ValueError as e:
                print(f"❌ ERRO DE CONVERSÃO: Não foi possível converter canal_id para inteiro")
                print(f"   canal_id recebido: {dados_acao.get('canal_id')}")
                print(f"   Tipo: {type(dados_acao.get('canal_id'))}")
                return False
        
        elif tipo_acao == "criar_reacao_cargo":
            try:
                canal_id = int(dados_acao["canal_id"])
                canal = guild.get_channel(canal_id)
                
                if not canal:
                    print(f"❌ Canal {canal_id} não encontrado!")
                    return False
                
                print(f"✅ Canal: #{canal.name}")
                print(f"📝 Conteúdo: {dados_acao['conteudo'][:100]}...")
                
                bot_member = guild.get_member(bot.user.id)
                if bot_member:
                    permissoes = canal.permissions_for(bot_member)
                    if not permissoes.send_messages:
                        print("❌ Sem permissão para enviar mensagens")
                        return False
                    if not permissoes.add_reactions:
                        print("❌ Sem permissão para adicionar reações")
                        return False
                
                mensagem = await canal.send(dados_acao["conteudo"])
                mensagem_id = str(mensagem.id)
                print(f"✅ Mensagem enviada com ID: {mensagem_id}")
                
                pares_str = dados_acao.get("emoji_cargo", "")
                print(f"🔄 String completa: '{pares_str}'")
                
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
                
                print(f"🔄 Processando {len(pares)} pares após parsing inteligente")
                print(f"   Pares encontrados: {pares}")
                
                dados_reacoes_cargos = {}
                
                for par in pares:
                    par = par.strip()
                    if not par:
                        print(f"   ⚠️ Ignorando par vazio")
                        continue
                    
                    indice_divisao = -1
                    profundidade_chaves = 0
                    
                    for i, char in enumerate(par):
                        if char == '<':
                            profundidade_chaves += 1
                        elif char == '>':
                            profundidade_chaves -= 1
                        elif char == ':' and profundidade_chaves == 0:
                            indice_divisao = i
                    
                    if indice_divisao == -1:
                        print(f"   ❌ Par sem ':' válido: {par}")
                        continue
                    
                    try:
                        emoji_str = par[:indice_divisao].strip()
                        nome_cargo = par[indice_divisao+1:].strip()
                        
                        print(f"   Processando: '{emoji_str}' -> '{nome_cargo}'")
                        
                        cargo = discord.utils.get(guild.roles, name=nome_cargo)
                        if not cargo:
                            print(f"   ❌ Cargo '{nome_cargo}' não encontrado!")
                            continue
                        
                        print(f"   🔍 String do emoji: '{emoji_str}'")
                        
                        emoji_processado = processar_emoji_str(emoji_str, guild)
                        
                        if emoji_processado is None:
                            print(f"   ❌ Emoji '{emoji_str}' inválido!")
                            continue
                        
                        print(f"   🔍 Emoji processado: {emoji_processado} (tipo: {type(emoji_processado)})")
                        
                        try:
                            if isinstance(emoji_processado, (discord.Emoji, discord.PartialEmoji)):
                                await mensagem.add_reaction(emoji_processado)
                                chave_emoji = str(emoji_processado.id)
                                print(f"   ✅ Reação adicionada (custom): {emoji_processado.name} (ID: {emoji_processado.id})")
                            else:
                                if isinstance(emoji_processado, str) and emoji_processado:
                                    await mensagem.add_reaction(emoji_processado)
                                    chave_emoji = str(emoji_processado)
                                    print(f"   ✅ Reação adicionada (Unicode): {emoji_processado}")
                                else:
                                    print(f"   ❌ Emoji inválido: {emoji_processado}")
                                    continue
                            
                            dados_reacoes_cargos[chave_emoji] = str(cargo.id)
                            print(f"   ✅ Mapeamento salvo: {chave_emoji} -> {cargo.name}")
                            
                        except discord.HTTPException as e:
                            print(f"   ❌ Erro Discord ao adicionar reação {emoji_str}: {e}")
                            continue
                        except Exception as e:
                            print(f"   ❌ Erro ao adicionar reação {emoji_str}: {e}")
                            import traceback
                            traceback.print_exc()
                            continue
                        
                    except Exception as e:
                        print(f"   ❌ Erro ao processar par {par}: {e}")
                        import traceback
                        traceback.print_exc()
                        continue
                
                if dados_reacoes_cargos:
                    dados.setdefault("reacoes_cargos", {})[mensagem_id] = dados_reacoes_cargos
                    salvar_dados_github("Reação cargo via site")
                    print(f"✅ Reação cargo salva: {mensagem_id}")
                    return True
                else:
                    print("⚠️ Nenhum mapeamento válido criado")
                    try:
                        await mensagem.delete()
                        print("🗑️ Mensagem deletada por falta de mapeamentos válidos")
                    except:
                        pass
                    return False
                    
            except ValueError as e:
                print(f"❌ ERRO DE CONVERSÃO: canal_id inválido: {e}")
                return False
            except Exception as e:
                print(f"❌ ERRO inesperado em criar_reacao_cargo: {e}")
                import traceback
                traceback.print_exc()
                return False
        
        elif tipo_acao == "criar_botoes_cargo":
            try:
                canal_id = int(dados_acao["canal_id"])
                canal = guild.get_channel(canal_id)
                
                if not canal:
                    print(f"❌ Canal {canal_id} não encontrado!")
                    return False
                
                print(f"✅ Canal: #{canal.name}")
                
                pares = dados_acao.get("cargos", "").split(",")
                dicionario_botoes = {}
                print(f"🔄 Processando {len(pares)} botões")
                
                for par in pares:
                    if ":" in par:
                        try:
                            nome_botao, nome_cargo = par.split(":", 1)
                            nome_botao = nome_botao.strip()
                            nome_cargo = nome_cargo.strip()
                            print(f"   Processando botão: {nome_botao} -> {nome_cargo}")
                            
                            cargo = discord.utils.get(guild.roles, name=nome_cargo)
                            if cargo:
                                dicionario_botoes[nome_botao] = cargo.id
                                print(f"   ✅ Botão mapeado: {nome_botao} -> {cargo.name}")
                            else:
                                print(f"   ❌ Cargo '{nome_cargo}' não encontrado!")
                        except Exception as e:
                            print(f"   ❌ Erro ao processar par {par}: {e}")
                
                if dicionario_botoes:
                    view = PersistentRoleButtonView(0, dicionario_botoes)
                    enviado = await canal.send(dados_acao["conteudo"], view=view)
                    print(f"✅ Mensagem com botões enviada: {enviado.id}")
                    
                    view.mensagem_id = enviado.id
                    for item in view.children:
                        if isinstance(item, PersistentRoleButton):
                            item.mensagem_id = enviado.id
                    
                    dados.setdefault("botoes_cargos", {})[str(enviado.id)] = dicionario_botoes
                    salvar_dados_github("Botões de cargo via site")
                    
                    print(f"✅ Botões de cargo criados em #{canal.name}")
                    return True
                else:
                    print("⚠️ Nenhum botão válido criado")
                    return False
                    
            except ValueError as e:
                print(f"❌ ERRO DE CONVERSÃO: canal_id inválido")
                return False
        
        elif tipo_acao == "advertir_membro":
            try:
                membro_id = int(dados_acao["membro_id"])
                membro = guild.get_member(membro_id)
                
                if not membro:
                    print(f"❌ Membro {membro_id} não encontrado!")
                    return False
                
                print(f"✅ Membro: {membro.display_name}")
                print(f"📝 Motivo: {dados_acao['motivo']}")
                
                entrada = {
                    "por": "admin_site",
                    "motivo": dados_acao["motivo"],
                    "ts": agora_br().strftime("%d/%m/%Y %H:%M"),
                    "admin": dados_acao.get('admin', 'Admin do Site')
                }
                dados.setdefault("advertencias", {}).setdefault(str(membro.id), []).append(entrada)
                salvar_dados_github(f"Advertência via site: {membro.display_name}")
                
                canal_logs_id = dados.get("config", {}).get("canal_logs")
                if canal_logs_id:
                    canal_logs = guild.get_channel(int(canal_logs_id))
                    if canal_logs:
                        await canal_logs.send(
                            f"⚠️ {membro.mention} foi advertido por {dados_acao.get('admin', 'Admin do Site')}.\n"
                            f"Motivo: {dados_acao['motivo']}"
                        )
                        print(f"📝 Log enviado para #{canal_logs.name}")
                
                print(f"✅ Membro advertido: {membro.display_name}")
                return True
                
            except ValueError as e:
                print(f"❌ ERRO DE CONVERSÃO: membro_id inválido")
                return False
        
        else:
            print(f"❌ Tipo de ação desconhecida: {tipo_acao}")
            return False
    
    except discord.Forbidden as e:
        print(f"❌ ERRO DE PERMISSÃO: {e}")
        print("   Verifique as permissões do bot no servidor!")
        return False
        
    except discord.HTTPException as e:
        print(f"❌ ERRO HTTP: {e}")
        return False
        
    except Exception as e:
        print(f"❌ Erro ao executar ação {tipo_acao}: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        print(f"{'='*50}\n")

async def processar_acoes_bot_continuo():
    global processador_acoes_rodando
    
    print("\n" + "="*60)
    print("🚀 PROCESSADOR DE AÇÕES DO SITE - INICIANDO")
    print("="*60)
    
    processador_acoes_rodando = True
    
    if not bot.is_ready():
        print("⏳ Aguardando bot ficar pronto...")
        await bot.wait_until_ready()
        await asyncio.sleep(2)
    
    print(f"✅ Bot está pronto: {bot.user}")
    
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID else None
    if guild:
        print(f"🎯 Servidor alvo: {guild.name} (ID: {guild.id})")
        print(f"   📍 Canais: {len(guild.text_channels)}")
        print(f"   👥 Membros: {len(guild.members)}")
    else:
        print(f"⚠️ AVISO: Servidor alvo não encontrado! ID: {GUILD_ID}")
        print(f"   Servidores disponíveis: {[g.name for g in bot.guilds]}")
    
    print("="*60)
    print("🔄 Iniciando loop principal de processamento...")
    print("="*60)
    
    contador_processadas = 0
    contador_erros = 0
    ultimo_status_tempo = time.time()
    
    try:
        while processador_acoes_rodando and not bot.is_closed():
            try:
                tempo_atual = time.time()
                if tempo_atual - ultimo_status_tempo > 30:
                    tamanho_fila = len(acoes_fila_bot)
                    print(f"[PROCESSADOR AÇÕES] Status: Fila={tamanho_fila} | Processadas={contador_processadas} | Erros={contador_erros}")
                    ultimo_status_tempo = tempo_atual
                
                if acoes_fila_bot:
                    acao = acoes_fila_bot[0]
                    tipo_acao = acao['tipo']
                    print(f"\n[PROCESSADOR AÇÕES] 🔄 Processando ação: {tipo_acao}")
                    print(f"   📅 Na fila desde: {acao.get('timestamp')}")
                    
                    try:
                        acao = acoes_fila_bot.pop(0)
                        sucesso = await executar_acao_bot_interno(acao)
                        
                        if sucesso:
                            contador_processadas += 1
                            print(f"[PROCESSADOR AÇÕES] ✅ Ação '{tipo_acao}' concluída! (Total: {contador_processadas})")
                        else:
                            contador_erros += 1
                            print(f"[PROCESSADOR AÇÕES] ❌ Falha na ação '{tipo_acao}'")
                            
                            tentativas = acao.get('tentativas', 0)
                            if tentativas < 3:
                                acao['tentativas'] = tentativas + 1
                                acao['tempo_retentativa'] = agora_br().isoformat()
                                acoes_fila_bot.insert(0, acao)
                                print(f"[PROCESSADOR AÇÕES] 🔄 Tentando novamente ({acao['tentativas']}/3)")
                            else:
                                print(f"[PROCESSADOR AÇÕES] 🗑️ Descarte após 3 tentativas falhas")
                    
                    except Exception as e:
                        contador_erros += 1
                        print(f"[PROCESSADOR AÇÕES] 💥 ERRO CRÍTICO: {e}")
                        import traceback
                        traceback.print_exc()
                
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                print("[PROCESSADOR AÇÕES] ⏹️ Recebido sinal de cancelamento")
                break
                
            except Exception as e:
                print(f"[PROCESSADOR AÇÕES] ⚠️ Erro no loop: {e}")
                await asyncio.sleep(5)
    
    except Exception as e:
        print(f"[PROCESSADOR AÇÕES] 💥 ERRO FATAL: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        processador_acoes_rodando = False
        print("\n" + "="*60)
        print("⏹️ PROCESSADOR DE AÇÕES ENCERRADO")
        print(f"   📊 Estatísticas finais:")
        print(f"   ✅ Ações processadas: {contador_processadas}")
        print(f"   ❌ Erros: {contador_erros}")
        print(f"   📝 Ações restantes na fila: {len(acoes_fila_bot)}")
        print("="*60)

def iniciar_processador_acoes():
    global processador_acoes_task, processador_acoes_rodando
    
    if processador_acoes_rodando:
        print("⚠️ Processador já está rodando")
        return False
    
    try:
        processador_acoes_task = bot.loop.create_task(processar_acoes_bot_continuo())
        print("✅ Processador de ações iniciado!")
        return True
    except Exception as e:
        print(f"❌ Erro ao iniciar processador: {e}")
        return False

def parar_processador_acoes():
    global processador_acoes_task, processador_acoes_rodando
    
    if not processador_acoes_rodando or processador_acoes_task is None:
        return False
    
    try:
        processador_acoes_rodando = False
        if not processador_acoes_task.done():
            processador_acoes_task.cancel()
        print("✅ Processador de ações parado")
        return True
    except Exception as e:
        print(f"❌ Erro ao parar processador: {e}")
        return False

# ========================
# CLASSES DE BOTÕES
# ========================
class PersistentRoleButtonView(ui.View):
    def __init__(self, mensagem_id: int, dicionario_botoes: dict):
        super().__init__(timeout=None)
        self.mensagem_id = mensagem_id
        for label, cargo_id in dicionario_botoes.items():
            self.add_item(PersistentRoleButton(label=label, cargo_id=cargo_id, mensagem_id=mensagem_id))

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

        adicionar_log(f"botao_cargo_clique: usuario={membro.id} cargo={cargo.id} mensagem={self.mensagem_id}")

# ========================
# ROTAS DO SITE - PÁGINA INICIAL
# ========================
@app.route("/", methods=["GET"])
def home():
    status_bot = "✅ Bot Online e Funcionando" if bot.is_ready() else "❌ Bot Offline"
    classe_bot = "online" if bot.is_ready() else "offline"
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Painel de Controle</title>
        <style>
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(135deg, #0a0a0a 0%, #1a1a1a 100%);
                margin: 0;
                padding: 0;
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                color: #e0e0e0;
            }}
            .container {{
                background: #121212;
                border-radius: 20px;
                padding: 40px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.5);
                text-align: center;
                max-width: 500px;
                width: 90%;
                border: 1px solid #333;
            }}
            h1 {{
                color: #5865F2;
                margin-bottom: 10px;
                text-shadow: 0 2px 4px rgba(0,0,0,0.5);
            }}
            .status {{
                padding: 10px;
                border-radius: 10px;
                margin: 20px 0;
                font-weight: bold;
            }}
            .online {{ background: #1a472a; color: #4ade80; border: 1px solid #2ecc71; }}
            .offline {{ background: #7f1d1d; color: #f87171; border: 1px solid #ef4444; }}
            .btn {{
                display: inline-block;
                background: #5865F2;
                color: white;
                padding: 12px 30px;
                border-radius: 8px;
                text-decoration: none;
                font-weight: bold;
                margin: 10px;
                transition: all 0.3s;
                border: none;
                cursor: pointer;
            }}
            .btn:hover {{
                background: #4752C4;
                transform: translateY(-2px);
                box-shadow: 0 5px 15px rgba(88, 101, 242, 0.3);
            }}
            .features {{
                text-align: left;
                margin: 20px 0;
                padding: 15px;
                background: #1a1a1a;
                border-radius: 10px;
                border: 1px solid #333;
            }}
            .features h3 {{
                color: #5865F2;
                margin-bottom: 10px;
            }}
            .features li {{
                margin: 8px 0;
                padding-left: 10px;
                color: #b0b0b0;
            }}
            .features ul {{
                list-style: none;
                padding: 0;
            }}
            .features li:before {{
                content: "✅";
                margin-right: 10px;
                color: #5865F2;
            }}
            p {{
                color: #b0b0b0;
            }}
            code {{
                background: #1a1a1a;
                padding: 2px 6px;
                border-radius: 4px;
                color: #4ade80;
                border: 1px solid #333;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Painel de Controle</h1>
            <div class="status {classe_bot}">
                {status_bot}
            </div>
            
            <div class="features">
                <h3>✨ Funcionalidades:</h3>
                <ul>
                    <li>Sistema de XP e Níveis</li>
                    <li>Reação com Cargos</li>
                    <li>Boas-vindas Personalizadas</li>
                    <li>Sistema de Moderação</li>
                    <li>Botões de Cargos</li>
                    <li>Painel Web de Controle</li>
                    <li>Sistema de Fila de Serviços</li>
                </ul>
            </div>
            
            {"<p>Faça login para configurar o bot pelo navegador</p><a href='/login' class='btn'>🔐 Login com Discord</a>" if 'usuario' not in session else f'<p>Olá, {session["usuario"].get("nome_usuario", "Administrador")}!</p><a href="/dashboard" class="btn">🚀 Ir para o Painel</a><a href="/logout" class="btn">🚪 Sair</a>'}
            
            <p style="margin-top: 20px; color: #888; font-size: 0.9em;">
                Use <code>/comando</code> no Discord ou configure pelo site!
            </p>
        </div>
    </body>
    </html>
    '''

@app.route("/login")
def login():
    if not CLIENT_ID or not CLIENT_SECRET:
        return "Erro: CLIENT_ID ou CLIENT_SECRET não configurados.", 500
    
    discord_auth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify%20guilds"
    )
    return redirect(discord_auth_url)

@app.route("/callback")
def callback():
    if not CLIENT_ID or not CLIENT_SECRET:
        return "Erro de configuração do servidor.", 500
    
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
        
        token_acesso = r.json()['access_token']
        
        usuario_r = requests.get('https://discord.com/api/users/@me', 
                            headers={'Authorization': f'Bearer {token_acesso}'})
        if usuario_r.status_code != 200:
            return "Erro ao obter informações", 400
        
        dados_usuario = usuario_r.json()
        
        servidores_r = requests.get('https://discord.com/api/users/@me/guilds',
                              headers={'Authorization': f'Bearer {token_acesso}'})
        servidores = servidores_r.json() if servidores_r.status_code == 200 else []
        
        eh_admin = False
        for servidor in servidores:
            if str(servidor['id']) == GUILD_ID and (servidor['permissions'] & 0x8):
                eh_admin = True
                break
        
        if not eh_admin:
            return f'''
            <!DOCTYPE html>
            <html>
            <head>
                <title>Acesso Negado</title>
                <style>
                    body {{
                        font-family: Arial;
                        text-align: center;
                        padding: 50px;
                        background: #121212;
                        color: #e0e0e0;
                    }}
                    h2 {{ color: #ff6b6b; }}
                    a {{
                        color: #5865F2;
                        text-decoration: none;
                    }}
                    a:hover {{ text-decoration: underline; }}
                </style>
            </head>
            <body>
                <h2>⚠️ Acesso Restrito</h2>
                <p>Apenas administradores do servidor podem acessar este painel.</p>
                <p>Servidor ID: {str(GUILD_ID)}</p>
                <a href="/">Voltar</a>
            </body>
            </html>
            ''', 403
        
        session['usuario'] = {
            'id': dados_usuario['id'],
            'nome_usuario': dados_usuario['username'],
            'avatar': dados_usuario.get('avatar'),
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
# ROTAS DO SISTEMA DE FILA (PÚBLICAS)
# ========================

@app.route("/fila")
def fila_publica():
    fila = obter_dados_fila()
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta http-equiv="refresh" content="30">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{escape_html(fila["nome"])} - Lista de Espera</title>
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            body {{
                font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, 'Roboto', sans-serif;
                background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
                min-height: 100vh;
                padding: 20px;
                color: #fff;
            }}
            .container {{
                max-width: 800px;
                margin: 0 auto;
            }}
            .header {{
                text-align: center;
                margin-bottom: 30px;
                padding: 20px;
                background: rgba(0,0,0,0.5);
                border-radius: 20px;
                backdrop-filter: blur(10px);
            }}
            h1 {{
                font-size: 2rem;
                margin-bottom: 10px;
                background: linear-gradient(135deg, #ff6b6b, #ffd93d);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                background-clip: text;
            }}
            .status {{
                display: inline-block;
                padding: 5px 15px;
                border-radius: 20px;
                font-size: 0.9rem;
                font-weight: bold;
            }}
            .status-aberta {{ background: #00b894; color: #fff; }}
            .status-fechada {{ background: #d63031; color: #fff; }}
            .info-fila {{
                text-align: center;
                margin-top: 10px;
                font-size: 0.9rem;
                color: #bbb;
            }}
            .lista-fila {{
                background: rgba(0,0,0,0.4);
                border-radius: 20px;
                overflow: hidden;
                backdrop-filter: blur(10px);
            }}
            .cabecalho-fila {{
                display: grid;
                grid-template-columns: 60px 1fr 1fr 80px;
                padding: 15px;
                background: rgba(255,255,255,0.1);
                font-weight: bold;
                border-bottom: 1px solid rgba(255,255,255,0.2);
            }}
            .item-fila {{
                display: grid;
                grid-template-columns: 60px 1fr 1fr 80px;
                padding: 12px 15px;
                border-bottom: 1px solid rgba(255,255,255,0.1);
                transition: background 0.3s;
            }}
            .item-fila:hover {{
                background: rgba(255,255,255,0.05);
            }}
            .posicao {{
                font-weight: bold;
                color: #ffd93d;
                font-size: 1.2rem;
            }}
            .nome-usuario {{
                font-weight: 600;
            }}
            .servico {{
                color: #a8e6cf;
                font-size: 0.9rem;
            }}
            .mensagem-vazia {{
                text-align: center;
                padding: 40px;
                color: #bbb;
            }}
            .footer {{
                text-align: center;
                margin-top: 20px;
                font-size: 0.8rem;
                color: #888;
            }}
            @media (max-width: 600px) {{
                .cabecalho-fila {{
                    font-size: 0.8rem;
                }}
                .item-fila {{
                    font-size: 0.8rem;
                }}
                .posicao {{
                    font-size: 1rem;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>📋 {escape_html(fila["nome"])}</h1>
                <div>
                    <span class="status status-{'aberta' if fila['configuracoes']['aberta'] else 'fechada'}">
                        {'🟢 ABERTA' if fila['configuracoes']['aberta'] else '🔴 FECHADA'}
                    </span>
                </div>
                <div class="info-fila">
                    📊 {len(fila["entradas"])} / {fila["configuracoes"]["tamanho_maximo"]} pessoas na fila
                </div>
            </div>
            
            <div class="lista-fila">
                <div class="cabecalho-fila">
                    <span>#</span>
                    <span>Jogador</span>
                    <span>Serviço</span>
                    <span></span>
                </div>
                
                {''.join(f'''
                <div class="item-fila">
                    <span class="posicao">{entrada["posicao"]}</span>
                    <span class="nome-usuario">{escape_html(entrada["nome_usuario"])}</span>
                    <span class="servico">{escape_html(entrada["servico"])}</span>
                    <span>⏳</span>
                </div>
                ''' for entrada in fila["entradas"]) or '<div class="mensagem-vazia">✨ Ninguém na fila no momento</div>'}
            </div>
            
            <div class="footer">
                Atualizado automaticamente a cada 30 segundos • {agora_br().strftime("%d/%m/%Y %H:%M:%S")}
            </div>
        </div>
    </body>
    </html>
    '''

@app.route("/fila/embed")
def fila_embed():
    fila = obter_dados_fila()
    
    entradas_html = ""
    for entrada in fila["entradas"][:10]:
        entradas_html += f'''
        <div style="display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.1);">
            <span style="color: #ffd93d; font-weight: bold;">#{entrada["posicao"]}</span>
            <span>{escape_html(entrada["nome_usuario"])}</span>
            <span style="color: #a8e6cf;">{escape_html(entrada["servico"])}</span>
        </div>
        '''
    
    if not fila["entradas"]:
        entradas_html = '<div style="text-align: center; padding: 20px;">✨ Fila vazia</div>'
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta http-equiv="refresh" content="15">
        <style>
            body {{
                margin: 0;
                padding: 10px;
                font-family: 'Segoe UI', Arial, sans-serif;
                background: transparent;
                color: white;
                font-size: 14px;
            }}
            .container-fila {{
                background: rgba(0,0,0,0.7);
                border-radius: 10px;
                padding: 10px;
                min-width: 250px;
            }}
            .header {{
                text-align: center;
                margin-bottom: 10px;
                padding-bottom: 5px;
                border-bottom: 1px solid rgba(255,255,255,0.2);
            }}
            .status {{
                font-size: 11px;
                padding: 2px 8px;
                border-radius: 10px;
                display: inline-block;
            }}
        </style>
    </head>
    <body>
        <div class="container-fila">
            <div class="header">
                <strong>📋 {escape_html(fila["nome"])}</strong>
                <span class="status" style="background: {'#00b894' if fila['configuracoes']['aberta'] else '#d63031'}">
                    {'ABERTA' if fila['configuracoes']['aberta'] else 'FECHADA'}
                </span>
            </div>
            {entradas_html}
            <div style="text-align: center; margin-top: 8px; font-size: 10px; color: #888;">
                Total: {len(fila["entradas"])} pessoas
            </div>
        </div>
    </body>
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
            "entradas": [
                {
                    "posicao": e["posicao"],
                    "nome_usuario": e["nome_usuario"],
                    "servico": e["servico"],
                    "timestamp": e["timestamp"],
                    "id": e["id"]
                }
                for e in fila["entradas"]
            ]
        }
    })

# ========================
# APIs DE CONTROLE DA FILA
# ========================

@app.route("/api/fila/adicionar", methods=["POST"])
def api_fila_adicionar():
    dados_req = request.json
    nome_usuario = dados_req.get("nome_usuario", "").strip()
    servico = dados_req.get("servico", "").strip()
    usuario_id = dados_req.get("usuario_id")
    
    if not nome_usuario or not servico:
        return jsonify({"sucesso": False, "mensagem": "Nome e serviço são obrigatórios"})
    
    sucesso, resultado = adicionar_fila(nome_usuario, servico, usuario_id)
    
    if sucesso:
        return jsonify({"sucesso": True, "mensagem": f"{nome_usuario} adicionado à fila!", "entrada": resultado})
    else:
        return jsonify({"sucesso": False, "mensagem": resultado})

@app.route("/api/fila/remover", methods=["POST"])
def api_fila_remover():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401
    
    entrada_id = request.json.get("entrada_id")
    if not entrada_id:
        return jsonify({"sucesso": False, "mensagem": "ID da entrada é obrigatório"})
    
    sucesso, resultado = remover_fila(entrada_id)
    return jsonify({"sucesso": sucesso, "mensagem": "Removido da fila" if sucesso else "Entrada não encontrada"})

@app.route("/api/fila/mover-cima", methods=["POST"])
def api_fila_mover_cima():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401
    
    entrada_id = request.json.get("entrada_id")
    sucesso, resultado = mover_cima(entrada_id)
    return jsonify({"sucesso": sucesso})

@app.route("/api/fila/mover-baixo", methods=["POST"])
def api_fila_mover_baixo():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401
    
    entrada_id = request.json.get("entrada_id")
    sucesso, resultado = mover_baixo(entrada_id)
    return jsonify({"sucesso": sucesso})

@app.route("/api/fila/concluir", methods=["POST"])
def api_fila_concluir():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401
    
    entrada_id = request.json.get("entrada_id")
    sucesso, resultado = concluir_servico(entrada_id)
    return jsonify({"sucesso": sucesso})

@app.route("/api/fila/limpar", methods=["POST"])
def api_fila_limpar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401
    
    limpar_fila()
    return jsonify({"sucesso": True, "mensagem": "Fila limpa com sucesso"})

@app.route("/api/fila/configuracoes", methods=["GET", "POST"])
def api_fila_configuracoes():
    if request.method == "GET":
        fila = obter_dados_fila()
        return jsonify({
            "sucesso": True,
            "configuracoes": fila["configuracoes"],
            "nome": fila["nome"]
        })
    
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401
    
    dados_req = request.json
    
    if "aberta" in dados_req:
        alternar_fila(dados_req["aberta"])
    if "tamanho_maximo" in dados_req:
        definir_tamanho_maximo(int(dados_req["tamanho_maximo"]))
    if "nome" in dados_req:
        definir_nome_fila(dados_req["nome"])
    
    return jsonify({"sucesso": True, "mensagem": "Configurações salvas"})

@app.route("/api/fila/historico")
def api_fila_historico():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401
    
    fila = obter_dados_fila()
    return jsonify({
        "sucesso": True,
        "historico": fila["historico"][-50:]
    })

# ========================
# APIs EXISTENTES (RESUMIDAS)
# ========================
@app.route("/api/config/boasvindas", methods=["POST"])
def api_config_boasvindas():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401
    try:
        dados_req = request.json
        config = dados.setdefault("config", {})
        if 'mensagem' in dados_req:
            config["mensagem_boas_vindas"] = dados_req['mensagem']
        if 'canal_id' in dados_req:
            config["canal_boas_vindas"] = dados_req['canal_id']
        if 'url_imagem' in dados_req:
            config["fundo_boas_vindas"] = dados_req['url_imagem']
        sucesso = salvar_dados_github("Config boas-vindas via site")
        return jsonify({"sucesso": sucesso, "mensagem": "Configuração salva!"})
    except Exception as e:
        return jsonify({"sucesso": False, "mensagem": f"Erro: {str(e)}"}), 500

@app.route("/api/config/xp", methods=["POST"])
def api_config_xp():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401
    try:
        dados_req = request.json
        config = dados.setdefault("config", {})
        if 'taxa' in dados_req:
            taxa = int(dados_req['taxa'])
            if 1 <= taxa <= 10:
                config["taxa_xp"] = taxa
        if 'canal_id' in dados_req:
            config["canal_levelup"] = dados_req['canal_id']
        sucesso = salvar_dados_github("Config XP via site")
        return jsonify({"sucesso": sucesso, "mensagem": "Configuração de XP salva!"})
    except Exception as e:
        return jsonify({"sucesso": False, "mensagem": f"Erro: {str(e)}"}), 500

@app.route("/api/servidor/membros")
def api_servidor_membros():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401
    try:
        guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID and bot.is_ready() else None
        if not guild:
            return jsonify({"sucesso": False, "mensagem": "Servidor não encontrado"})
        membros = []
        for member in guild.members:
            if not member.bot:
                membros.append({
                    "id": str(member.id),
                    "nome": member.display_name,
                    "avatar": str(member.avatar.url) if member.avatar else None
                })
        return jsonify({"sucesso": True, "membros": membros[:100]})
    except Exception as e:
        return jsonify({"sucesso": False, "mensagem": str(e)}), 500

@app.route("/api/comando/embed", methods=["POST"])
def api_comando_embed():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401
    try:
        dados_req = request.json
        canal_id = dados_req.get('canal_id')
        titulo = dados_req.get('titulo')
        corpo = dados_req.get('corpo')
        cor = dados_req.get('cor', '#5865F2')
        url_imagem = dados_req.get('url_imagem')
        mencao = dados_req.get('mencao')
        
        if not canal_id or not titulo or not corpo:
            return jsonify({"sucesso": False, "mensagem": "Preencha todos os campos obrigatórios"})
        
        sucesso = executar_acao_bot(
            "criar_embed",
            canal_id=canal_id,
            titulo=titulo,
            corpo=corpo,
            cor=cor,
            url_imagem=url_imagem,
            mencao=mencao,
            admin=session['usuario']['nome_usuario']
        )
        return jsonify({"sucesso": sucesso, "mensagem": f"✅ Embed será criada em instantes!"})
    except Exception as e:
        return jsonify({"sucesso": False, "mensagem": f"Erro: {str(e)}"}), 500

@app.route("/api/comando/advertir", methods=["POST"])
def api_comando_advertir():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401
    try:
        dados_req = request.json
        membro_id = dados_req.get('membro_id')
        motivo = dados_req.get('motivo', 'Sem motivo informado')
        if not membro_id:
            return jsonify({"sucesso": False, "mensagem": "ID do membro é obrigatório"})
        sucesso = executar_acao_bot("advertir_membro", membro_id=membro_id, motivo=motivo, admin=session['usuario']['nome_usuario'])
        return jsonify({"sucesso": sucesso, "mensagem": f"✅ Membro será advertido em instantes!"})
    except Exception as e:
        return jsonify({"sucesso": False, "mensagem": f"Erro: {str(e)}"}), 500

@app.route("/api/comando/limpar_advertencias", methods=["POST"])
def api_comando_limpar_advertencias():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401
    try:
        dados_req = request.json
        membro_id = str(dados_req.get('membro_id'))
        if not membro_id:
            return jsonify({"sucesso": False, "mensagem": "ID do membro é obrigatório"})
        if membro_id in dados.get("advertencias", {}):
            dados["advertencias"].pop(membro_id)
            salvar_dados_github(f"Limpar advertências via site: {membro_id}")
            return jsonify({"sucesso": True, "mensagem": "✅ Advertências removidas!"})
        else:
            return jsonify({"sucesso": False, "mensagem": "❌ Membro não tem advertências"})
    except Exception as e:
        return jsonify({"sucesso": False, "mensagem": f"Erro: {str(e)}"}), 500

@app.route("/api/reacao_cargo/criar", methods=["POST"])
def api_reacao_cargo_criar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401
    try:
        dados_req = request.json
        canal_id = dados_req.get('canal_id')
        conteudo = dados_req.get('conteudo')
        emoji_cargo = dados_req.get('emoji_cargo')
        if not canal_id or not conteudo or not emoji_cargo:
            return jsonify({"sucesso": False, "mensagem": "Preencha todos os campos"})
        sucesso = executar_acao_bot("criar_reacao_cargo", canal_id=canal_id, conteudo=conteudo, emoji_cargo=emoji_cargo, admin=session['usuario']['nome_usuario'])
        return jsonify({"sucesso": sucesso, "mensagem": "✅ Reação com cargo será criada em instantes!"})
    except Exception as e:
        return jsonify({"sucesso": False, "mensagem": f"Erro: {str(e)}"}), 500

@app.route("/api/botoes_cargo/criar", methods=["POST"])
def api_botoes_cargo_criar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401
    try:
        dados_req = request.json
        canal_id = dados_req.get('canal_id')
        conteudo = dados_req.get('conteudo')
        cargos = dados_req.get('cargos')
        if not canal_id or not conteudo or not cargos:
            return jsonify({"sucesso": False, "mensagem": "Preencha todos os campos"})
        sucesso = executar_acao_bot("criar_botoes_cargo", canal_id=canal_id, conteudo=conteudo, cargos=cargos, admin=session['usuario']['nome_usuario'])
        return jsonify({"sucesso": sucesso, "mensagem": "✅ Botões de cargo serão criados em instantes!"})
    except Exception as e:
        return jsonify({"sucesso": False, "mensagem": f"Erro: {str(e)}"}), 500

@app.route("/api/teste/bot", methods=["GET"])
def api_teste_bot():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autenticado"}), 401
    return jsonify({
        "sucesso": True,
        "bot": {"pronto": bot.is_ready(), "usuario": str(bot.user) if bot.user else None},
        "tamanho_fila": len(acoes_fila_bot)
    })

# ========================
# DASHBOARD PRINCIPAL
# ========================
@app.route("/dashboard")
def dashboard():
    if 'usuario' not in session:
        return redirect(url_for('login'))
    
    usuario = session['usuario']
    
    config = dados.get("config", {})
    msg_boas_vindas = config.get("mensagem_boas_vindas", "Olá {member}, seja bem-vindo(a)!")
    taxa_xp = config.get("taxa_xp", 3)
    fundo_boas_vindas = config.get("fundo_boas_vindas", "")
    canal_boas_vindas = config.get("canal_boas_vindas", "")
    canal_levelup = config.get("canal_levelup", "")
    
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID and bot.is_ready() else None
    canais = []
    cargos = []
    
    if guild:
        canais = [{"id": str(c.id), "nome": c.name} for c in guild.text_channels]
        cargos = [{"id": str(r.id), "nome": r.name} for r in guild.roles if r.name != "@everyone"]
    
    canais_json = json.dumps(canais, ensure_ascii=False)
    cargos_json = json.dumps(cargos, ensure_ascii=False)
    
    fila = obter_dados_fila()
    
    return f'''
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Painel - Bot</title>
        <style>
            :root {{
                --primary: #5865F2;
                --primary-dark: #4752C4;
                --success: #10b981;
                --danger: #ef4444;
                --warning: #f59e0b;
                --dark: #1a1a1a;
                --darker: #121212;
                --light: #e0e0e0;
                --gray: #333;
                --gray-light: #444;
            }}
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: 'Segoe UI', sans-serif; background: var(--darker); color: var(--light); }}
            header {{ background: var(--dark); padding: 1rem 2rem; border-bottom: 1px solid var(--gray); }}
            .header-content {{ display: flex; justify-content: space-between; align-items: center; max-width: 1200px; margin: 0 auto; }}
            h1 {{ color: var(--primary); }}
            .user-info {{ display: flex; align-items: center; gap: 1rem; }}
            .avatar {{ width: 40px; height: 40px; border-radius: 50%; border: 2px solid var(--primary); }}
            .btn {{ padding: 0.5rem 1rem; border: none; border-radius: 5px; cursor: pointer; font-weight: 600; text-decoration: none; display: inline-block; transition: all 0.2s; }}
            .btn-primary {{ background: var(--primary); color: white; }}
            .btn-primary:hover {{ background: var(--primary-dark); }}
            .btn-success {{ background: var(--success); color: white; }}
            .btn-danger {{ background: var(--danger); color: white; }}
            .btn-warning {{ background: var(--warning); color: white; }}
            .container {{ max-width: 1200px; margin: 2rem auto; padding: 0 1rem; }}
            .tab-nav {{ display: flex; gap: 0.5rem; margin-bottom: 1rem; border-bottom: 2px solid var(--gray); flex-wrap: wrap; }}
            .tab-btn {{ padding: 0.75rem 1.5rem; background: var(--gray); border: none; border-radius: 5px 5px 0 0; cursor: pointer; font-weight: 600; color: var(--light); }}
            .tab-btn:hover {{ background: var(--gray-light); }}
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
        </style>
    </head>
    <body>
        <header>
            <div class="header-content">
                <h1>Painel de Controle</h1>
                <div class="user-info">
                    <img src="https://cdn.discordapp.com/avatars/{usuario['id']}/{usuario.get('avatar', '')}.png" class="avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                    <span>{usuario['nome_usuario']}</span>
                    <a href="/" class="btn btn-primary">🏠 Início</a>
                    <a href="/logout" class="btn btn-danger">🚪 Sair</a>
                </div>
            </div>
        </header>
        
        <div class="container">
            <div class="tab-nav">
                <button class="tab-btn active" onclick="mostrarAba('visao_geral')">📊 Visão Geral</button>
                <button class="tab-btn" onclick="mostrarAba('boas_vindas')">👋 Boas-vindas</button>
                <button class="tab-btn" onclick="mostrarAba('xp')">⭐ Sistema XP</button>
                <button class="tab-btn" onclick="mostrarAba('cargos')">🎭 Cargos</button>
                <button class="tab-btn" onclick="mostrarAba('moderacao')">⚠️ Advertência</button>
                <button class="tab-btn" onclick="mostrarAba('fila')">📋 Fila</button>
            </div>
            
            <div id="visao_geral" class="tab active">
                <div class="card">
                    <h2>📊 Estatísticas</h2>
                    <div class="stats-grid">
                        <div class="stat-card"><h3>{len(dados.get("xp", {}))}</h3><p>Usuários com XP</p></div>
                        <div class="stat-card"><h3>{sum(len(w) for w in dados.get("advertencias", {}).values())}</h3><p>Advertências</p></div>
                        <div class="stat-card"><h3>{len(fila["entradas"])}</h3><p>Na Fila</p></div>
                    </div>
                </div>
                <div class="card">
                    <h2>⚡ Status</h2>
                    <p><strong>Bot:</strong> {'✅ Online' if bot.is_ready() else '❌ Offline'}</p>
                    <p><strong>Servidor:</strong> {guild.name if guild else 'Não conectado'}</p>
                    <p><strong>Fila:</strong> {fila["configuracoes"]["aberta"] and '🟢 Aberta' or '🔴 Fechada'} - {len(fila["entradas"])}/{fila["configuracoes"]["tamanho_maximo"]}</p>
                </div>
            </div>
            
            <div id="boas_vindas" class="tab">
                <div class="card">
                    <h2>👋 Boas-vindas</h2>
                    <div class="form-group"><label>Canal</label><select id="boas-vindas-canal" class="form-control"></select></div>
                    <div class="form-group"><label>Mensagem</label><textarea id="boas-vindas-mensagem" class="form-control" rows="3">{msg_boas_vindas}</textarea><small>Use {{member}} para mencionar</small></div>
                    <div class="form-group"><label>Imagem URL</label><input type="url" id="boas-vindas-imagem" class="form-control" value="{fundo_boas_vindas}"></div>
                    <button onclick="salvarConfigBoasVindas()" class="btn btn-primary">💾 Salvar</button>
                    <div id="boas-vindas-alert" class="alert"></div>
                </div>
            </div>
            
            <div id="xp" class="tab">
                <div class="card">
                    <h2>⭐ Sistema XP</h2>
                    <div class="form-group"><label>Taxa de XP</label><input type="number" id="xp-taxa" class="form-control" value="{taxa_xp}" min="1" max="10"></div>
                    <div class="form-group"><label>Canal Level Up</label><select id="levelup-canal" class="form-control"></select></div>
                    <button onclick="salvarConfigXP()" class="btn btn-primary">💾 Salvar</button>
                    <div id="xp-alert" class="alert"></div>
                </div>
            </div>
            
            <div id="cargos" class="tab">
                <div class="card">
                    <h2>🎭 Reação com Cargo</h2>
                    <div class="form-group"><label>Canal</label><select id="rr-canal" class="form-control"></select></div>
                    <div class="form-group"><label>Mensagem</label><textarea id="rr-conteudo" class="form-control" rows="3" placeholder="Reaja para receber cargos!"></textarea></div>
                    <div class="form-group"><label>Emoji:Cargo</label><input type="text" id="rr-pares" class="form-control" placeholder="✅:Verificado,👍:Aprovado"></div>
                    <button onclick="criarReacaoCargo()" class="btn btn-primary">✨ Criar</button>
                    <div id="cargos-alert" class="alert"></div>
                </div>
                <div class="card">
                    <h3>🔄 Botões de Cargos</h3>
                    <div class="form-group"><label>Canal</label><select id="btn-canal" class="form-control"></select></div>
                    <div class="form-group"><label>Mensagem</label><textarea id="btn-conteudo" class="form-control" rows="3"></textarea></div>
                    <div class="form-group"><label>Botão:Cargo</label><input type="text" id="btn-pares" class="form-control" placeholder="Notícias:Notícias,Eventos:Eventos"></div>
                    <button onclick="criarBotoesCargo()" class="btn btn-success">🔄 Criar</button>
                </div>
            </div>
            
            <div id="moderacao" class="tab">
                <div class="card">
                    <h2>⚠️ Advertência</h2>
                    <div class="form-group"><label>Membro</label><select id="advertir-membro" class="form-control"></select></div>
                    <div class="form-group"><label>Motivo</label><input type="text" id="advertir-motivo" class="form-control"></div>
                    <button onclick="executarAdvertir()" class="btn btn-warning">⚠️ Advertir</button>
                    <button onclick="limparAdvertencias()" class="btn btn-danger" style="margin-left: 10px;">🧹 Limpar Advertências</button>
                    <div id="advertir-alert" class="alert"></div>
                </div>
            </div>
            
            <div id="fila" class="tab">
                <div class="card">
                    <h2>📋 Sistema de Fila</h2>
                </div>
                
                <div class="card">
                    <h3>⚙️ Configurações</h3>
                    <div style="display: flex; gap: 1rem; flex-wrap: wrap;">
                        <div style="flex: 1;"><label>Nome da Fila</label><input type="text" id="fila-nome" class="form-control" value="{escape_html(fila['nome'])}"></div>
                        <div style="width: 150px;"><label>Máximo</label><input type="number" id="fila-tamanho-max" class="form-control" value="{fila['configuracoes']['tamanho_maximo']}" min="1" max="100"></div>
                        <div style="display: flex; align-items: flex-end;"><button onclick="salvarConfigFila()" class="btn btn-primary">💾 Salvar</button></div>
                        <div style="display: flex; align-items: flex-end;"><button onclick="alternarStatusFila()" id="fila-alternar-btn" class="btn {'btn-success' if fila['configuracoes']['aberta'] else 'btn-danger'}">{'🔓 Fechar Fila' if fila['configuracoes']['aberta'] else '🔒 Abrir Fila'}</button></div>
                    </div>
                    <div id="fila-status-display" style="margin-top: 10px; padding: 10px; background: #1a1a1a; border-radius: 5px;">
                        Status: <strong>{'🟢 ABERTA' if fila['configuracoes']['aberta'] else '🔴 FECHADA'}</strong> | Ocupação: {len(fila["entradas"])} / {fila["configuracoes"]["tamanho_maximo"]}
                    </div>
                </div>
                
                <div class="card">
                    <h3>➕ Adicionar</h3>
                    <div style="display: flex; gap: 1rem; flex-wrap: wrap;">
                        <input type="text" id="adicionar-nome" class="form-control" placeholder="Nome do jogador" style="flex: 1;">
                        <input type="text" id="adicionar-servico" class="form-control" placeholder="Serviço" style="flex: 1;">
                        <button onclick="adicionarFila()" class="btn btn-primary">➕ Adicionar</button>
                    </div>
                    <div id="adicionar-resultado" class="alert" style="margin-top: 10px; display: none;"></div>
                </div>
                
                <div class="card">
                    <h3>📋 Lista de Espera</h3>
                    <div style="overflow-x: auto;">
                        <table>
                            <thead>
                                <tr><th>#</th><th>Jogador</th><th>Serviço</th><th>Entrada</th><th>Ações</th></tr>
                            </thead>
                            <tbody id="fila-tabela-body">
                                <tr><td colspan="5">Carregando...</td></tr>
                            </tbody>
                        </table>
                    </div>
                    <div style="margin-top: 10px;">
                        <button onclick="limparFila()" class="btn btn-danger">🗑️ Limpar Toda Fila</button>
                        <button onclick="atualizarFila()" class="btn btn-primary">🔄 Atualizar</button>
                    </div>
                </div>
                
                <div class="card">
                    <h3>📎 Links para StreamElements/OBS</h3>
                    <div class="form-group"><label>URL da Lista (HTML)</label><input type="text" class="form-control" readonly value="{request.host_url}fila" onclick="this.select();"></div>
                    <div class="form-group"><label>URL Embed (Overlay)</label><input type="text" class="form-control" readonly value="{request.host_url}fila/embed" onclick="this.select();"></div>
                    <div class="form-group"><label>URL API (JSON)</label><input type="text" class="form-control" readonly value="{request.host_url}fila/api" onclick="this.select();"></div>
                </div>
            </div>
        </div>
        
        <script>
            const canaisServidor = {canais_json};
            
            function mostrarAba(abaId) {{
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                document.getElementById(abaId).classList.add('active');
                event.target.classList.add('active');
                if (abaId === 'fila') carregarFila();
                if (abaId === 'boas_vindas' || abaId === 'xp' || abaId === 'cargos') popularSelects();
                if (abaId === 'moderacao') carregarMembros();
            }}
            
            function popularSelects() {{
                const selects = ['boas-vindas-canal', 'levelup-canal', 'rr-canal', 'btn-canal'];
                selects.forEach(id => {{
                    const select = document.getElementById(id);
                    if (select) {{
                        select.innerHTML = '<option value="">Selecione um canal</option>';
                        canaisServidor.forEach(c => {{
                            const option = document.createElement('option');
                            option.value = c.id;
                            option.textContent = '#' + c.nome;
                            select.appendChild(option);
                        }});
                    }}
                }});
                const wc = document.getElementById('boas-vindas-canal');
                if (wc) wc.value = '{canal_boas_vindas}';
                const lc = document.getElementById('levelup-canal');
                if (lc) lc.value = '{canal_levelup}';
            }}
            
            async function carregarMembros() {{
                try {{
                    const resp = await fetch('/api/servidor/membros');
                    const data = await resp.json();
                    if (data.sucesso) {{
                        const select = document.getElementById('advertir-membro');
                        if (select) {{
                            select.innerHTML = '<option value="">Selecione membro</option>';
                            data.membros.forEach(m => {{
                                const opt = document.createElement('option');
                                opt.value = m.id;
                                opt.textContent = m.nome;
                                select.appendChild(opt);
                            }});
                        }}
                    }}
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function salvarConfigBoasVindas() {{
                const data = {{
                    mensagem: document.getElementById('boas-vindas-mensagem').value,
                    canal_id: document.getElementById('boas-vindas-canal').value,
                    url_imagem: document.getElementById('boas-vindas-imagem').value
                }};
                try {{
                    const resp = await fetch('/api/config/boasvindas', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(data)}});
                    const resultado = await resp.json();
                    mostrarAlerta('boas-vindas-alert', resultado.mensagem, resultado.sucesso);
                }} catch(e) {{ mostrarAlerta('boas-vindas-alert', 'Erro: ' + e.message, false); }}
            }}
            
            async function salvarConfigXP() {{
                const data = {{taxa: parseInt(document.getElementById('xp-taxa').value), canal_id: document.getElementById('levelup-canal').value}};
                try {{
                    const resp = await fetch('/api/config/xp', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(data)}});
                    const resultado = await resp.json();
                    mostrarAlerta('xp-alert', resultado.mensagem, resultado.sucesso);
                }} catch(e) {{ mostrarAlerta('xp-alert', 'Erro: ' + e.message, false); }}
            }}
            
            async function criarReacaoCargo() {{
                const canalId = document.getElementById('rr-canal').value;
                const conteudo = document.getElementById('rr-conteudo').value;
                const pares = document.getElementById('rr-pares').value;
                if (!canalId || !conteudo || !pares) {{ mostrarAlerta('cargos-alert', 'Preencha todos os campos', false); return; }}
                try {{
                    const resp = await fetch('/api/reacao_cargo/criar', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{canal_id: canalId, conteudo: conteudo, emoji_cargo: pares}})}});
                    const resultado = await resp.json();
                    mostrarAlerta('cargos-alert', resultado.mensagem, resultado.sucesso);
                    if (resultado.sucesso) {{ document.getElementById('rr-conteudo').value = ''; document.getElementById('rr-pares').value = ''; }}
                }} catch(e) {{ mostrarAlerta('cargos-alert', 'Erro: ' + e.message, false); }}
            }}
            
            async function criarBotoesCargo() {{
                const canalId = document.getElementById('btn-canal').value;
                const conteudo = document.getElementById('btn-conteudo').value;
                const pares = document.getElementById('btn-pares').value;
                if (!canalId || !conteudo || !pares) {{ mostrarAlerta('cargos-alert', 'Preencha todos os campos', false); return; }}
                try {{
                    const resp = await fetch('/api/botoes_cargo/criar', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{canal_id: canalId, conteudo: conteudo, cargos: pares}})}});
                    const resultado = await resp.json();
                    mostrarAlerta('cargos-alert', resultado.mensagem, resultado.sucesso);
                    if (resultado.sucesso) {{ document.getElementById('btn-conteudo').value = ''; document.getElementById('btn-pares').value = ''; }}
                }} catch(e) {{ mostrarAlerta('cargos-alert', 'Erro: ' + e.message, false); }}
            }}
            
            async function executarAdvertir() {{
                const membroId = document.getElementById('advertir-membro').value;
                const motivo = document.getElementById('advertir-motivo').value;
                if (!membroId || !motivo) {{ alert('Selecione membro e motivo'); return; }}
                try {{
                    const resp = await fetch('/api/comando/advertir', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{membro_id: membroId, motivo: motivo}})}});
                    const resultado = await resp.json();
                    alert(resultado.mensagem);
                    if (resultado.sucesso) document.getElementById('advertir-motivo').value = '';
                }} catch(e) {{ alert('Erro: ' + e.message); }}
            }}
            
            async function limparAdvertencias() {{
                const membroId = document.getElementById('advertir-membro').value;
                if (!membroId) {{ alert('Selecione um membro'); return; }}
                if (!confirm('Tem certeza?')) return;
                try {{
                    const resp = await fetch('/api/comando/limpar_advertencias', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{membro_id: membroId}})}});
                    const resultado = await resp.json();
                    alert(resultado.mensagem);
                }} catch(e) {{ alert('Erro: ' + e.message); }}
            }}
            
            async function carregarFila() {{
                try {{
                    const resp = await fetch('/fila/api');
                    const data = await resp.json();
                    if (data.sucesso) {{
                        const fila = data.fila;
                        const tbody = document.getElementById('fila-tabela-body');
                        if (fila.entradas.length === 0) {{
                            tbody.innerHTML = '<tr><td colspan="5">📭 Ninguém na fila</td></tr>';
                        }} else {{
                            tbody.innerHTML = fila.entradas.map(e => `
                                <tr>
                                    <td><strong style="color:#ffd93d;">#${{e.posicao}}</strong></td>
                                    <td>${{escapeHtml(e.nome_usuario)}}</td>
                                    <td style="color:#a8e6cf;">${{escapeHtml(e.servico)}}</td>
                                    <td>${{new Date(e.timestamp).toLocaleTimeString()}}</td>
                                    <td>
                                        <button onclick="moverCima('${{e.id}}')" class="btn btn-primary" style="padding:4px 8px;">⬆️</button>
                                        <button onclick="moverBaixo('${{e.id}}')" class="btn btn-primary" style="padding:4px 8px;">⬇️</button>
                                        <button onclick="concluirServico('${{e.id}}')" class="btn btn-success" style="padding:4px 8px;">✅</button>
                                        <button onclick="removerFila('${{e.id}}')" class="btn btn-danger" style="padding:4px 8px;">❌</button>
                                    </td>
                                </tr>
                            `).join('');
                        }}
                        document.getElementById('fila-status-display').innerHTML = `Status: <strong>${{fila.aberta ? '🟢 ABERTA' : '🔴 FECHADA'}}</strong> | Ocupação: ${{fila.contagem}} / ${{fila.tamanho_maximo}}`;
                        const toggleBtn = document.getElementById('fila-alternar-btn');
                        if (toggleBtn) {{
                            toggleBtn.className = fila.aberta ? 'btn btn-danger' : 'btn btn-success';
                            toggleBtn.textContent = fila.aberta ? '🔓 Fechar Fila' : '🔒 Abrir Fila';
                        }}
                    }}
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function adicionarFila() {{
                const nome = document.getElementById('adicionar-nome').value.trim();
                const servico = document.getElementById('adicionar-servico').value.trim();
                if (!nome || !servico) {{ mostrarAlertaFila('adicionar-resultado', 'Preencha nome e serviço', false); return; }}
                try {{
                    const resp = await fetch('/api/fila/adicionar', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{nome_usuario: nome, servico: servico}})}});
                    const data = await resp.json();
                    mostrarAlertaFila('adicionar-resultado', data.mensagem, data.sucesso);
                    if (data.sucesso) {{
                        document.getElementById('adicionar-nome').value = '';
                        document.getElementById('adicionar-servico').value = '';
                        carregarFila();
                    }}
                }} catch(e) {{ mostrarAlertaFila('adicionar-resultado', 'Erro: ' + e.message, false); }}
            }}
            
            async function removerFila(entradaId) {{
                if (!confirm('Remover esta pessoa da fila?')) return;
                try {{
                    await fetch('/api/fila/remover', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{entrada_id: entradaId}})}});
                    carregarFila();
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function moverCima(entradaId) {{
                try {{
                    await fetch('/api/fila/mover-cima', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{entrada_id: entradaId}})}});
                    carregarFila();
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function moverBaixo(entradaId) {{
                try {{
                    await fetch('/api/fila/mover-baixo', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{entrada_id: entradaId}})}});
                    carregarFila();
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function concluirServico(entradaId) {{
                if (!confirm('Marcar como concluído?')) return;
                try {{
                    await fetch('/api/fila/concluir', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{entrada_id: entradaId}})}});
                    carregarFila();
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function limparFila() {{
                if (!confirm('⚠️ LIMPAR TODA A FILA? Tem certeza?')) return;
                try {{
                    await fetch('/api/fila/limpar', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}}});
                    carregarFila();
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function salvarConfigFila() {{
                const nome = document.getElementById('fila-nome').value;
                const tamanho_max = parseInt(document.getElementById('fila-tamanho-max').value);
                try {{
                    const resp = await fetch('/api/fila/configuracoes', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{nome: nome, tamanho_maximo: tamanho_max}})}});
                    const data = await resp.json();
                    if (data.sucesso) {{
                        mostrarAlertaFila('adicionar-resultado', 'Configurações salvas!', true);
                        setTimeout(() => {{ document.getElementById('adicionar-resultado').style.display = 'none'; }}, 2000);
                        carregarFila();
                    }}
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function alternarStatusFila() {{
                try {{
                    await fetch('/api/fila/configuracoes', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{aberta: null}})}});
                    carregarFila();
                }} catch(e) {{ console.error(e); }}
            }}
            
            function atualizarFila() {{ carregarFila(); }}
            
            function mostrarAlerta(id, msg, sucesso) {{
                const el = document.getElementById(id);
                el.textContent = msg;
                el.className = 'alert ' + (sucesso ? 'alert-success' : 'alert-error');
                el.style.display = 'block';
                setTimeout(() => el.style.display = 'none', 3000);
            }}
            
            function mostrarAlertaFila(id, msg, sucesso) {{
                const el = document.getElementById(id);
                el.textContent = msg;
                el.className = 'alert ' + (sucesso ? 'alert-success' : 'alert-error');
                el.style.display = 'block';
                setTimeout(() => el.style.display = 'none', 3000);
            }}
            
            function escapeHtml(texto) {{
                if (!texto) return '';
                return texto.replace(/[&<>]/g, function(m) {{
                    if (m === '&') return '&amp;';
                    if (m === '<') return '&lt;';
                    if (m === '>') return '&gt;';
                    return m;
                }});
            }}
            
            document.addEventListener('DOMContentLoaded', function() {{
                popularSelects();
                carregarMembros();
                carregarFila();
            }});
        </script>
    </body>
    </html>
    '''

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
        except Exception as e:
            print(f"Erro no auto-ping: {e}")

Thread(target=auto_ping, daemon=True).start()

# ========================
# EVENTOS DO BOT
# ========================
@bot.event
async def on_ready():
    bot.start_time = datetime.now()
    
    print(f"\n{'='*50}")
    print(f"🤖 BOT INICIADO COM SUCESSO!")
    print(f"{'='*50}")
    print(f"📛 Nome: {bot.user}")
    print(f"🆔 ID: {bot.user.id}")
    print(f"{'='*50}")
    
    print(f"🏠 SERVIDORES CONECTADOS ({len(bot.guilds)}):")
    for i, guild in enumerate(bot.guilds, 1):
        print(f"  {i}. {guild.name} (ID: {guild.id}) - Membros: {len(guild.members)}")
    print(f"{'='*50}")
    
    if GUILD_ID:
        servidor_alvo = bot.get_guild(int(GUILD_ID))
        if servidor_alvo:
            print(f"🎯 SERVIDOR ALVO: {servidor_alvo.name}")
        else:
            print(f"⚠️ Servidor alvo não encontrado! ID: {GUILD_ID}")
    
    print(f"{'='*50}")
    
    print("📂 Carregando dados do GitHub...")
    sucesso_carregar = carregar_dados_github()
    print(f"   {'✅ Dados carregados' if sucesso_carregar else '⚠️ Usando dados locais'}")

    print("⚙️ Sincronizando comandos slash...")
    try:
        if GUILD_ID:
            await tree.sync(guild=discord.Object(id=int(GUILD_ID)))
            print(f"   ✅ Comandos sincronizados no servidor {GUILD_ID}")
        else:
            await tree.sync()
            print("   ✅ Comandos globais sincronizados")
    except Exception as e:
        print(f"   ❌ Erro ao sincronizar comandos: {e}")

    await asyncio.sleep(2)
    
    try:
        iniciar_processador_acoes()
        print("✅ Sistema de ações INICIADO!")
    except Exception as e:
        print(f"❌ Erro ao iniciar sistema de ações: {e}")
    
    print(f"{'='*50}")
    print(f"✨ BOT PRONTO PARA USO!")
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

    msg_boas_vindas = dados.get("config", {}).get("mensagem_boas_vindas", "Olá {member}, seja bem-vindo(a)!")
    msg_boas_vindas = msg_boas_vindas.replace("{member}", member.mention)

    caminho_fundo = dados.get("config", {}).get("fundo_boas_vindas", "")

    largura, altura = 900, 300
    img = Image.new("RGBA", (largura, altura), (0, 0, 0, 255))

    if caminho_fundo:
        try:
            response = requests.get(caminho_fundo)
            bg = Image.open(BytesIO(response.content)).convert("RGBA")
            bg = bg.resize((largura, altura))
            img.paste(bg, (0, 0))
        except Exception as e:
            print(f"Erro ao carregar imagem de fundo: {e}")

    overlay = Image.new("RGBA", (largura, altura), (50, 50, 50, 150))
    img = Image.alpha_composite(img, overlay)

    draw = ImageDraw.Draw(img)

    try:
        user_bytes = await member.avatar.read()
        user_avatar = Image.open(BytesIO(user_bytes)).convert("RGBA")

        tamanho_avatar = 150
        tamanho_borda = 5
        upscale = 4
        tamanho_grande = (tamanho_avatar + tamanho_borda * 2) * upscale

        user_avatar = user_avatar.resize((tamanho_avatar * upscale, tamanho_avatar * upscale))
        mask = Image.new("L", (tamanho_avatar * upscale, tamanho_avatar * upscale), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, tamanho_avatar * upscale, tamanho_avatar * upscale), fill=255)

        cor_borda = (200, 150, 255, 255)
        borda = Image.new("RGBA", (tamanho_grande, tamanho_grande), (0, 0, 0, 0))
        draw_borda = ImageDraw.Draw(borda)
        draw_borda.ellipse((0, 0, tamanho_grande, tamanho_grande), fill=cor_borda)

        borda.paste(user_avatar, (tamanho_borda * upscale, tamanho_borda * upscale), mask)
        borda = borda.resize((tamanho_avatar + tamanho_borda * 2, tamanho_avatar + tamanho_borda * 2), Image.Resampling.LANCZOS)

        x = (largura - borda.width) // 2
        y = 30
        img.paste(borda, (x, y), borda)
    except Exception as e:
        print(f"Erro ao carregar avatar do usuário: {e}")

    try:
        font_b = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_s = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
    except:
        font_b = ImageFont.load_default()
        font_s = ImageFont.load_default()

    cor_texto = (200, 150, 255)
    cor_sombra = (0, 0, 0, 180)

    nome_texto = member.display_name
    bbox_nome = draw.textbbox((0, 0), nome_texto, font=font_b)
    text_w = bbox_nome[2] - bbox_nome[0]
    text_x = (largura - text_w) // 2
    text_y = y + borda.height + 10

    draw.text((text_x + 2, text_y + 2), nome_texto, font=font_b, fill=cor_sombra)
    draw.text((text_x, text_y), nome_texto, font=font_b, fill=cor_texto)

    texto_contagem = f"Membro #{len(member.guild.members)}"
    bbox_contagem = draw.textbbox((0, 0), texto_contagem, font=font_s)
    text_w2 = bbox_contagem[2] - bbox_contagem[0]
    text_x2 = (largura - text_w2) // 2
    text_y2 = text_y + 50

    draw.text((text_x2 + 1, text_y2 + 1), texto_contagem, font=font_s, fill=cor_sombra)
    draw.text((text_x2, text_y2), texto_contagem, font=font_s, fill=cor_texto)

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    arquivo = discord.File(buf, filename="welcome.png")

    await canal.send(content=msg_boas_vindas, file=arquivo)
    adicionar_log(f"membro_entrou: {member.id} - {member}")

# ========================
# REACTION ROLES
# ========================
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    try:
        msgmap = dados.get("reacoes_cargos", {}).get(str(payload.message_id))
        if not msgmap:
            return

        cargo_id = None
        if payload.emoji.id and str(payload.emoji.id) in msgmap:
            cargo_id = msgmap[str(payload.emoji.id)]
        elif payload.emoji.id is not None and payload.emoji.name in msgmap:
            cargo_id = msgmap[payload.emoji.name]
        elif str(payload.emoji) in msgmap:
            cargo_id = msgmap[str(payload.emoji)]

        if not cargo_id:
            return

        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member:
            member = await guild.fetch_member(payload.user_id)
        cargo = guild.get_role(int(cargo_id))
        if member and cargo:
            await member.add_roles(cargo, reason="reaction role add")
            adicionar_log(f"reacao adicionar: usuario={member.id} cargo={cargo.id} msg={payload.message_id}")

    except Exception as e:
        print("on_raw_reaction_add error:", e)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    try:
        msgmap = dados.get("reacoes_cargos", {}).get(str(payload.message_id))
        if not msgmap:
            return

        cargo_id = None
        if payload.emoji.id and str(payload.emoji.id) in msgmap:
            cargo_id = msgmap[str(payload.emoji.id)]
        elif payload.emoji.id is not None and payload.emoji.name in msgmap:
            cargo_id = msgmap[payload.emoji.name]
        elif str(payload.emoji) in msgmap:
            cargo_id = msgmap[str(payload.emoji)]

        if not cargo_id:
            return

        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member:
            member = await guild.fetch_member(payload.user_id)
        cargo = guild.get_role(int(cargo_id))
        if member and cargo:
            await member.remove_roles(cargo, reason="reaction role remove")
            adicionar_log(f"reacao remover: usuario={member.id} cargo={cargo.id} msg={payload.message_id}")

    except Exception as e:
        print("on_raw_reaction_remove error:", e)

# ========================
# WARN HELPER
# ========================
async def adicionar_advertencia(member: discord.Member, motivo=""):
    uid = str(member.id)
    entrada = {
        "por": bot.user.id,
        "motivo": motivo,
        "ts": agora_br().strftime("%d/%m/%Y %H:%M")
    }
    dados.setdefault("advertencias", {}).setdefault(uid, []).append(entrada)
    salvar_dados_github("Auto-advertência")
    adicionar_log(f"advertencia: usuario={uid} por=bot motivo={motivo}")

# ========================
# ON MESSAGE
# ========================
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    uid = str(message.author.id)
    conteudo = message.content.strip()
    deletar_mensagem = False

    comandos_mudae = [
        "$w", "$wa", "$wg", "$h", "$ha", "$hg",
        "$W", "$WA", "$WG", "$H", "$HA", "$HG",
        "$tu", "$TU", "$dk", "$mmi", "$vote", "$rolls", "$k", "$mu"
    ]
    if any(conteudo.lower().startswith(cmd) for cmd in comandos_mudae):
        await bot.process_commands(message)
        return

    cargos_ignorados = {"Administrador", "Moderador"}
    cargos_membro = {r.name for r in message.author.roles}
    eh_staff = any(cargo in cargos_ignorados for cargo in cargos_membro)

    tem_midia = False
    if message.attachments:
        tem_midia = True
    if message.stickers:
        tem_midia = True
    dominios_gif = ["tenor.com", "media.tenor.com", "giphy.com", "imgur.com"]
    if any(dominio in conteudo.lower() for dominio in dominios_gif):
        tem_midia = True

    if tem_midia:
        await bot.process_commands(message)
        return

    canais_bloqueados = dados.get("canais_links_bloqueados", [])
    if message.channel.id in canais_bloqueados:
        padrao_url = r"https?://[^\s]+"
        if re.search(padrao_url, conteudo):
            if not eh_staff:
                try:
                    await message.delete()
                except discord.Forbidden:
                    pass
                await message.channel.send(f"⚠️ {message.author.mention}, links não são permitidos aqui!")
                await adicionar_advertencia(message.author, motivo="Enviou link em canal bloqueado")
                return

    msgs_usuario = dados.setdefault("ultimas_mensagens_conteudo", {}).setdefault(uid, [])
    if len(msgs_usuario) >= 5:
        msgs_usuario.pop(0)

    if msgs_usuario and conteudo == msgs_usuario[-1]:
        if not eh_staff:
            deletar_mensagem = True
            try:
                await message.delete()
            except discord.Forbidden:
                pass
            await message.channel.send(f"⚠️ {message.author.mention}, evite enviar mensagens repetidas!")
            await adicionar_advertencia(message.author, motivo="Spam detectado")
            return
    else:
        msgs_usuario.append(conteudo)
    dados["ultimas_mensagens_conteudo"][uid] = msgs_usuario

    if len(conteudo) > 5 and conteudo.isupper():
        if not eh_staff:
            deletar_mensagem = True
            try:
                await message.delete()
            except discord.Forbidden:
                pass
            await message.channel.send(f"⚠️ {message.author.mention}, evite escrever tudo em maiúsculas!")
            await adicionar_advertencia(message.author, motivo="Uso excessivo de maiúsculas")
            return

    if not deletar_mensagem:
        dados.setdefault("xp", {})
        dados.setdefault("nivel", {})

        taxa_xp = dados.get("config", {}).get("taxa_xp", 3)
        ganho_xp = max(1, xp_por_mensagem() // taxa_xp)
        dados["xp"][uid] = dados["xp"].get(uid, 0) + ganho_xp

        xp_atual = dados["xp"][uid]
        nivel_atual = xp_para_nivel(xp_atual)
        nivel_anterior = dados["nivel"].get(uid, 1)

        if nivel_atual > nivel_anterior:
            dados["nivel"][uid] = nivel_atual

            canal_levelup_id = dados.get("config", {}).get("canal_levelup")
            canal_enviar = None

            if canal_levelup_id:
                canal_enviar = message.guild.get_channel(int(canal_levelup_id))
            if not canal_enviar:
                canal_enviar = message.channel

            try:
                await canal_enviar.send(f"🎉 {message.author.mention} subiu para o nível **{nivel_atual}**!")
            except Exception as e:
                print(f"Erro ao enviar mensagem de level up: {e}")

            cargos_nivel = dados.get("cargos_nivel", {})
            cargo_id = cargos_nivel.get(str(nivel_atual))
            if cargo_id:
                cargo = message.guild.get_role(int(cargo_id))
                if cargo:
                    try:
                        await message.author.add_roles(cargo, reason=f"Alcançou nível {nivel_atual}")
                    except discord.Forbidden:
                        await canal_enviar.send(
                            f"⚠️ Não consegui dar o cargo {cargo.mention}, verifique minhas permissões."
                        )

            adicionar_log(f"level_up: usuario={uid} nivel={nivel_atual}")

    try:
        salvar_dados_github("Atualização XP")
    except Exception as e:
        print(f"Erro ao salvar XP: {e}")

    await bot.process_commands(message)

# ========================
# SLASH COMMANDS
# ========================
def verificar_admin(interaction: discord.Interaction) -> bool:
    try:
        permissoes = interaction.user.guild_permissions
        return permissoes.administrator or permissoes.manage_guild or permissoes.manage_roles
    except Exception:
        return False
        
def verificar_comando_permitido(interaction: discord.Interaction, nome_comando: str) -> bool:
    permitidos = dados.get("canais_comandos", {}).get(nome_comando, [])
    if not permitidos:
        return True
    return interaction.channel_id in permitidos

#/cargo_xp
@tree.command(name="cargo_xp", description="Define um cargo para ser atribuído ao atingir certo nível (admin)")
@app_commands.describe(nivel="Nível em que o cargo será dado", cargo="Cargo a ser atribuído")
async def set_level_role(interaction: discord.Interaction, nivel: int, cargo: discord.Role):
    if not verificar_admin(interaction):
        await interaction.response.send_message("❌ Você não tem permissão.", ephemeral=True)
        return

    if nivel < 1:
        await interaction.response.send_message("⚠️ O nível deve ser maior que 0.", ephemeral=True)
        return

    dados.setdefault("cargos_nivel", {})[str(nivel)] = str(cargo.id)
    salvar_dados_github("Set level role")

    await interaction.response.send_message(
        f"✅ Cargo {cargo.mention} será atribuído ao atingir o **nível {nivel}**.",
        ephemeral=False
    )

#/xp_rate
@tree.command(name="xp_rate", description="Define a taxa de ganho de XP (admin)")
@app_commands.describe(taxa="Taxa de XP — valores menores tornam o up mais lento")
async def set_xp_rate(interaction: discord.Interaction, taxa: int):
    if not verificar_admin(interaction):
        await interaction.response.send_message("❌ Você não tem permissão para usar este comando.", ephemeral=True)
        return

    if taxa < 1:
        await interaction.response.send_message("⚠️ O valor mínimo é 1.", ephemeral=True)
        return

    dados.setdefault("config", {})["taxa_xp"] = taxa
    salvar_dados_github("Set XP rate")

    await interaction.response.send_message(f"✅ Taxa de XP ajustada para **x{taxa}**.", ephemeral=False)

#/mensagem_personalizada
@tree.command(name="mensagem_personalizada", description="Cria uma mensagem personalizada (admin)")
@app_commands.describe(
    canal="Canal onde a mensagem será enviada",
    titulo="Título da mensagem",
    corpo="Texto interno (use \\n para quebra de linha)",
    imagem="Link da imagem (opcional)",
    cor="Cor em hexadecimal (ex: #5865F2)",
    mencionar="Mencionar @everyone (opcional)"
)
async def criar_embed(
    interaction: discord.Interaction,
    canal: discord.TextChannel,
    titulo: str,
    corpo: str,
    imagem: str = None,
    cor: str = "#5865F2",
    mencionar: str = None
):
    if not verificar_admin(interaction):
        await interaction.response.send_message("❌ Você não tem permissão para usar este comando.", ephemeral=True)
        return

    try:
        color = discord.Color(int(cor.replace("#", ""), 16))
    except:
        color = discord.Color.blurple()

    texto_formatado = corpo.replace("\\n", "\n").strip()
    texto_formatado = texto_formatado.replace("- ", "● ").replace("• ", "● ")
    linhas = texto_formatado.split("\n")
    texto_formatado = "\n\n".join(linha.strip() for linha in linhas if linha.strip())

    embed = discord.Embed(
        title=f"**{titulo}**",
        description=texto_formatado,
        color=color
    )

    if imagem:
        embed.set_image(url=imagem)

    texto_mencao = mencionar if mencionar in ["@everyone", "@here"] else ""
    await canal.send(content=texto_mencao, embed=embed)
    await interaction.response.send_message(f"✅ Embed enviada para {canal.mention}.", ephemeral=True)

#/perfil
@tree.command(name="perfil", description="mostra o seu perfil")
@app_commands.describe(membro="Membro a ver o rank (opcional)")
async def slash_rank(interaction: discord.Interaction, membro: discord.Member = None):
    if not verificar_comando_permitido(interaction, "rank"):
        await interaction.response.send_message("❌ Este comando só pode ser usado em canais autorizados.", ephemeral=True)
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

    try:
        font_b = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
        font_s = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
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
    except Exception as e:
        print("Erro avatar:", e)

    draw.text((160, 50), alvo.display_name, font=font_b, fill=(0, 255, 255))
    draw.text((largura - 220, 40), f"CLASSIFICAÇÃO #{pos}", font=font_s, fill=(0, 255, 255))
    draw.text((largura - 220, 80), f"NÍVEL {nivel}", font=font_s, fill=(255, 0, 255))

    proximo_xp = 100 + nivel*50
    atual = xp % proximo_xp
    barra_total_w, barra_h = 560, 36
    x0, y0 = 160, 140
    raio = barra_h // 2

    draw.rounded_rectangle([x0, y0, x0+barra_total_w, y0+barra_h], radius=raio, fill=(50, 50, 50))
    
    preenchimento_w = int(barra_total_w * min(1.0, atual / proximo_xp))
    if preenchimento_w > 0:
        barra_preenchida = Image.new("RGBA", (preenchimento_w, barra_h), (0,0,0,0))
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
    arquivo = discord.File(buf, filename="rank.png")
    await interaction.followup.send(file=arquivo)

#/rank
@tree.command(name="rank", description="Mostra top 10 de XP")
async def slash_top(interaction: discord.Interaction):
    if not verificar_comando_permitido(interaction, "top"):
        await interaction.response.send_message("❌ Este comando só pode ser usado em canais autorizados.", ephemeral=True)
        return
    await interaction.response.defer()
    ranking = sorted(dados.get("xp", {}).items(), key=lambda t: t[1], reverse=True)[:10]
    linhas = []
    for i, (uid, xp) in enumerate(ranking, 1):
        user = interaction.guild.get_member(int(uid))
        nome = user.display_name if user else f"Usuário {uid}"
        linhas.append(f"{i}. {nome} — {xp} XP")
    texto = "\n".join(linhas) if linhas else "Sem dados ainda."
    await interaction.followup.send(f"🏆 **Top 10 XP**\n{texto}")

#/advertir
@tree.command(name="advertir", description="Advertir um membro (admin)")
@app_commands.describe(membro="Membro a ser advertido", motivo="Motivo da advertência")
async def slash_warn(interaction: discord.Interaction, membro: discord.Member, motivo: str = "Sem motivo informado"):
    if not verificar_admin(interaction):
        await interaction.response.send_message("Você não tem permissão para usar este comando.", ephemeral=True)
        return
    uid = str(membro.id)
    entrada = {
        "por": interaction.user.id,
        "motivo": motivo,
        "ts": agora_br().strftime("%d/%m/%Y %H:%M")
    }
    dados.setdefault("advertencias", {}).setdefault(uid, []).append(entrada)
    salvar_dados_github("New warn")
    adicionar_log(f"advertencia: usuario={uid} por={interaction.user.id} motivo={motivo}")
    await interaction.response.send_message(f"⚠️ {membro.mention} advertido.\nMotivo: {motivo}")

#/savedata
@tree.command(name="savedata", description="Força salvar dados no GitHub (admin)")
async def slash_savedata(interaction: discord.Interaction):
    if not verificar_admin(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return
    ok = salvar_dados_github("Manual save via /savedata")
    await interaction.response.send_message("Dados salvos no GitHub." if ok else "Falha ao salvar (veja logs).")

#/definir_canal_boas-vindas
@tree.command(name="definir_canal_boas-vindas", description="Define canal de boas-vindas para o bot (admin)")
@app_commands.describe(canal="Canal de texto")
async def slash_setwelcome(interaction: discord.Interaction, canal: discord.TextChannel = None):
    if not verificar_admin(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return
    if canal is None:
        dados.setdefault("config", {})["canal_boas_vindas"] = None
        salvar_dados_github("Unset welcome channel")
        await interaction.response.send_message("Canal de boas-vindas removido.")
    else:
        dados.setdefault("config", {})["canal_boas_vindas"] = str(canal.id)
        salvar_dados_github("Set welcome channel")
        await interaction.response.send_message(f"Canal de boas-vindas definido: {canal.mention}")

#/canal_xp
@tree.command(name="canal_xp", description="Define o canal onde serão enviadas as mensagens de level up (admin)")
@app_commands.describe(canal="Canal onde o bot vai enviar as mensagens de level up")
async def set_levelup_channel(interaction: discord.Interaction, canal: discord.TextChannel):
    if not verificar_admin(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return

    dados.setdefault("config", {})["canal_levelup"] = canal.id
    salvar_dados_github("Set level up channel")

    await interaction.response.send_message(f"✅ Canal de level up definido para {canal.mention}.", ephemeral=False)

# REACTION ROLES GROUP
reactionrole_group = app_commands.Group(name="reajir_com_emoji", description="Gerenciar reaction roles (admin)")

@reactionrole_group.command(name="criar", description="Cria mensagem com reação e mapeia para um cargo (admin)")
@app_commands.describe(canal="Canal para enviar a mensagem", conteudo="Conteúdo da mensagem", emoji="Emoji (custom <:_name_:id> ou unicode)", cargo="Cargo a ser atribuído")
async def rr_create(interaction: discord.Interaction, canal: discord.TextChannel, conteudo: str, emoji: str, cargo: discord.Role):
    if not verificar_admin(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=False)
    
    emoji_processado = processar_emoji_str(emoji, guild=interaction.guild)
    
    try:
        enviado = await canal.send(conteudo)
    except Exception as e:
        await interaction.followup.send(f"Falha ao enviar mensagem: {e}")
        return
    
    try:
        if isinstance(emoji_processado, discord.Emoji) or isinstance(emoji_processado, discord.PartialEmoji):
            await enviado.add_reaction(emoji_processado)
            chave = str(emoji_processado.id)
        else:
            await enviado.add_reaction(emoji_processado)
            chave = str(emoji_processado)
    except Exception as e:
        await interaction.followup.send(f"Falha ao reagir com o emoji: {e}")
        return
    
    dados.setdefault("reacoes_cargos", {}).setdefault(str(enviado.id), {})[chave] = str(cargo.id)
    salvar_dados_github("reactionrole create")
    adicionar_log(f"reactionrole criada msg={enviado.id} emoji={chave} cargo={cargo.id}")
    await interaction.followup.send(f"Mensagem criada em {canal.mention} com ID `{enviado.id}`. Reaja para receber o cargo {cargo.mention}.")

tree.add_command(reactionrole_group)

# ========================
# START BOT AND FLASK
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
