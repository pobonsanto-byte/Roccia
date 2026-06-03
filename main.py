import os
import json
import base64
import re
import requests
import time
import secrets
from io import BytesIO
from threading import Thread
from zoneinfo import ZoneInfo
from datetime import datetime
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
bot_actions_queue = []
action_processor_task = None
action_processor_running = False

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
data = {
    "xp": {},
    "level": {},
    "warns": {},
    "reaction_roles": {},
    "config": {"welcome_channel": None},
    "logs": [],
    "queue": {
        "name": "Fila de Serviços",
        "settings": {"max_size": 50, "open": True},
        "entries": [],
        "history": []
    }
}

# ========================
# FUNÇÕES UTILITÁRIAS
# ========================
def now_br():
    return datetime.now(ZoneInfo("America/Sao_Paulo"))

def _gh_headers():
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

def load_data_from_github():
    try:
        r = requests.get(GITHUB_API_CONTENT, headers=_gh_headers(), params={"ref": BRANCH}, timeout=15)
        if r.status_code == 200:
            js = r.json()
            content_b64 = js.get("content", "")
            if content_b64:
                raw = base64.b64decode(content_b64)
                loaded = json.loads(raw.decode("utf-8"))
                data.update(loaded)
                # Garante que a estrutura da fila existe
                if "queue" not in data:
                    data["queue"] = {
                        "name": "Fila de Serviços",
                        "settings": {"max_size": 50, "open": True},
                        "entries": [],
                        "history": []
                    }
                print("✅ Dados carregados do GitHub.")
                return True
        else:
            print(f"⚠️ GitHub GET retornou {r.status_code} — iniciando com dados limpos.")
    except Exception as e:
        print(f"❌ Erro ao carregar dados do GitHub: {e}")
    return False

