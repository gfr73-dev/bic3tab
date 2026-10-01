import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timezone

import pyodbc
from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix


class Config:
#    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "mysql://root:@localhost/bic3tab")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "mysql://root:@192.168.70.149/bic3tab")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    AS400_DSN = os.getenv("AS400_DSN", "cli000")
    AS400_UID = os.getenv("AS400_UID", "mobile")
    AS400_PWD = os.getenv("AS400_PWD", "mobile")
    AS400_BIC_TABLE = os.getenv("AS400_TABLE", "OPERACOES.BIC3TAB")


app = Flask(__name__)
app.config.from_object(Config)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
if app.config["SQLALCHEMY_DATABASE_URI"].startswith("mysql://"):
    app.config["SQLALCHEMY_DATABASE_URI"] = app.config["SQLALCHEMY_DATABASE_URI"].replace("mysql://", "mysql+pymysql://", 1)
if os.environ.get("FLASK_BEHIND_HTTPS", "0") == "1":
    app.config["SESSION_COOKIE_SECURE"] = True
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

db = SQLAlchemy(app)


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    name = db.Column(db.String(255), nullable=False)
    username = db.Column(db.String(320), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)


class ChangeLog(db.Model):
    __tablename__ = "change_log"
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    username = db.Column(db.String(320), nullable=False, index=True)
    changed_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)
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


FIELDS = ("BICVCLI", "SHEMAIL2DE", "BICSNIF", "BICSCCC", "BICSPRACA", "BICSSERV", "BICSPROD")
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
    return secrets.compare_digest(session.get("csrf_token", ""), request.form.get("csrf_token", ""))


def encode_snapshot(values):
    packed = json.dumps(values, separators=(",", ":"), ensure_ascii=True).encode()
    payload = base64.urlsafe_b64encode(packed).decode().rstrip("=")
    signature = hmac.new(app.config["SECRET_KEY"].encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload, signature


def decode_snapshot(payload, signature):
    expected = hmac.new(app.config["SECRET_KEY"].encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ValueError("The original record snapshot is invalid. Search for the record again.")
    raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    values = json.loads(raw)
    if set(values) != set(ALL_FIELDS):
        raise ValueError("The original record snapshot is invalid. Search for the record again.")
    return values


@app.get("/")
def index():
    return redirect(url_for("bic3tab") if session.get("user_id") else url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if not valid_csrf():
            flash("Your form expired. Please try again.", "error")
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


@app.route("/bic3tab", methods=["GET", "POST"])
def bic3tab():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    values = {field: "" for field in FIELDS}
    key = session.get("searched_key")
    snapshot_payload = snapshot_signature = None
    found = False
    if request.method == "POST":
        if not valid_csrf():
            flash("Your form expired. Please try again.", "error")
        elif request.form.get("action") == "search":
            entered = request.form.get("BICCPTCLI", "")
            session.pop("searched_key", None)
            if not re.fullmatch(r"\d{8}", entered):
                flash("BICCPTCLI must contain exactly 8 numeric characters.", "error")
                session.pop("searched_key", None)
            else:
                try:
                    with as400_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute(f"SELECT {', '.join(ALL_FIELDS)} FROM {AS400_TABLE} WHERE BICCPTCLI = ?", entered)
                        row = cursor.fetchone()
                        names = [column[0].upper() for column in cursor.description] if cursor.description else []
                        original = dict(zip(names, row)) if row else None
                    if original:
                        original = {field: ("" if original[field] is None else str(original[field]).strip()) for field in ALL_FIELDS}
                        key, found = entered, True
                        session["searched_key"] = entered
                        for field in FIELDS:
                            values[field] = original[field]
                        snapshot_payload, snapshot_signature = encode_snapshot(original)
                        flash("Record loaded. Edit the fields and save your changes.", "success")
                    else:
                        session.pop("searched_key", None)
                        key = None
                        flash("No BIC3TAB record was found for that BICCPTCLI.", "error")
                except Exception:
                    app.logger.exception("IBM i record lookup failed")
                    flash("The record could not be read from the configured IBM i DSN.", "error")
        elif request.form.get("action") == "update":
            key = session.get("searched_key")
            as400_updated = False
            try:
                if not key:
                    raise ValueError("Search for a record before saving.")
                original = decode_snapshot(request.form.get("snapshot", ""), request.form.get("snapshot_signature", ""))
                if original["BICCPTCLI"] != key:
                    raise ValueError("The original record snapshot does not match the searched key. Search again.")
                values = {field: request.form.get(field, "").strip() for field in FIELDS}
                valid_fields(values)
                new_values = {"BICCPTCLI": key, **values}
                with as400_connection() as conn:
                    cursor = conn.cursor()
                    assignments = ", ".join(f"{field} = ?" for field in FIELDS)
                    params = [int(values["BICVCLI"])] + [values[field] for field in FIELDS[1:]] + [key]
                    cursor.execute(f"UPDATE {AS400_TABLE} SET {assignments} WHERE BICCPTCLI = ?", params)
                    if cursor.rowcount == 0:
                        raise ValueError("No row was updated. The record may have been changed or removed.")
                    conn.commit()
                    as400_updated = True
                audit = ChangeLog(username=session["username"], changed_at=datetime.now(timezone.utc).replace(tzinfo=None))
                for field in ALL_FIELDS:
                    setattr(audit, f"{field}_OLD", int(original[field]) if field == "BICVCLI" else original[field])
                    setattr(audit, f"{field}_NEW", int(new_values[field]) if field == "BICVCLI" else new_values[field])
                db.session.add(audit)
                db.session.commit()
                flash("BIC3TAB record updated successfully.", "success")
                found = True
                snapshot_payload, snapshot_signature = encode_snapshot(new_values)
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "error")
                found = bool(key)
                if key:
                    snapshot_payload = request.form.get("snapshot")
                    snapshot_signature = request.form.get("snapshot_signature")
            except Exception:
                db.session.rollback()
                app.logger.exception("IBM i record update or MySQL audit insert failed")
                if as400_updated:
                    flash("IBM i was updated, but the audit entry could not be saved in MySQL. Contact your administrator.", "error")
                else:
                    flash("The record could not be updated. Check the values and connection settings.", "error")
                found = bool(key)
                if key:
                    snapshot_payload = request.form.get("snapshot")
                    snapshot_signature = request.form.get("snapshot_signature")
    else:
        key = session.get("searched_key")
    return render_template("bic3tab.html", csrf_token=csrf_token(), values=values, key=key, found=found,
                           snapshot=snapshot_payload, snapshot_signature=snapshot_signature)


def valid_fields(values):
    if not re.fullmatch(r"\d{1,5}", values["BICVCLI"]) or int(values["BICVCLI"]) > 99999:
        raise ValueError("BICVCLI must be a whole number from 0 to 99999.")
    limits = {"SHEMAIL2DE": 1, "BICSNIF": 9, "BICSCCC": 11, "BICSPRACA": 2, "BICSSERV": 3, "BICSPROD": 3}
    for field, limit in limits.items():
        if len(values[field]) > limit:
            raise ValueError(f"{field} must be at most {limit} characters.")


@app.post("/logout")
def logout():
    if not valid_csrf():
        flash("Your form expired. Please try again.", "error")
        return redirect(url_for("bic3tab"))
    session.clear()
    return redirect(url_for("login"))


@app.context_processor
def inject_connection_target():
    return {"connection_target": app.config["AS400_DSN"]}


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
