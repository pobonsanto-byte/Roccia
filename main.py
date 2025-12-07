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

from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import discord
from discord import app_commands
from discord.ext import commands
from discord import ui, Interaction, ButtonStyle
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

# Configurações do site
CLIENT_ID = os.getenv("CLIENT_ID")  # ID do aplicativo Discord
CLIENT_SECRET = os.getenv("CLIENT_SECRET")  # Segredo do aplicativo Discord
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://seu-site.onrender.com/callback")
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))

if not BOT_TOKEN or not GITHUB_TOKEN:
    raise SystemExit("Defina BOT_TOKEN e GITHUB_TOKEN nas variáveis de ambiente.")

GITHUB_API_CONTENT = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{DATA_FILE}"

# -------------------------
# Flask App
# -------------------------
app = Flask(__name__)
app.secret_key = SECRET_KEY

# -------------------------
# Funções auxiliares do site
# -------------------------
@app.route("/", methods=["GET"])
def home():
    """Página inicial do site"""
    bot_online = bot.is_ready() if hasattr(bot, 'is_ready') else False
    return render_template_string(INDEX_TEMPLATE, 
                                  bot_online=bot_online,
                                  user=session.get('user'))

@app.route("/login")
def login():
    """Redireciona para o OAuth do Discord"""
    discord_auth_url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds"
    return redirect(discord_auth_url)

@app.route("/callback")
def callback():
    """Callback do OAuth do Discord"""
    code = request.args.get('code')
    if not code:
        return "Erro: código não recebido", 400
    
    # Troca o código por um token de acesso
    data = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI,
        'scope': 'identify guilds'
    }
    
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    
    r = requests.post('https://discord.com/api/oauth2/token', data=data, headers=headers)
    if r.status_code != 200:
        return f"Erro ao obter token: {r.text}", 400
    
    access_token = r.json()['access_token']
    
    # Obtém informações do usuário
    user_r = requests.get('https://discord.com/api/users/@me', headers={
        'Authorization': f'Bearer {access_token}'
    })
    
    if user_r.status_code != 200:
        return "Erro ao obter informações do usuário", 400
    
    user_data = user_r.json()
    
    # Verifica se o usuário é administrador do servidor
    guilds_r = requests.get('https://discord.com/api/users/@me/guilds', headers={
        'Authorization': f'Bearer {access_token}'
    })
    
    guilds = guilds_r.json() if guilds_r.status_code == 200 else []
    is_admin = False
    
    for guild in guilds:
        if str(guild['id']) == GUILD_ID and (guild['permissions'] & 0x8):  # 0x8 = Administrador
            is_admin = True
            break
    
    if not is_admin:
        return "Apenas administradores do servidor podem acessar este painel.", 403
    
    # Salva na sessão
    session['user'] = {
        'id': user_data['id'],
        'username': user_data['username'],
        'discriminator': user_data.get('discriminator', '0'),
        'avatar': user_data.get('avatar'),
        'is_admin': True
    }
    
    return redirect(url_for('dashboard'))

@app.route("/logout")
def logout():
    """Logout do usuário"""
    session.clear()
    return redirect(url_for('home'))

