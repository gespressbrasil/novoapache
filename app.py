# app.py
import os
import re
import csv
import io
import logging
from markupsafe import escape, Markup
import requests
from datetime import datetime, timedelta, timezone
from flask import Flask,render_template,request,redirect,url_for,flash,send_file,abort
from flask_migrate import Migrate
from dotenv import load_dotenv
from werkzeug.exceptions import HTTPException
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from db import db, Attempt, Safe, generate_combination 
from flask_wtf import FlaskForm
from wtforms import StringField, HiddenField, SubmitField
from wtforms.validators import InputRequired, Length
from flask_talisman import Talisman 
import redis


# =============================================================================
# Carregar variáveis de ambiente (.env)
# =============================================================================
load_dotenv()

# =============================================================================
# Configuração básica do Flask
# =============================================================================
app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "SQLALCHEMY_DATABASE_URI", 
    "sqlite:///cofre.db"
)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "chave-secreta-padrao")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Strict'


# =============================================================================
# Configuração do Google reCAPTCHA v3
# =============================================================================
app.config["RECAPTCHA_PUBLIC_KEY"] = os.getenv("RECAPTCHA_PUBLIC_KEY")
app.config["RECAPTCHA_PRIVATE_KEY"] = os.getenv("RECAPTCHA_PRIVATE_KEY")
RECAPTCHA_THRESHOLD = 0.5  # Ajuste conforme necessário



# Definição da Política de Content Security Policy (CSP)
# =============================================================================
CSP_POLICY = {
    # Fonte padrão para todos os recursos
    "default-src": ["'self'"],
    # Scripts: permite scripts do próprio domínio, Google, reCAPTCHA e permite inline (com cautela)
    "script-src": [
        "'self'",
        "https://www.google.com",
        "https://www.gstatic.com",
        "https://www.recaptcha.net",
        "'unsafe-inline'"  
    ],
    # Estilos: permite estilos do próprio domínio e do Google Fonts, e CSS inline
    "style-src": [
        "'self'",
        "'unsafe-inline'",  
        "https://fonts.googleapis.com"
    ],
    # Fontes: permite fontes do próprio domínio e do Google Fonts
    "font-src": [
        "'self'",
        "https://fonts.gstatic.com"
    ],
    # Imagens: permite imagens do próprio domínio, imagens embutidas em data-uri e de fontes do Google
    "img-src": [
        "'self'",  # Permite imagens do próprio domínio
        "data:",  # Permite imagens embutidas em base64
        "https://www.google.com",
        "https://www.gstatic.com"
    ],
    # Conexões: permite conexões AJAX e WebSocket ao próprio domínio e fontes do Google
    "connect-src": [
        "'self'",
        "https://www.google.com",
        "https://www.gstatic.com"
    ],
    # Frames: permite frames do próprio domínio, Google e reCAPTCHA
    "frame-src": [
        "'self'",
        "https://www.google.com",
        "https://www.recaptcha.net"
    ],
    # Bloquear objetos (plugins, por exemplo)
    "object-src": ["'none'"],  # Nenhum objeto pode ser carregado
    # Restringir URI base a somente o próprio domínio
    "base-uri": ["'self'"],
    # Restringe a ações de formulários para o próprio domínio
    "form-action": ["'self'"]
}
# =============================================================================
# Inicializa o Talisman com a política de CSP definida
# =============================================================================
Talisman(app, content_security_policy=CSP_POLICY)


@app.before_request
def check_user_agent():
    user_agent = request.headers.get('User-Agent', '')
    if 'Mozilla/4.0' in user_agent or 'MSIE 8.0' in user_agent:
        abort(403, description="User-Agent inválido")

# Conectar ao Redis
redis_connection = redis.StrictRedis(host='localhost', port=6379, db=0)

# Inicializar Limiter com Redis como backend de armazenamento
limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri="redis://localhost:6379/0"  # Redis como backend de armazenamento
)




