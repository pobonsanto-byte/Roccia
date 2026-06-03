import os
import json
import base64
import re
import requests
import time
import secrets
from io import BytesIO
from threading import Thread
from datetime import datetime, timezone
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
    """Retorna a data/hora atual no fuso horário de Brasília (UTC-3)"""
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-3)))

# Criar um timedelta para UTC-3
from datetime import timedelta
UTC_MINUS_3 = timezone(timedelta(hours=-3))

def now_br_alt():
    """Alternativa usando timezone fixo UTC-3"""
    return datetime.now(UTC_MINUS_3)

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
    if not emoji_str:
        return None
    
    emoji_str = emoji_str.strip()
    
    print(f"[DEBUG EMOJI] Processando: '{emoji_str}'")
    
    m = EMOJI_RE.match(emoji_str)
    if m:
        name, id_str = m.groups()
        try:
            eid = int(id_str)
            animated = emoji_str.startswith('<a:')
            print(f"[DEBUG EMOJI] É emoji personalizado: nome={name}, id={eid}, animado={animated}")
            
            if guild:
                e = discord.utils.get(guild.emojis, id=eid)
                if e:
                    print(f"[DEBUG EMOJI] Encontrado no servidor: {e.name}")
                    return e
            
            print(f"[DEBUG EMOJI] Criando PartialEmoji")
            return discord.PartialEmoji(name=name, id=eid, animated=animated)
        except Exception as e:
            print(f"[DEBUG EMOJI] Erro ao processar emoji personalizado: {e}")
            pass
    
    m2 = EMOJI_NAME_RE.match(emoji_str)
    if m2:
        emoji_name = m2.group(1)
        print(f"[DEBUG EMOJI] É formato :nome:: {emoji_name}")
        
        if guild:
            emoji = discord.utils.get(guild.emojis, name=emoji_name)
            if emoji:
                print(f"[DEBUG EMOJI] Encontrado no servidor por nome: {emoji.name}")
                return emoji
        
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
        
        print(f"[DEBUG EMOJI] Retornando como string: {emoji_str}")
        return emoji_str
    
    if len(emoji_str) <= 10:
        import unicodedata
        has_emoji_char = any('EMOJI' in unicodedata.name(c, '') for c in emoji_str)
        
        if has_emoji_char or any(c in emoji_str for c in ['👍', '👎', '✅', '❌', '⚠️', '❗', '❓', '⭐', '❤️', '🔥', '🚀', '🎉']):
            print(f"[DEBUG EMOJI] É emoji Unicode: {emoji_str}")
            return emoji_str
    
    print(f"[DEBUG EMOJI] Retornando string original: {emoji_str}")
    return emoji_str

# ========================
# SISTEMA DE FILA
# ========================

def get_queue_data():
    data.setdefault("queue", {
        "name": "Fila de Serviços",
        "settings": {"max_size": 50, "open": True},
        "entries": [],
        "history": []
    })
    return data["queue"]

def save_queue():
    return save_data_to_github("Queue update")

def add_to_queue(username: str, service: str, user_id: str = None):
    queue = get_queue_data()
    
    if not queue["settings"]["open"]:
        return False, "Fila está fechada no momento"
    
    if len(queue["entries"]) >= queue["settings"]["max_size"]:
        return False, "Fila está cheia"
    
    for entry in queue["entries"]:
        if entry["username"].lower() == username.lower():
            return False, f"{username} já está na fila"
    
    entry = {
        "id": str(int(datetime.now().timestamp() * 1000)),
        "username": username,
        "service": service,
        "user_id": user_id or username,
        "timestamp": now_br().isoformat(),
        "status": "waiting",
        "position": len(queue["entries"]) + 1
    }
    
    queue["entries"].append(entry)
    update_positions(queue["entries"])
    save_queue()
    
    add_log(f"queue_add: {username} - {service}")
    return True, entry

def remove_from_queue(entry_id: str):
    queue = get_queue_data()
    
    for i, entry in enumerate(queue["entries"]):
        if entry["id"] == entry_id:
            removed = queue["entries"].pop(i)
            removed["removed_at"] = now_br().isoformat()
            queue["history"].append(removed)
            
            if len(queue["history"]) > 100:
                queue["history"] = queue["history"][-100:]
            
            update_positions(queue["entries"])
            save_queue()
            add_log(f"queue_remove: {removed['username']} - {removed['service']}")
            return True, removed
    
    return False, None

def update_positions(entries):
    for i, entry in enumerate(entries):
        entry["position"] = i + 1
        entry["status"] = "waiting"