@app.route("/dashboard")
def dashboard():
    """Dashboard com formulários interativos"""
    if 'user' not in session:
        return redirect(url_for('login'))
    
    try:
        user = session['user']
        
        # Carrega dados
        welcome_message = data.get("config", {}).get("welcome_message", "Olá {member}, seja bem-vindo(a)!")
        xp_rate = data.get("config", {}).get("xp_rate", 3)
        welcome_background = data.get("config", {}).get("welcome_background", "")
        welcome_channel = data.get("config", {}).get("welcome_channel", "")
        levelup_channel = data.get("config", {}).get("levelup_channel", "")
        
        # Obtém informações da guild
        guild = None
        channels = []
        roles = []
        
        if GUILD_ID and bot.is_ready():
            try:
                guild = bot.get_guild(int(GUILD_ID))
                if guild:
                    channels = [{"id": c.id, "name": c.name} for c in guild.text_channels]
                    roles = [{"id": r.id, "name": r.name} for r in guild.roles if r.name != "@everyone"]
            except Exception as e:
                print(f"Erro ao carregar guild: {e}")
        
        # Converte listas para JSON seguro
        import json
        channels_json = json.dumps(channels, ensure_ascii=False)
        roles_json = json.dumps(roles, ensure_ascii=False)
        
        # HTML usando template string mais seguro
        html_template = '''
        <!DOCTYPE html>
        <html lang="pt-BR">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Dashboard - Imune Bot</title>
            <style>
                :root {
                    --primary: #5865F2;
                    --primary-dark: #4752C4;
                    --success: #28a745;
                    --danger: #dc3545;
                }
                body {
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    background: #f5f5f5;
                    margin: 0;
                }
                header {
                    background: white;
                    padding: 1rem 2rem;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                }
                .header-content {
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                }
                h1 {
                    color: var(--primary);
                    margin: 0;
                }
                .container {
                    max-width: 1200px;
                    margin: 2rem auto;
                    padding: 0 1rem;
                }
                .card {
                    background: white;
                    border-radius: 10px;
                    padding: 1.5rem;
                    margin: 1rem 0;
                    box-shadow: 0 2px 5px rgba(0,0,0,0.1);
                }
                .form-group {
                    margin-bottom: 1rem;
                }
                label {
                    display: block;
                    margin-bottom: 0.5rem;
                    font-weight: 600;
                }
                input, select, textarea {
                    width: 100%;
                    padding: 0.5rem;
                    border: 1px solid #ddd;
                    border-radius: 5px;
                    font-size: 1rem;
                }
                textarea {
                    resize: vertical;
                }
                .btn {
                    padding: 0.5rem 1rem;
                    border: none;
                    border-radius: 5px;
                    cursor: pointer;
                    font-weight: 600;
                    text-decoration: none;
                    display: inline-block;
                }
                .btn-primary {
                    background: var(--primary);
                    color: white;
                }
                .btn-primary:hover {
                    background: var(--primary-dark);
                }
                .alert {
                    padding: 1rem;
                    border-radius: 5px;
                    margin: 1rem 0;
                    display: none;
                }
                .alert-success {
                    background: #d4edda;
                    color: #155724;
                    border: 1px solid #c3e6cb;
                }
                .alert-error {
                    background: #f8d7da;
                    color: #721c24;
                    border: 1px solid #f5c6cb;
                }
                .tab {
                    display: none;
                }
                .tab.active {
                    display: block;
                }
                .tab-nav {
                    display: flex;
                    gap: 0.5rem;
                    margin-bottom: 1rem;
                    border-bottom: 2px solid #eee;
                    padding-bottom: 0.5rem;
                }
                .tab-btn {
                    padding: 0.5rem 1.5rem;
                    background: #e9ecef;
                    border: none;
                    border-radius: 5px 5px 0 0;
                    cursor: pointer;
                    font-weight: 600;
                }
                .tab-btn.active {
                    background: var(--primary);
                    color: white;
                }
                .stats-grid {
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                    gap: 1rem;
                    margin: 1rem 0;
                }
                .stat-card {
                    background: linear-gradient(135deg, var(--primary), var(--primary-dark));
                    color: white;
                    padding: 1.5rem;
                    border-radius: 10px;
                    text-align: center;
                }
                .stat-card h3 {
                    font-size: 2rem;
                    margin: 0;
                }
                small {
                    color: #666;
                    font-size: 0.875rem;
                }
            </style>
        </head>
        <body>
            <header>
                <div class="header-content">
                    <h1>🤖 Imune Bot Dashboard</h1>
                    <div>
                        <span style="margin-right: 1rem;">Olá, ''' + user.get('username', 'Usuário') + '''!</span>
                        <a href="/logout" class="btn btn-primary">Sair</a>
                    </div>
                </div>
            </header>
            
            <div class="container">
                <div class="tab-nav">
                    <button class="tab-btn active" onclick="showTab('overview')">📊 Visão Geral</button>
                    <button class="tab-btn" onclick="showTab('welcome')">👋 Boas-vindas</button>
                    <button class="tab-btn" onclick="showTab('xp')">⭐ Sistema XP</button>
                    <button class="tab-btn" onclick="showTab('commands')">⚙️ Comandos</button>
                </div>
                
                <!-- Tab: Visão Geral -->
                <div id="overview" class="tab active">
                    <div class="card">
                        <h2>📊 Estatísticas do Bot</h2>
                        <div class="stats-grid">
                            <div class="stat-card">
                                <h3>''' + str(len(data.get("xp", {}))) + '''</h3>
                                <p>Usuários com XP</p>
                            </div>
                            <div class="stat-card">
                                <h3>''' + str(len(data.get("warns", {}))) + '''</h3>
                                <p>Advertências</p>
                            </div>
                            <div class="stat-card">
                                <h3>''' + str(len(data.get("reaction_roles", {}))) + '''</h3>
                                <p>Reaction Roles</p>
                            </div>
                            <div class="stat-card">
                                <h3>''' + str(len(data.get("logs", []))) + '''</h3>
                                <p>Logs</p>
                            </div>
                        </div>
                    </div>
                    
                    <div class="card">
                        <h2>⚡ Status do Sistema</h2>
                        <p><strong>Status do Bot:</strong> ''' + ("✅ Online" if bot.is_ready() else "❌ Offline") + '''</p>
                        <p><strong>Servidor:</strong> ''' + (guild.name if guild else "Não conectado") + '''</p>
                        <p><strong>Membros no servidor:</strong> ''' + (str(len(guild.members)) if guild else "0") + '''</p>
                    </div>
                </div>
                
                <!-- Tab: Boas-vindas -->
                <div id="welcome" class="tab">
                    <div class="card">
                        <h2>👋 Configurações de Boas-vindas</h2>
                        
                        <div class="form-group">
                            <label>Canal de Boas-vindas</label>
                            <select id="welcome-channel" class="form-control">
                                <option value="">Selecione um canal</option>
                            </select>
                        </div>
                        
                        <div class="form-group">
                            <label>Mensagem de Boas-vindas</label>
                            <textarea id="welcome-message" class="form-control" rows="3">''' + welcome_message + '''</textarea>
                            <small>Use {member} para mencionar o novo membro</small>
                        </div>
                        
                        <div class="form-group">
                            <label>Imagem de Fundo (URL)</label>
                            <input type="url" id="welcome-image" class="form-control" 
                                   value="''' + welcome_background + '''" 
                                   placeholder="https://exemplo.com/imagem.jpg">
                            <small>Deixe vazio para usar imagem padrão</small>
                        </div>
                        
                        <button onclick="saveWelcomeConfig()" class="btn btn-primary">Salvar Configurações</button>
                        
                        <div id="welcome-alert" class="alert"></div>
                    </div>
                </div>
                
                <!-- Tab: Sistema XP -->
                <div id="xp" class="tab">
                    <div class="card">
                        <h2>⭐ Sistema de XP e Níveis</h2>
                        
                        <div class="form-group">
                            <label>Taxa de XP</label>
                            <input type="number" id="xp-rate" class="form-control" 
                                   value="''' + str(xp_rate) + '''" min="1" max="10">
                            <small>Quanto maior, mais difícil subir de nível (1 = normal, 10 = muito difícil)</small>
                        </div>
                        
                        <div class="form-group">
                            <label>Canal de Level Up</label>
                            <select id="levelup-channel" class="form-control">
                                <option value="">Selecione um canal</option>
                            </select>
                        </div>
                        
                        <button onclick="saveXPConfig()" class="btn btn-primary">Salvar Configurações</button>
                        
                        <div id="xp-alert" class="alert"></div>
                    </div>
                    
                    <div class="card">
                        <h3>🎭 Cargos por Nível</h3>
                        <div id="level-roles-container">
                            <p>Carregando cargos por nível...</p>
                        </div>
                        
                        <div class="form-group">
                            <label>Adicionar Novo Cargo por Nível</label>
                            <div style="display: flex; gap: 1rem; align-items: center;">
                                <input type="number" id="new-level" class="form-control" placeholder="Nível" min="1" style="flex: 1;">
                                <select id="new-role" class="form-control" style="flex: 2;">
                                    <option value="">Selecione um cargo</option>
                                </select>
                                <button onclick="addLevelRole()" class="btn btn-primary">Adicionar</button>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- Tab: Comandos -->
                <div id="commands" class="tab">
                    <div class="card">
                        <h2>⚙️ Configuração de Comandos</h2>
                        <p>Defina em quais canais cada comando pode ser usado.</p>
                        
                        <div id="commands-list">
                            <p>Carregando comandos...</p>
                        </div>
                        
                        <div id="commands-alert" class="alert"></div>
                    </div>
                </div>
            </div>
            
            <script>
                // Dados da guild
                const guildChannels = ''' + channels_json + ''';
                const guildRoles = ''' + roles_json + ''';
                
                // Sistema de tabs
                function showTab(tabId) {
                    // Esconde todas as tabs
                    document.querySelectorAll('.tab').forEach(tab => {
                        tab.classList.remove('active');
                    });
                    document.querySelectorAll('.tab-btn').forEach(btn => {
                        btn.classList.remove('active');
                    });
                    
                    // Mostra a tab selecionada
                    document.getElementById(tabId).classList.add('active');
                    event.target.classList.add('active');
                    
                    // Carrega dados específicos da tab
                    if (tabId === 'xp') {
                        loadLevelRoles();
                    } else if (tabId === 'commands') {
                        loadCommands();
                    }
                }
                
                // Preenche selects de canais e cargos
                function populateSelects() {
                    // Canais
                    const welcomeSelect = document.getElementById('welcome-channel');
                    const levelupSelect = document.getElementById('levelup-channel');
                    const newRoleSelect = document.getElementById('new-role');
                    
                    if (welcomeSelect && guildChannels) {
                        guildChannels.forEach(channel => {
                            const option = document.createElement('option');
                            option.value = channel.id;
                            option.textContent = '#' + channel.name;
                            welcomeSelect.appendChild(option.cloneNode(true));
                            if (levelupSelect) levelupSelect.appendChild(option);
                        });
                        
                        // Seleciona valores atuais
                        if ('welcome_channel' in {'welcome_channel': ''' + welcome_channel + '''}) {
                            welcomeSelect.value = ''' + welcome_channel + ''';
                        }
                        if ('levelup_channel' in {'levelup_channel': ''' + levelup_channel + '''}) {
                            levelupSelect.value = ''' + levelup_channel + ''';
                        }
                    }
                    
                    // Cargos
                    if (newRoleSelect && guildRoles) {
                        guildRoles.forEach(role => {
                            const option = document.createElement('option');
                            option.value = role.id;
                            option.textContent = role.name;
                            newRoleSelect.appendChild(option);
                        });
                    }
                }
                
                // Salvar configurações de boas-vindas
                async function saveWelcomeConfig() {
                    const data = {
                        message: document.getElementById('welcome-message').value,
                        channel_id: document.getElementById('welcome-channel').value,
                        image_url: document.getElementById('welcome-image').value
                    };
                    
                    try {
                        const response = await fetch('/api/config/welcome', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(data)
                        });
                        
                        const result = await response.json();
                        showAlert('welcome-alert', result.message || result.error, result.success);
                    } catch (error) {
                        showAlert('welcome-alert', 'Erro de conexão: ' + error.message, false);
                    }
                }
                
                // Salvar configurações de XP
                async function saveXPConfig() {
                    const rate = parseInt(document.getElementById('xp-rate').value);
                    if (rate < 1 || rate > 10) {
                        showAlert('xp-alert', 'Taxa de XP deve ser entre 1 e 10', false);
                        return;
                    }
                    
                    const data = {
                        rate: rate,
                        channel_id: document.getElementById('levelup-channel').value
                    };
                    
                    try {
                        const response = await fetch('/api/config/xp', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(data)
                        });
                        
                        const result = await response.json();
                        showAlert('xp-alert', result.message || result.error, result.success);
                    } catch (error) {
                        showAlert('xp-alert', 'Erro de conexão: ' + error.message, false);
                    }
                }
                
                // Carregar cargos por nível
                async function loadLevelRoles() {
                    try {
                        const response = await fetch('/api/level-roles');
                        const result = await response.json();
                        
                        const container = document.getElementById('level-roles-container');
                        if (!result.level_roles || Object.keys(result.level_roles).length === 0) {
                            container.innerHTML = '<p>Nenhum cargo por nível configurado.</p>';
                            return;
                        }
                        
                        let html = '<div style="display: flex; flex-wrap: wrap; gap: 0.5rem;">';
                        for (const [level, roleId] of Object.entries(result.level_roles)) {
                            const roleName = guildRoles.find(r => r.id == roleId)?.name || 'Cargo não encontrado';
                            html += `
                                <div style="background: #e9ecef; padding: 0.5rem 1rem; border-radius: 5px; display: flex; align-items: center; gap: 0.5rem;">
                                    <strong>Nível ${level}:</strong> ${roleName}
                                    <button onclick="removeLevelRole(${level})" style="background: #dc3545; color: white; border: none; border-radius: 3px; padding: 0.25rem 0.5rem; cursor: pointer;">×</button>
                                </div>
                            `;
                        }
                        html += '</div>';
                        container.innerHTML = html;
                    } catch (error) {
                        console.error('Erro ao carregar cargos por nível:', error);
                    }
                }
                
                // Adicionar cargo por nível
                async function addLevelRole() {
                    const level = document.getElementById('new-level').value;
                    const roleId = document.getElementById('new-role').value;
                    
                    if (!level || !roleId) {
                        showAlert('xp-alert', 'Preencha o nível e selecione um cargo', false);
                        return;
                    }
                    
                    try {
                        const response = await fetch('/api/level-roles', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ level: level, role_id: roleId })
                        });
                        
                        const result = await response.json();
                        if (result.success) {
                            document.getElementById('new-level').value = '';
                            document.getElementById('new-role').value = '';
                            loadLevelRoles();
                        }
                        showAlert('xp-alert', result.message || result.error, result.success);
                    } catch (error) {
                        showAlert('xp-alert', 'Erro: ' + error.message, false);
                    }
                }
                
                // Remover cargo por nível
                async function removeLevelRole(level) {
                    if (!confirm('Remover cargo do nível ' + level + '?')) return;
                    
                    try {
                        const response = await fetch('/api/level-roles?level=' + level, {
                            method: 'DELETE'
                        });
                        
                        const result = await response.json();
                        if (result.success) {
                            loadLevelRoles();
                        }
                        showAlert('xp-alert', result.message || result.error, result.success);
                    } catch (error) {
                        showAlert('xp-alert', 'Erro: ' + error.message, false);
                    }
                }
                
                // Carregar comandos
                async function loadCommands() {
                    const commands = {
                        'rank': 'Mostra o perfil de XP',
                        'perfil': 'Mostra o perfil de XP',
                        'top': 'Mostra ranking de XP',
                        'advertir': 'Adverte um membro',
                        'lista_de_advertência': 'Lista advertências'
                    };
                    
                    const container = document.getElementById('commands-list');
                    let html = '';
                    
                    for (const [cmd, desc] of Object.entries(commands)) {
                        html += `
                            <div class="form-group" style="margin-bottom: 1.5rem; padding: 1rem; background: #f8f9fa; border-radius: 5px;">
                                <h4 style="margin-top: 0;">/${cmd}</h4>
                                <p style="margin: 0.5rem 0; color: #666;">${desc}</p>
                                <div style="display: flex; gap: 1rem; align-items: center;">
                                    <select id="channel-${cmd}" class="form-control" style="flex: 1;">
                                        <option value="">Selecione um canal</option>
                                    </select>
                                    <button onclick="addCommandChannel('${cmd}')" class="btn btn-primary">Adicionar Canal</button>
                                </div>
                                <div id="channels-${cmd}" style="margin-top: 0.5rem;"></div>
                            </div>
                        `;
                    }
                    
                    container.innerHTML = html;
                    
                    // Preenche selects de canais para comandos
                    for (const cmd in commands) {
                        const select = document.getElementById('channel-' + cmd);
                        if (select && guildChannels) {
                            guildChannels.forEach(channel => {
                                const option = document.createElement('option');
                                option.value = channel.id;
                                option.textContent = '#' + channel.name;
                                select.appendChild(option);
                            });
                        }
                    }
                    
                    // Carrega canais configurados
                    loadCommandChannels();
                }
                
                // Carregar canais de comandos configurados
                async function loadCommandChannels() {
                    try {
                        const response = await fetch('/api/command-channels');
                        const result = await response.json();
                        
                        if (result.command_channels) {
                            for (const [cmd, channels] of Object.entries(result.command_channels)) {
                                updateCommandChannelsList(cmd, channels);
                            }
                        }
                    } catch (error) {
                        console.error('Erro ao carregar canais de comandos:', error);
                    }
                }
                
                // Atualizar lista de canais de um comando
                function updateCommandChannelsList(cmd, channelIds) {
                    const container = document.getElementById('channels-' + cmd);
                    if (!container) return;
                    
                    if (!channelIds || channelIds.length === 0) {
                        container.innerHTML = '<p style="color: #666; font-size: 0.875rem; margin: 0;">✅ Todos os canais permitidos</p>';
                        return;
                    }
                    
                    let html = '<p style="color: #666; font-size: 0.875rem; margin-bottom: 0.25rem;">Canais permitidos:</p>';
                    channelIds.forEach(channelId => {
                        const channel = guildChannels.find(c => c.id == channelId);
                        const channelName = channel ? '#' + channel.name : 'ID: ' + channelId;
                        html += `
                            <span style="display: inline-flex; align-items: center; background: #e9ecef; padding: 0.25rem 0.5rem; border-radius: 4px; margin: 0.25rem;">
                                ${channelName}
                                <button onclick="removeCommandChannel('${cmd}', '${channelId}')" 
                                        style="background: none; border: none; color: #dc3545; cursor: pointer; margin-left: 0.5rem; font-weight: bold;">
                                    ×
                                </button>
                            </span>
                        `;
                    });
                    
                    container.innerHTML = html;
                }
                
                // Adicionar canal para comando
                async function addCommandChannel(command) {
                    const select = document.getElementById('channel-' + command);
                    const channelId = select.value;
                    
                    if (!channelId) {
                        showAlert('commands-alert', 'Selecione um canal', false);
                        return;
                    }
                    
                    try {
                        const response = await fetch('/api/command-channels', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                command: command,
                                channel_id: channelId,
                                action: 'add'
                            })
                        });
                        
                        const result = await response.json();
                        if (result.success) {
                            select.value = '';
                            loadCommandChannels();
                        }
                        showAlert('commands-alert', result.message || result.error, result.success);
                    } catch (error) {
                        showAlert('commands-alert', 'Erro: ' + error.message, false);
                    }
                }
                
                // Remover canal de comando
                async function removeCommandChannel(command, channelId) {
                    if (!confirm('Remover este canal do comando /' + command + '?')) return;
                    
                    try {
                        const response = await fetch('/api/command-channels', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                command: command,
                                channel_id: channelId,
                                action: 'remove'
                            })
                        });
                        
                        const result = await response.json();
                        if (result.success) {
                            loadCommandChannels();
                        }
                        showAlert('commands-alert', result.message || result.error, result.success);
                    } catch (error) {
                        showAlert('commands-alert', 'Erro: ' + error.message, false);
                    }
                }
                
                // Mostrar alerta
                function showAlert(elementId, message, isSuccess) {
                    const alertEl = document.getElementById(elementId);
                    if (!alertEl) return;
                    
                    alertEl.textContent = message;
                    alertEl.className = 'alert ' + (isSuccess ? 'alert-success' : 'alert-error');
                    alertEl.style.display = 'block';
                    
                    setTimeout(() => {
                        alertEl.style.display = 'none';
                    }, 5000);
                }
                
                // Inicialização
                document.addEventListener('DOMContentLoaded', function() {
                    populateSelects();
                    // Carrega dados da tab ativa
                    if (document.getElementById('overview').classList.contains('active')) {
                        // Nada especial para overview
                    }
                });
            </script>
        </body>
        </html>
        '''
        
        return html_template
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"ERRO no dashboard: {e}")
        print(error_details)
        
        return f'''
        <!DOCTYPE html>
        <html>
        <head><title>Erro</title></head>
        <body>
            <h1>Erro no Dashboard</h1>
            <p><strong>Erro:</strong> {str(e)}</p>
            <pre style="background: #f0f0f0; padding: 10px; overflow: auto;">{error_details[:500]}</pre>
            <a href="/" class="btn btn-primary">Voltar</a>
        </body>
        </html>
        ''', 500

