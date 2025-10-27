"""
Imune Bot - single file (main.py)
Features:
- Slash commands (discord.app_commands) para: /rank, /top, /warn, /warns, /savedata, /reactionrole (create/remove/list)
- Sistema de XP + levelup
- Embed de boas-vindas
- Reaction roles com suporte a emoji customizado
- Persistência de dados no GitHub
- Flask keepalive para Render free
"""

import os
import json
import base64
import re
import requests
from io import BytesIO
from threading import Thread
from zoneinfo import ZoneInfo
from datetime import datetime

from flask import Flask
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

# -------------------------
# Config / Ambiente
# -------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USER = os.getenv("GITHUB_USER", "pobonsanto-byte")
GITHUB_REPO = os.getenv("GITHUB_REPO", "imune-bot-data")
DATA_FILE = os.getenv("DATA_FILE", "data.json")
BRANCH = os.getenv("GITHUB_BRANCH", "main")
PORT = int(os.getenv("PORT", 8080))
GUILD_ID = os.getenv("GUILD_ID")

if not BOT_TOKEN or not GITHUB_TOKEN:
    raise SystemExit("Defina BOT_TOKEN e GITHUB_TOKEN nas variáveis de ambiente.")

GITHUB_API_CONTENT = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{DATA_FILE}"

# -------------------------
# Flask keepalive
# -------------------------
app = Flask("imunebot")

@app.route("/", methods=["GET"])
def home():
    return "Imune Bot is active!"

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

Thread(target=run_flask, daemon=True).start()

# -------------------------
# Bot setup
# -------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree

# -------------------------
# Função helper de horário BR
# -------------------------
def now_br():
    return datetime.now(ZoneInfo("America/Sao_Paulo"))

# -------------------------
# Estrutura de dados em memória
# -------------------------
data = {
    "xp": {},
    "level": {},
    "warns": {},
    "reaction_roles": {},
    "config": {"welcome_channel": None},
    "logs": []
}

# -------------------------
# GitHub persistence
# -------------------------
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
                print("Dados carregados do GitHub.")
                return True
        else:
            print(f"GitHub GET retornou {r.status_code} — iniciando com dados limpos.")
    except Exception as e:
        print("Erro ao carregar dados do GitHub:", e)
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
            print("Dados salvos no GitHub.")
            return True
        else:
            print("Erro ao salvar no GitHub:", put.status_code, put.text[:400])
    except Exception as e:
        print("Exception saving to GitHub:", e)
    return False

def add_log(entry):
    ts = now_br().isoformat()
    data.setdefault("logs", []).append({"ts": ts, "entry": entry})
    try:
        save_data_to_github(f"log: {entry}")
    except Exception:
        pass

# -------------------------
# XP / level
# -------------------------
def xp_for_message():
    return 15

def xp_to_level(xp):
    lvl = int((xp / 100) ** 0.6) + 1
    return max(lvl, 1)

# -------------------------
# Parse emoji
# -------------------------
EMOJI_RE = re.compile(r"<a?:([a-zA-Z0-9_]+):([0-9]+)>")

def parse_emoji_str(emoji_str, guild: discord.Guild = None):
    if not emoji_str:
        return None
    m = EMOJI_RE.match(emoji_str.strip())
    if m:
        name, id_str = m.groups()
        try:
            eid = int(id_str)
            if guild:
                e = discord.utils.get(guild.emojis, id=eid)
                if e:
                    return e
            return discord.PartialEmoji(name=name, id=eid)
        except Exception:
            pass
    return emoji_str