# =============================================================================
# Inicializar banco de dados e migrações
# =============================================================================
db.init_app(app)
migrate = Migrate(app, db)

# =============================================================================
# Configurar Limiter
# =============================================================================
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"]
)

# =============================================================================
# Variável global para rastrear se o cofre foi configurado
# =============================================================================
safe_initialized = False

# =============================================================================
# setup_safe() - Configura o cofre uma única vez
# =============================================================================
def setup_safe():
    """
    Configura o cofre com uma combinação inicial e prêmio,
    caso ainda não tenha sido configurado.
    Chamada apenas uma vez na inicialização ou em run_app().
    """
    global safe_initialized
    if safe_initialized:
        return

    try:
        if not Safe.query.first():
            combination = generate_combination()
            prize = "Um super prêmio incrível"
            donor = "Um doador generoso"
            reset_time = datetime.now(timezone.utc) + timedelta(days=30)
            safe = Safe(
                combination=combination,
                prize=prize,
                donor=donor,
                reset_time=reset_time,
            )
            db.session.add(safe)
            db.session.commit()
            app.logger.info("Cofre configurado com sucesso!")
    except Exception as e:
        app.logger.error(f"Erro ao configurar o cofre: {e}")
    finally:
        safe_initialized = True

# =============================================================================
# Função que verifica o reCAPTCHA
# =============================================================================
def verify_recaptcha(token: str, action: str) -> bool:
    """
    Verifica o token do reCAPTCHA v3 usando a API do Google.
    :param token: Token retornado pelo reCAPTCHA no frontend
    :param action: Ação definida (ex.: 'login')
    :return: True se a verificação foi bem-sucedida e acima do threshold
    """
    secret_key = app.config["RECAPTCHA_PRIVATE_KEY"]
    if not secret_key:
        app.logger.warning("Chave secreta do reCAPTCHA não configurada; verificação ignorada.")
        return True

    payload = {
        "secret": secret_key,
        "response": token,
        "remoteip": request.remote_addr,
    }
    try:
        response = requests.post(
            "https://www.google.com/recaptcha/api/siteverify", 
            data=payload
        )
        result = response.json()
        app.logger.info(f"Resposta do reCAPTCHA: {result}")
        if (
            result.get("success")
            and result.get("action") == action
            and result.get("score", 0) >= RECAPTCHA_THRESHOLD
        ):
            return True
        else:
            app.logger.warning(f"Falha na verificação do reCAPTCHA v3. Resultado: {result}")
            return False
    except Exception as e:
        app.logger.error(f"Erro ao verificar reCAPTCHA: {e}")
        return False

# =============================================================================
# Validações
# =============================================================================
def validate_username(username: str) -> bool:
    """
    Valida o formato do nome de usuário usando regex:
    - Deve começar com '@'
    - Ter ao menos 3 caracteres (contando o '@')
    """
    pattern = r"^@[A-Za-z0-9_]{2,}$"
    return bool(re.match(pattern, username))

def validate_combination(combination: str) -> bool:
    """
    Valida a combinação usando regex:
    - Deve conter 6 números entre 1 e 60, separados por vírgulas.
    Ex.: '1,2,30,45,59,60'
    """
    pattern = r"^([1-9]|[1-5]\d|60)(,\s?([1-9]|[1-5]\d|60)){5}$"
    return bool(re.match(pattern, combination.replace(" ", "")))


# =============================================================================
# Decoradores de segurança
# =============================================================================
def admin_required(func):
    """
    Decorador simples para rotas que exigem 'autenticação' de administrador.
    Aqui, usamos um token fixo (RESET_TOKEN) como exemplo.
    Idealmente, utilize flask-login ou OAuth para algo mais robusto.
    """
    def wrapper(*args, **kwargs):
        auth_token = request.args.get("auth")
        if auth_token != os.getenv("RESET_TOKEN"):
            abort(403)  # Forbidden
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper

