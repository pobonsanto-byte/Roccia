"""
Imune Bot - single file (main.py)
Features:
- Slash commands (discord.app_commands) for: /rank, /top, /warn, /warns, /savedata, /reactionrole (create/remove/list)
- XP system + levelups
- Welcome embed on member join
- Reaction roles with custom emoji support (creates message + adds reaction)
- Persist data to GitHub repo (DATA_FILE) via GitHub REST API
- Flask keepalive for Render free
"""

import os
import json
import base64
import re
import requests
from io import BytesIO
from datetime import datetime
from threading import Thread

from flask import Flask
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont



# -------------------------
# Environment / Config
# -------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USER = os.getenv("GITHUB_USER", "pobonsanto-byte")
GITHUB_REPO = os.getenv("GITHUB_REPO", "imune-bot-data")
DATA_FILE = os.getenv("DATA_FILE", "data.json")
BRANCH = os.getenv("GITHUB_BRANCH", "main")
PORT = int(os.getenv("PORT", 8080))
# If provided, register slash commands to this guild only (fast). Otherwise global.
GUILD_ID = os.getenv("GUILD_ID")  # optional

if not BOT_TOKEN or not GITHUB_TOKEN:
    raise SystemExit("Defina BOT_TOKEN e GITHUB_TOKEN nas variáveis de ambiente.")

GITHUB_API_CONTENT = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{DATA_FILE}"

# -------------------------
# Flask keepalive (Render)
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

bot = commands.Bot(command_prefix="/", intents=intents)  # prefix unused, slash commands used
tree = bot.tree

# -------------------------
# In-memory data structure
# -------------------------
# data schema:
# {
#   "xp": { user_id: xp_int, ... },
#   "level": { user_id: level_int, ... },
#   "warns": { user_id: [ {by:int, reason:str, ts:str}, ... ], ... },
#   "reaction_roles": { message_id_str: { emoji_key: role_id_str, ... }, ... },
#   "config": { "welcome_channel": channel_id_str or None }
#   "logs": [ {ts, entry}, ... ]
# }
data = {
    "xp": {},
    "level": {},
    "warns": {},
    "reaction_roles": {},
    "config": {"welcome_channel": None},
    "logs": []
}

# -------------------------
# Utils: GitHub persistence
# -------------------------
def _gh_headers():
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

def load_data_from_github():
    """Load data.json from GitHub if exists, else keep defaults."""
    try:
        r = requests.get(GITHUB_API_CONTENT, headers=_gh_headers(), params={"ref": BRANCH}, timeout=15)
        if r.status_code == 200:
            js = r.json()
            content_b64 = js.get("content", "")
            if content_b64:
                raw = base64.b64decode(content_b64)
                loaded = json.loads(raw.decode("utf-8"))
                # Merge loaded into data (avoid replacing if structure missing fields)
                data.update(loaded)
                print("Dados carregados do GitHub.")
                return True
        else:
            print(f"GitHub GET returned {r.status_code} — starting with fresh data.")
    except Exception as e:
        print("Erro ao carregar dados do GitHub:", e)
    return False

