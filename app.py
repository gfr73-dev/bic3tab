import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from pathlib import Path
from datetime import datetime, timezone

import pyodbc
from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.engine import URL
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix


SHARED_MYSQL_ENV_FILE = Path(
    os.environ.get(
        "MYSQL_APP_ENV_FILE", Path(__file__).resolve().parents[1] / ".mysql-app.env"
    )
).expanduser()
load_dotenv(SHARED_MYSQL_ENV_FILE, override=True)


def mysql_database_url(database):
    password = os.getenv("MYSQL_APP_PASSWORD")
    if not password:
        raise RuntimeError(
            "MYSQL_APP_PASSWORD is not configured. Run mysql_security_setup.py on the XAMPP host."
        )
    return URL.create(
        "mysql+pymysql",
        username=os.getenv("MYSQL_APP_USER", "flask_apps"),
        password=password,
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        database=database,
        query={"charset": "utf8mb4"},
    )


class Config:
    SQLALCHEMY_DATABASE_URI = mysql_database_url("bic3tab")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    AS400_DSN = os.getenv("AS400_DSN", "cli000")
    AS400_UID = os.getenv("AS400_UID", "mobile")
    AS400_PWD = os.getenv("AS400_PWD", "mobile")
    AS400_BIC_TABLE = os.getenv("AS400_TABLE", "OPERACOES.BIC3TAB")


app = Flask(__name__)
app.config.from_object(Config)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
if os.environ.get("FLASK_BEHIND_HTTPS", "0") == "1":
    app.config["SESSION_COOKIE_SECURE"] = True

# Waitress is bound to localhost behind one trusted Nginx reverse proxy.
# Honor forwarded host, scheme, client IP, and the /bic3tab-flask-app URL prefix.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

db = SQLAlchemy(app)


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    name = db.Column(db.String(255), nullable=False)
    username = db.Column(db.String(320), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    isAdmin = db.Column("isAdmin", db.Boolean, nullable=False, default=False)


class ChangeLog(db.Model):
    __tablename__ = "change_log"
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    username = db.Column(db.String(320), nullable=False, index=True)
    changed_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        index=True,
    )
    BICCPTCLI_OLD = db.Column(db.String(8), nullable=False)
    BICVCLI_OLD = db.Column(db.Numeric(5, 0), nullable=False)
    SHEMAIL2DE_OLD = db.Column(db.String(1), nullable=True)
    BICSNIF_OLD = db.Column(db.String(9), nullable=True)
    BICSCCC_OLD = db.Column(db.String(11), nullable=True)
    BICSPRACA_OLD = db.Column(db.String(2), nullable=True)
    BICSSERV_OLD = db.Column(db.String(3), nullable=True)
    BICSPROD_OLD = db.Column(db.String(3), nullable=True)
    BICCPTCLI_NEW = db.Column(db.String(8), nullable=False)
    BICVCLI_NEW = db.Column(db.Numeric(5, 0), nullable=False)
    SHEMAIL2DE_NEW = db.Column(db.String(1), nullable=True)
    BICSNIF_NEW = db.Column(db.String(9), nullable=True)
    BICSCCC_NEW = db.Column(db.String(11), nullable=True)
    BICSPRACA_NEW = db.Column(db.String(2), nullable=True)
    BICSSERV_NEW = db.Column(db.String(3), nullable=True)
    BICSPROD_NEW = db.Column(db.String(3), nullable=True)


FIELDS = (
    "BICVCLI",
    "SHEMAIL2DE",
    "BICSNIF",
    "BICSCCC",
    "BICSPRACA",
    "BICSSERV",
    "BICSPROD",
)
ALL_FIELDS = ("BICCPTCLI",) + FIELDS
TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#@]*(\.[A-Za-z_][A-Za-z0-9_$#@]*)?$")
AS400_TABLE = app.config["AS400_BIC_TABLE"].strip()
if not TABLE_RE.fullmatch(AS400_TABLE):
    raise RuntimeError("AS400_TABLE must be a table name or library.table identifier")


def as400_connection():
    conn_string = f"DSN={app.config['AS400_DSN']};UID={app.config['AS400_UID']};PWD={app.config['AS400_PWD']}"
    return pyodbc.connect(conn_string, autocommit=False)


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def valid_csrf():
    return secrets.compare_digest(
        session.get("csrf_token", ""), request.form.get("csrf_token", "")
    )