# =============================================================================
# Formulário de Tentativa
# =============================================================================
class AttemptForm(FlaskForm):
    username = StringField(
        "Seu @ do Instagram",
        validators=[
            InputRequired(message="O nome de usuário é obrigatório."),
            Length(min=3, max=100, message="O nome de usuário deve ter entre 3 e 100 caracteres.")
        ]
    )
    numbers = HiddenField(
        "Numbers",
        validators=[
            InputRequired(message="A combinação de números é obrigatória.")
        ]
    )
    submit = SubmitField("Tentar Abrir o Cofre")


# =============================================================================
# Rota Principal: index
# =============================================================================
@app.route("/", methods=["GET", "POST"])
@limiter.limit("10/minute")
def index():
    """
    Página principal com o formulário para tentar abrir o cofre.
    """
    safe = Safe.query.first()

    if not safe:
        flash("O cofre ainda não foi configurado.", "error")
        return render_template("index.html", safe=None, attempts=[], form=None)

    # Impede que tentativas sejam feitas enquanto o cofre estiver "resetando"
    if safe.winner:
        flash("O cofre já foi aberto! Parabéns ao vencedor!", "success")
        return redirect(url_for("winner"))

    attempts = Attempt.query.order_by(Attempt.timestamp.desc()).limit(10).all()
    form = AttemptForm()

    if form.validate_on_submit():
        username = form.username.data.strip()
        combination = form.numbers.data.strip()  # Pega a combinação de números enviada pelo formulário
        recaptcha_token = request.form.get("g-recaptcha-response", "")

        # Verifica reCAPTCHA v3
        if not verify_recaptcha(recaptcha_token, "login"):
            flash("Verificação reCAPTCHA falhou. Tente novamente.", "error")
            return redirect(url_for("index"))

        # Valida username e combination
        if not validate_username(username):
            flash("Nome de usuário inválido! Exemplo válido: @usuario123", "error")
            return redirect(url_for("index"))

        if not validate_combination(combination):
            flash("Combinação inválida! Ex.: '1,2,30,45,59,60'", "error")
            return redirect(url_for("index"))

        # Verifica tentativas do mesmo usuário a cada 2 horas
        time_2_hours_ago = datetime.now(timezone.utc) - timedelta(hours=2)
        attempts_count = Attempt.query.filter(
            Attempt.username == username,
            Attempt.timestamp >= time_2_hours_ago
        ).count()
        if attempts_count >= 5:
            flash("Você atingiu o limite de 5 tentativas a cada 2 horas. Tente novamente mais tarde.", "error")
            return redirect(url_for("index"))

        # Registrar a tentativa
        new_attempt = Attempt(username=escape(username), attempt=escape(combination))
        db.session.add(new_attempt)
        db.session.commit()

        # Comparação de combinações desconsiderando a ordem
        user_combination = sorted([int(num) for num in combination.split(',')])
        safe_combination = sorted([int(num) for num in safe.combination.split('-')])

        if user_combination == safe_combination:
            safe.winner = username
            db.session.commit()
            flash("Parabéns! Você abriu o cofre!", "success")
            return redirect(url_for("winner"))
        else:
            flash("Combinação incorreta. Tente novamente.", "error")

        # Redireciona para a página inicial após o processamento do formulário
        return redirect(url_for("index"))

    # Cálculo opcional de tempo restante para reset
    days = hours = minutes = 0
    if safe and safe.reset_time:
        delta = safe.reset_time.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)
        if delta.total_seconds() > 0:
            days = delta.days
            hours = (delta.seconds // 3600) % 24
            minutes = (delta.seconds // 60) % 60

    return render_template(
        "index.html",
        form=form,
        safe=safe,
        attempts=attempts,
        days=days,
        hours=hours,
        minutes=minutes,
        recaptcha_site_key=app.config.get("RECAPTCHA_PUBLIC_KEY"),
    )
# =============================================================================
# Rota Winner
# =============================================================================
@app.route("/winner", methods=["GET"])
def winner():
    """
    Página exibida ao vencedor do cofre.
    """
    safe = Safe.query.first()
    if not safe or not safe.winner:
        abort(404, description="Nenhum vencedor encontrado.")
    return render_template("winner.html", username=safe.winner, safe=safe)

# =============================================================================
# Rota Reset
# =============================================================================
@app.route("/reset", methods=["POST"])
def reset_safe():
    """
    Reseta o cofre com uma nova combinação e prêmio.
    Rota protegida com token via POST.
    """
    # Verifica o token de segurança
    token = request.form.get("token")
    if token != os.getenv("RESET_TOKEN"):
        abort(403, description="Token inválido para resetar o cofre.")

    try:
        # Busca o cofre ativo
        safe = Safe.query.first()  # O cofre atual, caso exista
        if not safe:
            flash("Cofre não encontrado.", "error")
            return redirect(url_for("index"))

        # Gere uma nova combinação e outros detalhes do prêmio
        new_combination = generate_combination()
        new_prize = request.form.get("prize", "Um super prêmio incrível")  # Prêmio do formulário ou padrão
        new_donor = request.form.get("donor", "Um doador generoso")  # Doador do formulário ou padrão

        # Resetando o cofre
        safe.reset(new_combination=new_combination, new_prize=new_prize, new_donor=new_donor)

        # Confirmação de sucesso
        db.session.commit()  # Certifique-se de que o commit está sendo feito para salvar no banco
        app.logger.info(f"Cofre resetado com nova combinação: {new_combination}")  # Log de confirmação
        flash("Cofre resetado com sucesso!", "success")
    
    except Exception as e:
        db.session.rollback()  # Reverte alterações no banco se ocorrer um erro
        app.logger.error(f"Erro ao resetar o cofre: {str(e)}")
        flash(f"Erro ao resetar o cofre: {str(e)}", "error")

    # Redireciona de volta para a página principal
    return redirect(url_for("index"))
# =============================================================================
# Rota Exportar Auditoria
# =============================================================================
@app.route("/exportar_auditoria", methods=["GET"])
@admin_required
def exportar_auditoria():
    """
    Função para exportar a auditoria em CSV.
    Agora protegida com 'admin_required'.
    Ajuste para um sistema de login real em produção.
    """
    # Verifica se a aplicação está rodando em ambiente de desenvolvimento
    if os.environ.get("FLASK_ENV") != "development":
        abort(403, description="Rota somente permitida em desenvolvimento.")

    attempts = Attempt.query.all()

    si = io.StringIO()
    writer = csv.writer(si)
    writer.writerow(["Data/Hora", "Usuário", "Tentativa"])
    for attempt in attempts:
        writer.writerow([
            attempt.timestamp.strftime("%d/%m/%Y %H:%M:%S"),
            attempt.username,
            attempt.attempt
        ])

    output = io.BytesIO()
    output.write(si.getvalue().encode("utf-8"))
    output.seek(0)

    return send_file(
        output,
        mimetype="text/csv",
        as_attachment=True,
        download_name="tentativas.csv",
    )

# =============================================================================
# Tratamento de Erros
# =============================================================================
@app.errorhandler(HTTPException)
def handle_exception(e):
    """
    Lidar com exceções HTTP e exibir mensagens amigáveis.
    """
    flash(Markup(f"Ocorreu um erro: {e.name} - {e.description}"), "error")
    return redirect(url_for("index"))

# =============================================================================
# Execução da Aplicação
# =============================================================================
def run_app():
    """
    Inicializa a aplicação e chama setup_safe() apenas uma vez.
    Removeu-se db.create_all() para usar Flask-Migrate.
    """
    with app.app_context():
        # Aqui removemos db.create_all(), pois estamos usando Flask-Migrate
        setup_safe()
    app.run(debug=True, host="0.0.0.0", port=5001)

if __name__ == "__main__":
    run_app()