@app.route("/debug/detailed")
def debug_detailed():
    """Debug detalhado com todas as informações"""
    try:
        info = {
            "flask_session": dict(session) if session else {},
            "environment": {
                "CLIENT_ID": "DEFINIDO" if CLIENT_ID else "NÃO DEFINIDO",
                "CLIENT_SECRET": "DEFINIDO" if CLIENT_SECRET else "NÃO DEFINIDO",
                "GUILD_ID": GUILD_ID,
                "REDIRECT_URI": REDIRECT_URI,
                "RENDER_URL": os.environ.get("RENDER_EXTERNAL_URL"),
                "PORT": os.environ.get("PORT"),
            },
            "bot": {
                "ready": bot.is_ready() if hasattr(bot, 'is_ready') else False,
                "user": str(bot.user) if bot.user else "Não conectado",
                "guilds": len(bot.guilds) if hasattr(bot, 'guilds') else 0,
            },
            "data": {
                "has_data": bool(data),
                "xp_users": len(data.get("xp", {})),
                "warns": len(data.get("warns", {})),
                "config_keys": list(data.get("config", {}).keys()),
            }
        }
        
        # Tenta obter informações da guild
        if GUILD_ID and bot.is_ready():
            try:
                guild = bot.get_guild(int(GUILD_ID))
                if guild:
                    info["guild"] = {
                        "name": guild.name,
                        "id": guild.id,
                        "member_count": len(guild.members),
                        "channel_count": len(guild.channels),
                        "role_count": len(guild.roles),
                    }
            except Exception as e:
                info["guild_error"] = str(e)
        
        return jsonify(info)
        
    except Exception as e:
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500