def move_up(entry_id: str):
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
    queue = get_queue_data()
    
    for i, entry in enumerate(queue["entries"]):
        if entry["id"] == entry_id:
            removed = queue["entries"].pop(i)
            removed["status"] = "completed"
            removed["completed_at"] = now_br().isoformat()
            queue["history"].append(removed)
            
            update_positions(queue["entries"])
            save_queue()
            add_log(f"queue_complete: {removed['username']}")
            return True, removed
    
    return False, None

def clear_queue():
    queue = get_queue_data()
    
    for entry in queue["entries"]:
        entry["status"] = "cleared"
        entry["cleared_at"] = now_br().isoformat()
        queue["history"].append(entry)
    
    queue["entries"] = []
    save_queue()
    add_log("queue_cleared")
    return True

def toggle_queue(open_status: bool = None):
    queue = get_queue_data()
    
    if open_status is None:
        queue["settings"]["open"] = not queue["settings"]["open"]
    else:
        queue["settings"]["open"] = open_status
    
    save_queue()
    add_log(f"queue_toggle: {'open' if queue['settings']['open'] else 'closed'}")
    return queue["settings"]["open"]

def set_max_size(size: int):
    queue = get_queue_data()
    queue["settings"]["max_size"] = max(1, min(size, 100))
    save_queue()
    return queue["settings"]["max_size"]

def set_queue_name(name: str):
    queue = get_queue_data()
    queue["name"] = name[:50]
    save_queue()
    return queue["name"]

# ========================
# DIAGNÓSTICO DE CONEXÃO
# ========================
async def check_bot_connection():
    await bot.wait_until_ready()
    
    print("\n" + "="*60)
    print("🔍 DIAGNÓSTICO DE CONEXÃO BOT-SITE")
    print("="*60)
    
    if GUILD_ID:
        guild = bot.get_guild(int(GUILD_ID))
        if guild:
            print(f"✅ Guild encontrada: {guild.name} (ID: {guild.id})")
            print(f"   👥 Membros: {len(guild.members)}")
            print(f"   📝 Canais: {len(guild.text_channels)}")
            
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
    bot_actions_queue.append({
        "type": action_type,
        "data": kwargs,
        "timestamp": now_br().isoformat()
    })
    print(f"🤖 [BOT ACTION] Adicionada ação: {action_type}")
    print(f"   📊 Dados: {kwargs}")
    return True

