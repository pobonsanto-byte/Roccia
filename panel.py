import os
import json
import base64
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from functools import wraps
from datetime import datetime
import secrets

# Configurações
BOT_TOKEN = os.getenv("BOT_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USER = os.getenv("GITHUB_USER", "pobonsanto-byte")
GITHUB_REPO = os.getenv("GITHUB_REPO", "imune-bot-data")
DATA_FILE = os.getenv("DATA_FILE", "data.json")
BRANCH = os.getenv("GITHUB_BRANCH", "main")
SECRET_KEY = os.getenv("PANEL_SECRET_KEY", secrets.token_hex(16))
ADMIN_PASSWORD = os.getenv("PANEL_ADMIN_PASSWORD", "admin123")  # Mude isso!

GITHUB_API_CONTENT = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{DATA_FILE}"

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ========== Funções Auxiliares ==========
def _gh_headers():
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

def load_data():
    """Carrega dados do GitHub"""
    try:
        r = requests.get(GITHUB_API_CONTENT, headers=_gh_headers(), params={"ref": BRANCH}, timeout=15)
        if r.status_code == 200:
            js = r.json()
            content_b64 = js.get("content", "")
            if content_b64:
                raw = base64.b64decode(content_b64)
                return json.loads(raw.decode("utf-8"))
    except Exception as e:
        print(f"Erro ao carregar dados: {e}")
    return {}

def save_data(data_dict, message="Update via Painel Web"):
    """Salva dados no GitHub"""
    try:
        r = requests.get(GITHUB_API_CONTENT, headers=_gh_headers(), params={"ref": BRANCH}, timeout=15)
        sha = None
        if r.status_code == 200:
            sha = r.json().get("sha")

        content = json.dumps(data_dict, ensure_ascii=False, indent=2).encode("utf-8")
        payload = {
            "message": f"{message} @ {datetime.now().isoformat()}",
            "content": base64.b64encode(content).decode("utf-8"),
            "branch": BRANCH
        }
        if sha:
            payload["sha"] = sha

        put = requests.put(GITHUB_API_CONTENT, headers=_gh_headers(), json=payload, timeout=30)
        return put.status_code in (200, 201)
    except Exception as e:
        print(f"Erro ao salvar: {e}")
        return False

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash('Faça login para acessar o painel', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ========== Rotas de Autenticação ==========
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == ADMIN_PASSWORD:
            session['logged_in'] = True
            flash('Login realizado com sucesso!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Senha incorreta', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash('Logout realizado', 'info')
    return redirect(url_for('login'))

# ========== Dashboard Principal ==========
@app.route('/')
@login_required
def dashboard():
    data = load_data()
    
    # Estatísticas
    stats = {
        'total_users': len(data.get('xp', {})),
        'total_warns': sum(len(warns) for warns in data.get('warns', {}).values()),
        'role_buttons': len(data.get('role_buttons', {})),
        'reaction_roles': len(data.get('reaction_roles', {})),
        'blocked_channels': len(data.get('blocked_links_channels', []))
    }
    
    # Top 5 XP
    ranking = sorted(data.get('xp', {}).items(), key=lambda x: x[1], reverse=True)[:5]
    
    return render_template('dashboard.html', stats=stats, ranking=ranking, data=data)

# ========== Configurações do Sistema ==========
@app.route('/config/system', methods=['GET', 'POST'])
@login_required
def system_config():
    data = load_data()
    config = data.get('config', {})
    
    if request.method == 'POST':
        # XP Rate
        xp_rate = request.form.get('xp_rate')
        if xp_rate:
            config['xp_rate'] = int(xp_rate)
        
        # Canal de Boas-Vindas
        welcome_channel = request.form.get('welcome_channel')
        config['welcome_channel'] = welcome_channel if welcome_channel else None
        
        # Canal de Level Up
        levelup_channel = request.form.get('levelup_channel')
        config['levelup_channel'] = levelup_channel if levelup_channel else None
        
        # Mensagem de Boas-Vindas
        welcome_message = request.form.get('welcome_message')
        if welcome_message:
            config['welcome_message'] = welcome_message
        
        # Imagem de Fundo
        welcome_background = request.form.get('welcome_background')
        if welcome_background:
            config['welcome_background'] = welcome_background
        
        data['config'] = config
        
        if save_data(data, "Atualização de Configuração do Sistema"):
            flash('Configurações salvas com sucesso!', 'success')
        else:
            flash('Erro ao salvar configurações', 'error')
        
        return redirect(url_for('system_config'))
    
    return render_template('system_config.html', config=config)

# ========== Cargos por Nível ==========
@app.route('/config/level-roles', methods=['GET', 'POST'])
@login_required
def level_roles():
    data = load_data()
    level_roles = data.get('level_roles', {})
    
    if request.method == 'POST':
        if 'add_role' in request.form:
            level = request.form.get('level')
            role_id = request.form.get('role_id')
            if level and role_id:
                level_roles[level] = role_id
        
        elif 'remove_role' in request.form:
            level_to_remove = request.form.get('level_to_remove')
            if level_to_remove in level_roles:
                del level_roles[level_to_remove]
        
        data['level_roles'] = level_roles
        
        if save_data(data, "Atualização de Cargos por Nível"):
            flash('Cargos por nível atualizados!', 'success')
        else:
            flash('Erro ao salvar', 'error')
        
        return redirect(url_for('level_roles'))
    
    return render_template('level_roles.html', level_roles=level_roles)

# ========== Botões de Cargos ==========
@app.route('/config/role-buttons', methods=['GET', 'POST'])
@login_required
def role_buttons():
    data = load_data()
    role_buttons = data.get('role_buttons', {})
    
    if request.method == 'POST':
        if 'add_button' in request.form:
            message_id = request.form.get('message_id')
            button_label = request.form.get('button_label')
            role_id = request.form.get('role_id')
            
            if message_id and button_label and role_id:
                if message_id not in role_buttons:
                    role_buttons[message_id] = {}
                role_buttons[message_id][button_label] = role_id
        
        elif 'remove_button' in request.form:
            msg_id = request.form.get('msg_id')
            btn_label = request.form.get('btn_label')
            
            if msg_id in role_buttons and btn_label in role_buttons[msg_id]:
                del role_buttons[msg_id][btn_label]
                if not role_buttons[msg_id]:
                    del role_buttons[msg_id]
        
        data['role_buttons'] = role_buttons
        
        if save_data(data, "Atualização de Botões de Cargo"):
            flash('Botões de cargo atualizados!', 'success')
        else:
            flash('Erro ao salvar', 'error')
        
        return redirect(url_for('role_buttons'))
    
    return render_template('role_buttons.html', role_buttons=role_buttons)

# ========== Reaction Roles ==========
@app.route('/config/reaction-roles', methods=['GET', 'POST'])
@login_required
def reaction_roles():
    data = load_data()
    reaction_roles = data.get('reaction_roles', {})
    
    if request.method == 'POST':
        if 'add_reaction' in request.form:
            message_id = request.form.get('message_id')
            emoji = request.form.get('emoji')
            role_id = request.form.get('role_id')
            
            if message_id and emoji and role_id:
                if message_id not in reaction_roles:
                    reaction_roles[message_id] = {}
                reaction_roles[message_id][emoji] = role_id
        
        elif 'remove_reaction' in request.form:
            msg_id = request.form.get('msg_id')
            emoji_key = request.form.get('emoji_key')
            
            if msg_id in reaction_roles and emoji_key in reaction_roles[msg_id]:
                del reaction_roles[msg_id][emoji_key]
                if not reaction_roles[msg_id]:
                    del reaction_roles[msg_id]
        
        data['reaction_roles'] = reaction_roles
        
        if save_data(data, "Atualização de Reaction Roles"):
            flash('Reaction Roles atualizadas!', 'success')
        else:
            flash('Erro ao salvar', 'error')
        
        return redirect(url_for('reaction_roles'))
    
    return render_template('reaction_roles.html', reaction_roles=reaction_roles)

# ========== Canais Bloqueados ==========
@app.route('/config/blocked-channels', methods=['GET', 'POST'])
@login_required
def blocked_channels():
    data = load_data()
    blocked = data.get('blocked_links_channels', [])
    
    if request.method == 'POST':
        if 'add_channel' in request.form:
            channel_id = request.form.get('channel_id')
            if channel_id and channel_id not in blocked:
                blocked.append(channel_id)
        
        elif 'remove_channel' in request.form:
            channel_to_remove = request.form.get('channel_to_remove')
            if channel_to_remove in blocked:
                blocked.remove(channel_to_remove)
        
        data['blocked_links_channels'] = blocked
        
        if save_data(data, "Atualização de Canais Bloqueados"):
            flash('Canais bloqueados atualizados!', 'success')
        else:
            flash('Erro ao salvar', 'error')
        
        return redirect(url_for('blocked_channels'))
    
    return render_template('blocked_channels.html', blocked=blocked)

# ========== Advertências ==========
@app.route('/warns')
@login_required
def view_warns():
    data = load_data()
    warns = data.get('warns', {})
    
    # Organiza por usuário
    user_warns = []
    for user_id, warn_list in warns.items():
        user_warns.append({
            'user_id': user_id,
            'count': len(warn_list),
            'warns': warn_list[:5]  # Mostra apenas as 5 mais recentes
        })
    
    return render_template('warns.html', user_warns=user_warns)

# ========== Comandos Restritos ==========
@app.route('/config/command-channels', methods=['GET', 'POST'])
@login_required
def command_channels():
    data = load_data()
    command_channels = data.get('command_channels', {})
    
    if request.method == 'POST':
        command = request.form.get('command')
        channel_id = request.form.get('channel_id')
        action = request.form.get('action')
        
        if command and channel_id:
            if command not in command_channels:
                command_channels[command] = []
            
            if action == 'add':
                if channel_id not in command_channels[command]:
                    command_channels[command].append(channel_id)
            elif action == 'remove':
                if channel_id in command_channels[command]:
                    command_channels[command].remove(channel_id)
                    if not command_channels[command]:
                        del command_channels[command]
        
        data['command_channels'] = command_channels
        
        if save_data(data, "Atualização de Canais de Comando"):
            flash('Canais de comando atualizados!', 'success')
        else:
            flash('Erro ao salvar', 'error')
        
        return redirect(url_for('command_channels'))
    
    return render_template('command_channels.html', command_channels=command_channels)

# ========== Logs ==========
@app.route('/logs')
@login_required
def view_logs():
    data = load_data()
    logs = data.get('logs', [])
    # Ordena do mais recente para o mais antigo
    logs.reverse()
    return render_template('logs.html', logs=logs[:100])  # Mostra apenas os 100 mais recentes

# ========== API para Dados em Tempo Real ==========
@app.route('/api/stats')
@login_required
def api_stats():
    data = load_data()
    stats = {
        'total_users': len(data.get('xp', {})),
        'total_xp': sum(data.get('xp', {}).values()),
        'active_today': 0,  # Implementar lógica de atividade
        'warns_today': 0,   # Implementar lógica por data
        'role_buttons': len(data.get('role_buttons', {})),
        'reaction_roles': len(data.get('reaction_roles', {}))
    }
    return jsonify(stats)

@app.route('/api/backup')
@login_required
def backup_data():
    data = load_data()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"backup_{timestamp}.json"
    
    # Cria um arquivo de download
    from flask import make_response
    response = make_response(json.dumps(data, indent=2, ensure_ascii=False))
    response.headers['Content-Type'] = 'application/json'
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    return response

# ========== Página de Erro ==========
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

# ========== Inicialização ==========
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=True)