@app.route("/api/config", methods=["POST"])
def api_config():
    """API para atualizar configurações"""
    if 'user' not in session or not session['user'].get('is_admin'):
        return jsonify({"error": "Não autorizado"}), 403
    
    config_data = request.json
    config_type = config_data.get('type')
    
    if config_type == 'welcome':
        # Atualizar mensagem de boas-vindas
        data.setdefault("config", {})["welcome_message"] = config_data.get('message', '')
        data.setdefault("config", {})["welcome_channel"] = config_data.get('channel_id', '')
        save_data_to_github("Configuração de boas-vindas atualizada via site")
        
    elif config_type == 'welcome_image':
        # Atualizar imagem de fundo
        data.setdefault("config", {})["welcome_background"] = config_data.get('url', '')
        save_data_to_github("Imagem de boas-vindas atualizada via site")
        
    elif config_type == 'xp':
        # Atualizar configurações de XP
        data.setdefault("config", {})["xp_rate"] = int(config_data.get('rate', 3))
        data.setdefault("config", {})["levelup_channel"] = config_data.get('channel_id', '')
        save_data_to_github("Configuração de XP atualizada via site")
        
    elif config_type == 'level_role':
        # Adicionar/remover cargo por nível
        level = str(config_data.get('level'))
        role_id = config_data.get('role_id', '')
        
        if role_id:
            data.setdefault("level_roles", {})[level] = role_id
        elif level in data.get("level_roles", {}):
            del data["level_roles"][level]
        
        save_data_to_github("Cargos por nível atualizados via site")
        
    elif config_type == 'command_channel':
        # Configurar canais para comandos
        command = config_data.get('command', '').lower()
        channel_id = config_data.get('channel_id', '')
        action = config_data.get('action', 'add')  # 'add' ou 'remove'
        
        cmd_channels = data.setdefault("command_channels", {})
        channels = cmd_channels.setdefault(command, [])
        
        if action == 'add' and channel_id not in channels:
            channels.append(channel_id)
        elif action == 'remove' and channel_id in channels:
            channels.remove(channel_id)
            
        save_data_to_github("Canais de comandos atualizados via site")
    
    return jsonify({"success": True})

# -------------------------
# APIs para configuração do bot
# -------------------------