# -------------------------
# Eventos
# -------------------------
@bot.event
async def on_ready():
    print(f"Logado como {bot.user} (id: {bot.user.id})")
    load_data_from_github()
    try:
        if GUILD_ID:
            gid = int(GUILD_ID)
            guild = discord.Object(id=gid)
            await tree.sync(guild=guild)
            print(f"Comandos slash sincronizados no servidor {gid}.")
        else:
            await tree.sync()
            print("Comandos slash globais sincronizados.")
    except Exception as e:
        print("Erro ao sincronizar comandos:", e)

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

    # Mensagem de boas-vindas customizada
    welcome_msg = data.get("config", {}).get("welcome_message", "Olá {member}, seja bem-vindo(a)!")
    welcome_msg = welcome_msg.replace("{member}", member.mention)

    # ----- Criando a imagem -----
    width, height = 900, 300
    img = Image.new("RGBA", (width, height), (0, 0, 0, 255))

    # Fundo: banner do bot (ou avatar se não tiver)
    try:
        if bot.user.banner:
            banner_bytes = await bot.user.banner.read()
            banner = Image.open(BytesIO(banner_bytes)).convert("RGBA")
            banner = banner.resize((width, height))
            img.paste(banner, (0, 0))
        else:
            bot_avatar_bytes = await bot.user.avatar.read()
            bot_avatar = Image.open(BytesIO(bot_avatar_bytes)).convert("RGBA")
            bot_avatar = bot_avatar.resize((width, height))
            img.paste(bot_avatar, (0, 0))
    except Exception as e:
        print(f"Erro ao carregar banner/avatar do bot: {e}")

    # Overlay cinza semi-transparente
    overlay = Image.new("RGBA", (width, height), (50, 50, 50, 150))
    img = Image.alpha_composite(img, overlay)

    # Criar o draw depois do alpha_composite
    draw = ImageDraw.Draw(img)

    # Avatar do usuário com borda sólida suave
    try:
        user_bytes = await member.avatar.read()
        user_avatar = Image.open(BytesIO(user_bytes)).convert("RGBA")

        avatar_size = 150
        border_size = 10
        upscale = 4  # usado para suavizar borda
        big_size = (avatar_size + border_size * 2) * upscale

        # Redimensiona avatar
        user_avatar = user_avatar.resize((avatar_size * upscale, avatar_size * upscale))

        # Máscara circular em alta resolução (antialias)
        mask = Image.new("L", (avatar_size * upscale, avatar_size * upscale), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, avatar_size * upscale, avatar_size * upscale), fill=255)

        # Cria imagem com borda sólida
        border_color = (180, 120, 255, 255)  # roxo claro
        border = Image.new("RGBA", (big_size, big_size), (0, 0, 0, 0))
        draw_border = ImageDraw.Draw(border)
        draw_border.ellipse((0, 0, big_size, big_size), fill=border_color)

        # Cola avatar recortado dentro da borda
        border.paste(user_avatar, (border_size * upscale, border_size * upscale), mask)

        # Reduz suavemente para o tamanho final
        border = border.resize((avatar_size + border_size * 2, avatar_size + border_size * 2), Image.Resampling.LANCZOS)

        # Centraliza
        x = (width - border.width) // 2
        y = 30
        img.paste(border, (x, y), border)

    except Exception as e:
        print(f"Erro ao carregar avatar do usuário: {e}")

    # Texto com sombra
    try:
        font_b = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_s = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
    except:
        font_b = ImageFont.load_default()
        font_s = ImageFont.load_default()

    text_color = (180, 120, 255)
    shadow_color = (0, 0, 0, 180)

    # Nome do usuário
    text_name = member.display_name
    bbox_name = draw.textbbox((0, 0), text_name, font=font_b)
    text_w = bbox_name[2] - bbox_name[0]
    text_h = bbox_name[3] - bbox_name[1]
    text_x = (width - text_w) // 2
    text_y = y + border.height + 10

    draw.text((text_x + 2, text_y + 2), text_name, font=font_b, fill=shadow_color)
    draw.text((text_x, text_y), text_name, font=font_b, fill=text_color)

    # Contagem de membros
    text_count = f"Membro #{len(member.guild.members)}"
    bbox_count = draw.textbbox((0, 0), text_count, font=font_s)
    text_w2 = bbox_count[2] - bbox_count[0]
    text_h2 = bbox_count[3] - bbox_count[1]
    text_x2 = (width - text_w2) // 2
    text_y2 = text_y + 50

    draw.text((text_x2 + 1, text_y2 + 1), text_count, font=font_s, fill=shadow_color)
    draw.text((text_x2, text_y2), text_count, font=font_s, fill=text_color)

    # Salvar e enviar
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    file = discord.File(buf, filename="welcome.png")

    await channel.send(content=welcome_msg, file=file)
    add_log(f"member_join: {member.id} - {member}")