async def execute_bot_action_internal(action):
    action_type = action["type"]
    action_data = action["data"]
    
    print(f"\n{'='*50}")
    print(f"🤖 EXECUTANDO AÇÃO DO SITE: {action_type}")
    print(f"📊 Dados: {action_data}")
    print(f"⏰ Timestamp: {action.get('timestamp')}")
    print(f"{'='*50}")
    
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
                
                bot_member = guild.get_member(bot.user.id)
                if bot_member:
                    permissions = channel.permissions_for(bot_member)
                    if not permissions.send_messages:
                        print("❌ Bot não tem permissão para enviar mensagens neste canal!")
                        return False
                    if not permissions.embed_links:
                        print("❌ Bot não tem permissão para enviar embeds neste canal!")
                        return False
                
                color = discord.Color.blue()
                if action_data.get('color'):
                    try:
                        color_hex = action_data['color'].replace('#', '')
                        color = discord.Color(int(color_hex, 16))
                    except:
                        print(f"⚠️ Cor inválida: {action_data.get('color')}, usando padrão")
                
                embed = discord.Embed(
                    title=action_data["title"],
                    description=action_data["body"],
                    color=color
                )
                
                if action_data.get('image_url'):
                    embed.set_image(url=action_data['image_url'])
                
                mention_text = ""
                if action_data.get('mention') == 'everyone':
                    mention_text = "@everyone"
                elif action_data.get('mention') == 'here':
                    mention_text = "@here"
                
                print("📤 Enviando embed...")
                await channel.send(content=mention_text, embed=embed)
                print(f"✅ Embed enviada com sucesso para #{channel.name}")
                
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
                
                bot_member = guild.get_member(bot.user.id)
                if bot_member:
                    permissions = channel.permissions_for(bot_member)
                    if not permissions.send_messages:
                        print("❌ Sem permissão para enviar mensagens")
                        return False
                    if not permissions.add_reactions:
                        print("❌ Sem permissão para adicionar reações")
                        return False
                
                message = await channel.send(action_data["content"])
                message_id = str(message.id)
                print(f"✅ Mensagem enviada com ID: {message_id}")
                
                pairs_str = action_data.get("emoji_cargo", "")
                print(f"🔄 String completa: '{pairs_str}'")
                
                pairs = []
                current_pair = ""
                bracket_count = 0
                
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
                    
                    split_index = -1
                    bracket_depth = 0
                    
                    for i, char in enumerate(pair):
                        if char == '<':
                            bracket_depth += 1
                        elif char == '>':
                            bracket_depth -= 1
                        elif char == ':' and bracket_depth == 0:
                            split_index = i
                    
                    if split_index == -1:
                        print(f"   ❌ Par sem ':' válido: {pair}")
                        continue
                    
                    try:
                        emoji_str = pair[:split_index].strip()
                        role_name = pair[split_index+1:].strip()
                        
                        print(f"   Processando: '{emoji_str}' -> '{role_name}'")
                        
                        role = discord.utils.get(guild.roles, name=role_name)
                        if not role:
                            print(f"   ❌ Cargo '{role_name}' não encontrado!")
                            continue
                        
                        print(f"   🔍 String do emoji: '{emoji_str}'")
                        
                        parsed_emoji = parse_emoji_str(emoji_str, guild)
                        
                        if parsed_emoji is None:
                            print(f"   ❌ Emoji '{emoji_str}' inválido!")
                            continue
                        
                        print(f"   🔍 Emoji parseado: {parsed_emoji} (tipo: {type(parsed_emoji)})")
                        
                        try:
                            if isinstance(parsed_emoji, (discord.Emoji, discord.PartialEmoji)):
                                await message.add_reaction(parsed_emoji)
                                emoji_key = str(parsed_emoji.id)
                                print(f"   ✅ Reação adicionada (custom): {parsed_emoji.name} (ID: {parsed_emoji.id})")
                            else:
                                if isinstance(parsed_emoji, str) and parsed_emoji:
                                    await message.add_reaction(parsed_emoji)
                                    emoji_key = str(parsed_emoji)
                                    print(f"   ✅ Reação adicionada (Unicode): {parsed_emoji}")
                                else:
                                    print(f"   ❌ Emoji inválido: {parsed_emoji}")
                                    continue
                            
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
                
                if reaction_roles_data:
                    data.setdefault("reaction_roles", {})[message_id] = reaction_roles_data
                    save_data_to_github("Reaction role via site")
                    print(f"✅ Reaction role salva: {message_id}")
                    return True
                else:
                    print("⚠️ Nenhum mapeamento válido criado")
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
                            
                            role = discord.utils.get(guild.roles, name=role_name)
                            if role:
                                buttons_dict[button_name] = role.id
                                print(f"   ✅ Botão mapeado: {button_name} -> {role.name}")
                            else:
                                print(f"   ❌ Cargo '{role_name}' não encontrado!")
                        except Exception as e:
                            print(f"   ❌ Erro ao processar par {pair}: {e}")
                
                if buttons_dict:
                    view = PersistentRoleButtonView(0, buttons_dict)
                    sent = await channel.send(action_data["content"], view=view)
                    print(f"✅ Mensagem com botões enviada: {sent.id}")
                    
                    view.message_id = sent.id
                    for item in view.children:
                        if isinstance(item, PersistentRoleButton):
                            item.message_id = sent.id
                    
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
                
                entry = {
                    "by": "site_admin",
                    "reason": action_data["reason"],
                    "ts": now_br().strftime("%d/%m/%Y %H:%M"),
                    "admin": action_data.get('admin', 'Site Admin')
                }
                data.setdefault("warns", {}).setdefault(str(member.id), []).append(entry)
                save_data_to_github(f"Warn via site: {member.display_name}")
                
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
    global action_processor_running
    
    print("\n" + "="*60)
    print("🚀 PROCESSADOR DE AÇÕES DO SITE - INICIANDO")
    print("="*60)
    
    action_processor_running = True
    
    if not bot.is_ready():
        print("⏳ Aguardando bot ficar pronto...")
        await bot.wait_until_ready()
        await asyncio.sleep(2)
    
    print(f"✅ Bot está pronto: {bot.user}")
    
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
                current_time = time.time()
                if current_time - last_status_time > 30:
                    queue_len = len(bot_actions_queue)
                    print(f"[ACTION PROCESSOR] Status: Fila={queue_len} | Processadas={processed_count} | Erros={error_count}")
                    last_status_time = current_time
                
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
                            
                            attempts = action.get('attempts', 0)
                            if attempts < 3:
                                action['attempts'] = attempts + 1
                                action['retry_time'] = now_br().isoformat()
                                bot_actions_queue.insert(0, action)
                                print(f"[ACTION PROCESSOR] 🔄 Tentando novamente ({action['attempts']}/3)")
                            else:
                                print(f"[ACTION PROCESSOR] 🗑️ Descarte após 3 tentativas falhas")
                    
                    except Exception as e:
                        error_count += 1
                        print(f"[ACTION PROCESSOR] 💥 ERRO CRÍTICO: {e}")
                        import traceback
                        traceback.print_exc()
                
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
# ROTAS DO SITE - PÁGINA INICIAL
# ========================
@app.route("/", methods=["GET"])
def home():
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
    session.clear()
    return redirect(url_for('home'))

