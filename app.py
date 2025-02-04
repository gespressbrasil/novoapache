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
from flask_compress import Compress
from flask_assets import Environment, Bundle
from zoneinfo import ZoneInfo




load_dotenv()


print("RECAPTCHA_PUBLIC_KEY:", os.getenv("RECAPTCHA_PUBLIC_KEY"))
print("RECAPTCHA_PRIVATE_KEY:", os.getenv("RECAPTCHA_PRIVATE_KEY"))


app = Flask(__name__)

Compress(app)  


assets = Environment(app)
js = Bundle('src/js/*.js', filters='jsmin', output='dist/js/all.min.js')
assets.register('js_all', js)

css = Bundle('src/css/*.css', filters='cssmin', output='dist/css/all.min.css')
assets.register('css_all', css)





app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "SQLALCHEMY_DATABASE_URI", 
    "sqlite:///cofre.db"
)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "chave-secreta-padrao")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Strict'



RECAPTCHA_PUBLIC_KEY = "6Lc2-q8qAAAAAF8c69VaSI1SRKIenNoCi-GCgTKv"
RECAPTCHA_PRIVATE_KEY = "6Lc2-q8qAAAAAGd23DAf4NPZVfA8pKUAggasNp2K"
RECAPTCHA_THRESHOLD = 0.5  


app.config["RECAPTCHA_PUBLIC_KEY"] = RECAPTCHA_PUBLIC_KEY
app.config["RECAPTCHA_PRIVATE_KEY"] = RECAPTCHA_PRIVATE_KEY
app.config["RECAPTCHA_THRESHOLD"] = RECAPTCHA_THRESHOLD


app.logger.debug(f"RECAPTCHA_PUBLIC_KEY: {RECAPTCHA_PUBLIC_KEY}")
app.logger.debug(f"RECAPTCHA_PRIVATE_KEY: {RECAPTCHA_PRIVATE_KEY}")
app.logger.debug(f"Threshold de reCAPTCHA: {RECAPTCHA_THRESHOLD}")


if not RECAPTCHA_PUBLIC_KEY or not RECAPTCHA_PRIVATE_KEY:
    app.logger.error("As chaves do reCAPTCHA não estão configuradas corretamente!")
else:
    app.logger.info(f"Chave pública do reCAPTCHA: {RECAPTCHA_PUBLIC_KEY}")
    app.logger.info(f"Chave privada do reCAPTCHA: {RECAPTCHA_PRIVATE_KEY}")


app.logger.debug(f"RECAPTCHA_PUBLIC_KEY: {RECAPTCHA_PUBLIC_KEY}")
app.logger.debug(f"RECAPTCHA_PRIVATE_KEY: {RECAPTCHA_PRIVATE_KEY}")


app.logger.info(f"Threshold de reCAPTCHA: {RECAPTCHA_THRESHOLD}")


print(f"RECAPTCHA_PUBLIC_KEY: {RECAPTCHA_PUBLIC_KEY}")
print(f"RECAPTCHA_PRIVATE_KEY: {RECAPTCHA_PRIVATE_KEY}")
print(f"Threshold de reCAPTCHA: {RECAPTCHA_THRESHOLD}")


CSP_POLICY = {
    "default-src": ["'self'"],
    
    "script-src": [
        "'self'",
        "https://www.google.com",
        "https://www.gstatic.com",
        "https://www.recaptcha.net",
        "https://www.googletagmanager.com",  # Para Google Tag Manager
        "'unsafe-inline'"
    ],
    
    "style-src": [
        "'self'",
        "'unsafe-inline'",
        "https://fonts.googleapis.com"
    ],
    
    "font-src": [
        "'self'",
        "https://fonts.gstatic.com"
    ],
    
    "img-src": [
        "'self'",
        "data:",
        "https://www.google.com",
        "https://www.gstatic.com",
        "https://www.youtube.com",           # Para imagens do YouTube
        "https://i.ytimg.com"               # Para thumbnails do YouTube
    ],
    
    "connect-src": [
        "'self'",
        "https://www.google.com",
        "https://www.gstatic.com",
        "https://www.google-analytics.com"   # Adicionado para Google Analytics
    ],
    
    "frame-src": [
        "'self'",
        "https://www.google.com",
        "https://www.recaptcha.net",
        "https://www.youtube.com",           # Para embeds do YouTube
        "https://www.youtube-nocookie.com"   # Para embeds com cookies restritos do YouTube
    ],
    
    "object-src": ["'none'"],
    
    "base-uri": ["'self'"],
    
    "form-action": ["'self'"]
}

Talisman(app, content_security_policy=CSP_POLICY)

@app.before_request
def check_user_agent():
    user_agent = request.headers.get('User-Agent', '')
    if 'Mozilla/4.0' in user_agent or 'MSIE 8.0' in user_agent:
        abort(403, description="User-Agent inválido")



db.init_app(app)
migrate = Migrate(app, db)


storage_uri = "redis://localhost:6379/0"


logging.basicConfig(level=logging.DEBUG)

limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri=storage_uri,  
    default_limits=["250 per day", "80 per hour"]
)


@app.before_request
def log_redis_usage():
    app.logger.debug("Usando Redis como backend para Flask Limiter!")


safe_initialized = False


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
        return False  

    payload = {
        "secret": secret_key,
        "response": token,
        "remoteip": request.remote_addr,
    }
    
    try:
        response = requests.post("https://www.google.com/recaptcha/api/siteverify", data=payload)
        result = response.json()
        app.logger.info(f"Resposta do reCAPTCHA: {result}")

        
        if result.get("success") and result.get("action") == action and result.get("score", 0) >= RECAPTCHA_THRESHOLD:
            app.logger.info("Verificação reCAPTCHA bem-sucedida.")
            return True
        else:
            app.logger.warning(f"Falha na verificação do reCAPTCHA v3. Resultado: {result}")
            return False
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Erro de requisição ao verificar o reCAPTCHA: {e}")
        return False


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