@app.route("/api/config/welcome", methods=["POST"])
def api_config_welcome():
    """API para configurar mensagem de boas-vindas"""
    if 'user' not in session:
        return jsonify({"error": "Não autenticado"}), 401
    
    try:
        data = request.json
        message = data.get('message', '')
        channel_id = data.get('channel_id', '')
        
        # Atualiza configuração
        config = data.setdefault("config", {})
        if message:
            config["welcome_message"] = message
        if channel_id:
            config["welcome_channel"] = channel_id
        
        # Salva no GitHub
        success = save_data_to_github("Config via dashboard")
        
        return jsonify({
            "success": success,
            "message": "Configuração salva com sucesso!" if success else "Erro ao salvar"
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/config/xp", methods=["POST"])
def api_config_xp():
    """API para configurar sistema de XP"""
    if 'user' not in session:
        return jsonify({"error": "Não autenticado"}), 401
    
    try:
        data = request.json
        xp_rate = data.get('rate')
        levelup_channel = data.get('channel_id', '')
        
        # Validação
        if xp_rate is not None:
            if xp_rate < 1 or xp_rate > 10:
                return jsonify({"error": "Taxa de XP deve ser entre 1 e 10"}), 400
            data.setdefault("config", {})["xp_rate"] = xp_rate
        
        if levelup_channel:
            data.setdefault("config", {})["levelup_channel"] = levelup_channel
        
        # Salva no GitHub
        success = save_data_to_github("Config XP via dashboard")
        
        return jsonify({
            "success": success,
            "message": "Configuração de XP salva!" if success else "Erro ao salvar"
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/level-roles", methods=["GET", "POST", "DELETE"])
def api_level_roles():
    """API para gerenciar cargos por nível"""
    if 'user' not in session:
        return jsonify({"error": "Não autenticado"}), 401
    
    try:
        if request.method == "GET":
            # Retorna lista de cargos por nível
            level_roles = data.get("level_roles", {})
            return jsonify({"level_roles": level_roles})
        
        elif request.method == "POST":
            # Adiciona/atualiza cargo por nível
            req_data = request.json
            level = str(req_data.get('level'))
            role_id = req_data.get('role_id')
            
            if not level or not role_id:
                return jsonify({"error": "Nível e cargo são obrigatórios"}), 400
            
            data.setdefault("level_roles", {})[level] = role_id
            save_data_to_github(f"Add level role: nível {level}")
            
            return jsonify({"success": True, "message": f"Cargo definido para nível {level}"})
        
        elif request.method == "DELETE":
            # Remove cargo por nível
            level = request.args.get('level')
            if not level:
                return jsonify({"error": "Nível é obrigatório"}), 400
            
            if level in data.get("level_roles", {}):
                del data["level_roles"][level]
                save_data_to_github(f"Remove level role: nível {level}")
                return jsonify({"success": True, "message": f"Cargo removido do nível {level}"})
            else:
                return jsonify({"error": "Nível não encontrado"}), 404
                
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/command-channels", methods=["GET", "POST"])
def api_command_channels():
    """API para gerenciar canais de comandos"""
    if 'user' not in session:
        return jsonify({"error": "Não autenticado"}), 401
    
    try:
        command_channels = data.setdefault("command_channels", {})
        
        if request.method == "GET":
            return jsonify({"command_channels": command_channels})
        
        elif request.method == "POST":
            req_data = request.json
            command = req_data.get('command', '').lower()
            channel_id = req_data.get('channel_id')
            action = req_data.get('action', 'add')  # add ou remove
            
            if not command or not channel_id:
                return jsonify({"error": "Comando e canal são obrigatórios"}), 400
            
            channels = command_channels.setdefault(command, [])
            
            if action == 'add':
                if channel_id not in channels:
                    channels.append(channel_id)
                    message = f"Canal adicionado para /{command}"
            elif action == 'remove':
                if channel_id in channels:
                    channels.remove(channel_id)
                    message = f"Canal removido de /{command}"
                else:
                    return jsonify({"error": "Canal não encontrado para este comando"}), 404
            else:
                return jsonify({"error": "Ação inválida"}), 400
            
            save_data_to_github(f"Command channels update: {command}")
            return jsonify({"success": True, "message": message})
                
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/stats")
def api_stats():
    """API para obter estatísticas do bot"""
    if 'user' not in session or not session['user'].get('is_admin'):
        return jsonify({"error": "Não autorizado"}), 403
    
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID and bot.is_ready() else None
    
    stats = {
        "guild_name": guild.name if guild else "N/A",
        "member_count": len(guild.members) if guild else 0,
        "online_members": sum(1 for m in guild.members if m.status != discord.Status.offline) if guild else 0,
        "total_xp_users": len(data.get("xp", {})),
        "total_warns": sum(len(warns) for warns in data.get("warns", {}).values()),
        "reaction_roles": len(data.get("reaction_roles", {})),
        "role_buttons": len(data.get("role_buttons", {})),
        "bot_uptime": str(datetime.now() - bot.start_time) if hasattr(bot, 'start_time') else "N/A"
    }
    
    # Top 5 XP
    ranking = sorted(data.get("xp", {}).items(), key=lambda t: t[1], reverse=True)[:5]
    top_users = []
    
    for uid, xp in ranking:
        if guild:
            member = guild.get_member(int(uid))
            name = member.display_name if member else f"Usuário {uid}"
        else:
            name = f"Usuário {uid}"
        top_users.append({"name": name, "xp": xp, "level": xp_to_level(xp)})
    
    stats["top_users"] = top_users
    
    return jsonify(stats)

# -------------------------
# Templates HTML (inline)
# -------------------------
INDEX_TEMPLATE = '''
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Imune Bot - Dashboard</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            margin: 0;
            padding: 0;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .container {
            background: white;
            border-radius: 20px;
            padding: 40px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            text-align: center;
            max-width: 400px;
            width: 90%;
        }
        h1 {
            color: #333;
            margin-bottom: 20px;
        }
        .status {
            padding: 10px;
            border-radius: 10px;
            margin: 20px 0;
            font-weight: bold;
        }
        .online {
            background: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        .offline {
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        .btn {
            display: inline-block;
            background: #5865F2;
            color: white;
            padding: 12px 30px;
            border-radius: 8px;
            text-decoration: none;
            font-weight: bold;
            margin: 10px;
            transition: transform 0.2s, background 0.2s;
        }
        .btn:hover {
            background: #4752C4;
            transform: translateY(-2px);
        }
        .btn-logout {
            background: #dc3545;
        }
        .btn-logout:hover {
            background: #c82333;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 Imune Bot Dashboard</h1>
        <div class="status {{ 'online' if bot_online else 'offline' }}">
            {{ '✅ Bot Online' if bot_online else '❌ Bot Offline' }}
        </div>
        {% if user %}
            <p>Olá, <strong>{{ user.username }}</strong>!</p>
            <a href="/dashboard" class="btn">Painel de Controle</a>
            <a href="/logout" class="btn btn-logout">Sair</a>
        {% else %}
            <p>Faça login para configurar o bot</p>
            <a href="/login" class="btn">Login com Discord</a>
        {% endif %}
    </div>
</body>
</html>
'''

DASHBOARD_TEMPLATE = '''
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Painel de Controle - Imune Bot</title>
    <style>
        :root {
            --primary: #5865F2;
            --primary-dark: #4752C4;
            --success: #28a745;
            --danger: #dc3545;
            --warning: #ffc107;
            --dark: #343a40;
            --light: #f8f9fa;
            --gray: #6c757d;
        }
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #f5f5f5;
            color: #333;
        }
        header {
            background: white;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            padding: 1rem 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header-left h1 {
            color: var(--primary);
            font-size: 1.5rem;
        }
        .header-right {
            display: flex;
            align-items: center;
            gap: 1rem;
        }
        .user-info {
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        .avatar {
            width: 32px;
            height: 32px;
            border-radius: 50%;
        }
        .btn {
            padding: 0.5rem 1rem;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 600;
            text-decoration: none;
            display: inline-block;
            transition: all 0.2s;
        }
        .btn-primary {
            background: var(--primary);
            color: white;
        }
        .btn-primary:hover {
            background: var(--primary-dark);
        }
        .btn-danger {
            background: var(--danger);
            color: white;
        }
        .btn-danger:hover {
            background: #c82333;
        }
        .container {
            max-width: 1200px;
            margin: 2rem auto;
            padding: 0 1rem;
            display: grid;
            grid-template-columns: 250px 1fr;
            gap: 2rem;
        }
        .sidebar {
            background: white;
            border-radius: 10px;
            padding: 1.5rem;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            height: fit-content;
        }
        .nav-item {
            padding: 0.75rem 1rem;
            margin: 0.25rem 0;
            border-radius: 6px;
            cursor: pointer;
            transition: background 0.2s;
        }
        .nav-item:hover, .nav-item.active {
            background: var(--primary);
            color: white;
        }
        .content {
            background: white;
            border-radius: 10px;
            padding: 2rem;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        .section {
            display: none;
        }
        .section.active {
            display: block;
        }
        .section h2 {
            margin-bottom: 1.5rem;
            color: var(--dark);
            border-bottom: 2px solid var(--primary);
            padding-bottom: 0.5rem;
        }
        .form-group {
            margin-bottom: 1.5rem;
        }
        .form-group label {
            display: block;
            margin-bottom: 0.5rem;
            font-weight: 600;
            color: var(--dark);
        }
        .form-control {
            width: 100%;
            padding: 0.75rem;
            border: 1px solid #ddd;
            border-radius: 6px;
            font-size: 1rem;
        }
        .form-control:focus {
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 0 3px rgba(88, 101, 242, 0.1);
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin-bottom: 2rem;
        }
        .stat-card {
            background: linear-gradient(135deg, var(--primary), var(--primary-dark));
            color: white;
            padding: 1.5rem;
            border-radius: 10px;
            text-align: center;
        }
        .stat-card h3 {
            font-size: 2rem;
            margin-bottom: 0.5rem;
        }
        .alert {
            padding: 1rem;
            border-radius: 6px;
            margin: 1rem 0;
        }
        .alert-success {
            background: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        .alert-error {
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        .hidden {
            display: none;
        }
        .table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 1rem;
        }
        .table th, .table td {
            padding: 0.75rem;
            border-bottom: 1px solid #ddd;
            text-align: left;
        }
        .table th {
            background: #f8f9fa;
            font-weight: 600;
        }
        .table tr:hover {
            background: #f8f9fa;
        }
    </style>
</head>
<body>
    <header>
        <div class="header-left">
            <h1>🤖 Imune Bot Dashboard</h1>
        </div>
        <div class="header-right">
            <div class="user-info">
                <img src="https://cdn.discordapp.com/avatars/{{ user.id }}/{{ user.avatar }}.png" alt="Avatar" class="avatar">
                <span>{{ user.username }}</span>
            </div>
            <a href="/" class="btn btn-primary">Início</a>
            <a href="/logout" class="btn btn-danger">Sair</a>
        </div>
    </header>
    
    <div class="container">
        <div class="sidebar">
            <div class="nav-item active" onclick="showSection('overview')">📊 Visão Geral</div>
            <div class="nav-item" onclick="showSection('welcome')">👋 Boas-vindas</div>
            <div class="nav-item" onclick="showSection('xp')">⭐ Sistema XP</div>
            <div class="nav-item" onclick="showSection('commands')">⚙️ Comandos</div>
            <div class="nav-item" onclick="showSection('roles')">🎭 Cargos</div>
            <div class="nav-item" onclick="showSection('moderation')">🛡️ Moderação</div>
        </div>
        
        <div class="content">
            <!-- Seção: Visão Geral -->
            <div id="overview" class="section active">
                <h2>📊 Visão Geral do Bot</h2>
                <div id="stats-container">
                    <p>Carregando estatísticas...</p>
                </div>
                <div id="top-users">
                    <h3>🏆 Top 5 XP</h3>
                    <table class="table">
                        <thead>
                            <tr>
                                <th>Posição</th>
                                <th>Usuário</th>
                                <th>XP</th>
                                <th>Nível</th>
                            </tr>
                        </thead>
                        <tbody id="top-users-body">
                            <!-- Preenchido por JavaScript -->
                        </tbody>
                    </table>
                </div>
            </div>
            
            <!-- Seção: Boas-vindas -->
            <div id="welcome" class="section">
                <h2>👋 Configurações de Boas-vindas</h2>
                <form id="welcome-form">
                    <div class="form-group">
                        <label>Canal de Boas-vindas</label>
                        <select id="welcome-channel" class="form-control">
                            <option value="">Selecione um canal</option>
                            {% for channel in channels %}
                                <option value="{{ channel.id }}" {% if welcome_channel and channel.id == welcome_channel.id %}selected{% endif %}>
                                    #{{ channel.name }}
                                </option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Mensagem de Boas-vindas</label>
                        <textarea id="welcome-message" class="form-control" rows="4">{{ welcome_message }}</textarea>
                        <small>Use {member} para mencionar o novo membro</small>
                    </div>
                    <div class="form-group">
                        <label>Imagem de Fundo (URL)</label>
                        <input type="url" id="welcome-image" class="form-control" value="{{ welcome_background }}">
                        <small>Deixe em branco para usar imagem padrão</small>
                    </div>
                    <button type="submit" class="btn btn-primary">Salvar Configurações</button>
                </form>
                <div id="welcome-alert" class="alert hidden"></div>
            </div>
            
            <!-- Seção: Sistema XP -->
            <div id="xp" class="section">
                <h2>⭐ Sistema de XP e Níveis</h2>
                <form id="xp-form">
                    <div class="form-group">
                        <label>Taxa de XP (dificuldade)</label>
                        <input type="number" id="xp-rate" class="form-control" value="{{ xp_rate }}" min="1" max="10">
                        <small>Valores mais altos = mais difícil subir de nível</small>
                    </div>
                    <div class="form-group">
                        <label>Canal de Level Up</label>
                        <select id="levelup-channel" class="form-control">
                            <option value="">Selecione um canal</option>
                            {% for channel in channels %}
                                <option value="{{ channel.id }}" {% if levelup_channel and channel.id == levelup_channel.id %}selected{% endif %}>
                                    #{{ channel.name }}
                                </option>
                            {% endfor %}
                        </select>
                    </div>
                    <h3>Cargos por Nível</h3>
                    <div id="level-roles-container">
                        <!-- Cargos por nível serão adicionados aqui -->
                    </div>
                    <div class="form-group">
                        <label>Adicionar Novo Cargo por Nível</label>
                        <div style="display: flex; gap: 1rem;">
                            <input type="number" id="new-level" class="form-control" placeholder="Nível" min="1">
                            <select id="new-role" class="form-control">
                                <option value="">Selecione um cargo</option>
                                {% for role in roles|reverse %}
                                    {% if role.name != "@everyone" %}
                                        <option value="{{ role.id }}">{{ role.name }}</option>
                                    {% endif %}
                                {% endfor %}
                            </select>
                            <button type="button" onclick="addLevelRole()" class="btn btn-primary">Adicionar</button>
                        </div>
                    </div>
                    <button type="submit" class="btn btn-primary">Salvar Configurações</button>
                </form>
                <div id="xp-alert" class="alert hidden"></div>
            </div>
            
            <!-- Seção: Comandos -->
            <div id="commands" class="section">
                <h2>⚙️ Configuração de Comandos</h2>
                <p>Configure em quais canais cada comando pode ser usado</p>
                <div id="commands-list">
                    <!-- Lista de comandos será preenchida por JavaScript -->
                </div>
                <div id="commands-alert" class="alert hidden"></div>
            </div>
            
            <!-- Seção: Cargos -->
            <div id="roles" class="section">
                <h2>🎭 Gerenciamento de Cargos</h2>
                <p>Esta seção permite configurar reaction roles e botões de cargos</p>
                <p><em>Use os comandos do bot para configurar reaction roles e botões de cargos:</em></p>
                <ul>
                    <li><code>/reajir_com_emoji criar</code> - Cria uma mensagem com reaction role</li>
                    <li><code>/criar_reação_com_botao</code> - Cria mensagem com botões de cargos</li>
                    <li><code>/cargo_xp</code> - Define cargo para ser atribuído por nível</li>
                </ul>
            </div>
            
            <!-- Seção: Moderação -->
            <div id="moderation" class="section">
                <h2>🛡️ Configurações de Moderação</h2>
                <div class="form-group">
                    <label>Canais com Links Bloqueados</label>
                    <div id="blocked-channels-list">
                        <!-- Canais bloqueados serão listados aqui -->
                    </div>
                    <div style="display: flex; gap: 1rem; margin-top: 1rem;">
                        <select id="block-channel-select" class="form-control">
                            <option value="">Selecione um canal</option>
                            {% for channel in channels %}
                                <option value="{{ channel.id }}">#{{ channel.name }}</option>
                            {% endfor %}
                        </select>
                        <button type="button" onclick="toggleBlockChannel()" class="btn btn-primary">Bloquear/Desbloquear Links</button>
                    </div>
                </div>
                <div id="moderation-alert" class="alert hidden"></div>
            </div>
        </div>
    </div>
    
    <script>
        // Funções JavaScript para o dashboard
        let currentSection = 'overview';
        
        function showSection(sectionId) {
            // Atualiza navegação
            document.querySelectorAll('.nav-item').forEach(item => {
                item.classList.remove('active');
            });
            event.target.classList.add('active');
            
            // Esconde todas as seções
            document.querySelectorAll('.section').forEach(section => {
                section.classList.remove('active');
            });
            
            // Mostra a seção selecionada
            document.getElementById(sectionId).classList.add('active');
            currentSection = sectionId;
            
            // Carrega dados específicos da seção
            if (sectionId === 'overview') {
                loadStats();
            } else if (sectionId === 'xp') {
                loadLevelRoles();
            } else if (sectionId === 'commands') {
                loadCommands();
            } else if (sectionId === 'moderation') {
                loadBlockedChannels();
            }
        }
        
        function loadStats() {
            fetch('/api/stats')
                .then(response => response.json())
                .then(data => {
                    if (data.error) {
                        document.getElementById('stats-container').innerHTML = 
                            `<div class="alert alert-error">${data.error}</div>`;
                        return;
                    }
                    
                    // Atualiza estatísticas
                    document.getElementById('stats-container').innerHTML = `
                        <div class="stats-grid">
                            <div class="stat-card">
                                <h3>${data.member_count}</h3>
                                <p>Membros</p>
                            </div>
                            <div class="stat-card">
                                <h3>${data.online_members}</h3>
                                <p>Online</p>
                            </div>
                            <div class="stat-card">
                                <h3>${data.total_xp_users}</h3>
                                <p>Usuários com XP</p>
                            </div>
                            <div class="stat-card">
                                <h3>${data.reaction_roles}</h3>
                                <p>Reaction Roles</p>
                            </div>
                        </div>
                    `;
                    
                    // Atualiza top usuários
                    let topUsersHTML = '';
                    data.top_users.forEach((user, index) => {
                        topUsersHTML += `
                            <tr>
                                <td>${index + 1}</td>
                                <td>${user.name}</td>
                                <td>${user.xp}</td>
                                <td>${user.level}</td>
                            </tr>
                        `;
                    });
                    document.getElementById('top-users-body').innerHTML = topUsersHTML;
                })
                .catch(error => {
                    console.error('Erro ao carregar estatísticas:', error);
                });
        }
        
        function loadLevelRoles() {
            // Carrega cargos por nível do data.json
            const levelRoles = {{ data.get('level_roles', {})|tojson|safe }};
            let html = '';
            
            for (const [level, roleId] of Object.entries(levelRoles)) {
                const roleName = Array.from({{ roles|tojson|safe }}).find(r => r.id == roleId)?.name || 'Cargo não encontrado';
                html += `
                    <div class="form-group" style="display: flex; align-items: center; gap: 1rem;">
                        <span><strong>Nível ${level}:</strong> ${roleName}</span>
                        <button type="button" onclick="removeLevelRole(${level})" class="btn btn-danger">Remover</button>
                    </div>
                `;
            }
            
            document.getElementById('level-roles-container').innerHTML = html || 
                '<p>Nenhum cargo por nível configurado.</p>';
        }
        
        function loadCommands() {
            const commands = {
                'rank': 'Mostra o perfil de XP',
                'top': 'Mostra o ranking de XP',
                'perfil': 'Mostra o perfil de XP',
                'advertir': 'Adverte um membro',
                'lista_de_advertência': 'Lista advertências',
                'savedata': 'Força salvamento de dados',
                'xp_rate': 'Define taxa de XP',
                'cargo_xp': 'Define cargo por nível',
                'mensagem_personalizada': 'Cria mensagem personalizada'
            };
            
            let html = '';
            for (const [cmd, desc] of Object.entries(commands)) {
                html += `
                    <div class="form-group">
                        <h4>/${cmd}</h4>
                        <p><small>${desc}</small></p>
                        <div style="display: flex; gap: 1rem; align-items: center;">
                            <select id="channel-${cmd}" class="form-control" style="flex: 1;">
                                <option value="">Selecione um canal para adicionar</option>
                                {% for channel in channels %}
                                    <option value="{{ channel.id }}">#{{ channel.name }}</option>
                                {% endfor %}
                            </select>
                            <button type="button" onclick="addCommandChannel('${cmd}')" class="btn btn-primary">Adicionar Canal</button>
                        </div>
                        <div id="channels-${cmd}" style="margin-top: 0.5rem;">
                            <!-- Canais permitidos serão listados aqui -->
                        </div>
                    </div>
                `;
            }
            
            document.getElementById('commands-list').innerHTML = html;
            
            // Carrega canais permitidos para cada comando
            const commandChannels = {{ data.get('command_channels', {})|tojson|safe }};
            for (const [cmd, channels] of Object.entries(commandChannels)) {
                updateCommandChannelsList(cmd, channels);
            }
        }
        
        function loadBlockedChannels() {
            const blockedChannels = {{ data.get('blocked_links_channels', [])|tojson|safe }};
            const container = document.getElementById('blocked-channels-list');
            
            if (!blockedChannels || blockedChannels.length === 0) {
                container.innerHTML = '<p>Nenhum canal com links bloqueados.</p>';
                return;
            }
            
            let html = '<div style="display: flex; flex-wrap: wrap; gap: 0.5rem;">';
            blockedChannels.forEach(channelId => {
                const channelName = Array.from({{ channels|tojson|safe }}).find(c => c.id == channelId)?.name || `ID: ${channelId}`;
                html += `
                    <span style="display: inline-block; background: #e9ecef; padding: 0.5rem 1rem; border-radius: 4px;">
                        #${channelName}
                    </span>
                `;
            });
            html += '</div>';
            
            container.innerHTML = html;
        }
        
        function updateCommandChannelsList(cmd, channelIds) {
            const container = document.getElementById(`channels-${cmd}`);
            if (!container) return;
            
            if (!channelIds || channelIds.length === 0) {
                container.innerHTML = '<p><small>Todos os canais permitidos</small></p>';
                return;
            }
            
            let html = '<p><small>Canais permitidos:</small></p>';
            channelIds.forEach(channelId => {
                const channelName = Array.from({{ channels|tojson|safe }}).find(c => c.id == channelId)?.name || `ID: ${channelId}`;
                html += `
                    <span style="display: inline-block; background: #e9ecef; padding: 0.25rem 0.5rem; border-radius: 4px; margin: 0.25rem;">
                        #${channelName}
                        <button type="button" onclick="removeCommandChannel('${cmd}', ${channelId})" style="background: none; border: none; color: #dc3545; cursor: pointer; margin-left: 0.5rem;">×</button>
                    </span>
                `;
            });
            
            container.innerHTML = html;
        }
        
        // Formulários
        document.getElementById('welcome-form')?.addEventListener('submit', function(e) {
            e.preventDefault();
            
            const data = {
                type: 'welcome',
                channel_id: document.getElementById('welcome-channel').value,
                message: document.getElementById('welcome-message').value,
                url: document.getElementById('welcome-image').value
            };
            
            saveConfig(data, 'welcome-alert');
        });
        
        document.getElementById('xp-form')?.addEventListener('submit', function(e) {
            e.preventDefault();
            
            const data = {
                type: 'xp',
                rate: document.getElementById('xp-rate').value,
                channel_id: document.getElementById('levelup-channel').value
            };
            
            saveConfig(data, 'xp-alert');
        });
        
        function saveConfig(data, alertId) {
            fetch('/api/config', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(data)
            })
            .then(response => response.json())
            .then(result => {
                const alertEl = document.getElementById(alertId);
                if (result.success) {
                    alertEl.className = 'alert alert-success';
                    alertEl.textContent = '✅ Configuração salva com sucesso!';
                } else {
                    alertEl.className = 'alert alert-error';
                    alertEl.textContent = '❌ Erro ao salvar configuração: ' + (result.error || 'Erro desconhecido');
                }
                alertEl.classList.remove('hidden');
                
                // Esconde a mensagem após 3 segundos
                setTimeout(() => {
                    alertEl.classList.add('hidden');
                }, 3000);
            })
            .catch(error => {
                const alertEl = document.getElementById(alertId);
                alertEl.className = 'alert alert-error';
                alertEl.textContent = '❌ Erro de conexão: ' + error;
                alertEl.classList.remove('hidden');
            });
        }
        
        function addLevelRole() {
            const level = document.getElementById('new-level').value;
            const roleId = document.getElementById('new-role').value;
            
            if (!level || !roleId) {
                alert('Preencha todos os campos');
                return;
            }
            
            const data = {
                type: 'level_role',
                level: level,
                role_id: roleId
            };
            
            saveConfig(data, 'xp-alert');
            
            // Limpa campos
            document.getElementById('new-level').value = '';
            document.getElementById('new-role').value = '';
            
            // Atualiza lista
            setTimeout(loadLevelRoles, 500);
        }
        
        function removeLevelRole(level) {
            if (!confirm(`Remover cargo do nível ${level}?`)) return;
            
            const data = {
                type: 'level_role',
                level: level,
                role_id: ''
            };
            
            saveConfig(data, 'xp-alert');
            setTimeout(loadLevelRoles, 500);
        }
        
        function addCommandChannel(command) {
            const channelId = document.getElementById(`channel-${command}`).value;
            
            if (!channelId) {
                alert('Selecione um canal');
                return;
            }
            
            const data = {
                type: 'command_channel',
                command: command,
                channel_id: channelId,
                action: 'add'
            };
            
            saveConfig(data, 'commands-alert');
            
            // Atualiza lista
            setTimeout(() => {
                document.getElementById(`channel-${command}`).value = '';
                // Simula atualização
                const currentChannels = Array.from({{ data.get('command_channels', {})|tojson|safe }}[command] || []);
                if (!currentChannels.includes(channelId)) {
                    currentChannels.push(channelId);
                }
                updateCommandChannelsList(command, currentChannels);
            }, 500);
        }
        
        function removeCommandChannel(command, channelId) {
            if (!confirm(`Remover este canal do comando /${command}?`)) return;
            
            const data = {
                type: 'command_channel',
                command: command,
                channel_id: channelId,
                action: 'remove'
            };
            
            saveConfig(data, 'commands-alert');
            
            // Atualiza lista
            setTimeout(() => {
                const currentChannels = Array.from({{ data.get('command_channels', {})|tojson|safe }}[command] || []);
                const index = currentChannels.indexOf(channelId);
                if (index > -1) {
                    currentChannels.splice(index, 1);
                }
                updateCommandChannelsList(command, currentChannels);
            }, 500);
        }
        
        function toggleBlockChannel() {
            const channelId = document.getElementById('block-channel-select').value;
            
            if (!channelId) {
                alert('Selecione um canal');
                return;
            }
            
            // Como não temos API específica para isso, mostra mensagem
            const alertEl = document.getElementById('moderation-alert');
            alertEl.className = 'alert alert-success';
            alertEl.textContent = '✅ Use o comando /bloquear_links no Discord para bloquear/desbloquear links neste canal.';
            alertEl.classList.remove('hidden');
            
            setTimeout(() => {
                alertEl.classList.add('hidden');
            }, 5000);
        }
        
        // Carrega dados na inicialização
        document.addEventListener('DOMContentLoaded', function() {
            loadStats();
            loadLevelRoles();
            loadCommands();
            loadBlockedChannels();
        });
    </script>
</body>
</html>
'''

def render_template_string(template, **context):
    """Renderiza uma string como template Jinja2"""
    from jinja2 import Template
    return Template(template).render(**context)

# -------------------------
# Auto ping (manter bot ativo)
# -------------------------
def auto_ping():
    while True:
        try:
            url = os.environ.get("REPLIT_URL") or os.environ.get("SELF_URL")
            if url:
                requests.get(url)
            time.sleep(300)  # ping a cada 5 minutos
        except Exception as e:
            print(f"Erro no auto-ping: {e}")

Thread(target=auto_ping, daemon=True).start()

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
# Função de horário BR
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
# emoji
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
# múltiplos botões
# -------------------------
class PersistentRoleButtonView(ui.View):
    def __init__(self, message_id: int, buttons_dict: dict):
        """
        message_id: ID da mensagem que contém os botões
        buttons_dict = {
            "Nome do Botão 1": role_id1,
            "Nome do Botão 2": role_id2,
        }
        """
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

        # Log
        add_log(f"role_button_click: user={member.id} role={role.id} message={self.message_id}")

# -------------------------
# Eventos
# -------------------------
@bot.event
async def on_ready():
    bot.start_time = datetime.now()
    print(f"Logado como {bot.user} (id: {bot.user.id})")
    load_data_from_github()

    # Sincronizar comandos slash
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

    # ---------- Reconstruir botões persistentes ----------
    for msg_id_str, buttons_dict in data.get("role_buttons", {}).items():
        try:
            msg_id = int(msg_id_str)
            message = None
            for guild in bot.guilds:
                for channel in guild.text_channels:
                    try:
                        message = await channel.fetch_message(msg_id)
                        break
                    except Exception:
                        continue
                if message:
                    break
            if message:
                view = PersistentRoleButtonView(msg_id, buttons_dict)
                await message.edit(view=view)
                print(f"Role Buttons restaurados para mensagem {msg_id}")
        except Exception as e:
            print(f"Erro ao restaurar role buttons para a mensagem {msg_id_str}: {e}")


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

    # ----- Imagem de fundo personalizada -----
    background_path = data.get("config", {}).get("welcome_background")


    width, height = 900, 300
    img = Image.new("RGBA", (width, height), (0, 0, 0, 255))

    # Fundo (baixa via URL)
    try:
        import requests
        response = requests.get(background_path)
        bg = Image.open(BytesIO(response.content)).convert("RGBA")
        bg = bg.resize((width, height))
        img.paste(bg, (0, 0))
    except Exception as e:
        print(f"Erro ao carregar imagem de fundo: {e}")

    # Overlay cinza translúcido para melhorar contraste do texto
    overlay = Image.new("RGBA", (width, height), (50, 50, 50, 150))
    img = Image.alpha_composite(img, overlay)

    draw = ImageDraw.Draw(img)

    # Avatar do usuário centralizado com borda roxa clara e sem pixelar
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

        border_color = (200, 150, 255, 255)  # roxo mais claro
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

    # ----- Texto -----
    try:
        font_b = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_s = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
    except:
        font_b = ImageFont.load_default()
        font_s = ImageFont.load_default()

    text_color = (200, 150, 255)
    shadow_color = (0, 0, 0, 180)

    # Nome do usuário
    text_name = member.display_name
    bbox_name = draw.textbbox((0, 0), text_name, font=font_b)
    text_w = bbox_name[2] - bbox_name[0]
    text_x = (width - text_w) // 2
    text_y = y + border.height + 10

    draw.text((text_x + 2, text_y + 2), text_name, font=font_b, fill=shadow_color)
    draw.text((text_x, text_y), text_name, font=font_b, fill=text_color)

    # Contagem de membros
    text_count = f"Membro #{len(member.guild.members)}"
    bbox_count = draw.textbbox((0, 0), text_count, font=font_s)
    text_w2 = bbox_count[2] - bbox_count[0]
    text_x2 = (width - text_w2) // 2
    text_y2 = text_y + 50

    draw.text((text_x2 + 1, text_y2 + 1), text_count, font=font_s, fill=shadow_color)
    draw.text((text_x2, text_y2), text_count, font=font_s, fill=text_color)

    # ----- Enviar mensagem -----
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
    entry = {
        "by": bot.user.id,
        "reason": reason,
        "ts": now_br().strftime("%d/%m/%Y %H:%M")  # dia/mês/ano hora:minuto
    }
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
    delete_message = False

    # -------- IGNORAR COMANDOS DO MUDAE --------
    mudae_commands = [
        "$w", "$wa", "$wg", "$h", "$ha", "$hg",
        "$W", "$WA", "$WG", "$H", "$HA", "$HG",
        "$tu", "$TU", "$dk", "$mmi", "$vote", "$rolls", "$k", "$mu"
    ]
    if any(content.lower().startswith(cmd) for cmd in mudae_commands):
        await bot.process_commands(message)
        return

    # -------- IGNORAR ADVERTÊNCIAS PARA ADM E MOD --------
    ignored_roles = {"Administrador", "Moderador"}
    member_roles = {r.name for r in message.author.roles}
    is_staff = any(role in ignored_roles for role in member_roles)

    # -------- IGNORAR MÍDIA (imagem, vídeo, gif, sticker, arquivo) --------
    has_media = False

    # Imagens/vídeos/arquivos
    if message.attachments:
        has_media = True

    # Stickers
    if message.stickers:
        has_media = True

    # GIFs de sites conhecidos
    gif_domains = ["tenor.com", "media.tenor.com", "giphy.com", "imgur.com"]
    if any(domain in content.lower() for domain in gif_domains):
        has_media = True

    if has_media:
        await bot.process_commands(message)
        return

    # -------- BLOQUEIO DE LINKS --------
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

    # -------- ANTI-SPAM --------
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

    # -------- SISTEMA DE XP --------
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

    # -------- SALVAR DADOS --------
    try:
        save_data_to_github("XP update")
    except Exception as e:
        print(f"Erro ao salvar XP: {e}")

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
        
def is_command_allowed(interaction: discord.Interaction, command_name: str) -> bool:
    allowed = data.get("command_channels", {}).get(command_name, [])
    # Se nenhum canal estiver configurado, o comando é liberado em todos
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


# -------------------------
# /setxprate — ajusta a taxa de ganho de XP
# -------------------------
@tree.command(name="xp_rate", description="Define a taxa de ganho de XP (admin)")
@app_commands.describe(rate="Taxa de XP — valores menores tornam o up mais lento (ex: 1 = normal, 2 = 2x mais difícil, 4 = 4x mais difícil)")
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

    # Converter a cor de string para objeto Color
    try:
        color = discord.Color(int(cor.replace("#", ""), 16))
    except:
        color = discord.Color.blurple()

    # 🔹 Formatar o texto da descrição
    formatted_text = corpo.replace("\\n", "\n").strip()

    # Substitui marcadores por ● (grande e sólido)
    formatted_text = formatted_text.replace("- ", "● ").replace("• ", "● ")

    # Adiciona espaçamento entre linhas
    lines = formatted_text.split("\n")
    formatted_text = "\n\n".join(line.strip() for line in lines if line.strip())

    # Cria a embed
    embed = discord.Embed(
        title=f"**{titulo}**",  # sem emoji
        description=formatted_text,
        color=color
    )

    # Imagem (se fornecida)
    if imagem:
        embed.set_image(url=imagem)

    # Envia a embed
    mention_text = mencionar if mencionar in ["@everyone", "@here"] else ""
    await canal.send(content=mention_text, embed=embed)
    await interaction.response.send_message(f"✅ Embed enviada para {canal.mention}.", ephemeral=True)


# -------------------------
# /setwelcomeimage - Define ou remove a imagem de fundo da mensagem de boas-vindas
# -------------------------
@tree.command(name="selecionar_imagem_boas-vindas", description="Define ou remove a imagem de fundo da mensagem de boas-vindas (admin)")
@app_commands.describe(url="URL da imagem que será usada no fundo (deixe vazio para remover)")
async def slash_setwelcomeimage(interaction: discord.Interaction, url: str = None):
    if not is_admin_check(interaction):
        await interaction.response.send_message("❌ Você não tem permissão para usar este comando.", ephemeral=True)
        return

    config = data.setdefault("config", {})

    # --- Remover imagem personalizada ---
    if not url:
        if "welcome_background" in config:
            del config["welcome_background"]
            save_data_to_github("Unset welcome background")
            await interaction.response.send_message("🧹 Imagem de fundo personalizada removida. Voltará a usar a padrão.", ephemeral=False)
        else:
            await interaction.response.send_message("ℹ️ Nenhuma imagem personalizada estava configurada.", ephemeral=True)
        return

    # --- Validar URL ---
    if not (url.startswith("http://") or url.startswith("https://")):
        await interaction.response.send_message("❌ Forneça uma URL válida começando com http:// ou https://", ephemeral=True)
        return

    # --- Salvar nova imagem ---
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

# -------------------------
# Comando para criar mensagem com botões
# -------------------------
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

    # Criar dicionário de botões
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

    # Envia mensagem
    view = PersistentRoleButtonView(0, buttons_dict)  # temporário, substituiremos depois pelo ID
    sent = await channel.send(content=content, view=view)

    # Atualiza view com ID real da mensagem
    view.message_id = sent.id
    for item in view.children:
        if isinstance(item, PersistentRoleButton):
            item.message_id = sent.id

    # Salva no data.json
    data.setdefault("role_buttons", {})[str(sent.id)] = buttons_dict
    save_data_to_github("Create role buttons")

    await interaction.response.send_message(f"Mensagem criada em {channel.mention} com {len(buttons_dict)} botões.", ephemeral=True)


# Comando para bloquear/desbloquear links em um canal
@tree.command(name="bloquear_links", description="Bloqueia ou desbloqueia links em um canal (admin)")
@app_commands.describe(channel="Canal para bloquear/desbloquear links")
async def block_links(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return

    data.setdefault("blocked_links_channels", [])
    
    if channel.id in data["blocked_links_channels"]:
        # Remove o bloqueio
        data["blocked_links_channels"].remove(channel.id)
        save_data_to_github("Unblock links channel")
        await interaction.response.send_message(f"✅ Links desbloqueados no canal {channel.mention}.")
    else:
        # Adiciona o bloqueio
        data["blocked_links_channels"].append(channel.id)
        save_data_to_github("Block links channel")
        await interaction.response.send_message(f"✅ Links bloqueados no canal {channel.mention}.")


# /perfil
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

# /definir_boas-vindas
@tree.command(name="definir_boas-vindas", description="Define a mensagem de boas-vindas (admin)")
@app_commands.describe(message="Mensagem (use {member} para mencionar)")
async def slash_setwelcome(interaction: discord.Interaction, message: str):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return

    data.setdefault("config", {})["welcome_message"] = message
    save_data_to_github("Set welcome message")
    await interaction.response.send_message(f"Mensagem de boas-vindas definida!\n{message}")


# /rank
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

# /advertir
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
        "ts": datetime.utcnow().strftime("%d/%m/%Y %H:%M")  # formato dia/mês/ano hora:minuto
    }
    data.setdefault("warns", {}).setdefault(uid, []).append(entry)
    save_data_to_github("New warn")
    add_log(f"warn: user={uid} by={interaction.user.id} reason={reason}")
    await interaction.response.send_message(f"⚠️ {member.mention} advertido.\nMotivo: {reason}")

# /lista_de_advertência
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

# /savedata (admin)
@tree.command(name="savedata", description="Força salvar dados no GitHub (admin)")
async def slash_savedata(interaction: discord.Interaction):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return
    ok = save_data_to_github("Manual save via /savedata")
    await interaction.response.send_message("Dados salvos no GitHub." if ok else "Falha ao salvar (veja logs).")

# /definir_canal_boas-vindas (admin)
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
        
#/Canal_xp
@tree.command(name="canal_xp", description="Define o canal onde serão enviadas as mensagens de level up (admin)")
@app_commands.describe(channel="Canal onde o bot vai enviar as mensagens de level up")
async def set_levelup_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return

    data.setdefault("config", {})["levelup_channel"] = channel.id
    save_data_to_github("Set level up channel")

    await interaction.response.send_message(f"✅ Canal de level up definido para {channel.mention}.", ephemeral=False)


# reajir_com_emoji
reactionrole_group = app_commands.Group(name="reajir_com_emoji", description="Gerenciar reaction roles (admin)")

@reactionrole_group.command(name="criar", description="Cria mensagem com reação e mapeia para um cargo (admin)")
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

    # Processa os pares emoji:cargo
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

        # Adiciona reação e salva
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
    # if too long, send as file
    if len(content) > 1900:
        await interaction.response.send_message("Resultado muito grande, enviando arquivo...", ephemeral=True)
        await interaction.followup.send(file=discord.File(BytesIO(content.encode()), filename="reactionroles.txt"))
    else:
        await interaction.response.send_message(f"Reaction roles:\n{content}", ephemeral=False)

# add the group to the tree
tree.add_command(reactionrole_group)

# -------------------------
# Start bot and Flask
# -------------------------
def run_flask():
    """Inicia o servidor Flask"""
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# Inicia o Flask em uma thread separada
Thread(target=run_flask, daemon=True).start()

if __name__ == "__main__":
    try:
        bot.run(BOT_TOKEN)
    except Exception as e:
        print("Erro ao iniciar o bot:", e)