# -------------------------
# Reaction roles
# -------------------------
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    try:
        msgmap = data.get("reaction_roles", {}).get(str(payload.message_id))
        if not msgmap:
            return

        # Resolver o emoji
        role_id = None
        # Checa pelo ID (custom emoji)
        if payload.emoji.id and str(payload.emoji.id) in msgmap:
            role_id = msgmap[str(payload.emoji.id)]
        # Checa pelo nome (custom emoji)
        elif payload.emoji.id is not None and payload.emoji.name in msgmap:
            role_id = msgmap[payload.emoji.name]
        # Checa unicode
        elif str(payload.emoji) in msgmap:
            role_id = msgmap[str(payload.emoji)]

        if not role_id:
            return

        # Busca guild e member
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


# -------------------------
# Warn helper
# -------------------------
async def add_warn(member: discord.Member, reason=""):
    uid = str(member.id)
    entry = {"by": bot.user.id, "reason": reason, "ts": now_br().isoformat()}
    data.setdefault("warns", {}).setdefault(uid, []).append(entry)
    save_data_to_github("Auto-warn")
    add_log(f"warn: user={uid} by=bot reason={reason}")

# -------------------------
# on_message
# -------------------------
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    uid = str(message.author.id)
    content = message.content.strip()
    delete_message = False  # Flag para saber se devemos deletar e não dar XP

    # Histórico de mensagens do usuário
    user_msgs = data.setdefault("last_messages_content", {}).setdefault(uid, [])

    # Limita histórico para as últimas 5 mensagens
    if len(user_msgs) >= 5:
        user_msgs.pop(0)

    # Checa spam por mensagens repetidas
    if user_msgs and content == user_msgs[-1]:
        delete_message = True
        try:
            await message.delete()
        except discord.Forbidden:
            pass
        await message.channel.send(f"⚠️ {message.author.mention}, evite spam com mensagens repetidas!")
        await add_warn(message.author, reason="Spam detectado")
    else:
        user_msgs.append(content)

    data["last_messages_content"][uid] = user_msgs

    # Checa mensagens em maiúsculas
    if len(content) > 5 and content.isupper():
        delete_message = True
        try:
            await message.delete()
        except discord.Forbidden:
            pass
        await message.channel.send(f"⚠️ {message.author.mention}, evite escrever tudo em maiúsculas!")
        await add_warn(message.author, reason="Uso excessivo de maiúsculas")

    # Só dá XP se a mensagem não for deletada
    if not delete_message:
        data.setdefault("xp", {})
        data.setdefault("level", {})
        data["xp"][uid] = data["xp"].get(uid, 0) + xp_for_message()
        xp_now = data["xp"][uid]
        lvl_now = xp_to_level(xp_now)
        if lvl_now > data["level"].get(uid, 1):
            data["level"][uid] = lvl_now
            try:
                await message.channel.send(f"🎉 {message.author.mention} subiu para o nível **{lvl_now}**!")
            except Exception:
                pass
            add_log(f"level_up: user={uid} level={lvl_now}")

    # Salva dados
    try:
        save_data_to_github("XP update")
    except Exception:
        pass

    await bot.process_commands(message)


# -------------------------
# Slash commands
# -------------------------
def is_admin_check(interaction: discord.Interaction) -> bool:
    try:
        perms = interaction.user.guild_permissions
        return perms.administrator or perms.manage_guild or perms.manage_roles
    except Exception:
        return False