# ========================
# ROTAS DO SISTEMA DE FILA (PÚBLICAS)
# ========================

@app.route("/queue")
def queue_public():
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
                Atualizado automaticamente a cada 30 segundos • {now_br().strftime("%d/%m/%Y %H:%M:%S")}
            </div>
        </div>
    </body>
    </html>
    '''

@app.route("/queue/embed")
def queue_embed():
    queue = get_queue_data()
    
    entries_html = ""
    for entry in queue["entries"][:10]:
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
                    "timestamp": e["timestamp"],
                    "id": e["id"]
                }
                for e in queue["entries"]
            ]
        }
    })

# ========================
# APIs DE CONTROLE DA FILA
# ========================

@app.route("/api/queue/add", methods=["POST"])
def api_queue_add():
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
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    entry_id = request.json.get("entry_id")
    if not entry_id:
        return jsonify({"success": False, "message": "ID da entrada é obrigatório"})
    
    success, result = remove_from_queue(entry_id)
    return jsonify({"success": success, "message": "Removido da fila" if success else "Entrada não encontrada"})

@app.route("/api/queue/move-up", methods=["POST"])
def api_queue_move_up():
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    entry_id = request.json.get("entry_id")
    success, result = move_up(entry_id)
    return jsonify({"success": success})

@app.route("/api/queue/move-down", methods=["POST"])
def api_queue_move_down():
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    entry_id = request.json.get("entry_id")
    success, result = move_down(entry_id)
    return jsonify({"success": success})

@app.route("/api/queue/complete", methods=["POST"])
def api_queue_complete():
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    entry_id = request.json.get("entry_id")
    success, result = complete_service(entry_id)
    return jsonify({"success": success})

@app.route("/api/queue/clear", methods=["POST"])
def api_queue_clear():
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    clear_queue()
    return jsonify({"success": True, "message": "Fila limpa com sucesso"})

@app.route("/api/queue/settings", methods=["GET", "POST"])
def api_queue_settings():
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
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    
    queue = get_queue_data()
    return jsonify({
        "success": True,
        "history": queue["history"][-50:]
    })

# ========================
# APIs EXISTENTES (RESUMIDAS)
# ========================
@app.route("/api/config/welcome", methods=["POST"])
def api_config_welcome():
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

@app.route("/api/guild/members")
def api_guild_members():
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

@app.route("/api/command/embed", methods=["POST"])
def api_command_embed():
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
            return jsonify({"success": False, "message": "Preencha todos os campos obrigatórios"})
        
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
        return jsonify({"success": success, "message": f"✅ Embed será criada em instantes!"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro: {str(e)}"}), 500

@app.route("/api/command/warn", methods=["POST"])
def api_command_warn():
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    try:
        req_data = request.json
        member_id = req_data.get('member_id')
        reason = req_data.get('reason', 'Sem motivo informado')
        if not member_id:
            return jsonify({"success": False, "message": "ID do membro é obrigatório"})
        success = execute_bot_action("warn_member", member_id=member_id, reason=reason, admin=session['user']['username'])
        return jsonify({"success": success, "message": f"✅ Membro será advertido em instantes!"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro: {str(e)}"}), 500

@app.route("/api/command/clearwarns", methods=["POST"])
def api_command_clearwarns():
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

@app.route("/api/reactionrole/create", methods=["POST"])
def api_reactionrole_create():
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    try:
        req_data = request.json
        channel_id = req_data.get('channel_id')
        content = req_data.get('content')
        emoji_cargo = req_data.get('emoji_cargo')
        if not channel_id or not content or not emoji_cargo:
            return jsonify({"success": False, "message": "Preencha todos os campos"})
        success = execute_bot_action("create_reaction_role", channel_id=channel_id, content=content, emoji_cargo=emoji_cargo, admin=session['user']['username'])
        return jsonify({"success": success, "message": "✅ Reaction role será criada em instantes!"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro: {str(e)}"}), 500

@app.route("/api/rolebuttons/create", methods=["POST"])
def api_rolebuttons_create():
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    try:
        req_data = request.json
        channel_id = req_data.get('channel_id')
        content = req_data.get('content')
        roles = req_data.get('roles')
        if not channel_id or not content or not roles:
            return jsonify({"success": False, "message": "Preencha todos os campos"})
        success = execute_bot_action("create_role_buttons", channel_id=channel_id, content=content, roles=roles, admin=session['user']['username'])
        return jsonify({"success": success, "message": "✅ Botões de cargo serão criados em instantes!"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro: {str(e)}"}), 500

@app.route("/api/test/bot", methods=["GET"])
def api_test_bot():
    if 'user' not in session:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    return jsonify({
        "success": True,
        "bot": {"ready": bot.is_ready(), "user": str(bot.user) if bot.user else None},
        "queue_length": len(bot_actions_queue)
    })

# ========================
# DASHBOARD PRINCIPAL
# ========================
@app.route("/dashboard")
def dashboard():
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
                    <img src="https://cdn.discordapp.com/avatars/{user['id']}/{user.get('avatar', '')}.png" class="avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
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
                <button class="tab-btn" onclick="showTab('moderation')">🛡️ Moderação</button>
                <button class="tab-btn" onclick="showTab('queue')">📋 Fila</button>
            </div>
            
            <div id="overview" class="tab active">
                <div class="card">
                    <h2>📊 Estatísticas</h2>
                    <div class="stats-grid">
                        <div class="stat-card"><h3>{len(data.get("xp", {}))}</h3><p>Usuários com XP</p></div>
                        <div class="stat-card"><h3>{sum(len(w) for w in data.get("warns", {}).values())}</h3><p>Advertências</p></div>
                        <div class="stat-card"><h3>{len(queue["entries"])}</h3><p>Na Fila</p></div>
                    </div>
                </div>
                <div class="card">
                    <h2>⚡ Status</h2>
                    <p><strong>Bot:</strong> {'✅ Online' if bot.is_ready() else '❌ Offline'}</p>
                    <p><strong>Servidor:</strong> {guild.name if guild else 'Não conectado'}</p>
                    <p><strong>Fila:</strong> {queue["settings"]["open"] and '🟢 Aberta' or '🔴 Fechada'} - {len(queue["entries"])}/{queue["settings"]["max_size"]}</p>
                </div>
            </div>
            
            <div id="welcome" class="tab">
                <div class="card">
                    <h2>👋 Boas-vindas</h2>
                    <div class="form-group"><label>Canal</label><select id="welcome-channel" class="form-control"></select></div>
                    <div class="form-group"><label>Mensagem</label><textarea id="welcome-message" class="form-control" rows="3">{welcome_msg}</textarea><small>Use {{member}} para mencionar</small></div>
                    <div class="form-group"><label>Imagem URL</label><input type="url" id="welcome-image" class="form-control" value="{welcome_bg}"></div>
                    <button onclick="saveWelcomeConfig()" class="btn btn-primary">💾 Salvar</button>
                    <div id="welcome-alert" class="alert"></div>
                </div>
            </div>
            
            <div id="xp" class="tab">
                <div class="card">
                    <h2>⭐ Sistema XP</h2>
                    <div class="form-group"><label>Taxa de XP</label><input type="number" id="xp-rate" class="form-control" value="{xp_rate}" min="1" max="10"></div>
                    <div class="form-group"><label>Canal Level Up</label><select id="levelup-channel" class="form-control"></select></div>
                    <button onclick="saveXPConfig()" class="btn btn-primary">💾 Salvar</button>
                    <div id="xp-alert" class="alert"></div>
                </div>
            </div>
            
            <div id="roles" class="tab">
                <div class="card">
                    <h2>🎭 Reaction Role</h2>
                    <div class="form-group"><label>Canal</label><select id="rr-channel" class="form-control"></select></div>
                    <div class="form-group"><label>Mensagem</label><textarea id="rr-content" class="form-control" rows="3" placeholder="Reaja para receber cargos!"></textarea></div>
                    <div class="form-group"><label>Emoji:Cargo</label><input type="text" id="rr-pair" class="form-control" placeholder="✅:Verificado,👍:Aprovado"></div>
                    <button onclick="createReactionRole()" class="btn btn-primary">✨ Criar</button>
                    <div id="roles-alert" class="alert"></div>
                </div>
                <div class="card">
                    <h3>🔄 Botões de Cargos</h3>
                    <div class="form-group"><label>Canal</label><select id="btn-channel" class="form-control"></select></div>
                    <div class="form-group"><label>Mensagem</label><textarea id="btn-content" class="form-control" rows="3"></textarea></div>
                    <div class="form-group"><label>Botão:Cargo</label><input type="text" id="btn-pairs" class="form-control" placeholder="Notícias:Notícias,Eventos:Eventos"></div>
                    <button onclick="createRoleButtons()" class="btn btn-success">🔄 Criar</button>
                </div>
            </div>
            
            <div id="moderation" class="tab">
                <div class="card">
                    <h2>🛡️ Moderação</h2>
                    <div class="form-group"><label>Membro</label><select id="warn-member" class="form-control"></select></div>
                    <div class="form-group"><label>Motivo</label><input type="text" id="warn-reason" class="form-control"></div>
                    <button onclick="executeWarn()" class="btn btn-warning">⚠️ Advertir</button>
                    <button onclick="clearWarns()" class="btn btn-danger" style="margin-left: 10px;">🧹 Limpar Advertências</button>
                    <div id="warn-alert" class="alert"></div>
                </div>
            </div>
            
            <div id="queue" class="tab">
                <div class="card">
                    <h2>📋 Sistema de Fila</h2>
                </div>
                
                <div class="card">
                    <h3>⚙️ Configurações</h3>
                    <div style="display: flex; gap: 1rem; flex-wrap: wrap;">
                        <div style="flex: 1;"><label>Nome da Fila</label><input type="text" id="queue-name" class="form-control" value="{escape_html(queue['name'])}"></div>
                        <div style="width: 150px;"><label>Máximo</label><input type="number" id="queue-max-size" class="form-control" value="{queue['settings']['max_size']}" min="1" max="100"></div>
                        <div style="display: flex; align-items: flex-end;"><button onclick="saveQueueSettings()" class="btn btn-primary">💾 Salvar</button></div>
                        <div style="display: flex; align-items: flex-end;"><button onclick="toggleQueueStatus()" id="queue-toggle-btn" class="btn {'btn-success' if queue['settings']['open'] else 'btn-danger'}">{'🔓 Fechar Fila' if queue['settings']['open'] else '🔒 Abrir Fila'}</button></div>
                    </div>
                    <div id="queue-status-display" style="margin-top: 10px; padding: 10px; background: #1a1a1a; border-radius: 5px;">
                        Status: <strong>{'🟢 ABERTA' if queue['settings']['open'] else '🔴 FECHADA'}</strong> | Ocupação: {len(queue["entries"])} / {queue["settings"]["max_size"]}
                    </div>
                </div>
                
                <div class="card">
                    <h3>➕ Adicionar</h3>
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
                                <tr><th>#</th><th>Jogador</th><th>Serviço</th><th>Entrada</th><th>Ações</th></tr>
                            </thead>
                            <tbody id="queue-table-body">
                                <tr><td colspan="5">Carregando...</td></tr>
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
                    <div class="form-group"><label>URL da Lista (HTML)</label><input type="text" class="form-control" readonly value="{request.host_url}queue" onclick="this.select();"></div>
                    <div class="form-group"><label>URL Embed (Overlay)</label><input type="text" class="form-control" readonly value="{request.host_url}queue/embed" onclick="this.select();"></div>
                    <div class="form-group"><label>URL API (JSON)</label><input type="text" class="form-control" readonly value="{request.host_url}queue/api" onclick="this.select();"></div>
                </div>
            </div>
        </div>
        
        <script>
            const guildChannels = {channels_json};
            
            function showTab(tabId) {{
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                document.getElementById(tabId).classList.add('active');
                event.target.classList.add('active');
                if (tabId === 'queue') loadQueue();
                if (tabId === 'welcome' || tabId === 'xp' || tabId === 'roles') populateSelects();
                if (tabId === 'moderation') loadMembers();
            }}
            
            function populateSelects() {{
                const selects = ['welcome-channel', 'levelup-channel', 'rr-channel', 'btn-channel'];
                selects.forEach(id => {{
                    const select = document.getElementById(id);
                    if (select) {{
                        select.innerHTML = '<option value="">Selecione um canal</option>';
                        guildChannels.forEach(c => {{
                            const option = document.createElement('option');
                            option.value = c.id;
                            option.textContent = '#' + c.name;
                            select.appendChild(option);
                        }});
                    }}
                }});
                const wc = document.getElementById('welcome-channel');
                if (wc) wc.value = '{welcome_chan}';
                const lc = document.getElementById('levelup-channel');
                if (lc) lc.value = '{levelup_chan}';
            }}
            
            async function loadMembers() {{
                try {{
                    const resp = await fetch('/api/guild/members');
                    const data = await resp.json();
                    if (data.success) {{
                        const select = document.getElementById('warn-member');
                        if (select) {{
                            select.innerHTML = '<option value="">Selecione membro</option>';
                            data.members.forEach(m => {{
                                const opt = document.createElement('option');
                                opt.value = m.id;
                                opt.textContent = m.name;
                                select.appendChild(opt);
                            }});
                        }}
                    }}
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function saveWelcomeConfig() {{
                const data = {{
                    message: document.getElementById('welcome-message').value,
                    channel_id: document.getElementById('welcome-channel').value,
                    image_url: document.getElementById('welcome-image').value
                }};
                try {{
                    const resp = await fetch('/api/config/welcome', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(data)}});
                    const result = await resp.json();
                    showAlert('welcome-alert', result.message, result.success);
                }} catch(e) {{ showAlert('welcome-alert', 'Erro: ' + e.message, false); }}
            }}
            
            async function saveXPConfig() {{
                const data = {{rate: parseInt(document.getElementById('xp-rate').value), channel_id: document.getElementById('levelup-channel').value}};
                try {{
                    const resp = await fetch('/api/config/xp', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(data)}});
                    const result = await resp.json();
                    showAlert('xp-alert', result.message, result.success);
                }} catch(e) {{ showAlert('xp-alert', 'Erro: ' + e.message, false); }}
            }}
            
            async function createReactionRole() {{
                const channelId = document.getElementById('rr-channel').value;
                const content = document.getElementById('rr-content').value;
                const pairs = document.getElementById('rr-pair').value;
                if (!channelId || !content || !pairs) {{ showAlert('roles-alert', 'Preencha todos os campos', false); return; }}
                try {{
                    const resp = await fetch('/api/reactionrole/create', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{channel_id: channelId, content: content, emoji_cargo: pairs}})}});
                    const result = await resp.json();
                    showAlert('roles-alert', result.message, result.success);
                    if (result.success) {{ document.getElementById('rr-content').value = ''; document.getElementById('rr-pair').value = ''; }}
                }} catch(e) {{ showAlert('roles-alert', 'Erro: ' + e.message, false); }}
            }}
            
            async function createRoleButtons() {{
                const channelId = document.getElementById('btn-channel').value;
                const content = document.getElementById('btn-content').value;
                const pairs = document.getElementById('btn-pairs').value;
                if (!channelId || !content || !pairs) {{ showAlert('roles-alert', 'Preencha todos os campos', false); return; }}
                try {{
                    const resp = await fetch('/api/rolebuttons/create', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{channel_id: channelId, content: content, roles: pairs}})}});
                    const result = await resp.json();
                    showAlert('roles-alert', result.message, result.success);
                    if (result.success) {{ document.getElementById('btn-content').value = ''; document.getElementById('btn-pairs').value = ''; }}
                }} catch(e) {{ showAlert('roles-alert', 'Erro: ' + e.message, false); }}
            }}
            
            async function executeWarn() {{
                const memberId = document.getElementById('warn-member').value;
                const reason = document.getElementById('warn-reason').value;
                if (!memberId || !reason) {{ alert('Selecione membro e motivo'); return; }}
                try {{
                    const resp = await fetch('/api/command/warn', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{member_id: memberId, reason: reason}})}});
                    const result = await resp.json();
                    alert(result.message);
                    if (result.success) document.getElementById('warn-reason').value = '';
                }} catch(e) {{ alert('Erro: ' + e.message); }}
            }}
            
            async function clearWarns() {{
                const memberId = document.getElementById('warn-member').value;
                if (!memberId) {{ alert('Selecione um membro'); return; }}
                if (!confirm('Tem certeza?')) return;
                try {{
                    const resp = await fetch('/api/command/clearwarns', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{member_id: memberId}})}});
                    const result = await resp.json();
                    alert(result.message);
                }} catch(e) {{ alert('Erro: ' + e.message); }}
            }}
            
            async function loadQueue() {{
                try {{
                    const resp = await fetch('/queue/api');
                    const data = await resp.json();
                    if (data.success) {{
                        const queue = data.queue;
                        const tbody = document.getElementById('queue-table-body');
                        if (queue.entries.length === 0) {{
                            tbody.innerHTML = '<tr><td colspan="5">📭 Ninguém na fila</td></tr>';
                        }} else {{
                            tbody.innerHTML = queue.entries.map(e => `
                                <tr>
                                    <td><strong style="color:#ffd93d;">#${{e.position}}</strong></td>
                                    <td>${{escapeHtml(e.username)}}</td>
                                    <td style="color:#a8e6cf;">${{escapeHtml(e.service)}}</td>
                                    <td>${{new Date(e.timestamp).toLocaleTimeString()}}</td>
                                    <td>
                                        <button onclick="moveUp('${{e.id}}')" class="btn btn-primary" style="padding:4px 8px;">⬆️</button>
                                        <button onclick="moveDown('${{e.id}}')" class="btn btn-primary" style="padding:4px 8px;">⬇️</button>
                                        <button onclick="completeService('${{e.id}}')" class="btn btn-success" style="padding:4px 8px;">✅</button>
                                        <button onclick="removeFromQueue('${{e.id}}')" class="btn btn-danger" style="padding:4px 8px;">❌</button>
                                    </td>
                                </tr>
                            `).join('');
                        }}
                        document.getElementById('queue-status-display').innerHTML = `Status: <strong>${{queue.open ? '🟢 ABERTA' : '🔴 FECHADA'}}</strong> | Ocupação: ${{queue.count}} / ${{queue.max_size}}`;
                        const toggleBtn = document.getElementById('queue-toggle-btn');
                        if (toggleBtn) {{
                            toggleBtn.className = queue.open ? 'btn btn-danger' : 'btn btn-success';
                            toggleBtn.textContent = queue.open ? '🔓 Fechar Fila' : '🔒 Abrir Fila';
                        }}
                    }}
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function addToQueue() {{
                const username = document.getElementById('add-username').value.trim();
                const service = document.getElementById('add-service').value.trim();
                if (!username || !service) {{ showQueueAlert('add-result', 'Preencha nome e serviço', false); return; }}
                try {{
                    const resp = await fetch('/api/queue/add', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{username, service}})}});
                    const data = await resp.json();
                    showQueueAlert('add-result', data.message, data.success);
                    if (data.success) {{
                        document.getElementById('add-username').value = '';
                        document.getElementById('add-service').value = '';
                        loadQueue();
                    }}
                }} catch(e) {{ showQueueAlert('add-result', 'Erro: ' + e.message, false); }}
            }}
            
            async function removeFromQueue(entryId) {{
                if (!confirm('Remover esta pessoa da fila?')) return;
                try {{
                    await fetch('/api/queue/remove', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{entry_id: entryId}})}});
                    loadQueue();
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function moveUp(entryId) {{
                try {{
                    await fetch('/api/queue/move-up', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{entry_id: entryId}})}});
                    loadQueue();
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function moveDown(entryId) {{
                try {{
                    await fetch('/api/queue/move-down', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{entry_id: entryId}})}});
                    loadQueue();
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function completeService(entryId) {{
                if (!confirm('Marcar como concluído?')) return;
                try {{
                    await fetch('/api/queue/complete', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{entry_id: entryId}})}});
                    loadQueue();
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function clearQueue() {{
                if (!confirm('⚠️ LIMPAR TODA A FILA? Tem certeza?')) return;
                try {{
                    await fetch('/api/queue/clear', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}}});
                    loadQueue();
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function saveQueueSettings() {{
                const name = document.getElementById('queue-name').value;
                const max_size = parseInt(document.getElementById('queue-max-size').value);
                try {{
                    const resp = await fetch('/api/queue/settings', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{name, max_size}})}});
                    const data = await resp.json();
                    if (data.success) {{
                        showQueueAlert('add-result', 'Configurações salvas!', true);
                        setTimeout(() => {{ document.getElementById('add-result').style.display = 'none'; }}, 2000);
                        loadQueue();
                    }}
                }} catch(e) {{ console.error(e); }}
            }}
            
            async function toggleQueueStatus() {{
                try {{
                    await fetch('/api/queue/settings', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{open: null}})}});
                    loadQueue();
                }} catch(e) {{ console.error(e); }}
            }}
            
            function refreshQueue() {{ loadQueue(); }}
            
            function showAlert(id, msg, success) {{
                const el = document.getElementById(id);
                el.textContent = msg;
                el.className = 'alert ' + (success ? 'alert-success' : 'alert-error');
                el.style.display = 'block';
                setTimeout(() => el.style.display = 'none', 3000);
            }}
            
            function showQueueAlert(id, msg, success) {{
                const el = document.getElementById(id);
                el.textContent = msg;
                el.className = 'alert ' + (success ? 'alert-success' : 'alert-error');
                el.style.display = 'block';
                setTimeout(() => el.style.display = 'none', 3000);
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
                loadMembers();
                loadQueue();
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
    
    print(f"🏠 GUILDS CONECTADAS ({len(bot.guilds)}):")
    for i, guild in enumerate(bot.guilds, 1):
        print(f"  {i}. {guild.name} (ID: {guild.id}) - Membros: {len(guild.members)}")
    print(f"{'='*50}")
    
    if GUILD_ID:
        target_guild = bot.get_guild(int(GUILD_ID))
        if target_guild:
            print(f"🎯 GUILD ALVO: {target_guild.name}")
        else:
            print(f"⚠️ Guild alvo não encontrada! ID: {GUILD_ID}")
    
    print(f"{'='*50}")
    
    print("📂 Carregando dados do GitHub...")
    load_success = load_data_from_github()
    print(f"   {'✅ Dados carregados' if load_success else '⚠️ Usando dados locais'}")

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
        start_action_processor()
        print("✅ Sistema de ações INICIADO!")
    except Exception as e:
        print(f"❌ Erro ao iniciar sistema de ações: {e}")
    
    print(f"{'='*50}")
    print(f"✨ BOT PRONTO PARA USO!")
    print(f"{'='*50}\n")

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

    await interaction.response.send_message(f"✅ Taxa de XP ajustada para **x{rate}**.", ephemeral=False)

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
        "ts": now_br().strftime("%d/%m/%Y %H:%M")
    }
    data.setdefault("warns", {}).setdefault(uid, []).append(entry)
    save_data_to_github("New warn")
    add_log(f"warn: user={uid} by={interaction.user.id} reason={reason}")
    await interaction.response.send_message(f"⚠️ {member.mention} advertido.\nMotivo: {reason}")

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
async def slash_setwelcome(interaction: discord.Interaction, channel: discord.TextChannel = None):
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