def save_data_to_github(message="Bot update"):
    """Save `data` to DATA_FILE in GitHub. This creates or updates the file."""
    try:
        # Get existing file sha (if exists)
        r = requests.get(GITHUB_API_CONTENT, headers=_gh_headers(), params={"ref": BRANCH}, timeout=15)
        sha = None
        if r.status_code == 200:
            sha = r.json().get("sha")

        content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        payload = {
            "message": f"{message} @ {datetime.utcnow().isoformat()}",
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
    ts = datetime.utcnow().isoformat()
    data.setdefault("logs", []).append({"ts": ts, "entry": entry})
    # best-effort save (non-blocking in this simple implementation)
    try:
        save_data_to_github(f"log: {entry}")
    except Exception:
        pass

# -------------------------
# Utilities: XP / level
# -------------------------
def xp_for_message():
    return 15

def xp_to_level(xp):
    # Tunable formula. Returns int level >=1
    # Use a slightly exponential progression
    lvl = int((xp / 100) ** 0.6) + 1
    if lvl < 1:
        lvl = 1
    return lvl

# -------------------------
# Helper: parse custom emoji string like "<:name:123456789012345678>"
# returns discord.PartialEmoji or plain unicode char
# -------------------------
EMOJI_RE = re.compile(r"<a?:([a-zA-Z0-9_]+):([0-9]+)>")

def parse_emoji_str(emoji_str, guild: discord.Guild = None):
    """Try to resolve custom emoji ID, or return emoji_str for unicode emoji."""
    if not emoji_str:
        return None
    m = EMOJI_RE.match(emoji_str.strip())
    if m:
        name, id_str = m.groups()
        try:
            eid = int(id_str)
            # Try to get the emoji object from cache
            if guild:
                e = discord.utils.get(guild.emojis, id=eid)
                if e:
                    return e  # Regular Emoji object
            # fallback to PartialEmoji
            return discord.PartialEmoji(name=name, id=eid)
        except Exception:
            pass
    # maybe user sent emoji directly (unicode)
    return emoji_str

# -------------------------
# Events
# -------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id: {bot.user.id})")
    # load data from GitHub at startup
    load_data_from_github()
    # sync slash commands
    try:
        if GUILD_ID:
            gid = int(GUILD_ID)
            guild = discord.Object(id=gid)
            await tree.sync(guild=guild)
            print(f"Slash commands synced to guild {gid}.")
        else:
            await tree.sync()
            print("Global slash commands synced.")
    except Exception as e:
        print("Erro ao sincronizar comandos:", e)

@bot.event
async def on_member_join(member: discord.Member):
    # send welcome embed to configured channel or try channel named 'boas-vindas'
    ch_id = data.get("config", {}).get("welcome_channel")
    channel = None
    if ch_id:
        try:
            channel = member.guild.get_channel(int(ch_id))
        except Exception:
            channel = None
    if not channel:
        channel = discord.utils.get(member.guild.text_channels, name="boas-vindas")
    if channel:
        embed = discord.Embed(title="Seja bem-vindo(a)! 🎉", color=0x6EC1FF)
        embed.add_field(name="Boas-vindas", value=f"Olá {member.mention}, seja bem-vindo(a)!", inline=False)
        embed.set_footer(text=f"Membros: {len(member.guild.members)}")
        await channel.send(embed=embed)
    add_log(f"member_join: {member.id} - {member}")

# -------------------------
# Reaction Roles corrigido
# -------------------------
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    try:
        msgmap = data.get("reaction_roles", {}).get(str(payload.message_id))
        if not msgmap:
            return

        # Resolver o emoji
        key1 = str(payload.emoji)  # unicode ou <:name:id>
        key2 = getattr(payload.emoji, "id", None)
        if key2:
            key2 = str(key2)
        role_id = None

        # Preferência: id > string > name
        if key2 and key2 in msgmap:
            role_id = msgmap[key2]
        elif key1 in msgmap:
            role_id = msgmap[key1]
        else:
            name_key = getattr(payload.emoji, "name", None)
            if name_key and name_key in msgmap:
                role_id = msgmap[name_key]
        if not role_id:
            return

        # Pega guild e member (fetch se não cacheado)
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

        key1 = str(payload.emoji)
        key2 = getattr(payload.emoji, "id", None)
        if key2:
            key2 = str(key2)
        role_id = None

        if key2 and key2 in msgmap:
            role_id = msgmap[key2]
        elif key1 in msgmap:
            role_id = msgmap[key1]
        else:
            name_key = getattr(payload.emoji, "name", None)
            if name_key and name_key in msgmap:
                role_id = msgmap[name_key]
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
# Helper para warn automático
# -------------------------
async def add_warn(member: discord.Member, reason=""):
    uid = str(member.id)
    entry = {"by": bot.user.id, "reason": reason, "ts": datetime.utcnow().isoformat()}
    data.setdefault("warns", {}).setdefault(uid, []).append(entry)
    save_data_to_github("Auto-warn")
    add_log(f"warn: user={uid} by=bot reason={reason}")

# -------------------------
# on_message atualizado
# -------------------------
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    uid = str(message.author.id)
    now_ts = datetime.utcnow().timestamp()

    # --- Anti-spam: mensagens repetidas em 10s ---
    last_msgs = data.setdefault("last_messages", {}).setdefault(uid, [])
    last_msgs = [t for t in last_msgs if now_ts - t < 10]  # mantém só últimos 10s
    if last_msgs:
        await message.channel.send(f"⚠️ {message.author.mention}, evite spam!")
        await add_warn(message.author, reason="Spam detectado")
    last_msgs.append(now_ts)
    data["last_messages"][uid] = last_msgs

    # --- Caps lock excessivo ---
    content = message.content.strip()
    if len(content) > 5 and content.isupper():
        await message.channel.send(f"⚠️ {message.author.mention}, evite escrever tudo em maiúsculas!")
        await add_warn(message.author, reason="Uso excessivo de maiúsculas")

    # --- XP / levelup ---
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

    # Best-effort save
    try:
        save_data_to_github("XP update")
    except Exception:
        pass

    # Processa comandos normalmente
    await bot.process_commands(message)

# -------------------------
# Slash commands (app_commands)
# -------------------------
# Helper: check admin
def is_admin_check(interaction: discord.Interaction) -> bool:
    try:
        perms = interaction.user.guild_permissions
        return perms.administrator or perms.manage_guild or perms.manage_roles
    except Exception:
        return False

# /rank
@tree.command(name="rank", description="Mostra seu rank (imagem)")
@app_commands.describe(member="Membro a ver o rank (opcional)")
async def slash_rank(interaction: discord.Interaction, member: discord.Member = None):
    await interaction.response.defer()
    target = member or interaction.user
    uid = str(target.id)
    xp = data.get("xp", {}).get(uid, 0)
    lvl = data.get("level", {}).get(uid, xp_to_level(xp))
    # position
    ranking = sorted(data.get("xp", {}).items(), key=lambda t: t[1], reverse=True)
    pos = next((i+1 for i, (u, _) in enumerate(ranking) if u == uid), len(ranking))
    # generate image
    img = Image.new("RGBA", (800, 220), (28, 28, 28, 255))
    draw = ImageDraw.Draw(img)
    try:
        font_b = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
        font_s = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except Exception:
        font_b = ImageFont.load_default()
        font_s = ImageFont.load_default()
    draw.text((20, 30), f"{target.display_name}", font=font_b, fill=(230,230,230))
    draw.text((20, 80), f"Nível: {lvl}   •   XP: {xp}   •   Posição: #{pos}", font=font_s, fill=(200,200,200))
    # progress bar
    next_xp = 100 + lvl * 50
    cur = xp % next_xp
    perc = min(1.0, cur / next_xp if next_xp > 0 else 0.0)
    bar_total_w = 740
    bar_h = 28
    x0, y0 = 30, 140
    draw.rectangle([x0, y0, x0 + bar_total_w, y0 + bar_h], fill=(60,60,60))
    draw.rectangle([x0, y0, x0 + int(bar_total_w * perc), y0 + bar_h], fill=(80,180,255))
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    file = discord.File(buf, filename="rank.png")
    await interaction.followup.send(file=file)

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