# /rank
@tree.command(name="rank", description="Rank estilo anime/fundo preto, XP dentro da barra")
@app_commands.describe(member="Membro a ver o rank (opcional)")
async def slash_rank(interaction: discord.Interaction, member: discord.Member = None):
    await interaction.response.defer(thinking=True)

    target = member or interaction.user
    uid = str(target.id)
    xp = data.get("xp", {}).get(uid, 0)
    lvl = data.get("level", {}).get(uid, xp_to_level(xp))

    ranking = sorted(data.get("xp", {}).items(), key=lambda t: t[1], reverse=True)
    pos = next((i+1 for i, (u, _) in enumerate(ranking) if u == uid), len(ranking))

    # Imagem base
    width, height = 900, 200
    img = Image.new("RGBA", (width, height), (0, 0, 0, 255))  # fundo preto
    draw = ImageDraw.Draw(img)

    # Fontes
    try:
        font_b = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
        font_s = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
    except:
        font_b = ImageFont.load_default()
        font_s = ImageFont.load_default()

    # Avatar circular
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

    # Nome do usuário
    draw.text((160, 50), target.display_name, font=font_b, fill=(0, 255, 255))

    # Classificação e nível no canto direito
    draw.text((width - 220, 40), f"CLASSIFICAÇÃO #{pos}", font=font_s, fill=(0, 255, 255))
    draw.text((width - 220, 80), f"NÍVEL {lvl}", font=font_s, fill=(255, 0, 255))

    # Barra XP arredondada
    next_xp = 100 + lvl*50
    cur = xp % next_xp
    bar_total_w, bar_h = 560, 36
    x0, y0 = 160, 140
    radius = bar_h // 2

    # Fundo da barra
    draw.rounded_rectangle([x0, y0, x0+bar_total_w, y0+bar_h], radius=radius, fill=(50, 50, 50))

    
    # Barra preenchida (gradiente azul neon) arredondada
    fill_w = int(bar_total_w * min(1.0, cur / next_xp))
    if fill_w > 0:
    # Cria a barra preenchida com mesmo raio que o fundo
        filled_bar = Image.new("RGBA", (fill_w, bar_h), (0,0,0,0))
        fill_draw = ImageDraw.Draw(filled_bar)
        fill_draw.rounded_rectangle([0, 0, fill_w, bar_h], radius=radius, fill=(0, 200, 255))
    
    # Se quiser gradiente, pode substituir fill por um gradiente similar ao que já fazia
        img.paste(filled_bar, (x0, y0), filled_bar)
        


    # Texto XP dentro da barra, centralizado verticalmente
    xp_text = f"{cur} / {next_xp} XP"
    bbox = draw.textbbox((0, 0), xp_text, font=font_s)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    text_x = x0 + (bar_total_w - text_w) // 2
    text_y = y0 + (bar_h - text_h) // 2
    draw.text((text_x, text_y), xp_text, font=font_s, fill=(255, 255, 255))

    # Enviar imagem
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    file = discord.File(buf, filename="rank.png")
    await interaction.followup.send(file=file)

# /setwelcome
@tree.command(name="setwelcome", description="Define a mensagem de boas-vindas (admin)")
@app_commands.describe(message="Mensagem (use {member} para mencionar)")
async def slash_setwelcome(interaction: discord.Interaction, message: str):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return

    data.setdefault("config", {})["welcome_message"] = message
    save_data_to_github("Set welcome message")
    await interaction.response.send_message(f"Mensagem de boas-vindas definida!\n{message}")


# /top
@tree.command(name="top", description="Mostra top 10 de XP")
async def slash_top(interaction: discord.Interaction):
    await interaction.response.defer()
    ranking = sorted(data.get("xp", {}).items(), key=lambda t: t[1], reverse=True)[:10]
    lines = []
    for i, (uid, xp) in enumerate(ranking, 1):
        lines.append(f"{i}. <@{uid}> — {xp} XP")
    text = "\n".join(lines) if lines else "Sem dados ainda."
    await interaction.followup.send(f"🏆 **Top 10 XP**\n{text}")

# /warn (admin)
@tree.command(name="warn", description="Advertir um membro (admin)")
@app_commands.describe(member="Membro a ser advertido", reason="Motivo da advertência")
async def slash_warn(interaction: discord.Interaction, member: discord.Member, reason: str = "Sem motivo informado"):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão para usar este comando.", ephemeral=True)
        return
    uid = str(member.id)
    entry = {"by": interaction.user.id, "reason": reason, "ts": datetime.utcnow().isoformat()}
    data.setdefault("warns", {}).setdefault(uid, []).append(entry)
    save_data_to_github("New warn")
    add_log(f"warn: user={uid} by={interaction.user.id} reason={reason}")
    await interaction.response.send_message(f"⚠️ {member.mention} advertido.\nMotivo: {reason}")