@app.route("/", methods=["GET", "POST"])
@limiter.limit("20/minute")
def index():
    """
    Página principal com o formulário para tentar abrir o cofre.
    """
    from zoneinfo import ZoneInfo  

    safe = Safe.query.first()

    if not safe:
        flash("O cofre ainda não foi configurado.", "error")
        return render_template("index.html", safe=None, attempts=[], form=None)

    
    if safe.winner:
        flash("O cofre já foi aberto! Parabéns ao vencedor!", "success")
        return redirect(url_for("winner"))

    
    attempts_utc = Attempt.query.order_by(Attempt.timestamp.desc()).limit(10).all()
    
    
    tz_sp = ZoneInfo("America/Sao_Paulo")
    attempts = []
    for a in attempts_utc:
        attempts.append({
            "id": a.id,
            "username": a.username,
            "attempt": a.attempt,
            "timestamp": a.timestamp.replace(tzinfo=timezone.utc).astimezone(tz_sp)
        })

    form = AttemptForm()

    if form.validate_on_submit():
        username = form.username.data.strip()
        combination = form.numbers.data.strip()  
        recaptcha_token = request.form.get("g-recaptcha-response", "")

       
        if not verify_recaptcha(recaptcha_token, "login"):
            flash("Verificação reCAPTCHA falhou. Tente novamente.", "error")
            return redirect(url_for("index"))

        
        if not validate_username(username):
            flash("Nome de usuário inválido! Exemplo válido: @usuario123", "error")
            return redirect(url_for("index"))

        if not validate_combination(combination):
            flash("Combinação inválida! Ex.: '1,2,30,45,59,60'", "error")
            return redirect(url_for("index"))

        
        time_2_hours_ago = datetime.now(timezone.utc) - timedelta(hours=2)
        attempts_count = Attempt.query.filter(
            Attempt.username == username,
            Attempt.timestamp >= time_2_hours_ago
        ).count()
        if attempts_count >= 3:
            flash("Você atingiu o limite de 3 tentativas a cada 2 horas. Tente novamente mais tarde.", "error")
            return redirect(url_for("index"))

        
        new_attempt = Attempt(username=escape(username), attempt=escape(combination))
        db.session.add(new_attempt)
        db.session.commit()

       
        user_combination = sorted([int(num) for num in combination.split(',')])
        safe_combination = sorted([int(num) for num in safe.combination.split('-')])

        if user_combination == safe_combination:
            safe.winner = username
            db.session.commit()
            flash("Parabéns! Você abriu o cofre!", "success")
            return redirect(url_for("winner"))
        else:
            flash("Combinação incorreta. Tente novamente.", "error")

        
        return redirect(url_for("index"))
    
    
    total_attempts = Attempt.query.count()

    
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
        total_attempts=total_attempts,
        days=days,
        hours=hours,
        minutes=minutes,
        recaptcha_site_key=app.config.get("RECAPTCHA_PUBLIC_KEY"),
    )


@app.route("/winner", methods=["GET"])
def winner():
    """
    Página exibida ao vencedor do cofre.
    """
    safe = Safe.query.first()
    if not safe or not safe.winner:
        abort(404, description="Nenhum vencedor encontrado.")
    return render_template("winner.html", username=safe.winner, safe=safe)


@app.route("/reset", methods=["POST"])
def reset_safe():
    """
    Reseta o cofre com uma nova combinação e prêmio.
    Rota protegida com token via POST.
    """
    
    token = request.form.get("token")
    if token != os.getenv("RESET_TOKEN"):
        abort(403, description="Token inválido para resetar o cofre.")

    try:
        
        safe = Safe.query.first()  
        if not safe:
            flash("Cofre não encontrado.", "error")
            return redirect(url_for("index"))

        
        Attempt.query.delete()
        db.session.commit()  
        
        
        new_combination = generate_combination()
        new_prize = request.form.get("prize", "Um super prêmio incrível")  
        new_donor = request.form.get("donor", "Um doador generoso")  
        
        
        safe.reset(new_combination=new_combination, new_prize=new_prize, new_donor=new_donor)

        
        db.session.commit()  
        app.logger.info(f"Cofre resetado com nova combinação: {new_combination}")  
        flash("Cofre resetado com sucesso e as tentativas foram apagadas!", "success")
    
    except Exception as e:
        db.session.rollback()  
        app.logger.error(f"Erro ao resetar o cofre: {str(e)}")
        flash(f"Erro ao resetar o cofre: {str(e)}", "error")

    
    return redirect(url_for("index"))


@app.route("/exportar_auditoria", methods=["GET"])
@admin_required
def exportar_auditoria():
    """
    Função para exportar a auditoria em CSV.
    Agora protegida com 'admin_required'.
    Ajuste para um sistema de login real em produção.
    """
    
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



@app.errorhandler(HTTPException)
def handle_exception(e):
    """
    Lidar com exceções HTTP e exibir mensagens amigáveis.
    """
    flash(Markup(f"Ocorreu um erro: {e.name} - {e.description}"), "error")
    return redirect(url_for("index"))


def run_app():
    """
    Inicializa a aplicação e chama setup_safe() apenas uma vez.
    Removeu-se db.create_all() para usar Flask-Migrate.
    """
    with app.app_context():
       
        setup_safe()
    app.run(debug=True, host="0.0.0.0", port=5001)

if __name__ == "__main__":
    run_app()