def encode_snapshot(values):
    packed = json.dumps(values, separators=(",", ":"), ensure_ascii=True).encode()
    payload = base64.urlsafe_b64encode(packed).decode().rstrip("=")
    signature = hmac.new(
        app.config["SECRET_KEY"].encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return payload, signature


def decode_snapshot(payload, signature):
    expected = hmac.new(
        app.config["SECRET_KEY"].encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ValueError(
            "The original record snapshot is invalid. Search for the record again."
        )
    raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    values = json.loads(raw)
    if set(values) != set(ROW_FIELDS):
        raise ValueError(
            "The original record snapshot is invalid. Search for the record again."
        )
    return values


@app.get("/")
def index():
    return redirect(url_for("bic3tab") if session.get("user_id") else url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if not valid_csrf():
            flash("A sua sessão expirou. Por favor, tente novamente.", "error")
        else:
            username = request.form.get("user", "").strip().lower()
            password = request.form.get("password", "")
            user = User.query.filter_by(username=username).first() if username else None
            if user and check_password_hash(user.password_hash, password):
                session.clear()
                session["user_id"] = user.id
                session["username"] = user.username
                session["name"] = user.name
                return redirect(url_for("bic3tab"))
            flash("Invalid email address or password.", "error")
    return render_template("login.html", csrf_token=csrf_token())


DISPLAY_FIELDS = ("BICCPTCLI", "BICACT", "BICSNIF", "BICSCCC", "BICSPRACA", "BICSSERV", "BICSPROD", "SERVICE_RC")
ROW_FIELDS = tuple(dict.fromkeys(ALL_FIELDS + DISPLAY_FIELDS))


def load_matches(prefix):
    with as400_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {', '.join(ROW_FIELDS)} FROM {AS400_TABLE} WHERE BICCPTCLI LIKE ? AND SERVICE_RC = ? ORDER BY 1",
            prefix + "%", "860",
        )
        names = [column[0].upper() for column in cursor.description]
        found = []
        for result in cursor.fetchall():
            raw = dict(zip(names, result))
            normalized = {field: ("" if raw.get(field) is None else str(raw[field]).strip()) for field in ROW_FIELDS}
            found.append(normalized)
    return found


@app.route("/bic3tab", methods=["GET", "POST"])
def bic3tab():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    key = request.args.get("account") or session.get("searched_key")
    if request.method == "POST":
        if not valid_csrf():
            flash("Your form expired. Please try again.", "error")
        elif request.form.get("action") == "search":
            entered = request.form.get("BICCPTCLI", "").strip()
            if not re.fullmatch(r"[0-9]{6}", entered):
                flash("A conta deve conter exactamente 6 dígitos numéricos.", "error")
                key = None
            else:
                key = entered
                session["searched_key"] = key
                try:
                    matches = load_matches(key)
                    flash(f"Pesquisa concluída: {len(matches)} registo(s).", "success")
                except Exception:
                    app.logger.exception("IBM i falha ao pesquisar registos")
                    flash("Os registos não puderam ser lidos a partir do DSN configurado.", "error")
        elif request.form.get("action") == "update":
            key = session.get("searched_key")
            as400_updated = False
            try:
                if not key:
                    raise ValueError("Pesquise os registos novamente antes de guardar.")
                original = decode_snapshot(request.form.get("snapshot", ""), request.form.get("snapshot_signature", ""))
                if not original["BICCPTCLI"].startswith(key) or original["SERVICE_RC"] != "860":
                    raise ValueError("O registo não pertence à pesquisa. Pesquise novamente.")
                values = {field: request.form.get(field, "").strip() for field in FIELDS}
                values["BICVCLI"] = original["BICVCLI"]
                values["SHEMAIL2DE"] = original["SHEMAIL2DE"]
                valid_fields(values)
                new_values = {"BICCPTCLI": original["BICCPTCLI"], **values}
                where_fields = ROW_FIELDS
                with as400_connection() as conn:
                    cursor = conn.cursor()
                    assignments = ", ".join(f"{field} = ?" for field in FIELDS)
                    where_parts, where_params = [], []
                    for field in where_fields:
                        if original[field] == "":
                            where_parts.append(f"({field} = ? OR {field} IS NULL)")
                            where_params.append("")
                        else:
                            where_parts.append(f"{field} = ?")
                            where_params.append(original[field])
                    params = [int(values["BICVCLI"])] + [values[field] for field in FIELDS[1:]] + where_params
                    cursor.execute(f"UPDATE {AS400_TABLE} SET {assignments} WHERE {' AND '.join(where_parts)}", params)
                    if cursor.rowcount != 1:
                        raise ValueError("O registo mudou ou não pôde ser identificado de forma única. Pesquise novamente.")
                    conn.commit()
                    as400_updated = True
                audit = ChangeLog(username=session["username"], changed_at=datetime.now(timezone.utc).replace(tzinfo=None))
                for field in ALL_FIELDS:
                    setattr(audit, f"{field}_OLD", int(original[field]) if field == "BICVCLI" else original[field])
                    setattr(audit, f"{field}_NEW", int(new_values[field]) if field == "BICVCLI" else new_values[field])
                db.session.add(audit)
                db.session.commit()
                flash("Registo actualizado com sucesso.", "success")
                return redirect(url_for("bic3tab", account=key))
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "error")
            except Exception:
                db.session.rollback()
                app.logger.exception("IBM i record update or MySQL audit insert failed")
                flash("IBM i foi actualizado, mas ocorreu um erro ao guardar o histórico." if as400_updated else "O registo não pôde ser actualizado.", "error")
    if key and re.fullmatch(r"[0-9]{6}", key):
        session["searched_key"] = key
        try:
            matches = load_matches(key)
        except Exception:
            app.logger.exception("IBM i falha ao carregar resultados")
            matches = []
    else:
        matches = []
    edit_index = request.args.get("edit", type=int)
    rows = []
    for i, record in enumerate(matches):
        snapshot, signature = encode_snapshot(record)
        rows.append({"display": record, "snapshot": snapshot, "signature": signature, "index": i, "editing": i == edit_index})
    return render_template("bic3tab.html", csrf_token=csrf_token(), key=key, searched=bool(key), rows=rows)


@app.get("/changes/<account>")
def account_changes(account):
    if not session.get("user_id"):
        return redirect(url_for("login"))
    if not re.fullmatch(r"[0-9]{6,8}", account):
        abort(404)
    changes = ChangeLog.query.filter(ChangeLog.BICCPTCLI_OLD.like(account + "%")).order_by(ChangeLog.changed_at.desc()).all()
    key = session.get("searched_key")
    try:
        matches = load_matches(key) if key and re.fullmatch(r"[0-9]{6}", key) else []
    except Exception:
        matches = []
    rows = [{"display": row, "index": i, "editing": False, "snapshot": "", "signature": ""} for i, row in enumerate(matches)]
    return render_template("bic3tab.html", csrf_token=csrf_token(), key=key, searched=bool(key), rows=rows, changes=changes, changes_account=account)


def valid_fields(values):
    limits = {"BICSNIF": 9, "BICSCCC": 11, "BICSPRACA": 2, "BICSSERV": 3, "BICSPROD": 3}
    for field, limit in limits.items():
        if len(values[field]) > limit:
            raise ValueError(f"{field} must be at most {limit} characters.")


@app.post("/logout")
def logout():
    if not valid_csrf():
        flash("A sua sessão expirou. Por favor, tente novamente.", "error")
        return redirect(url_for("bic3tab"))
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin/users/new", methods=["GET", "POST"])
def add_user():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    # Recheck the database on each request so removed admin access takes effect
    # even when the user already has an active session.
    admin = db.session.get(User, session["user_id"])
    if not admin or not admin.isAdmin:
        abort(403)

    if request.method == "POST":
        if not valid_csrf():
            flash("A sua sessão expirou. Por favor, tente novamente.", "error")
        else:
            name = request.form.get("name", "").strip()
            username = request.form.get("username", "").strip().lower()
            password = request.form.get("password", "")
            confirmation = request.form.get("password_confirmation", "")
            if not name or not username or not password:
                flash("Nome, email e palavra-passe são obrigatórios.", "error")
            elif len(name) > 255 or len(username) > 320:
                flash("O nome ou email é demasiado longo.", "error")
            elif not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", username):
                flash("Introduza um endereço de email válido.", "error")
            elif password != confirmation:
                flash("As palavras-passe não coincidem.", "error")
            elif User.query.filter_by(username=username).first():
                flash("Já existe uma conta com esse email.", "error")
            else:
                user = User(
                    name=name,
                    username=username,
                    password_hash=generate_password_hash(password),
                    isAdmin=False,
                )
                db.session.add(user)
                try:
                    db.session.commit()
                    flash(f"Utilizador {username} criado com sucesso.", "success")
                    return redirect(url_for("add_user"))
                except Exception:
                    db.session.rollback()
                    app.logger.exception("Admin user creation failed")
                    flash("Não foi possível criar o utilizador. O email poderá já estar em uso.", "error")
    return render_template("add_user.html", csrf_token=csrf_token())


@app.context_processor
def inject_connection_target():
    current_user = db.session.get(User, session["user_id"]) if session.get("user_id") else None
    return {
        "connection_target": app.config["AS400_DSN"],
        "is_admin": bool(current_user and current_user.isAdmin),
    }


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5800")), debug=True)