# /warns
@tree.command(name="warns", description="Mostra advertências de um membro")
@app_commands.describe(member="Membro (opcional)")
async def slash_warns(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    arr = data.get("warns", {}).get(str(target.id), [])
    if not arr:
        await interaction.response.send_message(f"{target.mention} não tem advertências.", ephemeral=False)
        return
    text = "\n".join([f"- {w['reason']} (por <@{w['by']}>) em {w['ts']}" for w in arr])
    await interaction.response.send_message(f"⚠️ Advertências de {target.mention}:\n{text}")

# /savedata (admin)
@tree.command(name="savedata", description="Força salvar dados no GitHub (admin)")
async def slash_savedata(interaction: discord.Interaction):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return
    ok = save_data_to_github("Manual save via /savedata")
    await interaction.response.send_message("Dados salvos no GitHub." if ok else "Falha ao salvar (veja logs).")

# /setwelcomechannel (admin)
@tree.command(name="setwelcomechannel", description="Define canal de boas-vindas para o bot (admin)")
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

# ReactionRole group: /reactionrole create /reactionrole remove /reactionrole list
reactionrole_group = app_commands.Group(name="reactionrole", description="Gerenciar reaction roles (admin)")

@reactionrole_group.command(name="create", description="Cria mensagem com reação e mapeia para um cargo (admin)")
@app_commands.describe(channel="Canal para enviar a mensagem", content="Conteúdo da mensagem", emoji="Emoji (custom <:_name_:id> ou unicode)", role="Cargo a ser atribuído")
async def rr_create(interaction: discord.Interaction, channel: discord.TextChannel, content: str, emoji: str, role: discord.Role):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=False)
    # Parse emoji (custom or unicode)
    parsed = parse_emoji_str(emoji, guild=interaction.guild)
    # Send message
    try:
        sent = await channel.send(content)
    except Exception as e:
        await interaction.followup.send(f"Falha ao enviar mensagem: {e}")
        return
    # Add reaction
    try:
        if isinstance(parsed, discord.Emoji) or isinstance(parsed, discord.PartialEmoji):
            await sent.add_reaction(parsed)
            # store mapping by emoji id string
            key = str(parsed.id)
        else:
            # unicode char
            await sent.add_reaction(parsed)
            key = str(parsed)
    except Exception as e:
        # cleanup: delete message if reaction failed?
        await interaction.followup.send(f"Falha ao reagir com o emoji: {e}")
        return
    # store mapping
    data.setdefault("reaction_roles", {}).setdefault(str(sent.id), {})[key] = str(role.id)
    save_data_to_github("reactionrole create")
    add_log(f"reactionrole created msg={sent.id} emoji={key} role={role.id}")
    await interaction.followup.send(f"Mensagem criada em {channel.mention} com ID `{sent.id}`. Reaja para receber o cargo {role.mention}.")

@reactionrole_group.command(name="remove", description="Remove mapeamento reaction-role de uma mensagem (admin)")
@app_commands.describe(message_id="ID da mensagem", emoji="Emoji usado quando criado")
async def rr_remove(interaction: discord.Interaction, message_id: str, emoji: str):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return
    mapping = data.get("reaction_roles", {}).get(str(message_id), {})
    if not mapping:
        await interaction.response.send_message("Nenhum mapeamento encontrado para essa mensagem.", ephemeral=True)
        return
    # normalize emoji keys: try id and raw string
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
    # if message mapping empty, remove message key
    if not mapping:
        data["reaction_roles"].pop(str(message_id), None)
    save_data_to_github("reactionrole remove")
    add_log(f"reactionrole removed msg={message_id} emoji={found}")
    await interaction.response.send_message("Removido com sucesso.", ephemeral=False)

@reactionrole_group.command(name="list", description="Lista reaction-roles configurados")
async def rr_list(interaction: discord.Interaction):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return
    rr = data.get("reaction_roles", {})
    if not rr:
        await interaction.response.send_message("Nenhum reaction-role configurado.", ephemeral=True)
        return
    lines = []
    for msgid, mapping in rr.items():
        parts = []
        for ekey, rid in mapping.items():
            parts.append(f"{ekey}→<@&{rid}>")
        lines.append(f"Msg `{msgid}`: " + ", ".join(parts))
    content = "\n".join(lines)
    # if too long, send as file
    if len(content) > 1900:
        await interaction.response.send_message("Resultado muito grande, enviando arquivo...", ephemeral=True)
        await interaction.followup.send(file=discord.File(BytesIO(content.encode()), filename="reactionroles.txt"))
    else:
        await interaction.response.send_message(f"Reaction roles:\n{content}", ephemeral=False)

# add the group to the tree
tree.add_command(reactionrole_group)

# -------------------------
# Start bot
# -------------------------
if __name__ == "__main__":
    try:
        bot.run(BOT_TOKEN)
    except Exception as e:
        print("Erro ao iniciar o bot:", e)