def save_data_to_github(message="Bot update"):
    try:
        r = requests.get(GITHUB_API_CONTENT, headers=_gh_headers(), params={"ref": BRANCH}, timeout=15)
        sha = None
        if r.status_code == 200:
            sha = r.json().get("sha")

        content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        payload = {
            "message": f"{message} @ {now_br().isoformat()}",
            "content": base64.b64encode(content).decode("utf-8"),
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

def add_log(entry):
    ts = now_br().isoformat()
    data.setdefault("logs", []).append({"ts": ts, "entry": entry})
    try:
        save_data_to_github(f"log: {entry}")
    except Exception:
        pass

def xp_for_message():
    return 15

def xp_to_level(xp):
    lvl = int((xp / 100) ** 0.6) + 1
    return max(lvl, 1)

def escape_html(text):
    """Escapa caracteres HTML"""
    if not text:
        return ""
    return (text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )

EMOJI_RE = re.compile(r"<a?:([a-zA-Z0-9_]+):([0-9]+)>")
EMOJI_NAME_RE = re.compile(r":([a-zA-Z0-9_]+):")

def parse_emoji_str(emoji_str, guild: discord.Guild = None):
    """Analisa uma string de emoji e retorna o objeto apropriado"""
    if not emoji_str:
        return None
    
    emoji_str = emoji_str.strip()
    
    # Debug: mostra o que está sendo processado
    print(f"[DEBUG EMOJI] Processando: '{emoji_str}'")
    
    # Verifica se é um emoji personalizado (formato <:nome:id> ou <a:nome:id>)
    m = EMOJI_RE.match(emoji_str)
    if m:
        name, id_str = m.groups()
        try:
            eid = int(id_str)
            animated = emoji_str.startswith('<a:')
            print(f"[DEBUG EMOJI] É emoji personalizado: nome={name}, id={eid}, animado={animated}")
            
            # Primeiro tenta encontrar no servidor
            if guild:
                e = discord.utils.get(guild.emojis, id=eid)
                if e:
                    print(f"[DEBUG EMOJI] Encontrado no servidor: {e.name}")
                    return e
            
            # Se não encontrar, cria um PartialEmoji
            print(f"[DEBUG EMOJI] Criando PartialEmoji")
            return discord.PartialEmoji(name=name, id=eid, animated=animated)
        except Exception as e:
            print(f"[DEBUG EMOJI] Erro ao processar emoji personalizado: {e}")
            pass
    
    # Verifica se é um emoji padrão (formato :nome:)
    m2 = EMOJI_NAME_RE.match(emoji_str)
    if m2:
        emoji_name = m2.group(1)
        print(f"[DEBUG EMOJI] É formato :nome:: {emoji_name}")
        
        # Procura emoji no servidor primeiro
        if guild:
            emoji = discord.utils.get(guild.emojis, name=emoji_name)
            if emoji:
                print(f"[DEBUG EMOJI] Encontrado no servidor por nome: {emoji.name}")
                return emoji
        
        # Mapeamento de emojis padrão do Discord
        standard_emojis = {
            "thumbsup": "👍", "thumbsdown": "👎", "check": "✅", "x": "❌",
            "warning": "⚠️", "exclamation": "❗", "question": "❓", "star": "⭐",
            "heart": "❤️", "fire": "🔥", "rocket": "🚀", "tada": "🎉",
            "eyes": "👀", "smile": "😄", "sunglasses": "😎", "thinking": "🤔",
            "partying_face": "🥳", "ok_hand": "👌", "clap": "👏", "muscle": "💪",
            "pray": "🙏", "100": "💯", "poop": "💩", "skull": "💀"
        }
        
        emoji_name_lower = emoji_name.lower()
        if emoji_name_lower in standard_emojis:
            result = standard_emojis[emoji_name_lower]
            print(f"[DEBUG EMOJI] Mapeado para emoji padrão: {result}")
            return result
        
        # Se não for um emoji padrão conhecido, retorna como string
        print(f"[DEBUG EMOJI] Retornando como string: {emoji_str}")
        return emoji_str
    
    # Se for uma string única, pode ser um emoji Unicode
    # Emojis Unicode são geralmente 1-2 caracteres (alguns com variação de cor podem ter mais)
    if len(emoji_str) <= 10:  # Aumentei o limite para acomodar emojis mais complexos
        # Verifica se tem caracteres que parecem ser emojis
        import unicodedata
        has_emoji_char = any('EMOJI' in unicodedata.name(c, '') for c in emoji_str)
        
        if has_emoji_char or any(c in emoji_str for c in ['👍', '👎', '✅', '❌', '⚠️', '❗', '❓', '⭐', '❤️', '🔥', '🚀', '🎉']):
            print(f"[DEBUG EMOJI] É emoji Unicode: {emoji_str}")
            return emoji_str
    
    print(f"[DEBUG EMOJI] Retornando string original: {emoji_str}")
    # Se tudo falhar, retorna a string original
    return emoji_str

# ========================
# SISTEMA DE FILA
# ========================

def get_queue_data():
    """Retorna os dados da fila"""
    data.setdefault("queue", {
        "name": "Fila de Serviços",
        "settings": {"max_size": 50, "open": True},
        "entries": [],
        "history": []
    })
    return data["queue"]

def save_queue():
    """Salva a fila no GitHub"""
    return save_data_to_github("Queue update")

def add_to_queue(username: str, service: str, user_id: str = None):
    """Adiciona alguém à fila"""
    queue = get_queue_data()
    
    if not queue["settings"]["open"]:
        return False, "Fila está fechada no momento"
    
    if len(queue["entries"]) >= queue["settings"]["max_size"]:
        return False, "Fila está cheia"
    
    # Verifica se usuário já está na fila
    for entry in queue["entries"]:
        if entry["username"].lower() == username.lower():
            return False, f"{username} já está na fila"
    
    entry = {
        "id": str(int(datetime.now().timestamp() * 1000)),
        "username": username,
        "service": service,
        "user_id": user_id or username,
        "timestamp": datetime.now().isoformat(),
        "status": "waiting",
        "position": len(queue["entries"]) + 1
    }
    
    queue["entries"].append(entry)
    update_positions(queue["entries"])
    save_queue()
    
    add_log(f"queue_add: {username} - {service}")
    return True, entry

def remove_from_queue(entry_id: str):
    """Remove alguém da fila"""
    queue = get_queue_data()
    
    for i, entry in enumerate(queue["entries"]):
        if entry["id"] == entry_id:
            removed = queue["entries"].pop(i)
            removed["removed_at"] = datetime.now().isoformat()
            queue["history"].append(removed)
            
            # Mantém histórico limitado
            if len(queue["history"]) > 100:
                queue["history"] = queue["history"][-100:]
            
            update_positions(queue["entries"])
            save_queue()
            add_log(f"queue_remove: {removed['username']} - {removed['service']}")
            return True, removed
    
    return False, None

def update_positions(entries):
    """Atualiza as posições da fila"""
    for i, entry in enumerate(entries):
        entry["position"] = i + 1
        entry["status"] = "waiting"

def move_up(entry_id: str):
    """Move alguém uma posição para cima"""
    queue = get_queue_data()
    entries = queue["entries"]
    
    for i, entry in enumerate(entries):
        if entry["id"] == entry_id and i > 0:
            entries[i], entries[i-1] = entries[i-1], entries[i]
            update_positions(entries)
            save_queue()
            add_log(f"queue_move_up: {entry['username']}")
            return True, entry
    
    return False, None

def move_down(entry_id: str):
    """Move alguém uma posição para baixo"""
    queue = get_queue_data()
    entries = queue["entries"]
    
    for i, entry in enumerate(entries):
        if entry["id"] == entry_id and i < len(entries) - 1:
            entries[i], entries[i+1] = entries[i+1], entries[i]
            update_positions(entries)
            save_queue()
            add_log(f"queue_move_down: {entry['username']}")
            return True, entry
    
    return False, None

def complete_service(entry_id: str):
    """Completa o serviço e remove da fila"""
    queue = get_queue_data()
    
    for i, entry in enumerate(queue["entries"]):
        if entry["id"] == entry_id:
            removed = queue["entries"].pop(i)
            removed["status"] = "completed"
            removed["completed_at"] = datetime.now().isoformat()
            queue["history"].append(removed)
            
            update_positions(queue["entries"])
            save_queue()
            add_log(f"queue_complete: {removed['username']}")
            return True, removed
    
    return False, None

def clear_queue():
    """Limpa toda a fila"""
    queue = get_queue_data()
    
    # Move todos para histórico
    for entry in queue["entries"]:
        entry["status"] = "cleared"
        entry["cleared_at"] = datetime.now().isoformat()
        queue["history"].append(entry)
    
    queue["entries"] = []
    save_queue()
    add_log("queue_cleared")
    return True

def toggle_queue(open_status: bool = None):
    """Abre/fecha a fila"""
    queue = get_queue_data()
    
    if open_status is None:
        queue["settings"]["open"] = not queue["settings"]["open"]
    else:
        queue["settings"]["open"] = open_status
    
    save_queue()
    add_log(f"queue_toggle: {'open' if queue['settings']['open'] else 'closed'}")
    return queue["settings"]["open"]

def set_max_size(size: int):
    """Define tamanho máximo da fila"""
    queue = get_queue_data()
    queue["settings"]["max_size"] = max(1, min(size, 100))
    save_queue()
    return queue["settings"]["max_size"]

def set_queue_name(name: str):
    """Define nome da fila"""
    queue = get_queue_data()
    queue["name"] = name[:50]
    save_queue()
    return queue["name"]

# ========================
# DIAGNÓSTICO DE CONEXÃO
# ========================
async def check_bot_connection():
    """Verifica se o bot está conectado corretamente"""
    await bot.wait_until_ready()
    
    print("\n" + "="*60)
    print("🔍 DIAGNÓSTICO DE CONEXÃO BOT-SITE")
    print("="*60)
    
    # Verifica Guild
    if GUILD_ID:
        guild = bot.get_guild(int(GUILD_ID))
        if guild:
            print(f"✅ Guild encontrada: {guild.name} (ID: {guild.id})")
            print(f"   👥 Membros: {len(guild.members)}")
            print(f"   📝 Canais: {len(guild.text_channels)}")
            
            # Lista canais disponíveis
            print(f"   📋 Canais disponíveis:")
            for channel in guild.text_channels[:10]:
                print(f"      #{channel.name} (ID: {channel.id})")
            
            if len(guild.text_channels) > 10:
                print(f"      ... e mais {len(guild.text_channels) - 10} canais")
        else:
            print(f"❌ Guild NÃO encontrada! ID: {GUILD_ID}")
            print(f"   Guilds disponíveis: {[f'{g.name} ({g.id})' for g in bot.guilds]}")
    else:
        print("⚠️ GUILD_ID não configurado")
    
    # Verifica permissões
    if GUILD_ID and bot.get_guild(int(GUILD_ID)):
        guild = bot.get_guild(int(GUILD_ID))
        bot_member = guild.get_member(bot.user.id)
        if bot_member:
            permissions = bot_member.guild_permissions
            print(f"🔑 Permissões do bot em {guild.name}:")
            print(f"   📝 Enviar mensagens: {'✅' if permissions.send_messages else '❌'}")
            print(f"   📋 Gerenciar mensagens: {'✅' if permissions.manage_messages else '❌'}")
            print(f"   🎭 Gerenciar cargos: {'✅' if permissions.manage_roles else '❌'}")
            print(f"   📢 Menções @everyone: {'✅' if permissions.mention_everyone else '❌'}")
            print(f"   🔗 Embed links: {'✅' if permissions.embed_links else '❌'}")
            print(f"   🎨 Adicionar reações: {'✅' if permissions.add_reactions else '❌'}")
    
    print("="*60 + "\n")

# ========================
# SISTEMA DE AÇÕES DO SITE
# ========================
def execute_bot_action(action_type, **kwargs):
    """Adiciona uma ação à fila para ser executada pelo bot"""
    bot_actions_queue.append({
        "type": action_type,
        "data": kwargs,
        "timestamp": datetime.now().isoformat()
    })
    print(f"🤖 [BOT ACTION] Adicionada ação: {action_type}")
    print(f"   📊 Dados: {kwargs}")
    return True

async def execute_bot_action_internal(action):
    """Executa uma ação do bot internamente"""
    action_type = action["type"]
    action_data = action["data"]
    
    print(f"\n{'='*50}")
    print(f"🤖 EXECUTANDO AÇÃO DO SITE: {action_type}")
    print(f"📊 Dados: {action_data}")
    print(f"⏰ Timestamp: {action.get('timestamp')}")
    print(f"{'='*50}")
    
    # Verifica se o bot está pronto
    if not bot.is_ready():
        print("❌ Bot não está pronto ainda!")
        return False
    
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID else None
    if not guild:
        print(f"❌ Guild {GUILD_ID} não encontrada!")
        print(f"   Guilds disponíveis: {[g.id for g in bot.guilds]}")
        return False
    
    print(f"✅ Guild: {guild.name}")
    
    try:
        if action_type == "create_embed":
            try:
                channel_id = int(action_data["channel_id"])
                print(f"🔍 Procurando canal ID: {channel_id} ({type(channel_id)})")
                
                channel = guild.get_channel(channel_id)
                
                if not channel:
                    print(f"⚠️ Canal {channel_id} não encontrado via get_channel")
                    # Tenta encontrar manualmente
                    for c in guild.text_channels:
                        if c.id == channel_id:
                            channel = c
                            print(f"✅ Encontrado na iteração: #{c.name}")
                            break
                    
                    if not channel:
                        print("❌ Canal realmente não encontrado após iteração completa")
                        print("📋 Canais disponíveis:")
                        for c in guild.text_channels[:20]:
                            print(f"   {c.id}: #{c.name}")
                        return False
                
                print(f"✅ Canal encontrado: #{channel.name} ({channel.id})")
                print(f"📝 Título: {action_data['title'][:50]}...")
                print(f"📄 Corpo: {action_data['body'][:100]}...")
                
                # Verifica permissões
                bot_member = guild.get_member(bot.user.id)
                if bot_member:
                    permissions = channel.permissions_for(bot_member)
                    if not permissions.send_messages:
                        print("❌ Bot não tem permissão para enviar mensagens neste canal!")
                        return False
                    if not permissions.embed_links:
                        print("❌ Bot não tem permissão para enviar embeds neste canal!")
                        return False
                
                # Processa cor da embed
                color = discord.Color.blue()  # Default
                if action_data.get('color'):
                    try:
                        # Remove o # se existir
                        color_hex = action_data['color'].replace('#', '')
                        color = discord.Color(int(color_hex, 16))
                    except:
                        print(f"⚠️ Cor inválida: {action_data.get('color')}, usando padrão")
                
                # Cria e envia embed
                embed = discord.Embed(
                    title=action_data["title"],
                    description=action_data["body"],
                    color=color
                )
                
                # Adiciona imagem se fornecida
                if action_data.get('image_url'):
                    embed.set_image(url=action_data['image_url'])
                
                # Processa menção
                mention_text = ""
                if action_data.get('mention') == 'everyone':
                    mention_text = "@everyone"
                elif action_data.get('mention') == 'here':
                    mention_text = "@here"
                
                print("📤 Enviando embed...")
                await channel.send(content=mention_text, embed=embed)
                print(f"✅ Embed enviada com sucesso para #{channel.name}")
                
                # Log no canal de logs se configurado
                logs_channel_id = data.get("config", {}).get("logs_channel")
                if logs_channel_id:
                    logs_channel = guild.get_channel(int(logs_channel_id))
                    if logs_channel:
                        await logs_channel.send(
                            f"📝 Embed criada por {action_data.get('admin', 'Site Admin')} em #{channel.name}\n"
                            f"Título: {action_data['title'][:100]}"
                        )
                
                return True
                
            except ValueError as e:
                print(f"❌ ERRO DE CONVERSÃO: Não foi possível converter channel_id para inteiro")
                print(f"   channel_id recebido: {action_data.get('channel_id')}")
                print(f"   Tipo: {type(action_data.get('channel_id'))}")
                return False
        
        elif action_type == "create_reaction_role":
            try:
                channel_id = int(action_data["channel_id"])
                channel = guild.get_channel(channel_id)
                
                if not channel:
                    print(f"❌ Canal {channel_id} não encontrado!")
                    return False
                
                print(f"✅ Canal: #{channel.name}")
                print(f"📝 Conteúdo: {action_data['content'][:100]}...")
                
                # Verifica permissões
                bot_member = guild.get_member(bot.user.id)
                if bot_member:
                    permissions = channel.permissions_for(bot_member)
                    if not permissions.send_messages:
                        print("❌ Sem permissão para enviar mensagens")
                        return False
                    if not permissions.add_reactions:
                        print("❌ Sem permissão para adicionar reações")
                        return False
                
                # Envia UMA ÚNICA mensagem
                message = await channel.send(action_data["content"])
                message_id = str(message.id)
                print(f"✅ Mensagem enviada com ID: {message_id}")
                
                # Processa pares emoji:cargo
                pairs_str = action_data.get("emoji_cargo", "")
                print(f"🔄 String completa: '{pairs_str}'")
                
                # Divide os pares de forma mais inteligente
                pairs = []
                current_pair = ""
                bracket_count = 0
                
                # Processa caracter por caracter para lidar com emojis customizados
                for char in pairs_str:
                    if char == '<':
                        bracket_count += 1
                    elif char == '>':
                        bracket_count -= 1
                    
                    if char == ',' and bracket_count == 0:
                        if current_pair.strip():
                            pairs.append(current_pair.strip())
                            current_pair = ""
                    else:
                        current_pair += char
                
                # Adiciona o último par
                if current_pair.strip():
                    pairs.append(current_pair.strip())
                
                print(f"🔄 Processando {len(pairs)} pares após parsing inteligente")
                print(f"   Pares encontrados: {pairs}")
                
                reaction_roles_data = {}
                
                for pair in pairs:
                    pair = pair.strip()
                    if not pair:
                        print(f"   ⚠️ Ignorando par vazio")
                        continue
                    
                    # Procura o último ':' que não está dentro de < >
                    split_index = -1
                    bracket_depth = 0
                    
                    for i, char in enumerate(pair):
                        if char == '<':
                            bracket_depth += 1
                        elif char == '>':
                            bracket_depth -= 1
                        elif char == ':' and bracket_depth == 0:
                            # Encontra o último ':' fora de brackets
                            split_index = i
                    
                    if split_index == -1:
                        print(f"   ❌ Par sem ':' válido: {pair}")
                        continue
                    
                    try:
                        emoji_str = pair[:split_index].strip()
                        role_name = pair[split_index+1:].strip()
                        
                        print(f"   Processando: '{emoji_str}' -> '{role_name}'")
                        
                        # Encontra o cargo
                        role = discord.utils.get(guild.roles, name=role_name)
                        if not role:
                            print(f"   ❌ Cargo '{role_name}' não encontrado!")
                            continue
                        
                        # Debug: mostra informações do emoji
                        print(f"   🔍 String do emoji: '{emoji_str}'")
                        
                        # Analisa o emoji
                        parsed_emoji = parse_emoji_str(emoji_str, guild)
                        
                        if parsed_emoji is None:
                            print(f"   ❌ Emoji '{emoji_str}' inválido!")
                            continue
                        
                        print(f"   🔍 Emoji parseado: {parsed_emoji} (tipo: {type(parsed_emoji)})")
                        
                        # Tenta adicionar a reação
                        try:
                            # Para emojis personalizados (discord.Emoji ou discord.PartialEmoji)
                            if isinstance(parsed_emoji, (discord.Emoji, discord.PartialEmoji)):
                                await message.add_reaction(parsed_emoji)
                                emoji_key = str(parsed_emoji.id)
                                print(f"   ✅ Reação adicionada (custom): {parsed_emoji.name} (ID: {parsed_emoji.id})")
                            # Para emojis Unicode (strings)
                            else:
                                # Verifica se é um emoji Unicode válido
                                if isinstance(parsed_emoji, str) and parsed_emoji:
                                    await message.add_reaction(parsed_emoji)
                                    emoji_key = str(parsed_emoji)
                                    print(f"   ✅ Reação adicionada (Unicode): {parsed_emoji}")
                                else:
                                    print(f"   ❌ Emoji inválido: {parsed_emoji}")
                                    continue
                            
                            # Prepara dados para salvar
                            reaction_roles_data[emoji_key] = str(role.id)
                            print(f"   ✅ Mapeamento salvo: {emoji_key} -> {role.name}")
                            
                        except discord.HTTPException as e:
                            print(f"   ❌ Erro Discord ao adicionar reação {emoji_str}: {e}")
                            continue
                        except Exception as e:
                            print(f"   ❌ Erro ao adicionar reação {emoji_str}: {e}")
                            import traceback
                            traceback.print_exc()
                            continue
                        
                    except Exception as e:
                        print(f"   ❌ Erro ao processar par {pair}: {e}")
                        import traceback
                        traceback.print_exc()
                        continue
                
                # Salva no data.json se houver dados
                if reaction_roles_data:
                    data.setdefault("reaction_roles", {})[message_id] = reaction_roles_data
                    save_data_to_github("Reaction role via site")
                    print(f"✅ Reaction role salva: {message_id}")
                    return True
                else:
                    print("⚠️ Nenhum mapeamento válido criado")
                    # Se nenhum mapeamento foi criado, deleta a mensagem
                    try:
                        await message.delete()
                        print("🗑️ Mensagem deletada por falta de mapeamentos válidos")
                    except:
                        pass
                    return False
                    
            except ValueError as e:
                print(f"❌ ERRO DE CONVERSÃO: channel_id inválido: {e}")
                return False
            except Exception as e:
                print(f"❌ ERRO inesperado em create_reaction_role: {e}")
                import traceback
                traceback.print_exc()
                return False
        
        elif action_type == "create_role_buttons":
            try:
                channel_id = int(action_data["channel_id"])
                channel = guild.get_channel(channel_id)
                
                if not channel:
                    print(f"❌ Canal {channel_id} não encontrado!")
                    return False
                
                print(f"✅ Canal: #{channel.name}")
                
                # Processa pares botão:cargo
                pairs = action_data.get("roles", "").split(",")
                buttons_dict = {}
                print(f"🔄 Processando {len(pairs)} botões")
                
                for pair in pairs:
                    if ":" in pair:
                        try:
                            button_name, role_name = pair.split(":", 1)
                            button_name = button_name.strip()
                            role_name = role_name.strip()
                            print(f"   Processando botão: {button_name} -> {role_name}")
                            
                            # Encontra o cargo
                            role = discord.utils.get(guild.roles, name=role_name)
                            if role:
                                buttons_dict[button_name] = role.id
                                print(f"   ✅ Botão mapeado: {button_name} -> {role.name}")
                            else:
                                print(f"   ❌ Cargo '{role_name}' não encontrado!")
                        except Exception as e:
                            print(f"   ❌ Erro ao processar par {pair}: {e}")
                
                if buttons_dict:
                    # Cria view com botões
                    view = PersistentRoleButtonView(0, buttons_dict)
                    sent = await channel.send(action_data["content"], view=view)
                    print(f"✅ Mensagem com botões enviada: {sent.id}")
                    
                    # Atualiza IDs
                    view.message_id = sent.id
                    for item in view.children:
                        if isinstance(item, PersistentRoleButton):
                            item.message_id = sent.id
                    
                    # Salva no data.json
                    data.setdefault("role_buttons", {})[str(sent.id)] = buttons_dict
                    save_data_to_github("Role buttons via site")
                    
                    print(f"✅ Botões de cargo criados em #{channel.name}")
                    return True
                else:
                    print("⚠️ Nenhum botão válido criado")
                    return False
                    
            except ValueError as e:
                print(f"❌ ERRO DE CONVERSÃO: channel_id inválido")
                return False
        
        elif action_type == "warn_member":
            try:
                member_id = int(action_data["member_id"])
                member = guild.get_member(member_id)
                
                if not member:
                    print(f"❌ Membro {member_id} não encontrado!")
                    return False
                
                print(f"✅ Membro: {member.display_name}")
                print(f"📝 Motivo: {action_data['reason']}")
                
                # Adiciona advertência
                entry = {
                    "by": "site_admin",
                    "reason": action_data["reason"],
                    "ts": now_br().strftime("%d/%m/%Y %H:%M"),
                    "admin": action_data.get('admin', 'Site Admin')
                }
                data.setdefault("warns", {}).setdefault(str(member.id), []).append(entry)
                save_data_to_github(f"Warn via site: {member.display_name}")
                
                # Envia mensagem no canal de logs, se configurado
                logs_channel_id = data.get("config", {}).get("logs_channel")
                if logs_channel_id:
                    logs_channel = guild.get_channel(int(logs_channel_id))
                    if logs_channel:
                        await logs_channel.send(
                            f"⚠️ {member.mention} foi advertido por {action_data.get('admin', 'Site Admin')}.\n"
                            f"Motivo: {action_data['reason']}"
                        )
                        print(f"📝 Log enviado para #{logs_channel.name}")
                
                print(f"✅ Membro advertido: {member.display_name}")
                return True
                
            except ValueError as e:
                print(f"❌ ERRO DE CONVERSÃO: member_id inválido")
                return False
        
        else:
            print(f"❌ Tipo de ação desconhecida: {action_type}")
            return False
    
    except discord.Forbidden as e:
        print(f"❌ ERRO DE PERMISSÃO: {e}")
        print("   Verifique as permissões do bot no servidor!")
        return False
        
    except discord.HTTPException as e:
        print(f"❌ ERRO HTTP: {e}")
        return False
        
    except Exception as e:
        print(f"❌ Erro ao executar ação {action_type}: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        print(f"{'='*50}\n")

async def process_bot_actions_continuous():
    """Processa ações do site continuamente - VERSÃO CORRIGIDA"""
    global action_processor_running
    
    print("\n" + "="*60)
    print("🚀 PROCESSADOR DE AÇÕES DO SITE - INICIANDO")
    print("="*60)
    
    # Marca como rodando
    action_processor_running = True
    
    # Aguarda o bot ficar totalmente pronto
    if not bot.is_ready():
        print("⏳ Aguardando bot ficar pronto...")
        await bot.wait_until_ready()
        await asyncio.sleep(2)
    
    print(f"✅ Bot está pronto: {bot.user}")
    
    # Verifica a guild
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID else None
    if guild:
        print(f"🎯 Guild alvo: {guild.name} (ID: {guild.id})")
        print(f"   📍 Canais: {len(guild.text_channels)}")
        print(f"   👥 Membros: {len(guild.members)}")
    else:
        print(f"⚠️ AVISO: Guild alvo não encontrada! ID: {GUILD_ID}")
        print(f"   Guilds disponíveis: {[g.name for g in bot.guilds]}")
    
    print("="*60)
    print("🔄 Iniciando loop principal de processamento...")
    print("="*60)
    
    processed_count = 0
    error_count = 0
    last_status_time = time.time()
    
    try:
        while action_processor_running and not bot.is_closed():
            try:
                # Log de status a cada 30 segundos
                current_time = time.time()
                if current_time - last_status_time > 30:
                    queue_len = len(bot_actions_queue)
                    print(f"[ACTION PROCESSOR] Status: Fila={queue_len} | Processadas={processed_count} | Erros={error_count}")
                    last_status_time = current_time
                
                # Processa ações se houver
                if bot_actions_queue:
                    action = bot_actions_queue[0]
                    action_type = action['type']
                    print(f"\n[ACTION PROCESSOR] 🔄 Processando ação: {action_type}")
                    print(f"   📅 Na fila desde: {action.get('timestamp')}")
                    
                    try:
                        action = bot_actions_queue.pop(0)
                        success = await execute_bot_action_internal(action)
                        
                        if success:
                            processed_count += 1
                            print(f"[ACTION PROCESSOR] ✅ Ação '{action_type}' concluída! (Total: {processed_count})")
                        else:
                            error_count += 1
                            print(f"[ACTION PROCESSOR] ❌ Falha na ação '{action_type}'")
                            
                            # Tenta novamente (máximo 3 tentativas)
                            attempts = action.get('attempts', 0)
                            if attempts < 3:
                                action['attempts'] = attempts + 1
                                action['retry_time'] = datetime.now().isoformat()
                                bot_actions_queue.insert(0, action)
                                print(f"[ACTION PROCESSOR] 🔄 Tentando novamente ({action['attempts']}/3)")
                            else:
                                print(f"[ACTION PROCESSOR] 🗑️ Descarte após 3 tentativas falhas")
                    
                    except Exception as e:
                        error_count += 1
                        print(f"[ACTION PROCESSOR] 💥 ERRO CRÍTICO: {e}")
                        import traceback
                        traceback.print_exc()
                
                # Aguarda antes de verificar novamente
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                print("[ACTION PROCESSOR] ⏹️ Recebido sinal de cancelamento")
                break
                
            except Exception as e:
                print(f"[ACTION PROCESSOR] ⚠️ Erro no loop: {e}")
                await asyncio.sleep(5)
    
    except Exception as e:
        print(f"[ACTION PROCESSOR] 💥 ERRO FATAL: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        action_processor_running = False
        print("\n" + "="*60)
        print("⏹️ PROCESSADOR DE AÇÕES ENCERRADO")
        print(f"   📊 Estatísticas finais:")
        print(f"   ✅ Ações processadas: {processed_count}")
        print(f"   ❌ Erros: {error_count}")
        print(f"   📝 Ações restantes na fila: {len(bot_actions_queue)}")
        print("="*60)

def start_action_processor():
    """Inicia o processador de ações"""
    global action_processor_task, action_processor_running
    
    if action_processor_running:
        print("⚠️ Processador já está rodando")
        return False
    
    try:
        action_processor_task = bot.loop.create_task(process_bot_actions_continuous())
        print("✅ Processador de ações iniciado!")
        return True
    except Exception as e:
        print(f"❌ Erro ao iniciar processador: {e}")
        return False

def stop_action_processor():
    """Para o processador de ações"""
    global action_processor_task, action_processor_running
    
    if not action_processor_running or action_processor_task is None:
        return False
    
    try:
        action_processor_running = False
        if not action_processor_task.done():
            action_processor_task.cancel()
        print("✅ Processador de ações parado")
        return True
    except Exception as e:
        print(f"❌ Erro ao parar processador: {e}")
        return False

# ========================
# CLASSES DE BOTÕES
# ========================
class PersistentRoleButtonView(ui.View):
    def __init__(self, message_id: int, buttons_dict: dict):
        super().__init__(timeout=None)
        self.message_id = message_id
        for label, role_id in buttons_dict.items():
            self.add_item(PersistentRoleButton(label=label, role_id=role_id, message_id=message_id))

class PersistentRoleButton(ui.Button):
    def __init__(self, label: str, role_id: int, message_id: int):
        super().__init__(label=label, style=ButtonStyle.primary)
        self.role_id = role_id
        self.message_id = message_id

    async def callback(self, interaction: Interaction):
        guild = interaction.guild
        member = interaction.user
        role = guild.get_role(self.role_id)
        if not role:
            await interaction.response.send_message("Cargo não encontrado.", ephemeral=True)
            return

        if role in member.roles:
            await member.remove_roles(role, reason="Role button")
            await interaction.response.send_message(f"Você **removeu** o cargo {role.mention}.", ephemeral=True)
        else:
            await member.add_roles(role, reason="Role button")
            await interaction.response.send_message(f"Você **recebeu** o cargo {role.mention}.", ephemeral=True)

        add_log(f"role_button_click: user={member.id} role={role.id} message={self.message_id}")

# ========================
# ROTAS DO SITE
# ========================
@app.route("/", methods=["GET"])
def home():
    """Página inicial"""
    bot_status = "✅ Bot Online e Funcionando" if bot.is_ready() else "❌ Bot Offline"
    bot_class = "online" if bot.is_ready() else "offline"
    
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
            <div class="status {bot_class}">
                {bot_status}
            </div>
            
            <div class="features">
                <h3>✨ Funcionalidades:</h3>
                <ul>
                    <li>Sistema de XP e Níveis</li>
                    <li>Reaction Roles</li>
                    <li>Boas-vindas Personalizadas</li>
                    <li>Sistema de Moderação</li>
                    <li>Botões de Cargos</li>
                    <li>Painel Web de Controle</li>
                    <li>Sistema de Fila de Serviços</li>
                </ul>
            </div>
            
            {"<p>Faça login para configurar o bot pelo navegador</p><a href='/login' class='btn'>🔐 Login com Discord</a>" if 'user' not in session else f'<p>Olá, {session["user"].get("username", "Administrador")}!</p><a href="/dashboard" class="btn">🚀 Ir para o Painel</a><a href="/logout" class="btn">🚪 Sair</a>'}
            
            <p style="margin-top: 20px; color: #888; font-size: 0.9em;">
                Use <code>/comando</code> no Discord ou configure pelo site!
            </p>
        </div>
    </body>
    </html>
    '''

@app.route("/login")
def login():
    """Login com Discord"""
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
    """Callback do OAuth"""
    if not CLIENT_ID or not CLIENT_SECRET:
        return "Erro de configuração do servidor.", 500
    
    code = request.args.get('code')
    if not code:
        return "Erro: código não recebido", 400
    
    try:
        data_req = {
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': REDIRECT_URI,
            'scope': 'identify guilds'
        }
        
        r = requests.post('https://discord.com/api/oauth2/token', data=data_req)
        if r.status_code != 200:
            return f"Erro ao obter token: {r.text[:100]}", 400
        
        access_token = r.json()['access_token']
        
        user_r = requests.get('https://discord.com/api/users/@me', 
                            headers={'Authorization': f'Bearer {access_token}'})
        if user_r.status_code != 200:
            return "Erro ao obter informações", 400
        
        user_data = user_r.json()
        
        guilds_r = requests.get('https://discord.com/api/users/@me/guilds',
                              headers={'Authorization': f'Bearer {access_token}'})
        guilds = guilds_r.json() if guilds_r.status_code == 200 else []
        
        is_admin = False
        for guild in guilds:
            if str(guild['id']) == GUILD_ID and (guild['permissions'] & 0x8):
                is_admin = True
                break
        
        if not is_admin:
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
        
        session['user'] = {
            'id': user_data['id'],
            'username': user_data['username'],
            'avatar': user_data.get('avatar'),
            'is_admin': True
        }
        
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        return f"Erro interno: {str(e)}", 500

@app.route("/logout")
def logout():
    """Logout"""
    session.clear()
    return redirect(url_for('home'))

# ========================
# ROTAS DO SISTEMA DE FILA (PÚBLICAS)
# ========================

@app.route("/queue")
def queue_public():
    """Página pública da fila (para StreamElements)"""
    queue = get_queue_data()
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta http-equiv="refresh" content="30">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{escape_html(queue["name"])} - Lista de Espera</title>
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
            .status-open {{ background: #00b894; color: #fff; }}
            .status-closed {{ background: #d63031; color: #fff; }}
            .queue-info {{
                text-align: center;
                margin-top: 10px;
                font-size: 0.9rem;
                color: #bbb;
            }}
            .queue-list {{
                background: rgba(0,0,0,0.4);
                border-radius: 20px;
                overflow: hidden;
                backdrop-filter: blur(10px);
            }}
            .queue-header {{
                display: grid;
                grid-template-columns: 60px 1fr 1fr 80px;
                padding: 15px;
                background: rgba(255,255,255,0.1);
                font-weight: bold;
                border-bottom: 1px solid rgba(255,255,255,0.2);
            }}
            .queue-item {{
                display: grid;
                grid-template-columns: 60px 1fr 1fr 80px;
                padding: 12px 15px;
                border-bottom: 1px solid rgba(255,255,255,0.1);
                transition: background 0.3s;
            }}
            .queue-item:hover {{
                background: rgba(255,255,255,0.05);
            }}
            .position {{
                font-weight: bold;
                color: #ffd93d;
                font-size: 1.2rem;
            }}
            .username {{
                font-weight: 600;
            }}
            .service {{
                color: #a8e6cf;
                font-size: 0.9rem;
            }}
            .empty-message {{
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
                .queue-header {{
                    font-size: 0.8rem;
                }}
                .queue-item {{
                    font-size: 0.8rem;
                }}
                .position {{
                    font-size: 1rem;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>📋 {escape_html(queue["name"])}</h1>
                <div>
                    <span class="status status-{'open' if queue['settings']['open'] else 'closed'}">
                        {'🟢 ABERTA' if queue['settings']['open'] else '🔴 FECHADA'}
                    </span>
                </div>
                <div class="queue-info">
                    📊 {len(queue["entries"])} / {queue["settings"]["max_size"]} pessoas na fila
                </div>
            </div>
            
            <div class="queue-list">
                <div class="queue-header">
                    <span>#</span>
                    <span>Jogador</span>
                    <span>Serviço</span>
                    <span></span>
                </div>
                
                {''.join(f'''
                <div class="queue-item">
                    <span class="position">{entry["position"]}</span>
                    <span class="username">{escape_html(entry["username"])}</span>
                    <span class="service">{escape_html(entry["service"])}</span>
                    <span>⏳</span>
                </div>
                ''' for entry in queue["entries"]) or '<div class="empty-message">✨ Ninguém na fila no momento</div>'}
            </div>
            
            <div class="footer">
                Atualizado automaticamente a cada 30 segundos • {datetime.now().strftime("%d/%m/%Y %H:%M:%S")}
            </div>
        </div>
    </body>
    </html>
    '''

@app.route("/queue/embed")
def queue_embed():
    """Versão embed da fila para StreamElements overlay"""
    queue = get_queue_data()
    
    entries_html = ""
    for entry in queue["entries"][:10]:  # Limita a 10 para não ficar muito grande
        entries_html += f'''
        <div style="display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.1);">
            <span style="color: #ffd93d; font-weight: bold;">#{entry["position"]}</span>
            <span>{escape_html(entry["username"])}</span>
            <span style="color: #a8e6cf;">{escape_html(entry["service"])}</span>
        </div>
        '''
    
    if not queue["entries"]:
        entries_html = '<div style="text-align: center; padding: 20px;">✨ Fila vazia</div>'
    
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
            .queue-container {{
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
        <div class="queue-container">
            <div class="header">
                <strong>📋 {escape_html(queue["name"])}</strong>
                <span class="status" style="background: {'#00b894' if queue['settings']['open'] else '#d63031'}">
                    {'ABERTA' if queue['settings']['open'] else 'FECHADA'}
                </span>
            </div>
            {entries_html}
            <div style="text-align: center; margin-top: 8px; font-size: 10px; color: #888;">
                Total: {len(queue["entries"])} pessoas
            </div>
        </div>
    </body>
    </html>
    '''

@app.route("/queue/api")
def queue_api():
    """API pública da fila (JSON)"""
    queue = get_queue_data()
    return jsonify({
        "success": True,
        "queue": {
            "name": queue["name"],
            "open": queue["settings"]["open"],
            "max_size": queue["settings"]["max_size"],
            "count": len(queue["entries"]),
            "entries": [
                {
                    "position": e["position"],
                    "username": e["username"],
                    "service": e["service"],
                    "timestamp": e["timestamp"]
                }
                for e in queue["entries"]
            ]
        }
    })

# ========================
# APIs DE CONTROLE DA FILA (SITE)
# ========================

@app.route("/api/queue/add", methods=["POST"])
def api_queue_add():
    """Adiciona à fila"""
    req_data = request.json
    username = req_data.get("username", "").strip()
    service = req_data.get("service", "").strip()
    user_id = req_data.get("user_id")
    
    if not username or not service:
        return jsonify({"success": False, "message": "Nome e serviço são obrigatórios"})
    
    success, result = add_to_queue(username, service, user_id)
    
    if success:
        return jsonify({"success": True, "message": f"{username} adicionado à fila!", "entry": result})
    else:
        return jsonify({"success": False, "message": result})

@app.route("/api/queue/remove", methods=["POST"])
def api_queue_remove():
    """Remove da fila"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    entry_id = request.json.get("entry_id")
    if not entry_id:
        return jsonify({"success": False, "message": "ID da entrada é obrigatório"})
    
    success, result = remove_from_queue(entry_id)
    return jsonify({"success": success, "message": "Removido da fila" if success else "Entrada não encontrada"})

@app.route("/api/queue/move-up", methods=["POST"])
def api_queue_move_up():
    """Move para cima"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    entry_id = request.json.get("entry_id")
    success, result = move_up(entry_id)
    return jsonify({"success": success})

@app.route("/api/queue/move-down", methods=["POST"])
def api_queue_move_down():
    """Move para baixo"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    entry_id = request.json.get("entry_id")
    success, result = move_down(entry_id)
    return jsonify({"success": success})

@app.route("/api/queue/complete", methods=["POST"])
def api_queue_complete():
    """Completa serviço"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    entry_id = request.json.get("entry_id")
    success, result = complete_service(entry_id)
    return jsonify({"success": success})

@app.route("/api/queue/clear", methods=["POST"])
def api_queue_clear():
    """Limpa toda fila"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    clear_queue()
    return jsonify({"success": True, "message": "Fila limpa com sucesso"})

@app.route("/api/queue/settings", methods=["GET", "POST"])
def api_queue_settings():
    """Configurações da fila"""
    if request.method == "GET":
        queue = get_queue_data()
        return jsonify({
            "success": True,
            "settings": queue["settings"],
            "name": queue["name"]
        })
    
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    req_data = request.json
    
    if "open" in req_data:
        toggle_queue(req_data["open"])
    if "max_size" in req_data:
        set_max_size(int(req_data["max_size"]))
    if "name" in req_data:
        set_queue_name(req_data["name"])
    
    return jsonify({"success": True, "message": "Configurações salvas"})

@app.route("/api/queue/history")
def api_queue_history():
    """Histórico da fila"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    queue = get_queue_data()
    return jsonify({
        "success": True,
        "history": queue["history"][-50:]
    })

# ========================
# APIs DO SITE (EXISTENTES)
# ========================
@app.route("/api/config/welcome", methods=["POST"])
def api_config_welcome():
    """API para configurar boas-vindas"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    try:
        req_data = request.json
        config = data.setdefault("config", {})
        
        if 'message' in req_data:
            config["welcome_message"] = req_data['message']
        if 'channel_id' in req_data:
            config["welcome_channel"] = req_data['channel_id']
        if 'image_url' in req_data:
            config["welcome_background"] = req_data['image_url']
        
        success = save_data_to_github("Config boas-vindas via site")
        return jsonify({"success": success, "message": "Configuração salva!"})
        
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro: {str(e)}"}), 500

@app.route("/api/config/xp", methods=["POST"])
def api_config_xp():
    """API para configurar XP"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    try:
        req_data = request.json
        config = data.setdefault("config", {})
        
        if 'rate' in req_data:
            rate = int(req_data['rate'])
            if 1 <= rate <= 10:
                config["xp_rate"] = rate
        
        if 'channel_id' in req_data:
            config["levelup_channel"] = req_data['channel_id']
        
        success = save_data_to_github("Config XP via site")
        return jsonify({"success": success, "message": "Configuração de XP salva!"})
        
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro: {str(e)}"}), 500

@app.route("/api/level-roles", methods=["GET", "POST", "DELETE"])
def api_level_roles():
    """API para cargos por nível"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    try:
        if request.method == "GET":
            level_roles = data.get("level_roles", {})
            return jsonify({"success": True, "level_roles": level_roles})
        
        elif request.method == "POST":
            req_data = request.json
            level = str(req_data.get('level'))
            role_id = req_data.get('role_id')
            
            if not level or not role_id:
                return jsonify({"success": False, "message": "Nível e cargo são obrigatórios"})
            
            data.setdefault("level_roles", {})[level] = role_id
            save_data_to_github(f"Add level role {level}")
            return jsonify({"success": True, "message": f"Cargo definido para nível {level}"})
        
        elif request.method == "DELETE":
            level = request.args.get('level')
            if not level:
                return jsonify({"success": False, "message": "Nível é obrigatório"})
            
            if level in data.get("level_roles", {}):
                del data["level_roles"][level]
                save_data_to_github(f"Remove level role {level}")
                return jsonify({"success": True, "message": f"Cargo removido do nível {level}"})
            else:
                return jsonify({"success": False, "message": "Nível não encontrado"})
                
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro: {str(e)}"}), 500

@app.route("/api/guild/members")
def api_guild_members():
    """API para obter membros da guild"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    try:
        guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID and bot.is_ready() else None
        if not guild:
            return jsonify({"success": False, "message": "Guild não encontrada"})
        
        members = []
        for member in guild.members:
            if not member.bot:
                members.append({
                    "id": str(member.id),
                    "name": member.display_name,
                    "avatar": str(member.avatar.url) if member.avatar else None
                })
        
        return jsonify({"success": True, "members": members[:100]})
        
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/member/warns")
def api_member_warns():
    """API para ver advertências de um membro"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    member_id = request.args.get('member_id')
    if not member_id:
        return jsonify({"success": False, "message": "ID do membro é obrigatório"})
    
    warns = data.get("warns", {}).get(str(member_id), [])
    return jsonify({"success": True, "warns": warns})

@app.route("/api/command/warn", methods=["POST"])
def api_command_warn():
    """API para executar comando de advertência"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    try:
        req_data = request.json
        member_id = req_data.get('member_id')
        reason = req_data.get('reason', 'Sem motivo informado')
        
        if not member_id:
            return jsonify({"success": False, "message": "ID do membro é obrigatório"})
        
        success = execute_bot_action(
            "warn_member",
            member_id=member_id,
            reason=reason,
            admin=session['user']['username']
        )
        
        return jsonify({
            "success": success, 
            "message": f"✅ Membro será advertido em instantes!\nMotivo: {reason}"
        })
        
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro: {str(e)}"}), 500

@app.route("/api/command/clearwarns", methods=["POST"])
def api_command_clearwarns():
    """API para limpar advertências"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    try:
        req_data = request.json
        member_id = str(req_data.get('member_id'))
        
        if not member_id:
            return jsonify({"success": False, "message": "ID do membro é obrigatório"})
        
        if member_id in data.get("warns", {}):
            data["warns"].pop(member_id)
            save_data_to_github(f"Clear warns via site: {member_id}")
            return jsonify({"success": True, "message": "✅ Advertências removidas!"})
        else:
            return jsonify({"success": False, "message": "❌ Membro não tem advertências"})
            
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro: {str(e)}"}), 500

@app.route("/api/command/embed", methods=["POST"])
def api_command_embed():
    """API para criar embed com todas as opções"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    try:
        req_data = request.json
        channel_id = req_data.get('channel_id')
        title = req_data.get('title')
        body = req_data.get('body')
        color = req_data.get('color', '#5865F2')
        image_url = req_data.get('image_url')
        mention = req_data.get('mention')
        
        if not channel_id or not title or not body:
            return jsonify({"success": False, "message": "Preencha todos os campos obrigatórios (canal, título, corpo)"})
        
        success = execute_bot_action(
            "create_embed",
            channel_id=channel_id,
            title=title,
            body=body,
            color=color,
            image_url=image_url,
            mention=mention,
            admin=session['user']['username']
        )
        
        return jsonify({
            "success": success, 
            "message": f"✅ Embed '{title}' será criada em instantes!"
        })
        
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro: {str(e)}"}), 500

@app.route("/api/command/blocklinks", methods=["POST"])
def api_command_blocklinks():
    """API para bloquear links"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    try:
        req_data = request.json
        channel_id = req_data.get('channel_id')
        
        if not channel_id:
            return jsonify({"success": False, "message": "ID do canal é obrigatório"})
        
        blocked = data.setdefault("blocked_links_channels", [])
        
        if int(channel_id) in blocked:
            blocked.remove(int(channel_id))
            message = "✅ Links desbloqueados neste canal"
        else:
            blocked.append(int(channel_id))
            message = "✅ Links bloqueados neste canal"
        
        save_data_to_github("Toggle block links via site")
        
        return jsonify({"success": True, "message": message})
        
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro: {str(e)}"}), 500

@app.route("/api/reactionrole/create", methods=["POST"])
def api_reactionrole_create():
    """API para criar reaction role"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    try:
        req_data = request.json
        channel_id = req_data.get('channel_id')
        content = req_data.get('content')
        emoji_cargo = req_data.get('emoji_cargo')
        
        if not channel_id or not content or not emoji_cargo:
            return jsonify({"success": False, "message": "Preencha todos os campos"})
        
        success = execute_bot_action(
            "create_reaction_role",
            channel_id=channel_id,
            content=content,
            emoji_cargo=emoji_cargo,
            admin=session['user']['username']
        )
        
        return jsonify({
            "success": success,
            "message": "✅ Reaction role será criada em instantes!"
        })
        
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro: {str(e)}"}), 500

@app.route("/api/rolebuttons/create", methods=["POST"])
def api_rolebuttons_create():
    """API para criar botões de cargos"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    try:
        req_data = request.json
        channel_id = req_data.get('channel_id')
        content = req_data.get('content')
        roles = req_data.get('roles')
        
        if not channel_id or not content or not roles:
            return jsonify({"success": False, "message": "Preencha todos os campos"})
        
        success = execute_bot_action(
            "create_role_buttons",
            channel_id=channel_id,
            content=content,
            roles=roles,
            admin=session['user']['username']
        )
        
        return jsonify({
            "success": success,
            "message": "✅ Botões de cargo serão criados em instantes!"
        })
        
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro: {str(e)}"}), 500

@app.route("/api/command-channels")
def api_command_channels():
    """API para obter configuração de canais de comandos"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    command_channels = data.get("command_channels", {})
    return jsonify({"success": True, "command_channels": command_channels})

@app.route("/api/test/bot", methods=["GET"])
def api_test_bot():
    """API para testar conexão com o bot"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    bot_status = {
        "ready": bot.is_ready() if hasattr(bot, 'is_ready') else False,
        "user": str(bot.user) if hasattr(bot, 'user') else None,
        "guild_id": GUILD_ID,
        "guilds": []
    }
    
    if bot.is_ready():
        bot_status["guilds"] = [{"id": g.id, "name": g.name, "member_count": len(g.members)} for g in bot.guilds]
        
        if GUILD_ID:
            guild = bot.get_guild(int(GUILD_ID))
            if guild:
                bot_member = guild.get_member(bot.user.id)
                permissions = bot_member.guild_permissions if bot_member else None
                
                bot_status["target_guild"] = {
                    "name": guild.name,
                    "channels": [{"id": str(c.id), "name": c.name} for c in guild.text_channels],
                    "permissions": {
                        "send_messages": permissions.send_messages if permissions else False,
                        "embed_links": permissions.embed_links if permissions else False,
                        "manage_roles": permissions.manage_roles if permissions else False
                    }
                }
    
    return jsonify({
        "success": True,
        "bot": bot_status,
        "queue_length": len(bot_actions_queue),
        "timestamp": datetime.now().isoformat()
    })

# ========================
# APIs DE CONTROLE DO PROCESSADOR
# ========================
@app.route("/api/processor/start", methods=["POST"])
def api_processor_start():
    """Inicia o processador manualmente"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    try:
        success = start_action_processor()
        
        return jsonify({
            "success": success,
            "message": "✅ Processador iniciado!" if success else "❌ Falha ao iniciar processador",
            "queue_length": len(bot_actions_queue),
            "processor_running": action_processor_running
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro: {str(e)}"}), 500

@app.route("/api/processor/stop", methods=["POST"])
def api_processor_stop():
    """Para o processador manualmente"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    try:
        success = stop_action_processor()
        
        return jsonify({
            "success": success,
            "message": "✅ Processador parado!" if success else "❌ Falha ao parar processador",
            "queue_length": len(bot_actions_queue),
            "processor_running": action_processor_running
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro: {str(e)}"}), 500

@app.route("/api/processor/status", methods=["GET"])
def api_processor_status():
    """Verifica status do processador"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    return jsonify({
        "success": True,
        "processor_running": action_processor_running,
        "queue_length": len(bot_actions_queue),
        "queue": bot_actions_queue[:5],
        "bot_ready": bot.is_ready() if hasattr(bot, 'is_ready') else False,
        "has_processor_task": action_processor_task is not None,
        "task_done": action_processor_task.done() if action_processor_task else None,
        "task_running": not action_processor_task.done() if action_processor_task else None
    })

@app.route("/api/processor/process-one", methods=["POST"])
def api_processor_process_one():
    """Processa uma ação manualmente"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    try:
        if not bot_actions_queue:
            return jsonify({
                "success": False,
                "message": "❌ Nenhuma ação na fila para processar"
            })
        
        action = bot_actions_queue[0]
        action_type = action['type']
        
        async def process_action_directly():
            return await execute_bot_action_internal(action)
        
        try:
            future = asyncio.run_coroutine_threadsafe(process_action_directly(), bot.loop)
            success = future.result(timeout=15)
            
            if success:
                bot_actions_queue.pop(0)
                
            return jsonify({
                "success": success,
                "message": f"✅ Ação '{action_type}' processada com sucesso!" if success else f"❌ Falha ao processar ação '{action_type}'",
                "action_type": action_type,
                "queue_remaining": len(bot_actions_queue)
            })
            
        except asyncio.TimeoutError:
            return jsonify({
                "success": False,
                "message": "⏰ Timeout ao processar ação (15 segundos)"
            })
        except Exception as e:
            return jsonify({
                "success": False,
                "message": f"❌ Erro ao processar: {str(e)}"
            })
            
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro: {str(e)}"}), 500

@app.route("/api/debug/actions", methods=["GET"])
def api_debug_actions():
    """API para debug das ações"""
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    processing_active = action_processor_running
    
    return jsonify({
        "success": True,
        "queue_length": len(bot_actions_queue),
        "queue": bot_actions_queue[:5],
        "bot_ready": bot.is_ready() if hasattr(bot, 'is_ready') else False,
        "bot_user": str(bot.user) if hasattr(bot, 'user') else "None",
        "guild_id": GUILD_ID,
        "processing_active": processing_active,
        "processor_running": action_processor_running,
        "has_processor_task": action_processor_task is not None,
        "task_done": action_processor_task.done() if action_processor_task else None,
        "guilds": [g.name for g in bot.guilds] if hasattr(bot, 'guilds') else []
    })

# ========================
# AUTO PING (MANTER ATIVO)
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
    print(f"✅ Status: {'PRONTO' if bot.is_ready() else 'NÃO PRONTO'}")
    print(f"{'='*50}")
    
    print(f"🏠 GUILDS CONECTADAS ({len(bot.guilds)}):")
    for i, guild in enumerate(bot.guilds, 1):
        print(f"  {i}. {guild.name} (ID: {guild.id}) - Membros: {len(guild.members)}")
    print(f"{'='*50}")
    
    target_guild = None
    if GUILD_ID:
        target_guild = bot.get_guild(int(GUILD_ID))
        if target_guild:
            print(f"🎯 GUILD ALVO ENCONTRADA:")
            print(f"   Nome: {target_guild.name}")
            print(f"   ID: {target_guild.id}")
            print(f"   👥 Membros: {len(target_guild.members)}")
            print(f"   📝 Canais de texto: {len(target_guild.text_channels)}")
            print(f"   🎭 Cargos: {len(target_guild.roles)}")
        else:
            print(f"⚠️ AVISO CRÍTICO: Guild alvo não encontrada!")
            print(f"   GUILD_ID configurado: {GUILD_ID}")
            print(f"   Guilds disponíveis: {[g.id for g in bot.guilds]}")
    else:
        print(f"⚠️ AVISO: GUILD_ID não configurado no ambiente")
    
    print(f"{'='*50}")
    
    print("📂 Carregando dados do GitHub...")
    load_success = load_data_from_github()
    print(f"   {'✅ Dados carregados' if load_success else '⚠️ Usando dados locais'}")

    print("⚙️ Sincronizando comandos slash...")
    try:
        if GUILD_ID:
            gid = int(GUILD_ID)
            guild = discord.Object(id=gid)
            await tree.sync(guild=guild)
            print(f"   ✅ Comandos sincronizados no servidor {gid}")
        else:
            await tree.sync()
            print("   ✅ Comandos globais sincronizados")
    except Exception as e:
        print(f"   ❌ Erro ao sincronizar comandos: {e}")

    print("🔄 Restaurando botões persistentes...")
    role_buttons = data.get("role_buttons", {})
    if role_buttons:
        print(f"   📊 {len(role_buttons)} mensagens com botões para restaurar")
        restored = 0
        for msg_id_str, buttons_dict in role_buttons.items():
            try:
                msg_id = int(msg_id_str)
                message = None
                
                for guild in bot.guilds:
                    for channel in guild.text_channels:
                        try:
                            message = await channel.fetch_message(msg_id)
                            if message:
                                break
                        except discord.NotFound:
                            continue
                        except discord.Forbidden:
                            continue
                        except Exception:
                            continue
                    if message:
                        break
                
                if message:
                    view = PersistentRoleButtonView(msg_id, buttons_dict)
                    await message.edit(view=view)
                    restored += 1
                    print(f"   ✅ Botões restaurados para mensagem {msg_id}")
                else:
                    print(f"   ⚠️ Mensagem {msg_id} não encontrada (botões não restaurados)")
                    
            except Exception as e:
                print(f"   ❌ Erro ao restaurar botões para {msg_id_str}: {e}")
        
        print(f"   📊 {restored}/{len(role_buttons)} mensagens restauradas com sucesso")
    else:
        print("   ℹ️ Nenhum botão persistente para restaurar")

    print("\n" + "="*50)
    print("🚀 CONFIGURANDO SISTEMA DE AÇÕES DO SITE")
    print("="*50)
    
    await asyncio.sleep(3)
    
    try:
        start_action_processor()
        print("✅ Sistema de ações INICIADO com sucesso!")
        
    except Exception as e:
        print(f"❌ Erro ao iniciar sistema de ações: {e}")
        import traceback
        traceback.print_exc()
    
    print("="*50)
    
    print("🔍 Executando diagnóstico de conexão...")
    await check_bot_connection()
    
    print(f"{'='*50}")
    print(f"📊 ESTATÍSTICAS CARREGADAS:")
    print(f"   📈 Usuários com XP: {len(data.get('xp', {}))}")
    print(f"   ⚠️ Advertências: {sum(len(w) for w in data.get('warns', {}).values())}")
    print(f"   🎭 Reaction Roles: {len(data.get('reaction_roles', {}))}")
    print(f"   🔘 Botões de Cargo: {len(data.get('role_buttons', {}))}")
    print(f"   📝 Logs: {len(data.get('logs', []))}")
    print(f"   📋 Fila: {len(data.get('queue', {}).get('entries', []))} pessoas")
    print(f"{'='*50}")
    print(f"✨ BOT PRONTO PARA USO!")
    print(f"{'='*50}\n")
    
    add_log(f"Bot iniciado: {bot.user.name} ({bot.user.id}) em {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")

@bot.event
async def on_member_join(member: discord.Member):
    ch_id = data.get("config", {}).get("welcome_channel")
    channel = None
    if ch_id:
        channel = member.guild.get_channel(int(ch_id))
    if not channel:
        channel = discord.utils.get(member.guild.text_channels, name="boas-vindas")
    if not channel:
        return

    welcome_msg = data.get("config", {}).get("welcome_message", "Olá {member}, seja bem-vindo(a)!")
    welcome_msg = welcome_msg.replace("{member}", member.mention)

    background_path = data.get("config", {}).get("welcome_background", "")

    width, height = 900, 300
    img = Image.new("RGBA", (width, height), (0, 0, 0, 255))

    if background_path:
        try:
            response = requests.get(background_path)
            bg = Image.open(BytesIO(response.content)).convert("RGBA")
            bg = bg.resize((width, height))
            img.paste(bg, (0, 0))
        except Exception as e:
            print(f"Erro ao carregar imagem de fundo: {e}")

    overlay = Image.new("RGBA", (width, height), (50, 50, 50, 150))
    img = Image.alpha_composite(img, overlay)

    draw = ImageDraw.Draw(img)

    try:
        user_bytes = await member.avatar.read()
        user_avatar = Image.open(BytesIO(user_bytes)).convert("RGBA")

        avatar_size = 150
        border_size = 5
        upscale = 4
        big_size = (avatar_size + border_size * 2) * upscale

        user_avatar = user_avatar.resize((avatar_size * upscale, avatar_size * upscale))
        mask = Image.new("L", (avatar_size * upscale, avatar_size * upscale), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, avatar_size * upscale, avatar_size * upscale), fill=255)

        border_color = (200, 150, 255, 255)
        border = Image.new("RGBA", (big_size, big_size), (0, 0, 0, 0))
        draw_border = ImageDraw.Draw(border)
        draw_border.ellipse((0, 0, big_size, big_size), fill=border_color)

        border.paste(user_avatar, (border_size * upscale, border_size * upscale), mask)
        border = border.resize((avatar_size + border_size * 2, avatar_size + border_size * 2), Image.Resampling.LANCZOS)

        x = (width - border.width) // 2
        y = 30
        img.paste(border, (x, y), border)
    except Exception as e:
        print(f"Erro ao carregar avatar do usuário: {e}")

    try:
        font_b = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_s = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
    except:
        font_b = ImageFont.load_default()
        font_s = ImageFont.load_default()

    text_color = (200, 150, 255)
    shadow_color = (0, 0, 0, 180)

    text_name = member.display_name
    bbox_name = draw.textbbox((0, 0), text_name, font=font_b)
    text_w = bbox_name[2] - bbox_name[0]
    text_x = (width - text_w) // 2
    text_y = y + border.height + 10

    draw.text((text_x + 2, text_y + 2), text_name, font=font_b, fill=shadow_color)
    draw.text((text_x, text_y), text_name, font=font_b, fill=text_color)

    text_count = f"Membro #{len(member.guild.members)}"
    bbox_count = draw.textbbox((0, 0), text_count, font=font_s)
    text_w2 = bbox_count[2] - bbox_count[0]
    text_x2 = (width - text_w2) // 2
    text_y2 = text_y + 50

    draw.text((text_x2 + 1, text_y2 + 1), text_count, font=font_s, fill=shadow_color)
    draw.text((text_x2, text_y2), text_count, font=font_s, fill=text_color)

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    file = discord.File(buf, filename="welcome.png")

    await channel.send(content=welcome_msg, file=file)
    add_log(f"member_join: {member.id} - {member}")

# ========================
# REACTION ROLES
# ========================
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    try:
        msgmap = data.get("reaction_roles", {}).get(str(payload.message_id))
        if not msgmap:
            return

        role_id = None
        if payload.emoji.id and str(payload.emoji.id) in msgmap:
            role_id = msgmap[str(payload.emoji.id)]
        elif payload.emoji.id is not None and payload.emoji.name in msgmap:
            role_id = msgmap[payload.emoji.name]
        elif str(payload.emoji) in msgmap:
            role_id = msgmap[str(payload.emoji)]

        if not role_id:
            return

        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member:
            member = await guild.fetch_member(payload.user_id)
        role = guild.get_role(int(role_id))
        if member and role:
            await member.add_roles(role, reason="reaction role add")
            add_log(f"reaction add: user={member.id} role={role.id} msg={payload.message_id}")

    except Exception as e:
        print("on_raw_reaction_add error:", e)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    try:
        msgmap = data.get("reaction_roles", {}).get(str(payload.message_id))
        if not msgmap:
            return

        role_id = None
        if payload.emoji.id and str(payload.emoji.id) in msgmap:
            role_id = msgmap[str(payload.emoji.id)]
        elif payload.emoji.id is not None and payload.emoji.name in msgmap:
            role_id = msgmap[payload.emoji.name]
        elif str(payload.emoji) in msgmap:
            role_id = msgmap[str(payload.emoji)]

        if not role_id:
            return

        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member:
            member = await guild.fetch_member(payload.user_id)
        role = guild.get_role(int(role_id))
        if member and role:
            await member.remove_roles(role, reason="reaction role remove")
            add_log(f"reaction remove: user={member.id} role={role.id} msg={payload.message_id}")

    except Exception as e:
        print("on_raw_reaction_remove error:", e)

# ========================
# WARN HELPER
# ========================
async def add_warn(member: discord.Member, reason=""):
    uid = str(member.id)
    entry = {
        "by": bot.user.id,
        "reason": reason,
        "ts": now_br().strftime("%d/%m/%Y %H:%M")
    }
    data.setdefault("warns", {}).setdefault(uid, []).append(entry)
    save_data_to_github("Auto-warn")
    add_log(f"warn: user={uid} by=bot reason={reason}")

# ========================
# ON MESSAGE
# ========================
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    uid = str(message.author.id)
    content = message.content.strip()
    delete_message = False

    mudae_commands = [
        "$w", "$wa", "$wg", "$h", "$ha", "$hg",
        "$W", "$WA", "$WG", "$H", "$HA", "$HG",
        "$tu", "$TU", "$dk", "$mmi", "$vote", "$rolls", "$k", "$mu"
    ]
    if any(content.lower().startswith(cmd) for cmd in mudae_commands):
        await bot.process_commands(message)
        return

    ignored_roles = {"Administrador", "Moderador"}
    member_roles = {r.name for r in message.author.roles}
    is_staff = any(role in ignored_roles for role in member_roles)

    has_media = False
    if message.attachments:
        has_media = True
    if message.stickers:
        has_media = True
    gif_domains = ["tenor.com", "media.tenor.com", "giphy.com", "imgur.com"]
    if any(domain in content.lower() for domain in gif_domains):
        has_media = True

    if has_media:
        await bot.process_commands(message)
        return

    blocked_channels = data.get("blocked_links_channels", [])
    if message.channel.id in blocked_channels:
        import re
        url_pattern = r"https?://[^\s]+"
        if re.search(url_pattern, content):
            if not is_staff:
                try:
                    await message.delete()
                except discord.Forbidden:
                    pass
                await message.channel.send(f"⚠️ {message.author.mention}, links não são permitidos aqui!")
                await add_warn(message.author, reason="Enviou link em canal bloqueado")
                return

    user_msgs = data.setdefault("last_messages_content", {}).setdefault(uid, [])
    if len(user_msgs) >= 5:
        user_msgs.pop(0)

    if user_msgs and content == user_msgs[-1]:
        if not is_staff:
            delete_message = True
            try:
                await message.delete()
            except discord.Forbidden:
                pass
            await message.channel.send(f"⚠️ {message.author.mention}, evite enviar mensagens repetidas!")
            await add_warn(message.author, reason="Spam detectado")
            return
    else:
        user_msgs.append(content)
    data["last_messages_content"][uid] = user_msgs

    if len(content) > 5 and content.isupper():
        if not is_staff:
            delete_message = True
            try:
                await message.delete()
            except discord.Forbidden:
                pass
            await message.channel.send(f"⚠️ {message.author.mention}, evite escrever tudo em maiúsculas!")
            await add_warn(message.author, reason="Uso excessivo de maiúsculas")
            return

    if not delete_message:
        data.setdefault("xp", {})
        data.setdefault("level", {})

        xp_rate = data.get("config", {}).get("xp_rate", 3)
        xp_gain = max(1, xp_for_message() // xp_rate)
        data["xp"][uid] = data["xp"].get(uid, 0) + xp_gain

        xp_now = data["xp"][uid]
        lvl_now = xp_to_level(xp_now)
        prev_lvl = data["level"].get(uid, 1)

        if lvl_now > prev_lvl:
            data["level"][uid] = lvl_now

            levelup_channel_id = data.get("config", {}).get("levelup_channel")
            channel_to_send = None

            if levelup_channel_id:
                channel_to_send = message.guild.get_channel(int(levelup_channel_id))
            if not channel_to_send:
                channel_to_send = message.channel

            try:
                await channel_to_send.send(f"🎉 {message.author.mention} subiu para o nível **{lvl_now}**!")
            except Exception as e:
                print(f"Erro ao enviar mensagem de level up: {e}")

            level_roles = data.get("level_roles", {})
            role_id = level_roles.get(str(lvl_now))
            if role_id:
                role = message.guild.get_role(int(role_id))
                if role:
                    try:
                        await message.author.add_roles(role, reason=f"Alcançou nível {lvl_now}")
                    except discord.Forbidden:
                        await channel_to_send.send(
                            f"⚠️ Não consegui dar o cargo {role.mention}, verifique minhas permissões."
                        )

            add_log(f"level_up: user={uid} level={lvl_now}")

    try:
        save_data_to_github("XP update")
    except Exception as e:
        print(f"Erro ao salvar XP: {e}")

    await bot.process_commands(message)

# ========================
# SLASH COMMANDS
# ========================
def is_admin_check(interaction: discord.Interaction) -> bool:
    try:
        perms = interaction.user.guild_permissions
        return perms.administrator or perms.manage_guild or perms.manage_roles
    except Exception:
        return False
        
def is_command_allowed(interaction: discord.Interaction, command_name: str) -> bool:
    allowed = data.get("command_channels", {}).get(command_name, [])
    if not allowed:
        return True
    return interaction.channel_id in allowed

#/cargo_xp
@tree.command(name="cargo_xp", description="Define um cargo para ser atribuído ao atingir certo nível (admin)")
@app_commands.describe(level="Nível em que o cargo será dado", role="Cargo a ser atribuído")
async def set_level_role(interaction: discord.Interaction, level: int, role: discord.Role):
    if not is_admin_check(interaction):
        await interaction.response.send_message("❌ Você não tem permissão.", ephemeral=True)
        return

    if level < 1:
        await interaction.response.send_message("⚠️ O nível deve ser maior que 0.", ephemeral=True)
        return

    data.setdefault("level_roles", {})[str(level)] = str(role.id)
    save_data_to_github("Set level role")

    await interaction.response.send_message(
        f"✅ Cargo {role.mention} será atribuído ao atingir o **nível {level}**.",
        ephemeral=False
    )

#/xp_rate
@tree.command(name="xp_rate", description="Define a taxa de ganho de XP (admin)")
@app_commands.describe(rate="Taxa de XP — valores menores tornam o up mais lento")
async def set_xp_rate(interaction: discord.Interaction, rate: int):
    if not is_admin_check(interaction):
        await interaction.response.send_message("❌ Você não tem permissão para usar este comando.", ephemeral=True)
        return

    if rate < 1:
        await interaction.response.send_message("⚠️ O valor mínimo é 1.", ephemeral=True)
        return

    data.setdefault("config", {})["xp_rate"] = rate
    save_data_to_github("Set XP rate")

    await interaction.response.send_message(f"✅ Taxa de XP ajustada para **x{rate}**. Agora é **{rate}x mais difícil** subir de nível.", ephemeral=False)

#/mensagem_personalizada
@tree.command(name="mensagem_personalizada", description="Cria uma mensagem personalizada (admin)")
@app_commands.describe(
    canal="Canal onde a mensagem será enviada",
    titulo="Título da mensagem",
    corpo="Texto interno (use \n para quebra de linha)",
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
    if not is_admin_check(interaction):
        await interaction.response.send_message("❌ Você não tem permissão para usar este comando.", ephemeral=True)
        return

    try:
        color = discord.Color(int(cor.replace("#", ""), 16))
    except:
        color = discord.Color.blurple()

    formatted_text = corpo.replace("\\n", "\n").strip()
    formatted_text = formatted_text.replace("- ", "● ").replace("• ", "● ")
    lines = formatted_text.split("\n")
    formatted_text = "\n\n".join(line.strip() for line in lines if line.strip())

    embed = discord.Embed(
        title=f"**{titulo}**",
        description=formatted_text,
        color=color
    )

    if imagem:
        embed.set_image(url=imagem)

    mention_text = mencionar if mencionar in ["@everyone", "@here"] else ""
    await canal.send(content=mention_text, embed=embed)
    await interaction.response.send_message(f"✅ Embed enviada para {canal.mention}.", ephemeral=True)

#/selecionar_imagem_boas-vindas
@tree.command(name="selecionar_imagem_boas-vindas", description="Define ou remove a imagem de fundo da mensagem de boas-vindas (admin)")
@app_commands.describe(url="URL da imagem que será usada no fundo (deixe vazio para remover)")
async def slash_setwelcomeimage(interaction: discord.Interaction, url: str = None):
    if not is_admin_check(interaction):
        await interaction.response.send_message("❌ Você não tem permissão para usar este comando.", ephemeral=True)
        return

    config = data.setdefault("config", {})

    if not url:
        if "welcome_background" in config:
            del config["welcome_background"]
            save_data_to_github("Unset welcome background")
            await interaction.response.send_message("🧹 Imagem de fundo personalizada removida. Voltará a usar a padrão.", ephemeral=False)
        else:
            await interaction.response.send_message("ℹ️ Nenhuma imagem personalizada estava configurada.", ephemeral=True)
        return

    if not (url.startswith("http://") or url.startswith("https://")):
        await interaction.response.send_message("❌ Forneça uma URL válida começando com http:// ou https://", ephemeral=True)
        return

    config["welcome_background"] = url
    save_data_to_github("Set welcome background")
    await interaction.response.send_message(f"✅ Imagem de fundo definida com sucesso!\n{url}", ephemeral=False)

#/definir_canal_comando
@tree.command(name="definir_canal_comando", description="Define canais onde um comando pode ser usado (admin)")
@app_commands.describe(
    command="Nome do comando (ex: rank, top, aviso)",
    channel="Canal de texto para permitir o comando"
)
async def slash_setcommandchannel(interaction: discord.Interaction, command: str, channel: discord.TextChannel):
    if not is_admin_check(interaction):
        await interaction.response.send_message("❌ Você não tem permissão para usar este comando.", ephemeral=True)
        return

    cmd_channels = data.setdefault("command_channels", {})
    channels = cmd_channels.setdefault(command.lower(), [])

    if channel.id in channels:
        channels.remove(channel.id)
        msg = f"❌ O canal {channel.mention} **foi removido** da lista do comando `{command}`."
    else:
        channels.append(channel.id)
        msg = f"✅ O canal {channel.mention} **foi adicionado** para o comando `{command}`."

    save_data_to_github(f"Set command channel for {command}")
    await interaction.response.send_message(msg, ephemeral=False)

#/criar_reação_com_botao
@tree.command(name="criar_reação_com_botao", description="Cria uma mensagem com botões de cargos")
@app_commands.describe(
    channel="Canal para enviar a mensagem",
    content="Texto da mensagem",
    roles="Botão:Cargo separados por vírgula (ex: Aceitar:Regra,VIP:VIP)"
)
async def create_role_buttons(interaction: Interaction, channel: discord.TextChannel, content: str, roles: str):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return

    buttons_dict = {}
    for pair in [r.strip() for r in roles.split(",")]:
        try:
            button_name, role_name = pair.split(":")
        except ValueError:
            await interaction.response.send_message(f"Formato inválido: `{pair}`. Use Botão:Cargo", ephemeral=True)
            return
        
        role = discord.utils.get(interaction.guild.roles, name=role_name.strip())
        if not role:
            await interaction.response.send_message(f"Cargo `{role_name}` não encontrado.", ephemeral=True)
            return
        
        buttons_dict[button_name.strip()] = role.id

    view = PersistentRoleButtonView(0, buttons_dict)
    sent = await channel.send(content=content, view=view)

    view.message_id = sent.id
    for item in view.children:
        if isinstance(item, PersistentRoleButton):
            item.message_id = sent.id

    data.setdefault("role_buttons", {})[str(sent.id)] = buttons_dict
    save_data_to_github("Create role buttons")

    await interaction.response.send_message(f"Mensagem criada em {channel.mention} com {len(buttons_dict)} botões.", ephemeral=True)

#/bloquear_links
@tree.command(name="bloquear_links", description="Bloqueia ou desbloqueia links em um canal (admin)")
@app_commands.describe(channel="Canal para bloquear/desbloqueiar links")
async def block_links(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return

    data.setdefault("blocked_links_channels", [])
    
    if channel.id in data["blocked_links_channels"]:
        data["blocked_links_channels"].remove(channel.id)
        save_data_to_github("Unblock links channel")
        await interaction.response.send_message(f"✅ Links desbloqueados no canal {channel.mention}.")
    else:
        data["blocked_links_channels"].append(channel.id)
        save_data_to_github("Block links channel")
        await interaction.response.send_message(f"✅ Links bloqueados no canal {channel.mention}.")

#/perfil
@tree.command(name="perfil", description="mostra o seu perfil")
@app_commands.describe(member="Membro a ver o rank (opcional)")
async def slash_rank(interaction: discord.Interaction, member: discord.Member = None):
    if not is_command_allowed(interaction, "rank"):
        await interaction.response.send_message("❌ Este comando só pode ser usado em canais autorizados.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    target = member or interaction.user
    uid = str(target.id)
    xp = data.get("xp", {}).get(uid, 0)
    lvl = data.get("level", {}).get(uid, xp_to_level(xp))

    ranking = sorted(data.get("xp", {}).items(), key=lambda t: t[1], reverse=True)
    pos = next((i+1 for i, (u, _) in enumerate(ranking) if u == uid), len(ranking))

    width, height = 900, 200
    img = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)

    try:
        font_b = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
        font_s = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
    except:
        font_b = ImageFont.load_default()
        font_s = ImageFont.load_default()

    try:
        avatar_bytes = await target.avatar.read()
        avatar = Image.open(BytesIO(avatar_bytes)).convert("RGBA")
        avatar = avatar.resize((120, 120))
        mask = Image.new("L", (120, 120), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, 120, 120), fill=255)
        img.paste(avatar, (20, 40), mask)
    except Exception as e:
        print("Erro avatar:", e)

    draw.text((160, 50), target.display_name, font=font_b, fill=(0, 255, 255))
    draw.text((width - 220, 40), f"CLASSIFICAÇÃO #{pos}", font=font_s, fill=(0, 255, 255))
    draw.text((width - 220, 80), f"NÍVEL {lvl}", font=font_s, fill=(255, 0, 255))

    next_xp = 100 + lvl*50
    cur = xp % next_xp
    bar_total_w, bar_h = 560, 36
    x0, y0 = 160, 140
    radius = bar_h // 2

    draw.rounded_rectangle([x0, y0, x0+bar_total_w, y0+bar_h], radius=radius, fill=(50, 50, 50))
    
    fill_w = int(bar_total_w * min(1.0, cur / next_xp))
    if fill_w > 0:
        filled_bar = Image.new("RGBA", (fill_w, bar_h), (0,0,0,0))
        fill_draw = ImageDraw.Draw(filled_bar)
        fill_draw.rounded_rectangle([0, 0, fill_w, bar_h], radius=radius, fill=(0, 200, 255))
        img.paste(filled_bar, (x0, y0), filled_bar)

    xp_text = f"{cur} / {next_xp} XP"
    bbox = draw.textbbox((0, 0), xp_text, font=font_s)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    text_x = x0 + (bar_total_w - text_w) // 2
    text_y = y0 + (bar_h - text_h) // 2
    draw.text((text_x, text_y), xp_text, font=font_s, fill=(255, 255, 255))

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    file = discord.File(buf, filename="rank.png")
    await interaction.followup.send(file=file)

#/definir_boas-vindas
@tree.command(name="definir_boas-vindas", description="Define a mensagem de boas-vindas (admin)")
@app_commands.describe(message="Mensagem (use {member} para mencionar)")
async def slash_setwelcome(interaction: discord.Interaction, message: str):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return

    data.setdefault("config", {})["welcome_message"] = message
    save_data_to_github("Set welcome message")
    await interaction.response.send_message(f"Mensagem de boas-vindas definida!\n{message}")

#/rank
@tree.command(name="rank", description="Mostra top 10 de XP")
async def slash_top(interaction: discord.Interaction):
    if not is_command_allowed(interaction, "top"):
        await interaction.response.send_message("❌ Este comando só pode ser usado em canais autorizados.", ephemeral=True)
        return
    await interaction.response.defer()
    ranking = sorted(data.get("xp", {}).items(), key=lambda t: t[1], reverse=True)[:10]
    lines = []
    for i, (uid, xp) in enumerate(ranking, 1):
        user = interaction.guild.get_member(int(uid))
        name = user.display_name if user else f"Usuário {uid}"
        lines.append(f"{i}. {name} — {xp} XP")
    text = "\n".join(lines) if lines else "Sem dados ainda."
    await interaction.followup.send(f"🏆 **Top 10 XP**\n{text}")

#/advertir
@tree.command(name="advertir", description="Advertir um membro (admin)")
@app_commands.describe(member="Membro a ser advertido", reason="Motivo da advertência")
async def slash_warn(interaction: discord.Interaction, member: discord.Member, reason: str = "Sem motivo informado"):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão para usar este comando.", ephemeral=True)
        return
    uid = str(member.id)
    entry = {
        "by": interaction.user.id,
        "reason": reason,
        "ts": datetime.utcnow().strftime("%d/%m/%Y %H:%M")
    }
    data.setdefault("warns", {}).setdefault(uid, []).append(entry)
    save_data_to_github("New warn")
    add_log(f"warn: user={uid} by={interaction.user.id} reason={reason}")
    await interaction.response.send_message(f"⚠️ {member.mention} advertido.\nMotivo: {reason}")

#/lista_de_advertência
@tree.command(name="lista_de_advertência", description="Mostra advertências de um membro")
@app_commands.describe(member="Membro (opcional)")
async def slash_warns(interaction: discord.Interaction, member: discord.Member = None):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão para usar este comando.", ephemeral=True)
        return
    target = member or interaction.user
    arr = data.get("warns", {}).get(str(target.id), [])
    if not arr:
        await interaction.response.send_message(f"{target.mention} não tem advertências.", ephemeral=False)
        return
    text = "\n".join([f"- {w['reason']} (por <@{w['by']}>) em {w['ts']}" for w in arr])
    await interaction.response.send_message(f"⚠️ Advertências de {target.mention}:\n{text}")

#/savedata
@tree.command(name="savedata", description="Força salvar dados no GitHub (admin)")
async def slash_savedata(interaction: discord.Interaction):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return
    ok = save_data_to_github("Manual save via /savedata")
    await interaction.response.send_message("Dados salvos no GitHub." if ok else "Falha ao salvar (veja logs).")

#/definir_canal_boas-vindas
@tree.command(name="definir_canal_boas-vindas", description="Define canal de boas-vindas para o bot (admin)")
@app_commands.describe(channel="Canal de texto")
async def slash_setwelcome_channel(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return
    if channel is None:
        data.setdefault("config", {})["welcome_channel"] = None
        save_data_to_github("Unset welcome channel")
        await interaction.response.send_message("Canal de boas-vindas removido.")
    else:
        data.setdefault("config", {})["welcome_channel"] = str(channel.id)
        save_data_to_github("Set welcome channel")
        await interaction.response.send_message(f"Canal de boas-vindas definido: {channel.mention}")

#/canal_xp
@tree.command(name="canal_xp", description="Define o canal onde serão enviadas as mensagens de level up (admin)")
@app_commands.describe(channel="Canal onde o bot vai enviar as mensagens de level up")
async def set_levelup_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return

    data.setdefault("config", {})["levelup_channel"] = channel.id
    save_data_to_github("Set level up channel")

    await interaction.response.send_message(f"✅ Canal de level up definido para {channel.mention}.", ephemeral=False)

# REACTION ROLES GROUP
reactionrole_group = app_commands.Group(name="reajir_com_emoji", description="Gerenciar reaction roles (admin)")

@reactionrole_group.command(name="criar", description="Cria mensagem com reação e mapeia para um cargo (admin)")
@app_commands.describe(channel="Canal para enviar a mensagem", content="Conteúdo da mensagem", emoji="Emoji (custom <:_name_:id> ou unicode)", role="Cargo a ser atribuído")
async def rr_create(interaction: discord.Interaction, channel: discord.TextChannel, content: str, emoji: str, role: discord.Role):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=False)
    
    parsed = parse_emoji_str(emoji, guild=interaction.guild)
    
    try:
        sent = await channel.send(content)
    except Exception as e:
        await interaction.followup.send(f"Falha ao enviar mensagem: {e}")
        return
    
    try:
        if isinstance(parsed, discord.Emoji) or isinstance(parsed, discord.PartialEmoji):
            await sent.add_reaction(parsed)
            key = str(parsed.id)
        else:
            await sent.add_reaction(parsed)
            key = str(parsed)
    except Exception as e:
        await interaction.followup.send(f"Falha ao reagir com o emoji: {e}")
        return
    
    data.setdefault("reaction_roles", {}).setdefault(str(sent.id), {})[key] = str(role.id)
    save_data_to_github("reactionrole create")
    add_log(f"reactionrole created msg={sent.id} emoji={key} role={role.id}")
    await interaction.followup.send(f"Mensagem criada em {channel.mention} com ID `{sent.id}`. Reaja para receber o cargo {role.mention}.")
    
@reactionrole_group.command(name="multi", description="Adiciona vários emojis e cargos a uma mesma mensagem (admin)")
@app_commands.describe(
    message_id="ID da mensagem existente para adicionar as reações",
    emoji_cargo="Lista de emoji:cargo separados por vírgula."
)
async def rr_multi(interaction: discord.Interaction, message_id: str, emoji_cargo: str):
    if not is_admin_check(interaction):
        await interaction.response.send_message("❌ Você não tem permissão.", ephemeral=True)
        return

    guild = interaction.guild
    try:
        msg = await guild.get_channel(interaction.channel_id).fetch_message(int(message_id))
    except Exception:
        await interaction.response.send_message("❌ Mensagem não encontrada. Verifique o ID.", ephemeral=True)
        return

    pairs = [x.strip() for x in emoji_cargo.split(",") if ":" in x]
    if not pairs:
        await interaction.response.send_message("❌ Formato inválido. Use emoji:cargo separados por vírgula.", ephemeral=True)
        return

    data.setdefault("reaction_roles", {}).setdefault(str(msg.id), {})

    added = []
    for pair in pairs:
        emoji_str, role_name = pair.split(":", 1)
        emoji_str, role_name = emoji_str.strip(), role_name.strip()

        role = discord.utils.get(guild.roles, name=role_name)
        if not role:
            await interaction.followup.send(f"⚠️ Cargo `{role_name}` não encontrado.")
            continue

        parsed = parse_emoji_str(emoji_str, guild)
        if not parsed:
            await interaction.followup.send(f"⚠️ Emoji `{emoji_str}` inválido.")
            continue

        try:
            await msg.add_reaction(parsed)
            key = str(parsed.id) if isinstance(parsed, (discord.Emoji, discord.PartialEmoji)) else str(parsed)
            data["reaction_roles"][str(msg.id)][key] = str(role.id)
            added.append(f"{emoji_str} → {role.name}")
        except Exception as e:
            await interaction.followup.send(f"Erro ao adicionar {emoji_str}: {e}")

    save_data_to_github("ReactionRole multi")
    if added:
        await interaction.response.send_message(f"✅ Adicionados:\n" + "\n".join(added))
    else:
        await interaction.response.send_message("Nenhum emoji/cargo válido foi adicionado.")

@reactionrole_group.command(name="remover", description="Remove uma emoji com reação de uma mensagem (admin)")
@app_commands.describe(message_id="ID da mensagem", emoji="Emoji usado quando criado")
async def rr_remove(interaction: discord.Interaction, message_id: str, emoji: str):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return
    mapping = data.get("reaction_roles", {}).get(str(message_id), {})
    if not mapping:
        await interaction.response.send_message("Nenhum mapeamento encontrado para essa mensagem.", ephemeral=True)
        return
    
    parsed = parse_emoji_str(emoji, guild=interaction.guild)
    key_candidates = [str(parsed)]
    if isinstance(parsed, (discord.Emoji, discord.PartialEmoji)):
        key_candidates.append(str(parsed.id))
        if parsed.name:
            key_candidates.append(parsed.name)
    
    found = None
    for k in key_candidates:
        if k in mapping:
            found = k
            break
    
    if not found:
        await interaction.response.send_message("Emoji não encontrado no mapeamento da mensagem.", ephemeral=True)
        return
    
    del mapping[found]
    if not mapping:
        data["reaction_roles"].pop(str(message_id), None)
    
    save_data_to_github("reactionrole remove")
    add_log(f"reactionrole removed msg={message_id} emoji={found}")
    await interaction.response.send_message("Removido com sucesso.", ephemeral=False)

@reactionrole_group.command(name="lista", description="Lista de reação de emoji configuradas")
async def rr_list(interaction: discord.Interaction):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return
    rr = data.get("reaction_roles", {})
    if not rr:
        await interaction.response.send_message("Nenhuma reação com emoji configurada.", ephemeral=True)
        return
    
    lines = []
    for msgid, mapping in rr.items():
        parts = []
        for ekey, rid in mapping.items():
            parts.append(f"{ekey}→<@&{rid}>")
        lines.append(f"Msg `{msgid}`: " + ", ".join(parts))
    
    content = "\n".join(lines)
    if len(content) > 1900:
        await interaction.response.send_message("Resultado muito grande, enviando arquivo...", ephemeral=True)
        await interaction.followup.send(file=discord.File(BytesIO(content.encode()), filename="reactionroles.txt"))
    else:
        await interaction.response.send_message(f"Reaction roles:\n{content}", ephemeral=False)

tree.add_command(reactionrole_group)

# ========================
# DASHBOARD (VERSÃO COMPLETA COM ABA DE FILA)
# ========================
@app.route("/dashboard")
def dashboard():
    """Dashboard principal"""
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user = session['user']
    
    config = data.get("config", {})
    welcome_msg = config.get("welcome_message", "Olá {member}, seja bem-vindo(a)!")
    xp_rate = config.get("xp_rate", 3)
    welcome_bg = config.get("welcome_background", "")
    welcome_chan = config.get("welcome_channel", "")
    levelup_chan = config.get("levelup_channel", "")
    
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID and bot.is_ready() else None
    channels = []
    roles = []
    
    if guild:
        channels = [{"id": str(c.id), "name": c.name} for c in guild.text_channels]
        roles = [{"id": str(r.id), "name": r.name} for r in guild.roles if r.name != "@everyone"]
    
    channels_json = json.dumps(channels, ensure_ascii=False)
    roles_json = json.dumps(roles, ensure_ascii=False)
    
    queue = get_queue_data()
    
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
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: var(--darker);
                color: var(--light);
            }}
            header {{
                background: var(--dark);
                box-shadow: 0 2px 10px rgba(0,0,0,0.3);
                padding: 1rem 2rem;
                border-bottom: 1px solid var(--gray);
            }}
            .header-content {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                max-width: 1200px;
                margin: 0 auto;
            }}
            h1 {{
                color: var(--primary);
                text-shadow: 0 2px 4px rgba(0,0,0,0.5);
            }}
            .user-info {{
                display: flex;
                align-items: center;
                gap: 1rem;
            }}
            .avatar {{
                width: 40px;
                height: 40px;
                border-radius: 50%;
                border: 2px solid var(--primary);
            }}
            .btn {{
                padding: 0.5rem 1rem;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                font-weight: 600;
                text-decoration: none;
                display: inline-block;
                transition: all 0.2s;
            }}
            .btn-primary {{ background: var(--primary); color: white; }}
            .btn-primary:hover {{ background: var(--primary-dark); }}
            .btn-success {{ background: var(--success); color: white; }}
            .btn-danger {{ background: var(--danger); color: white; }}
            .btn-warning {{ background: var(--warning); color: white; }}
            
            .container {{
                max-width: 1200px;
                margin: 2rem auto;
                padding: 0 1rem;
            }}
            
            .tab-nav {{
                display: flex;
                gap: 0.5rem;
                margin-bottom: 1rem;
                border-bottom: 2px solid var(--gray);
                padding-bottom: 0.5rem;
                flex-wrap: wrap;
            }}
            .tab-btn {{
                padding: 0.75rem 1.5rem;
                background: var(--gray);
                border: none;
                border-radius: 5px 5px 0 0;
                cursor: pointer;
                font-weight: 600;
                color: var(--light);
                transition: all 0.2s;
            }}
            .tab-btn:hover {{
                background: var(--gray-light);
            }}
            .tab-btn.active {{
                background: var(--primary);
                color: white;
            }}
            .tab {{
                display: none;
                animation: fadeIn 0.3s;
            }}
            .tab.active {{ display: block; }}
            
            @keyframes fadeIn {{
                from {{ opacity: 0; transform: translateY(10px); }}
                to {{ opacity: 1; transform: translateY(0); }}
            }}
            
            .card {{
                background: var(--dark);
                border-radius: 10px;
                padding: 1.5rem;
                margin: 1rem 0;
                box-shadow: 0 2px 5px rgba(0,0,0,0.3);
                border: 1px solid var(--gray);
            }}
            .stats-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 1rem;
                margin: 1rem 0;
            }}
            .stat-card {{
                background: linear-gradient(135deg, var(--primary), var(--primary-dark));
                color: white;
                padding: 1.5rem;
                border-radius: 10px;
                text-align: center;
                box-shadow: 0 4px 6px rgba(0,0,0,0.2);
            }}
            .stat-card h3 {{
                font-size: 2rem;
                margin-bottom: 0.5rem;
            }}
            
            .form-group {{
                margin-bottom: 1.5rem;
            }}
            label {{
                display: block;
                margin-bottom: 0.5rem;
                font-weight: 600;
                color: var(--primary);
            }}
            .form-control {{
                width: 100%;
                padding: 0.75rem;
                background: var(--darker);
                border: 1px solid var(--gray);
                border-radius: 5px;
                font-size: 1rem;
                color: var(--light);
                transition: all 0.2s;
            }}
            .form-control:focus {{
                outline: none;
                border-color: var(--primary);
                box-shadow: 0 0 0 3px rgba(88, 101, 242, 0.2);
            }}
            textarea.form-control {{
                min-height: 100px;
                resize: vertical;
            }}
            select.form-control {{
                background: var(--darker);
                color: var(--light);
            }}
            
            .alert {{
                padding: 1rem;
                border-radius: 5px;
                margin: 1rem 0;
                display: none;
            }}
            .alert-success {{
                background: #1a472a;
                color: #4ade80;
                border: 1px solid #2ecc71;
            }}
            .alert-error {{
                background: #7f1d1d;
                color: #f87171;
                border: 1px solid #ef4444;
            }}
            
            table {{
                width: 100%;
                border-collapse: collapse;
            }}
            th, td {{
                text-align: left;
                padding: 12px;
                border-bottom: 1px solid var(--gray);
            }}
            th {{
                background: var(--gray);
            }}
        </style>
    </head>
    <body>
        <header>
            <div class="header-content">
                <h1>Painel de Controle</h1>
                <div class="user-info">
                    <img src="https://cdn.discordapp.com/avatars/{user['id']}/{user.get('avatar', '')}.png" 
                         alt="Avatar" class="avatar"
                         onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                    <span>{user['username']}</span>
                    <a href="/" class="btn btn-primary">🏠 Início</a>
                    <a href="/logout" class="btn btn-danger">🚪 Sair</a>
                </div>
            </div>
        </header>
        
        <div class="container">
            <div class="tab-nav">
                <button class="tab-btn active" onclick="showTab('overview')">📊 Visão Geral</button>
                <button class="tab-btn" onclick="showTab('welcome')">👋 Boas-vindas</button>
                <button class="tab-btn" onclick="showTab('xp')">⭐ Sistema XP</button>
                <button class="tab-btn" onclick="showTab('roles')">🎭 Cargos</button>
                <button class="tab-btn" onclick="showTab('commands')">⚡ Comandos</button>
                <button class="tab-btn" onclick="showTab('moderation')">🛡️ Moderação</button>
                <button class="tab-btn" onclick="showTab('diagnostic')">🔧 Diagnóstico</button>
                <button class="tab-btn" onclick="showTab('queue')">📋 Fila</button>
            </div>
            
            <!-- Tab: Visão Geral -->
            <div id="overview" class="tab active">
                <div class="card">
                    <h2>📊 Estatísticas do Bot</h2>
                    <div class="stats-grid">
                        <div class="stat-card">
                            <h3>{len(data.get("xp", {}))}</h3>
                            <p>Usuários com XP</p>
                        </div>
                        <div class="stat-card">
                            <h3>{sum(len(w) for w in data.get("warns", {}).values())}</h3>
                            <p>Advertências</p>
                        </div>
                        <div class="stat-card">
                            <h3>{len(data.get("reaction_roles", {}))}</h3>
                            <p>Reaction Roles</p>
                        </div>
                        <div class="stat-card">
                            <h3>{len(data.get("role_buttons", {}))}</h3>
                            <p>Botões de Cargos</p>
                        </div>
                        <div class="stat-card">
                            <h3>{len(queue["entries"])}</h3>
                            <p>Na Fila</p>
                        </div>
                    </div>
                </div>
                
                <div class="card">
                    <h2>⚡ Status do Sistema</h2>
                    <p><strong>Status do Bot:</strong> <span class="{'online' if bot.is_ready() else 'offline'}">{'✅ Online' if bot.is_ready() else '❌ Offline'}</span></p>
                    <p><strong>Servidor:</strong> {guild.name if guild else 'Não conectado'}</p>
                    <p><strong>Membros:</strong> {len(guild.members) if guild else 0}</p>
                    <p><strong>Taxa de XP atual:</strong> {xp_rate}x</p>
                    <p><strong>Ações na fila:</strong> {len(bot_actions_queue)}</p>
                    <p><strong>Fila de Serviços:</strong> {queue["settings"]["open"] and "🟢 Aberta" or "🔴 Fechada"} - {len(queue["entries"])}/{queue["settings"]["max_size"]}</p>
                </div>
            </div>
            
            <!-- Tab: Boas-vindas -->
            <div id="welcome" class="tab">
                <div class="card">
                    <h2>👋 Configurar Boas-vindas</h2>
                    <div class="form-group">
                        <label>Canal de Boas-vindas</label>
                        <select id="welcome-channel" class="form-control">
                            <option value="">Selecione um canal</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Mensagem de Boas-vindas</label>
                        <textarea id="welcome-message" class="form-control" rows="3">{welcome_msg}</textarea>
                        <small>Use {{member}} para mencionar o novo membro</small>
                    </div>
                    <div class="form-group">
                        <label>Imagem de Fundo (URL)</label>
                        <input type="url" id="welcome-image" class="form-control" value="{welcome_bg}" placeholder="https://exemplo.com/imagem.jpg">
                    </div>
                    <button onclick="saveWelcomeConfig()" class="btn btn-primary">💾 Salvar Configurações</button>
                    <div id="welcome-alert" class="alert"></div>
                </div>
            </div>
            
            <!-- Tab: Sistema XP -->
            <div id="xp" class="tab">
                <div class="card">
                    <h2>⭐ Sistema de XP</h2>
                    <div class="form-group">
                        <label>Taxa de XP</label>
                        <input type="number" id="xp-rate" class="form-control" value="{xp_rate}" min="1" max="10">
                        <small>1 = fácil, 10 = muito difícil</small>
                    </div>
                    <div class="form-group">
                        <label>Canal de Level Up</label>
                        <select id="levelup-channel" class="form-control">
                            <option value="">Selecione um canal</option>
                        </select>
                    </div>
                    <button onclick="saveXPConfig()" class="btn btn-primary">💾 Salvar Configurações</button>
                    <div id="xp-alert" class="alert"></div>
                </div>
            </div>
            
            <!-- Tab: Cargos -->
            <div id="roles" class="tab">
                <div class="card">
                    <h2>🎭 Gerenciar Cargo Emoji</h2>
                    <div class="form-group">
                        <label>Canal para Mensagem</label>
                        <select id="rr-channel" class="form-control">
                            <option value="">Selecione canal</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Conteúdo da Mensagem</label>
                        <textarea id="rr-content" class="form-control" rows="3" placeholder="Reaja para receber cargos!"></textarea>
                    </div>
                    <div class="form-group">
                        <label>Emoji e Cargo (emoji:cargo)</label>
                        <input type="text" id="rr-pair" class="form-control" placeholder=":thumbsup:👍,✅:Verificado">
                        <small>Separe múltiplos por vírgula</small>
                    </div>
                    <button onclick="createReactionRole()" class="btn btn-primary">✨ Criar Cargo Emoji</button>
                    <div id="roles-alert" class="alert"></div>
                </div>
                
                <div class="card">
                    <h3>🔄 Botões de Cargos</h3>
                    <div class="form-group">
                        <label>Canal para Mensagem</label>
                        <select id="btn-channel" class="form-control">
                            <option value="">Selecione canal</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Conteúdo da Mensagem</label>
                        <textarea id="btn-content" class="form-control" rows="3" placeholder="Clique nos botões para receber cargos!"></textarea>
                    </div>
                    <div class="form-group">
                        <label>Botões (nome:cargo)</label>
                        <input type="text" id="btn-pairs" class="form-control" placeholder="Notícias:Notícias,Eventos:Eventos">
                    </div>
                    <button onclick="createRoleButtons()" class="btn btn-success">🔄 Criar Botões</button>
                </div>
            </div>
            
            <!-- Tab: Comandos -->
            <div id="commands" class="tab">
                <div class="card">
                    <h2>⚡ Executar Comandos</h2>
                    <div class="form-group">
                        <label>Canal</label>
                        <select id="embed-channel" class="form-control">
                            <option value="">Selecione canal</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Título</label>
                        <input type="text" id="embed-title" class="form-control" placeholder="Título">
                    </div>
                    <div class="form-group">
                        <label>Corpo</label>
                        <textarea id="embed-body" class="form-control" rows="3" placeholder="Conteúdo"></textarea>
                    </div>
                    <div class="form-group">
                        <label>Cor (hex)</label>
                        <input type="text" id="embed-color" class="form-control" value="#5865F2">
                    </div>
                    <button onclick="createEmbed()" class="btn btn-primary">📝 Criar Embed</button>
                </div>
            </div>
            
            <!-- Tab: Moderação -->
            <div id="moderation" class="tab">
                <div class="card">
                    <h2>🛡️ Ferramentas de Moderação</h2>
                    <div class="form-group">
                        <label>Membro</label>
                        <select id="warn-member" class="form-control">
                            <option value="">Selecione membro</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Motivo</label>
                        <input type="text" id="warn-reason" class="form-control">
                    </div>
                    <button onclick="executeWarn()" class="btn btn-warning">⚠️ Advertir</button>
                </div>
            </div>
            
            <!-- Tab: Diagnóstico -->
            <div id="diagnostic" class="tab">
                <div class="card">
                    <h2>🔧 Diagnóstico</h2>
                    <button onclick="testBotConnection()" class="btn btn-primary">Testar Conexão</button>
                    <div id="bot-test-result" style="margin-top: 1rem;"></div>
                </div>
            </div>
            
            <!-- Tab: Fila -->
            <div id="queue" class="tab">
                <div class="card">
                    <h2>📋 Sistema de Fila</h2>
                    <p>Gerencie a fila de serviços diretamente pelo painel.</p>
                </div>
                
                <div class="card">
                    <h3>⚙️ Configurações</h3>
                    <div style="display: flex; gap: 1rem; flex-wrap: wrap;">
                        <div style="flex: 1;">
                            <label>Nome da Fila</label>
                            <input type="text" id="queue-name" class="form-control" value="{escape_html(queue['name'])}">
                        </div>
                        <div style="width: 150px;">
                            <label>Tamanho Máximo</label>
                            <input type="number" id="queue-max-size" class="form-control" value="{queue['settings']['max_size']}" min="1" max="100">
                        </div>
                        <div style="display: flex; align-items: flex-end;">
                            <button onclick="saveQueueSettings()" class="btn btn-primary">💾 Salvar</button>
                        </div>
                        <div style="display: flex; align-items: flex-end;">
                            <button onclick="toggleQueueStatus()" id="queue-toggle-btn" class="btn {'btn-success' if queue['settings']['open'] else 'btn-danger'}">
                                {queue['settings']['open'] and '🔓 Fechar Fila' or '🔒 Abrir Fila'}
                            </button>
                        </div>
                    </div>
                    <div id="queue-status-display" style="margin-top: 10px; padding: 10px; background: #1a1a1a; border-radius: 5px;">
                        Status: <strong>{'🟢 ABERTA' if queue['settings']['open'] else '🔴 FECHADA'}</strong> | 
                        Ocupação: {len(queue['entries'])} / {queue['settings']['max_size']}
                    </div>
                </div>
                
                <div class="card">
                    <h3>➕ Adicionar à Fila</h3>
                    <div style="display: flex; gap: 1rem; flex-wrap: wrap;">
                        <input type="text" id="add-username" class="form-control" placeholder="Nome do jogador" style="flex: 1;">
                        <input type="text" id="add-service" class="form-control" placeholder="Serviço" style="flex: 1;">
                        <button onclick="addToQueue()" class="btn btn-primary">➕ Adicionar</button>
                    </div>
                    <div id="add-result" class="alert" style="margin-top: 10px; display: none;"></div>
                </div>
                
                <div class="card">
                    <h3>📋 Lista de Espera</h3>
                    <div style="overflow-x: auto;">
                        <table>
                            <thead>
                                <tr>
                                    <th>#</th>
                                    <th>Jogador</th>
                                    <th>Serviço</th>
                                    <th>Entrada</th>
                                    <th>Ações</th>
                                </tr>
                            </thead>
                            <tbody id="queue-table-body">
                                <tr><td colspan="5" style="text-align: center;">Carregando...</td></tr>
                            </tbody>
                        </table>
                    </div>
                    <div style="margin-top: 10px;">
                        <button onclick="clearQueue()" class="btn btn-danger">🗑️ Limpar Toda Fila</button>
                        <button onclick="refreshQueue()" class="btn btn-primary">🔄 Atualizar</button>
                    </div>
                </div>
                
                <div class="card">
                    <h3>📎 Links para StreamElements/OBS</h3>
                    <div class="form-group">
                        <label>URL da Lista (HTML completa)</label>
                        <input type="text" id="queue-url" class="form-control" readonly value="{request.host_url}queue" onclick="this.select();">
                        <small>Use esta URL no Browser Source do OBS/StreamElements</small>
                    </div>
                    <div class="form-group">
                        <label>URL Embed (Overlay compacto)</label>
                        <input type="text" id="queue-embed-url" class="form-control" readonly value="{request.host_url}queue/embed" onclick="this.select();">
                        <small>Versão mais compacta para overlay</small>
                    </div>
                    <div class="form-group">
                        <label>URL API (JSON)</label>
                        <input type="text" id="queue-api-url" class="form-control" readonly value="{request.host_url}queue/api" onclick="this.select();">
                        <small>Para integrações personalizadas</small>
                    </div>
                </div>
            </div>
        </div>
        
        <script>
            const guildChannels = {channels_json};
            const guildRoles = {roles_json};
            
            function showTab(tabId) {{
                document.querySelectorAll('.tab').forEach(tab => tab.classList.remove('active'));
                document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
                document.getElementById(tabId).classList.add('active');
                event.target.classList.add('active');
                
                if (tabId === 'queue') loadQueue();
                if (tabId === 'welcome' || tabId === 'xp' || tabId === 'roles' || tabId === 'commands') populateSelects();
            }}
            
            function populateSelects() {{
                const channelSelects = ['welcome-channel', 'levelup-channel', 'rr-channel', 'btn-channel', 'embed-channel'];
                channelSelects.forEach(selectId => {{
                    const select = document.getElementById(selectId);
                    if (select) {{
                        select.innerHTML = '<option value="">Selecione um canal</option>';
                        guildChannels.forEach(channel => {{
                            const option = document.createElement('option');
                            option.value = channel.id;
                            option.textContent = '#' + channel.name;
                            select.appendChild(option);
                        }});
                    }}
                }});
                
                const welcomeChanSelect = document.getElementById('welcome-channel');
                const levelupChanSelect = document.getElementById('levelup-channel');
                if (welcomeChanSelect) welcomeChanSelect.value = '{welcome_chan}';
                if (levelupChanSelect) levelupChanSelect.value = '{levelup_chan}';
            }}
            
            async function saveWelcomeConfig() {{
                const data = {{
                    message: document.getElementById('welcome-message').value,
                    channel_id: document.getElementById('welcome-channel').value,
                    image_url: document.getElementById('welcome-image').value
                }};
                try {{
                    const response = await fetch('/api/config/welcome', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify(data)
                    }});
                    const result = await response.json();
                    showAlert('welcome-alert', result.message, result.success);
                }} catch(error) {{
                    showAlert('welcome-alert', 'Erro: ' + error.message, false);
                }}
            }}
            
            async function saveXPConfig() {{
                const data = {{
                    rate: parseInt(document.getElementById('xp-rate').value),
                    channel_id: document.getElementById('levelup-channel').value
                }};
                try {{
                    const response = await fetch('/api/config/xp', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify(data)
                    }});
                    const result = await response.json();
                    showAlert('xp-alert', result.message, result.success);
                }} catch(error) {{
                    showAlert('xp-alert', 'Erro: ' + error.message, false);
                }}
            }}
            
            async function createReactionRole() {{
                const channelId = document.getElementById('rr-channel').value;
                const content = document.getElementById('rr-content').value;
                const pairs = document.getElementById('rr-pair').value;
                
                if (!channelId || !content || !pairs) {{
                    showAlert('roles-alert', 'Preencha todos os campos', false);
                    return;
                }}
                
                try {{
                    const response = await fetch('/api/reactionrole/create', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ channel_id: channelId, content: content, emoji_cargo: pairs }})
                    }});
                    const result = await response.json();
                    showAlert('roles-alert', result.message, result.success);
                    if (result.success) {{
                        document.getElementById('rr-content').value = '';
                        document.getElementById('rr-pair').value = '';
                    }}
                }} catch(error) {{
                    showAlert('roles-alert', 'Erro: ' + error.message, false);
                }}
            }}
            
            async function createRoleButtons() {{
                const channelId = document.getElementById('btn-channel').value;
                const content = document.getElementById('btn-content').value;
                const pairs = document.getElementById('btn-pairs').value;
                
                if (!channelId || !content || !pairs) {{
                    showAlert('roles-alert', 'Preencha todos os campos', false);
                    return;
                }}
                
                try {{
                    const response = await fetch('/api/rolebuttons/create', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ channel_id: channelId, content: content, roles: pairs }})
                    }});
                    const result = await response.json();
                    showAlert('roles-alert', result.message, result.success);
                    if (result.success) {{
                        document.getElementById('btn-content').value = '';
                        document.getElementById('btn-pairs').value = '';
                    }}
                }} catch(error) {{
                    showAlert('roles-alert', 'Erro: ' + error.message, false);
                }}
            }}
            
            async function createEmbed() {{
                const channelId = document.getElementById('embed-channel').value;
                const title = document.getElementById('embed-title').value;
                const body = document.getElementById('embed-body').value;
                const color = document.getElementById('embed-color').value;
                
                if (!channelId || !title || !body) {{
                    alert('Preencha todos os campos');
                    return;
                }}
                
                try {{
                    const response = await fetch('/api/command/embed', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ channel_id: channelId, title: title, body: body, color: color }})
                    }});
                    const result = await response.json();
                    alert(result.message);
                    if (result.success) {{
                        document.getElementById('embed-title').value = '';
                        document.getElementById('embed-body').value = '';
                    }}
                }} catch(error) {{
                    alert('Erro: ' + error.message);
                }}
            }}
            
            async function executeWarn() {{
                const memberId = document.getElementById('warn-member').value;
                const reason = document.getElementById('warn-reason').value;
                
                if (!memberId || !reason) {{
                    alert('Selecione um membro e digite um motivo');
                    return;
                }}
                
                try {{
                    const response = await fetch('/api/command/warn', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ member_id: memberId, reason: reason }})
                    }});
                    const result = await response.json();
                    alert(result.message);
                    if (result.success) document.getElementById('warn-reason').value = '';
                }} catch(error) {{
                    alert('Erro: ' + error.message);
                }}
            }}
            
            async function testBotConnection() {{
                const resultDiv = document.getElementById('bot-test-result');
                resultDiv.innerHTML = '<p>Testando...</p>';
                try {{
                    const response = await fetch('/api/test/bot');
                    const data = await response.json();
                    if (data.success) {{
                        resultDiv.innerHTML = '<div class="alert-success">✅ Bot Online! Bot conectado ao servidor.</div>';
                    }} else {{
                        resultDiv.innerHTML = '<div class="alert-error">❌ Falha na conexão</div>';
                    }}
                }} catch(error) {{
                    resultDiv.innerHTML = '<div class="alert-error">❌ Erro: ' + error.message + '</div>';
                }}
            }}
            
            // Funções da Fila
            async function loadQueue() {{
                try {{
                    const response = await fetch('/queue/api');
                    const data = await response.json();
                    
                    if (data.success) {{
                        const queue = data.queue;
                        const tbody = document.getElementById('queue-table-body');
                        
                        if (queue.entries.length === 0) {{
                            tbody.innerHTML = '<tr><td colspan="5" style="text-align: center;">📭 Ninguém na fila</td></tr>';
                        }} else {{
                            tbody.innerHTML = queue.entries.map(entry => `
                                <tr>
                                    <td><strong style="color: #ffd93d;">#${{entry.position}}</strong></td>
                                    <td>${{escapeHtml(entry.username)}}</td>
                                    <td style="color: #a8e6cf;">${{escapeHtml(entry.service)}}</td>
                                    <td>${{new Date(entry.timestamp).toLocaleTimeString()}}</td>
                                    <td>
                                        <button onclick="moveUp('${{entry.id}}')" class="btn btn-primary" style="padding: 4px 8px; margin-right: 5px;">⬆️</button>
                                        <button onclick="moveDown('${{entry.id}}')" class="btn btn-primary" style="padding: 4px 8px; margin-right: 5px;">⬇️</button>
                                        <button onclick="completeService('${{entry.id}}')" class="btn btn-success" style="padding: 4px 8px; margin-right: 5px;">✅</button>
                                        <button onclick="removeFromQueue('${{entry.id}}')" class="btn btn-danger" style="padding: 4px 8px;">❌</button>
                                    </td>
                                </tr>
                            `).join('');
                        }}
                        
                        document.getElementById('queue-status-display').innerHTML = `
                            Status: <strong>${queue.open ? '🟢 ABERTA' : '🔴 FECHADA'}</strong> | 
                            Ocupação: ${queue.count} / ${queue.max_size} pessoas
                        `;
                        
                        const toggleBtn = document.getElementById('queue-toggle-btn');
                        if (toggleBtn) {{
                            toggleBtn.className = queue.open ? 'btn btn-danger' : 'btn btn-success';
                            toggleBtn.textContent = queue.open ? '🔓 Fechar Fila' : '🔒 Abrir Fila';
                        }}
                    }}
                }} catch(error) {{
                    console.error('Erro ao carregar fila:', error);
                }}
            }}
            
            async function addToQueue() {{
                const username = document.getElementById('add-username').value.trim();
                const service = document.getElementById('add-service').value.trim();
                
                if (!username || !service) {{
                    showQueueAlert('add-result', 'Preencha nome e serviço', false);
                    return;
                }}
                
                try {{
                    const response = await fetch('/api/queue/add', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ username, service }})
                    }});
                    
                    const data = await response.json();
                    showQueueAlert('add-result', data.message, data.success);
                    
                    if (data.success) {{
                        document.getElementById('add-username').value = '';
                        document.getElementById('add-service').value = '';
                        loadQueue();
                    }}
                }} catch(error) {{
                    showQueueAlert('add-result', 'Erro: ' + error.message, false);
                }}
            }}
            
            async function removeFromQueue(entryId) {{
                if (!confirm('Remover esta pessoa da fila?')) return;
                try {{
                    const response = await fetch('/api/queue/remove', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ entry_id: entryId }})
                    }});
                    const data = await response.json();
                    if (data.success) loadQueue();
                }} catch(error) {{
                    console.error('Erro:', error);
                }}
            }}
            
            async function moveUp(entryId) {{
                try {{
                    const response = await fetch('/api/queue/move-up', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ entry_id: entryId }})
                    }});
                    const data = await response.json();
                    if (data.success) loadQueue();
                }} catch(error) {{
                    console.error('Erro:', error);
                }}
            }}
            
            async function moveDown(entryId) {{
                try {{
                    const response = await fetch('/api/queue/move-down', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ entry_id: entryId }})
                    }});
                    const data = await response.json();
                    if (data.success) loadQueue();
                }} catch(error) {{
                    console.error('Erro:', error);
                }}
            }}
            
            async function completeService(entryId) {{
                if (!confirm('Marcar como concluído e remover da fila?')) return;
                try {{
                    const response = await fetch('/api/queue/complete', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ entry_id: entryId }})
                    }});
                    const data = await response.json();
                    if (data.success) loadQueue();
                }} catch(error) {{
                    console.error('Erro:', error);
                }}
            }}
            
            async function clearQueue() {{
                if (!confirm('⚠️ ISSO REMOVERÁ TODAS AS PESSOAS DA FILA! Tem certeza?')) return;
                try {{
                    const response = await fetch('/api/queue/clear', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }}
                    }});
                    const data = await response.json();
                    if (data.success) loadQueue();
                }} catch(error) {{
                    console.error('Erro:', error);
                }}
            }}
            
            async function saveQueueSettings() {{
                const name = document.getElementById('queue-name').value;
                const max_size = parseInt(document.getElementById('queue-max-size').value);
                
                try {{
                    const response = await fetch('/api/queue/settings', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ name, max_size }})
                    }});
                    const data = await response.json();
                    if (data.success) {{
                        showQueueAlert('add-result', 'Configurações salvas!', true);
                        setTimeout(() => {{
                            document.getElementById('add-result').style.display = 'none';
                        }}, 2000);
                        loadQueue();
                    }}
                }} catch(error) {{
                    console.error('Erro:', error);
                }}
            }}
            
            async function toggleQueueStatus() {{
                try {{
                    const response = await fetch('/api/queue/settings', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ open: null }})
                    }});
                    const data = await response.json();
                    if (data.success) loadQueue();
                }} catch(error) {{
                    console.error('Erro:', error);
                }}
            }}
            
            function refreshQueue() {{
                loadQueue();
            }}
            
            function showAlert(elementId, message, isSuccess) {{
                const alertEl = document.getElementById(elementId);
                alertEl.textContent = message;
                alertEl.className = 'alert ' + (isSuccess ? 'alert-success' : 'alert-error');
                alertEl.style.display = 'block';
                setTimeout(() => {{
                    alertEl.style.display = 'none';
                }}, 3000);
            }}
            
            function showQueueAlert(elementId, message, isSuccess) {{
                const alertEl = document.getElementById(elementId);
                alertEl.textContent = message;
                alertEl.className = 'alert ' + (isSuccess ? 'alert-success' : 'alert-error');
                alertEl.style.display = 'block';
                setTimeout(() => {{
                    alertEl.style.display = 'none';
                }}, 3000);
            }}
            
            function escapeHtml(text) {{
                if (!text) return '';
                return text.replace(/[&<>]/g, function(m) {{
                    if (m === '&') return '&amp;';
                    if (m === '<') return '&lt;';
                    if (m === '>') return '&gt;';
                    return m;
                }});
            }}
            
            document.addEventListener('DOMContentLoaded', function() {{
                populateSelects();
                loadQueue();
            }});
        </script>
    </body>
    </html>
    '''

# ========================
# START BOT AND FLASK
# ========================
def run_flask():
    """Inicia o servidor Flask"""
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

Thread(target=run_flask, daemon=True).start()

if __name__ == "__main__":
    try:
        bot.run(BOT_TOKEN)
    except Exception as e:
        print("Erro ao iniciar o bot:", e)
