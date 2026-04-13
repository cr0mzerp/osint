# ==========================================
# PROJECT JARVIS - OSINT TERMINAL CORE V3
# DEVELOPER: LORD
# MODULE: ADVANCED BACKEND ENGINE
# ==========================================

import os
import re
import time
import logging
import secrets
import threading
import subprocess
import json
import shutil
import csv
import hashlib
import io
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from collections import defaultdict
from functools import wraps

from flask import Flask, render_template, request, jsonify, Response, redirect, url_for, flash, session
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from flask_sqlalchemy import SQLAlchemy
from wtforms import StringField, PasswordField, BooleanField, validators
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from skills_catalog import DATABASE_SKILLS, SKILL_GROUP_LABELS_TR

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///osint_users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_SECURE'] = False  # True for HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

# Initialize extensions
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Bu sayfaya erişmek için giriş yapmalısınız.'
login_manager.login_message_category = 'info'
csrf = CSRFProtect(app)
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# --- TRACK USER ACTIVITY ---
@app.before_request
def update_last_active():
    """Update user's last_active timestamp on every authenticated request."""
    if current_user.is_authenticated:
        current_user.last_active = datetime.utcnow()
        db.session.commit()

# --- EMAIL VERIFICATION ---
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def send_verification_email(email: str, code: str) -> bool:
    """Send verification code to user's email."""
    try:
        # SMTP configuration (update with your SMTP settings)
        smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.environ.get('SMTP_PORT', '587'))
        smtp_username = os.environ.get('SMTP_USERNAME', '')
        smtp_password = os.environ.get('SMTP_PASSWORD', '')
        smtp_from = os.environ.get('SMTP_FROM', 'noreply@osint.local')

        # If SMTP credentials not configured, log to console for development
        if not smtp_username or not smtp_password:
            logger.info(f"[DEV MODE] Verification code for {email}: {code}")
            print(f" E-POSTA DOĞRULAMA KODU: {code}")
            print(f" Gönderildi: {email}")
            return True

        # Send email via SMTP
        msg = MIMEMultipart()
        msg['From'] = smtp_from
        msg['To'] = email
        msg['Subject'] = 'OSINT Terminal - E-posta Doğrulama Kodu'

        body = f"""
        OSINT Terminal'e hoş geldiniz!

        E-posta doğrulama kodunuz: {code}

        Bu kod 15 dakika geçerlidir.

        Eğer bu isteği siz yapmadıysanız, bu e-postayı görmezden gelin.
        """
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_username, smtp_password)
        server.send_message(msg)
        server.quit()

        logger.info(f"Verification email sent to {email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send verification email: {e}")
        return False

# --- DETAYLI LOGLAMA SİSTEMİ ---
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("osint_terminal_debug.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- AUTHENTICATION SYSTEM ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    last_active = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    is_admin = db.Column(db.Boolean, default=False)
    failed_login_attempts = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime)
    email_verified = db.Column(db.Boolean, default=False)
    verification_code = db.Column(db.String(6), nullable=True)
    verification_code_expires = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        return bcrypt.check_password_hash(self.password_hash, password)

    def is_locked(self):
        if self.locked_until and self.locked_until > datetime.utcnow():
            return True
        return False

    def reset_failed_attempts(self):
        self.failed_login_attempts = 0
        self.locked_until = None

    def increment_failed_attempts(self):
        self.failed_login_attempts += 1
        if self.failed_login_attempts >= 5:
            self.locked_until = datetime.utcnow() + timedelta(minutes=15)
        db.session.commit()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# WTForms
class RegistrationForm(FlaskForm):
    username = StringField('Kullanıcı adı', [
        validators.Length(min=4, max=25, message='4-25 karakter arası'),
        validators.DataRequired(message='Zorunlu alan')
    ])
    email = StringField('E-posta', [
        validators.Email(message='Geçerli e-posta girin'),
        validators.DataRequired(message='Zorunlu alan')
    ])
    password = PasswordField('Şifre', [
        validators.Length(min=12, message='En az 12 karakter'),
        validators.DataRequired(message='Zorunlu alan'),
        validators.Regexp(
            r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]',
            message='Şifre: 1 büyük, 1 küçük harf, 1 sayı, 1 özel karakter içermeli'
        )
    ])
    confirm_password = PasswordField('Şifre onay', [
        validators.DataRequired(message='Zorunlu alan'),
        validators.EqualTo('password', message='Şifreler eşleşmiyor')
    ])

class LoginForm(FlaskForm):
    username = StringField('Kullanıcı adı veya e-posta', [
        validators.DataRequired(message='Zorunlu alan')
    ])
    password = PasswordField('Şifre', [
        validators.DataRequired(message='Zorunlu alan')
    ])
    remember_me = BooleanField('Beni hatırla')

# --- AUTH ROUTES ---
@app.route('/register', methods=['GET', 'POST'])
@limiter.limit("10 per hour")
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    form = RegistrationForm()
    if form.validate_on_submit():
        if User.query.filter_by(username=form.username.data).first():
            flash('Bu kullanıcı adı zaten alınmış.', 'error')
            return render_template('register.html', form=form)
        if User.query.filter_by(email=form.email.data).first():
            flash('Bu e-posta zaten kayıtlı.', 'error')
            return render_template('register.html', form=form)
        
        # Generate verification code
        verification_code = str(secrets.randbelow(900000) + 100000)  # 6-digit code
        verification_expires = datetime.utcnow() + timedelta(minutes=15)
        
        user = User(username=form.username.data, email=form.email.data)
        user.set_password(form.password.data)
        user.verification_code = verification_code
        user.verification_code_expires = verification_expires
        user.email_verified = False  # Not verified yet
        db.session.add(user)
        db.session.commit()
        
        # Send verification email
        if send_verification_email(user.email, verification_code):
            flash('Kayıt başarılı! E-posta adresinize doğrulama kodu gönderildi.', 'success')
            return redirect(url_for('verify_email', email=user.email))
        else:
            flash('Kayıt başarılı ancak e-posta gönderilemedi. Lütfen daha sonra tekrar deneyin.', 'warning')
            return redirect(url_for('verify_email', email=user.email))
    
    return render_template('register.html', form=form)

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("20 per hour")
@csrf.exempt
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter(
            (User.username == form.username.data) | (User.email == form.username.data)
        ).first()
        if not user or not user.check_password(form.password.data):
            flash('Geçersiz kullanıcı adı veya şifre.', 'error')
            return render_template('login.html', form=form)
        if not user.is_active:
            flash('Hesabınız devre dışı bırakılmış.', 'error')
            return render_template('login.html', form=form)
        if user.is_locked():
            flash('Hesabınız çok fazla başarısız denemeden dolayı kilitlendi. 15 dakika sonra tekrar deneyin.', 'error')
            return render_template('login.html', form=form)
        if not user.email_verified and not user.is_admin:
            flash('E-posta adresiniz doğrulanmamış. Lütfen önce e-postanızı doğrulayın.', 'warning')
            return redirect(url_for('verify_email', email=user.email))
        if user.is_admin:
            flash('Admin hesapları sadece özel giriş sekmesinden giriş yapabilir.', 'error')
            return render_template('login.html', form=form)
        login_user(user, remember=form.remember_me.data)
        user.last_login = datetime.utcnow()
        user.reset_failed_attempts()
        db.session.commit()
        if form.remember_me.data:
            session.permanent = True
        next_page = request.args.get('next')
        return redirect(next_page) if next_page else redirect(url_for('home'))
    return render_template('login.html', form=form)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Çıkış yapıldı.', 'info')
    return redirect(url_for('login'))

@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html', user=current_user)

@app.route('/verify-email', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
@csrf.exempt
def verify_email():
    """Verify email with code or resend verification code."""
    email = request.args.get('email') or request.form.get('email')
    
    if not email:
        flash('E-posta adresi gerekli.', 'error')
        return redirect(url_for('register'))
    
    user = User.query.filter_by(email=email).first()
    if not user:
        flash('Kullanıcı bulunamadı.', 'error')
        return redirect(url_for('register'))
    
    if user.email_verified:
        flash('E-posta zaten doğrulanmış.', 'success')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        code = request.form.get('code')
        action = request.form.get('action')
        
        if action == 'resend':
            # Generate new code
            verification_code = str(secrets.randbelow(900000) + 100000)
            verification_expires = datetime.utcnow() + timedelta(minutes=15)
            user.verification_code = verification_code
            user.verification_code_expires = verification_expires
            db.session.commit()
            
            if send_verification_email(user.email, verification_code):
                flash('Yeni doğrulama kodu e-posta adresinize gönderildi.', 'success')
            else:
                flash('E-posta gönderilemedi. Lütfen daha sonra tekrar deneyin.', 'error')
            
            return render_template('verify_email.html', email=email)
        
        elif action == 'verify':
            # Verify code with strict validation
            if not code or len(code) != 6 or not code.isdigit():
                flash('Geçersiz doğrulama kodu. 6 haneli sayı girin.', 'error')
                return render_template('verify_email.html', email=email)
            
            # Check if verification code exists
            if not user.verification_code:
                flash('Doğrulama kodu bulunamadı. Yeni kod gönderin.', 'error')
                return render_template('verify_email.html', email=email)
            
            # Check if code matches
            if user.verification_code != code:
                flash('Yanlış doğrulama kodu.', 'error')
                return render_template('verify_email.html', email=email)
            
            # Check if code expired
            if user.verification_code_expires and user.verification_code_expires < datetime.utcnow():
                flash('Doğrulama kodunun süresi doldu. Yeni kod gönderin.', 'error')
                return render_template('verify_email.html', email=email)
            
            # Code is valid, verify email
            user.email_verified = True
            user.verification_code = None
            user.verification_code_expires = None
            db.session.commit()
            
            flash('E-posta başarıyla doğrulandı! Giriş yapabilirsiniz.', 'success')
            return redirect(url_for('login'))
    
    return render_template('verify_email.html', email=email)

# --- CREATE ADMIN HELPER ---
def create_admin_user(username: str, password: str, email: str = None):
    """Create admin user bypassing validation (for initial setup)."""
    with app.app_context():
        if User.query.filter_by(username=username).first():
            print(f"Kullanıcı '{username}' zaten var!")
            return False
        if email and User.query.filter_by(email=email).first():
            print(f"E-posta '{email}' zaten kayıtlı!")
            return False

        admin = User(username=username, email=email or f"{username}@admin.local")
        admin.set_password(password)
        admin.is_active = True
        admin.is_admin = True
        db.session.add(admin)
        db.session.commit()
        print(f"Admin kullanıcısı oluşturuldu: {username}")
        return True

# --- ADMIN DECORATOR ---
def admin_required(f):
    """Decorator for admin-only routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            return jsonify({"ok": False, "error": "Yetkisiz erişim"}), 403
        return f(*args, **kwargs)
    return decorated_function

# --- ADMIN ROUTES ---
@app.route('/x9z7k2m4q8w1', methods=['GET', 'POST'])
@limiter.limit("20 per hour")
def admin_login():
    """Hidden admin login route."""
    if current_user.is_authenticated:
        if current_user.is_admin:
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('home'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            flash('Kullanıcı adı ve şifre gerekli.', 'error')
            return render_template('admin_login.html')

        user = User.query.filter_by(username=username).first()
        if not user or not user.check_password(password):
            flash('Geçersiz admin bilgileri.', 'error')
            return render_template('admin_login.html')

        if not user.is_admin:
            flash('Bu hesap admin değil.', 'error')
            return render_template('admin_login.html')

        login_user(user)
        user.last_login = datetime.utcnow()
        db.session.commit()
        return redirect(url_for('admin_dashboard'))

    return render_template('admin_login.html')

@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    """Admin-only dashboard."""
    total_users = User.query.count()
    total_admins = User.query.filter_by(is_admin=True).count()
    recent_users = User.query.order_by(User.created_at.desc()).limit(5).all()
    
    return render_template('admin_dashboard.html',
                           total_users=total_users,
                           total_admins=total_admins,
                           recent_users=recent_users)

@app.route('/admin/api/users', methods=['GET'])
@login_required
@admin_required
def admin_list_users():
    """List all users (admin only)."""
    users = User.query.all()
    now = datetime.utcnow()
    five_minutes_ago = now - timedelta(minutes=5)
    
    user_list = []
    online_count = 0
    for u in users:
        is_online = u.last_active and u.last_active > five_minutes_ago
        if is_online:
            online_count += 1
        
        user_list.append({
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "is_admin": u.is_admin,
            "is_active": u.is_active,
            "is_online": is_online,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login": u.last_login.isoformat() if u.last_login else None,
            "last_active": u.last_active.isoformat() if u.last_active else None,
            "failed_login_attempts": u.failed_login_attempts,
        })
    
    return jsonify({
        "ok": True,
        "online_count": online_count,
        "users": user_list
    })

@app.route('/admin/api/users/<int:user_id>/toggle_active', methods=['POST'])
@login_required
@admin_required
@csrf.exempt
def admin_toggle_user_active(user_id):
    """Toggle user active status (admin only)."""
    user = User.query.get_or_404(user_id)
    if user.is_admin:
        return jsonify({"ok": False, "error": "Admin hesabı devre dışı bırakılamaz"}), 400
    user.is_active = not user.is_active
    db.session.commit()
    return jsonify({"ok": True, "is_active": user.is_active})

@app.route('/admin/api/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
@csrf.exempt
def admin_delete_user(user_id):
    """Delete user (admin only)."""
    user = User.query.get_or_404(user_id)
    if user.is_admin:
        return jsonify({"ok": False, "error": "Admin hesabı silinemez"}), 400
    if user.id == current_user.id:
        return jsonify({"ok": False, "error": "Kendi hesabınızı silemezsiniz"}), 400
    db.session.delete(user)
    db.session.commit()
    return jsonify({"ok": True})

# --- YEREL TXT TABANI (ALIEN_TXTBASE) ---
# Ortam değişkeni OSINT_LOG_ROOT ile geçersiz kılınabilir.
LOCAL_LOG_ROOT = os.environ.get("OSINT_LOG_ROOT", r"C:\Users\batin\ALIEN_TXTBASE")
PART_DIR_PATTERN = re.compile(r"^part\s*\d+$", re.IGNORECASE)

# --- SKİLLER: skills_catalog.py (150+ kayıt) — referans listesi ---

# --- YEREL BÜYÜK TXT ARAMA (satır bazlı, bellek dostu) ---
_PRESET_PATTERNS = {
    "preset_email": re.compile(
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    ),
    "preset_tc": re.compile(r"(?<!\d)[1-9]\d{10}(?!\d)"),
    "preset_iban": re.compile(r"(?<!\w)[A-Z]{2}\d{2}[A-Z0-9]{1,30}(?!\w)", re.IGNORECASE),
    "preset_phone": re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"),
    "preset_ip": re.compile(r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"),
    "preset_btc": re.compile(r"\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b"),
    "preset_eth": re.compile(r"\b0x[a-fA-F0-9]{40}\b"),
    "preset_url": re.compile(r"\bhttps?:\/\/[\w\-]+(\.[\w\-]+)+[/#?]?.*\b"),
}


def _is_path_under(parent: str, child: str) -> bool:
    parent = os.path.realpath(parent)
    child = os.path.realpath(child)
    try:
        return os.path.commonpath([parent, child]) == parent
    except ValueError:
        return False


def _tc_checksum_ok(digits: str) -> bool:
    if len(digits) != 11 or not digits.isdigit() or digits[0] == "0":
        return False
    d = [int(c) for c in digits]
    s_odd = sum(d[i] for i in range(0, 9, 2))
    s_even = sum(d[i] for i in range(1, 9, 2))
    if (s_odd * 7 - s_even) % 10 != d[9]:
        return False
    if sum(d[:10]) % 10 != d[10]:
        return False
    return True


def _part_sort_key(name: str):
    nums = re.sub(r"\D", "", name)
    return (int(nums) if nums else 0, name.lower())


def list_part_folders(root: str):
    """'part 1', 'part 24' gibi doğrudan alt klasörleri listeler."""
    out = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root), key=_part_sort_key):
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        if not PART_DIR_PATTERN.match(name.strip()):
            continue
        txt_count = 0
        try:
            with os.scandir(path) as it:
                for e in it:
                    if e.is_file() and e.name.lower().endswith(".txt"):
                        txt_count += 1
        except OSError:
            continue
        out.append({"name": name, "path": path, "txt_count": txt_count})
    return out


def resolve_scan_root(root: str, part_choice: str) -> str:
    root = os.path.realpath(root)
    if not os.path.isdir(root):
        raise FileNotFoundError("Kök dizin yok")
    if not part_choice or part_choice.strip().lower() == "all":
        return root
    name = os.path.basename(part_choice.strip())
    child = os.path.realpath(os.path.join(root, name))
    if not _is_path_under(root, child) or not os.path.isdir(child):
        raise ValueError("Geçersiz part klasörü")
    return child


def collect_txt_paths(scan_root: str, path_keyword: Optional[str] = None):
    """path_keyword: skill ile gelir; yalnızca göreli yol veya dosya adında bu alt dizge geçen .txt dosyaları."""
    paths = []
    root_real = os.path.realpath(scan_root)
    needle = (path_keyword or "").strip().lower()
    logger.info(f"[DEBUG] collect_txt_paths - scan_root: {scan_root}, path_keyword: {path_keyword}, needle: '{needle}'")
    try:
        for dirpath, _dirnames, filenames in os.walk(root_real, followlinks=False):
            for fn in filenames:
                if not fn.lower().endswith(".txt"):
                    continue
                full = os.path.join(dirpath, fn)
                if needle:
                    try:
                        rel = os.path.relpath(full, root_real).lower()
                    except ValueError:
                        rel = full.lower()
                    rel_slash = rel.replace("\\", "/")
                    fn_low = fn.lower()
                    if needle not in rel_slash and needle not in fn_low:
                        continue
                paths.append(full)
    except OSError as e:
        logger.warning("os.walk hatası: %s", e)
    logger.info(f"[DEBUG] collect_txt_paths - Found {len(paths)} .txt files")
    return paths


def _line_matches_preset(mode: str, line: str, strict_tc: bool) -> bool:
    pat = _PRESET_PATTERNS.get(mode)
    if not pat:
        return False
    if mode == "preset_tc":
        for m in pat.finditer(line):
            dig = m.group(0)
            if not strict_tc or _tc_checksum_ok(dig):
                return True
        return False
    return pat.search(line) is not None


def search_file_lines(
    filepath: str,
    mode: str,
    query: str,
    case_insensitive: bool,
    strict_tc: bool,
    max_per_file: int,
    snippet_len: int,
    stop_event: threading.Event,
):
    hits = []
    q = (query or "").strip()
    regex_obj = None
    if mode == "regex" and q:
        try:
            regex_obj = re.compile(q, re.IGNORECASE if case_insensitive else 0)
        except re.error:
            return hits, "Geçersiz regex"
    try:
        with open(
            filepath, "r", encoding="utf-8", errors="replace", newline=""
        ) as f:
            for line_no, line in enumerate(f, 1):
                if stop_event.is_set():
                    break
                if len(hits) >= max_per_file:
                    break
                ok = False
                if mode == "contains":
                    if not q:
                        continue
                    hay = line.lower() if case_insensitive else line
                    needle = q.lower() if case_insensitive else q
                    ok = needle in hay
                elif mode == "regex":
                    if not regex_obj:
                        continue
                    ok = regex_obj.search(line) is not None
                elif mode in _PRESET_PATTERNS:
                    ok = _line_matches_preset(mode, line, strict_tc)
                    if ok and q:
                        hq = line.lower() if case_insensitive else line
                        nq = q.lower() if case_insensitive else q
                        ok = nq in hq
                if ok:
                    snippet = line.strip()
                    if len(snippet) > snippet_len:
                        snippet = snippet[:snippet_len] + "…"
                    hits.append((line_no, snippet))
    except OSError as e:
        logger.debug("Dosya okunamadı %s: %s", filepath, e)
    return hits, None


def run_parallel_txt_search(
    scan_root: str,
    log_root: str,
    mode: str,
    query: str,
    case_insensitive: bool,
    strict_tc: bool,
    max_results: int,
    max_per_file: int,
    workers: int,
    stop_event: Optional[threading.Event] = None,
    progress: Optional[dict] = None,
    path_keyword_filter: Optional[str] = None,
):
    paths = collect_txt_paths(scan_root, path_keyword_filter)
    if not paths:
        if progress is not None:
            plock = progress.setdefault("lock", threading.Lock())
            with plock:
                progress["files_total"] = 0
                progress["files_done"] = 0
        if path_keyword_filter:
            return [], 0.0, False, "Filtre nedeniyle taranacak .txt dosyası bulunamadı. (skill/path_keyword)"
        return [], 0.0, False, "Taranacak .txt dosyası bulunamadı. Kök dizin/part seçimini kontrol et."

    workers = max(1, min(int(workers), 32))
    max_results = max(1, min(int(max_results), 5000))
    max_per_file = max(1, min(int(max_per_file), 500))
    snippet_len = 1200

    stop = stop_event if stop_event is not None else threading.Event()
    lock = threading.Lock()
    aggregated = []
    t0 = time.time()
    err_msg = None

    if progress is not None:
        plock = progress.setdefault("lock", threading.Lock())
        with plock:
            progress["files_total"] = len(paths)
            progress["files_done"] = 0

    def task(path: str):
        if stop.is_set():
            return []
        h, err = search_file_lines(
            path,
            mode,
            query,
            case_insensitive,
            strict_tc,
            max_per_file,
            snippet_len,
            stop,
        )
        if err:
            return err
        rows = []
        rel_base = os.path.relpath(path, log_root)
        folder, fname = os.path.split(rel_base)
        for line_no, snippet in h:
            rows.append(
                {
                    "rel_path": folder or ".",
                    "file": fname,
                    "line_no": line_no,
                    "snippet": snippet,
                }
            )
        return rows

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(task, p): p for p in paths}
        for fut in as_completed(futures):
            if progress is not None:
                plock = progress.get("lock") or threading.Lock()
                with plock:
                    progress["files_done"] = progress.get("files_done", 0) + 1
            try:
                got = fut.result()
            except Exception as e:
                logger.exception("Arama işçisi hatası: %s", e)
                continue
            if isinstance(got, str):
                err_msg = got
                stop.set()
                break
            with lock:
                for row in got:
                    if len(aggregated) >= max_results:
                        stop.set()
                        break
                    aggregated.append(row)
                if len(aggregated) >= max_results:
                    stop.set()
            if stop.is_set():
                break

    elapsed = round(time.time() - t0, 3)
    truncated = len(aggregated) >= max_results
    return aggregated, elapsed, truncated, err_msg


def _find_rg_path() -> Optional[str]:
    env_path = (os.environ.get("RG_PATH") or "").strip().strip('"')
    if env_path:
        if os.path.isfile(env_path):
            return env_path
        if os.path.isdir(env_path):
            cand = os.path.join(env_path, "rg.exe")
            if os.path.isfile(cand):
                return cand

    rg_path = shutil.which("rg")
    if rg_path:
        return rg_path

    # Yaygın Chocolatey konumu
    cand = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "chocolatey", "bin", "rg.exe")
    if os.path.isfile(cand):
        return cand

    return None


_RG_PRESET_PATTERNS = {
    "preset_email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    "preset_tc": r"\b[1-9]\d{10}\b",
    "preset_iban": r"\b[A-Z]{2}\d{2}[A-Z0-9]{1,30}\b",
    "preset_phone": r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
    "preset_ip": r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b",
    "preset_btc": r"\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b",
    "preset_eth": r"\b0x[a-fA-F0-9]{40}\b",
    "preset_url": r"\bhttps?:\/\/[\w\-]+(\.[\w\-]+)+[/#?]?.*\b",
}


def run_rg_search(
    scan_root: str,
    log_root: str,
    mode: str,
    query: str,
    case_insensitive: bool,
    max_results: int,
    max_per_file: int,
    stop_event: Optional[threading.Event] = None,
    progress: Optional[dict] = None,
    path_keyword_filter: Optional[str] = None,
    job: Optional[dict] = None,
):
    rg_path = _find_rg_path()
    if not rg_path:
        return [], 0.0, False, "rg (ripgrep) bulunamadı. PATH'e ekleyin veya RG_PATH ile rg.exe yolunu belirtin. (Örn: C:\\ProgramData\\chocolatey\\bin)"

    q = (query or "").strip()
    if mode in ("contains", "regex") and not q:
        return [], 0.0, False, "Bu mod için sorgu gerekli."

    rg_mode = mode
    rg_query = q
    if mode in _RG_PRESET_PATTERNS:
        rg_mode = "regex"
        rg_query = _RG_PRESET_PATTERNS[mode]
        if q:
            rg_query = f"(?:{rg_query})" + f".*{re.escape(q)}" if case_insensitive else f".*{q}"
    elif mode not in ("contains", "regex"):
        return [], 0.0, False, "rg modu bilinmeyen mod: " + mode

    max_results = max(1, min(int(max_results), 5000))
    max_per_file = max(1, min(int(max_per_file), 500))
    snippet_len = 1200
    stop = stop_event if stop_event is not None else threading.Event()

    if progress is not None:
        plock = progress.setdefault("lock", threading.Lock())
        with plock:
            progress["files_total"] = 1
            progress["files_done"] = 0

    cmd = [
        rg_path,
        "--json",
        "--no-messages",
        "--line-number",
        "--max-count",
        str(max_per_file),
        "--glob",
        "*.txt",
    ]
    if case_insensitive:
        cmd.append("-i")
    if rg_mode == "contains":
        cmd.append("-F")
    if path_keyword_filter:
        cmd.extend(["--glob", f"*{path_keyword_filter}*"])
    cmd.append(rg_query)
    cmd.append(os.path.realpath(scan_root))

    aggregated = []
    truncated = False
    err_msg = None
    t0 = time.time()

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as e:
        return [], 0.0, False, f"rg başlatılamadı: {e}"

    if job is not None:
        job["proc"] = proc

    def _finish_progress():
        if progress is not None:
            plock = progress.get("lock") or threading.Lock()
            with plock:
                progress["files_done"] = 1

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if stop.is_set():
                try:
                    proc.terminate()
                except OSError:
                    pass
                break
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "match":
                continue
            data = obj.get("data") or {}
            path_text = ((data.get("path") or {}).get("text"))
            if not path_text:
                continue
            line_no = (data.get("line_number") or 0)
            snippet = (((data.get("lines") or {}).get("text")) or "").strip("\r\n")
            if len(snippet) > snippet_len:
                snippet = snippet[:snippet_len] + "…"

            try:
                rel_base = os.path.relpath(path_text, log_root)
            except ValueError:
                rel_base = path_text
            folder, fname = os.path.split(rel_base)
            aggregated.append(
                {
                    "rel_path": folder or ".",
                    "file": fname,
                    "line_no": int(line_no) if line_no else 0,
                    "snippet": snippet,
                }
            )
            if len(aggregated) >= max_results:
                truncated = True
                try:
                    proc.terminate()
                except OSError:
                    pass
                break

        try:
            _out, _err = proc.communicate(timeout=1)
        except Exception:
            _err = ""
        rc = proc.poll()
        if rc not in (0, 1, None):
            # 1: no matches, 0: ok
            if _err:
                err_msg = _err.strip()[-500:]
            else:
                err_msg = f"rg hata kodu: {rc}"
    finally:
        _finish_progress()
        if job is not None:
            job.pop("proc", None)

    elapsed = round(time.time() - t0, 3)
    return aggregated, elapsed, truncated, err_msg


def _default_logs_form():
    return {
        "part": "all",
        "skill_id": "all",
        "path_keyword": None,
        "use_rg": True,
        "mode": "contains",
        "query": "",
        "max_results": 400,
        "max_per_file": 40,
        "workers": 6,
        "case_insensitive": True,
        "strict_tc": False,
        "deduplicate": False,
        "boolean_mode": False,
    }


def _resolve_skill_path_keyword(skill_id: Optional[str]):
    """(path_keyword|None, hata_mesajı|None) — 'all' veya boş ise filtre yok."""
    if not skill_id or str(skill_id).strip().lower() == "all":
        return None, None
    sid = str(skill_id).strip()
    for s in DATABASE_SKILLS:
        if s.get("id") == sid:
            return s.get("path_keyword"), None
    return None, "Geçersiz skill seçimi."


def _truthy(val) -> bool:
    return val in (True, "1", "on", 1, "true", "True")


def _parse_logs_payload(src: dict) -> dict:
    form = _default_logs_form()
    form["part"] = str(src.get("part") or "all")
    form["mode"] = str(src.get("mode") or "contains")
    form["query"] = (src.get("query") or "").strip()
    try:
        form["max_results"] = int(src.get("max_results", 400))
    except (TypeError, ValueError):
        form["max_results"] = 400
    try:
        form["max_per_file"] = int(src.get("max_per_file", 40))
    except (TypeError, ValueError):
        form["max_per_file"] = 40
    try:
        form["workers"] = int(src.get("workers", 6))
    except (TypeError, ValueError):
        form["workers"] = 6
    form["case_insensitive"] = _truthy(src.get("case_insensitive"))
    form["strict_tc"] = _truthy(src.get("strict_tc"))
    form["use_rg"] = _truthy(src.get("use_rg"))
    form["deduplicate"] = _truthy(src.get("deduplicate"))
    form["boolean_mode"] = _truthy(src.get("boolean_mode"))
    form["skill_id"] = str(src.get("skill_id") or "all").strip() or "all"
    return form


def _validate_logs_scan(root: str, exists: bool, form: dict):
    if not exists:
        return None, "Kök dizin bulunamadı."
    try:
        scan_root = resolve_scan_root(root, form["part"])
    except (FileNotFoundError, ValueError) as e:
        return None, str(e)
    mode = form["mode"]
    q = form["query"]
    if mode in ("contains", "regex") and mode not in _RG_PRESET_PATTERNS and not q:
        return None, "Bu mod için sorgu gerekli."
    if mode == "regex" and mode not in _RG_PRESET_PATTERNS and q:
        try:
            re.compile(q, re.IGNORECASE if form["case_insensitive"] else 0)
        except re.error as e:
            return None, f"Regex hatası: {e}"
    kw, sk_err = _resolve_skill_path_keyword(form.get("skill_id"))
    if sk_err:
        return None, sk_err
    form["path_keyword"] = kw

    if form.get("use_rg"):
        if form["mode"] not in ("contains", "regex") and form["mode"] not in _RG_PRESET_PATTERNS:
            return None, "rg modu bilinmeyen mod: " + form["mode"]
    return scan_root, None


# --- SEARCH JOBS MANAGER ---
SEARCH_JOBS: dict = {}
JOBS_LOCK = threading.Lock()
MAX_CONCURRENT_SEARCHES = 1  # Only 1 concurrent search to prevent system overload
MAX_SEARCH_JOBS = 48


def _prune_search_jobs():
    with JOBS_LOCK:
        while len(SEARCH_JOBS) > MAX_SEARCH_JOBS:
            first = next(iter(SEARCH_JOBS))
            del SEARCH_JOBS[first]


def _deduplicate_results(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove duplicate results based on snippet hash."""
    seen = set()
    unique = []
    for hit in hits:
        snippet = hit.get("snippet", "")
        hash_val = hashlib.md5(snippet.encode()).hexdigest()
        if hash_val not in seen:
            seen.add(hash_val)
            unique.append(hit)
    return unique


def _apply_boolean_search(query: str, hits: List[Dict[str, Any]], case_insensitive: bool) -> List[Dict[str, Any]]:
    """Apply AND, OR, NOT boolean logic to search results."""
    if not query:
        return hits
    
    # Parse boolean operators
    terms = []
    current = ""
    in_quotes = False
    for char in query:
        if char in ('"', "'"):
            in_quotes = not in_quotes
            current += char
        elif char in (' ', '\t') and not in_quotes:
            if current.strip():
                terms.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        terms.append(current.strip())
    
    # Process terms with operators
    must_have = []
    should_have = []
    must_not_have = []
    
    for term in terms:
        term_upper = term.upper()
        if term_upper.startswith("AND "):
            must_have.append(term[4:])
        elif term_upper.startswith("OR "):
            should_have.append(term[3:])
        elif term_upper.startswith("NOT "):
            must_not_have.append(term[4:])
        elif term_upper in ("AND", "OR", "NOT"):
            continue
        else:
            must_have.append(term)
    
    filtered = []
    for hit in hits:
        snippet = hit.get("snippet", "")
        search_text = snippet.lower() if case_insensitive else snippet
        
        # Check must_have (AND)
        must_match = True
        for term in must_have:
            t = term.lower() if case_insensitive else term
            if t not in search_text:
                must_match = False
                break
        
        # Check must_not_have (NOT)
        not_match = False
        for term in must_not_have:
            t = term.lower() if case_insensitive else term
            if t in search_text:
                not_match = True
                break
        
        # Check should_have (OR)
        should_match = True
        if should_have:
            should_match = False
            for term in should_have:
                t = term.lower() if case_insensitive else term
                if t in search_text:
                    should_match = True
                    break
        
        if must_match and not not_match and should_match:
            filtered.append(hit)
    
    return filtered


def _run_search_job(job_id: str, root: str, scan_root: str, form: dict):
    job = SEARCH_JOBS.get(job_id)
    if not job:
        return
    stop = job["stop"]
    progress = {"lock": threading.Lock(), "files_done": 0, "files_total": 0}
    with JOBS_LOCK:
        if job_id in SEARCH_JOBS:
            SEARCH_JOBS[job_id]["progress"] = progress
    try:
        if form.get("use_rg"):
            hits, elapsed, truncated, perr = run_rg_search(
                scan_root,
                root,
                form["mode"],
                form["query"],
                form["case_insensitive"],
                form["max_results"],
                form["max_per_file"],
                stop_event=stop,
                progress=progress,
                path_keyword_filter=form.get("path_keyword"),
                job=job,
            )
        else:
            hits, elapsed, truncated, perr = run_parallel_txt_search(
                scan_root,
                root,
                form["mode"],
                form["query"],
                form["case_insensitive"],
                form["strict_tc"],
                form["max_results"],
                form["max_per_file"],
                form["workers"],
                stop_event=stop,
                progress=progress,
                path_keyword_filter=form.get("path_keyword"),
            )
        
        # Apply deduplication if enabled
        if form.get("deduplicate") and hits:
            hits = _deduplicate_results(hits)
        
        # Apply boolean search if enabled
        if form.get("boolean_mode") and form.get("query") and hits:
            hits = _apply_boolean_search(form["query"], hits, form["case_insensitive"])
        
        with JOBS_LOCK:
            j = SEARCH_JOBS.get(job_id)
            if not j:
                return
            j["hits"] = hits
            j["hit_count"] = len(hits)
            j["exec_time"] = elapsed
            j["truncated"] = truncated
            j["error"] = perr
            if perr:
                j["state"] = "error"
            elif stop.is_set():
                j["state"] = "aborted"
            else:
                j["state"] = "done"
    except Exception as e:
        logger.exception("Arama işi hatası job=%s", job_id)
        with JOBS_LOCK:
            j = SEARCH_JOBS.get(job_id)
            if j:
                j["state"] = "error"
                j["error"] = str(e)


# --- ROUTES ---
@app.route('/')
@login_required
def home():
    # Admin-specific skills and limits
    admin_skills = DATABASE_SKILLS.copy() if isinstance(DATABASE_SKILLS, list) else DATABASE_SKILLS
    if current_user.is_admin:
        # Add admin-only skills
        if isinstance(admin_skills, list):
            admin_skills.append({
                "id": "admin_full_dump",
                "name": "Full Database Dump (Admin)",
                "description": "Tüm veritabanını dök (sadece admin)",
                "group": "admin",
                "path_keyword": "admin"
            })
            admin_skills.append({
                "id": "admin_raw_search",
                "name": "Raw Search (Admin)",
                "description": "Raw metin arama (sadece admin)",
                "group": "admin",
                "path_keyword": "admin"
            })
        else:
            admin_skills["admin_full_dump"] = {
                "id": "admin_full_dump",
                "name": "Full Database Dump (Admin)",
                "description": "Tüm veritabanını dök (sadece admin)",
                "group": "admin",
                "path_keyword": "admin"
            }
            admin_skills["admin_raw_search"] = {
                "id": "admin_raw_search",
                "name": "Raw Search (Admin)",
                "description": "Raw metin arama (sadece admin)",
                "group": "admin",
                "path_keyword": "admin"
            }
    
    return render_template(
        "index.html",
        skills=admin_skills if current_user.is_admin else DATABASE_SKILLS,
        skill_group_labels=SKILL_GROUP_LABELS_TR,
        nav_active="home",
        is_admin=current_user.is_admin,
    )


@app.route("/logs", methods=["GET"])
@login_required
def logs_page():
    root = LOCAL_LOG_ROOT
    exists = os.path.isdir(root)
    parts = list_part_folders(root) if exists else []
    form = _default_logs_form()
    
    # Admin-specific settings
    admin_skills = DATABASE_SKILLS.copy() if isinstance(DATABASE_SKILLS, list) else DATABASE_SKILLS
    if current_user.is_admin:
        # Admin gets higher limits but still conservative
        form["max_results"] = 5000  # Admin can get more results
        form["workers"] = 12  # Admin gets more workers but limited
        # Add admin-only skills
        if isinstance(admin_skills, list):
            admin_skills.append({
                "id": "admin_full_dump",
                "name": "Full Database Dump (Admin)",
                "description": "Tüm veritabanını dök (sadece admin)",
                "group": "admin",
                "path_keyword": "admin"
            })
        else:
            admin_skills["admin_full_dump"] = {
                "id": "admin_full_dump",
                "name": "Full Database Dump (Admin)",
                "description": "Tüm veritabanını dök (sadece admin)",
                "group": "admin",
                "path_keyword": "admin"
            }
    
    return render_template(
        "log_search.html",
        log_root=root,
        root_exists=exists,
        parts=parts,
        form=form,
        skills=admin_skills if current_user.is_admin else DATABASE_SKILLS,
        skill_group_labels=SKILL_GROUP_LABELS_TR,
        hits=None,
        exec_time=None,
        hit_count=0,
        truncated=False,
        scan_error=None,
        nav_active='logs',
        is_admin=current_user.is_admin,
    )


@app.route("/logs/api/search", methods=["POST"])
@login_required
@csrf.exempt
def logs_api_search_start():
    root = LOCAL_LOG_ROOT
    exists = os.path.isdir(root)
    payload = request.get_json(silent=True) or {}
    form = _parse_logs_payload(payload)
    
    # Check concurrent search limit to prevent system overload
    with JOBS_LOCK:
        running_count = sum(1 for j in SEARCH_JOBS.values() if j.get("state") == "running")
        if running_count >= MAX_CONCURRENT_SEARCHES:
            return jsonify({"ok": False, "error": f"Maksimum eşzamanlı arama limitine ulaşıldı. Lütfen bekleyin. (Maks: {MAX_CONCURRENT_SEARCHES})"}), 429
    
    # Admin gets higher limits but still conservative
    if current_user.is_admin:
        form["max_results"] = min(form.get("max_results", 5000), 10000)  # Admin can get up to 10k results
        form["workers"] = min(form.get("workers", 12), 24)  # Admin gets up to 24 workers
        form["max_per_file"] = min(form.get("max_per_file", 100), 500)  # Admin gets more per file
    else:
        # Regular users get conservative limits
        form["max_results"] = min(form.get("max_results", 400), 1000)
        form["workers"] = min(form.get("workers", 6), 12)
        form["max_per_file"] = min(form.get("max_per_file", 40), 100)
    
    scan_root, err = _validate_logs_scan(root, exists, form)
    if err:
        return jsonify({"ok": False, "error": err}), 400

    job_id = secrets.token_hex(12)
    stop_ev = threading.Event()
    _prune_search_jobs()
    with JOBS_LOCK:
        SEARCH_JOBS[job_id] = {
            "stop": stop_ev,
            "state": "running",
            "hits": [],
            "hit_count": 0,
            "error": None,
            "exec_time": None,
            "truncated": False,
            "progress": {"files_done": 0, "files_total": 0},
            "started": time.time(),
        }

    threading.Thread(
        target=_run_search_job,
        args=(job_id, root, scan_root, form),
        daemon=True,
    ).start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/logs/api/search/<job_id>", methods=["GET"])
@login_required
@limiter.exempt
def logs_api_search_poll(job_id: str):
    with JOBS_LOCK:
        j = SEARCH_JOBS.get(job_id)
        if not j:
            return jsonify({"ok": False, "error": "İş bulunamadı"}), 404
        prog = j.get("progress") or {}
        plock = prog.get("lock")
        files_done = prog.get("files_done", 0)
        files_total = prog.get("files_total", 0)
        if plock:
            with plock:
                files_done = prog.get("files_done", 0)
                files_total = prog.get("files_total", 0)
        out = {
            "ok": True,
            "state": j["state"],
            "files_done": files_done,
            "files_total": files_total,
            "hit_count": j.get("hit_count", 0),
            "exec_time": j.get("exec_time"),
            "truncated": j.get("truncated", False),
            "error": j.get("error"),
            "hits": j.get("hits", []),
        }
    return jsonify(out)


@app.route("/logs/api/search/<job_id>/abort", methods=["POST"])
@login_required
@csrf.exempt
def logs_api_search_abort(job_id: str):
    with JOBS_LOCK:
        j = SEARCH_JOBS.get(job_id)
        if not j:
            return jsonify({"ok": False, "error": "İş bulunamadı"}), 404
        j["stop"].set()
        j["state"] = "aborted"  # Immediately mark as aborted
        proc = j.get("proc")
        if proc is not None:
            try:
                proc.terminate()
                # Force kill if terminate doesn't work
                try:
                    proc.wait(timeout=1)
                except:
                    try:
                        proc.kill()
                    except:
                        pass
            except OSError:
                pass
    return jsonify({"ok": True})


# --- EXPORT ROUTES ---
@app.route("/logs/api/search/<job_id>/export/<format>", methods=["GET"])
@login_required
@csrf.exempt
def logs_api_search_export(job_id: str, format: str):
    """Export search results in CSV, JSON, or TXT format."""
    with JOBS_LOCK:
        j = SEARCH_JOBS.get(job_id)
        if not j:
            return jsonify({"ok": False, "error": "İş bulunamadı"}), 404
        hits = j.get("hits", [])

    if not hits:
        return jsonify({"ok": False, "error": "Sonuç yok"}), 400

    format = format.lower()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Dosya Yolu", "Dosya Adı", "Satır No", "Önizleme"])
        for hit in hits:
            writer.writerow([
                hit.get("rel_path", ""),
                hit.get("file", ""),
                hit.get("line_no", ""),
                hit.get("snippet", "")
            ])
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment;filename=search_results_{timestamp}.csv"}
        )
    elif format == "json":
        return Response(
            json.dumps(hits, indent=2, ensure_ascii=False),
            mimetype="application/json",
            headers={"Content-Disposition": f"attachment;filename=search_results_{timestamp}.json"}
        )
    elif format == "txt":
        lines = []
        for hit in hits:
            lines.append(f"[{hit.get('rel_path', '')}/{hit.get('file', '')}:{hit.get('line_no', '')}]")
            lines.append(hit.get("snippet", ""))
            lines.append("-" * 80)
        return Response(
            "\n".join(lines),
            mimetype="text/plain",
            headers={"Content-Disposition": f"attachment;filename=search_results_{timestamp}.txt"}
        )
    else:
        return jsonify({"ok": False, "error": "Geçersiz format (csv, json, txt)"}), 400


# --- SEARCH HISTORY ---
SEARCH_HISTORY_FILE = "search_history.json"
HISTORY_LOCK = threading.Lock()


def _load_search_history() -> List[Dict[str, Any]]:
    """Load search history from JSON file."""
    try:
        if os.path.exists(SEARCH_HISTORY_FILE):
            with open(SEARCH_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning("Search history yüklenemedi: %s", e)
    return []


def _save_search_history(history: List[Dict[str, Any]]):
    """Save search history to JSON file."""
    try:
        with open(SEARCH_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning("Search history kaydedilemedi: %s", e)


@app.route("/logs/api/history", methods=["GET", "POST"])
@login_required
@csrf.exempt
def logs_api_history():
    """Get or save search history."""
    if request.method == "GET":
        with HISTORY_LOCK:
            history = _load_search_history()
        return jsonify({"ok": True, "history": history})

    elif request.method == "POST":
        payload = request.get_json(silent=True) or {}
        search_entry = {
            "timestamp": datetime.now().isoformat(),
            "mode": payload.get("mode"),
            "query": payload.get("query"),
            "part": payload.get("part"),
            "skill_id": payload.get("skill_id"),
            "hit_count": payload.get("hit_count", 0),
            "exec_time": payload.get("exec_time"),
        }
        with HISTORY_LOCK:
            history = _load_search_history()
            history.insert(0, search_entry)
            history = history[:100]  # Keep last 100 searches
            _save_search_history(history)
        return jsonify({"ok": True, "saved": True})


@app.route("/logs/api/history/clear", methods=["POST"])
@login_required
@csrf.exempt
def logs_api_history_clear():
    """Clear search history."""
    with HISTORY_LOCK:
        _save_search_history([])
    return jsonify({"ok": True})


# --- STATISTICS ---
@app.route("/logs/api/stats", methods=["GET"])
@login_required
def logs_api_stats():
    """Get search statistics."""
    root = LOCAL_LOG_ROOT
    if not os.path.isdir(root):
        return jsonify({"ok": False, "error": "Kök dizin bulunamadı"}), 404

    parts = list_part_folders(root)
    total_txt_files = sum(p["txt_count"] for p in parts)

    # Search history stats
    with HISTORY_LOCK:
        history = _load_search_history()

    # Mode distribution
    mode_dist = defaultdict(int)
    for entry in history:
        mode = entry.get("mode", "unknown")
        mode_dist[mode] += 1

    # Recent searches
    recent_searches = history[:10]

    stats = {
        "ok": True,
        "total_parts": len(parts),
        "total_txt_files": total_txt_files,
        "total_searches": len(history),
        "mode_distribution": dict(mode_dist),
        "recent_searches": recent_searches,
    }

    return jsonify(stats)


# --- ALIST PROXY ---
def proxy_request(alist_url, method):
    """Helper function to proxy requests to Alist."""
    # Forward query string
    if request.query_string:
        alist_url += f"?{request.query_string.decode('utf-8')}"
    
    # Prepare headers - exclude problematic headers
    headers = {}
    for key, value in request.headers:
        if key.lower() not in ['host', 'content-length', 'transfer-encoding', 'connection', 'content-type']:
            headers[key] = value
    
    # Prepare body/data
    json_data = None
    form_data = None
    if method in ['POST', 'PUT', 'PATCH']:
        if request.is_json:
            json_data = request.get_json()
            headers['Content-Type'] = 'application/json'
        else:
            form_data = request.get_data()
    
    try:
        if method == 'GET':
            resp = requests.get(alist_url, headers=headers, timeout=30, allow_redirects=True)
        elif method == 'POST':
            resp = requests.post(alist_url, headers=headers, json=json_data, data=form_data, timeout=30, allow_redirects=True)
        elif method == 'PUT':
            resp = requests.put(alist_url, headers=headers, json=json_data, data=form_data, timeout=30, allow_redirects=True)
        elif method == 'DELETE':
            resp = requests.delete(alist_url, headers=headers, timeout=30, allow_redirects=True)
        elif method == 'PATCH':
            resp = requests.patch(alist_url, headers=headers, json=json_data, data=form_data, timeout=30, allow_redirects=True)
        else:
            return jsonify({"error": "Method not allowed"}), 405
        
        # Return response
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        response_headers = [(name, value) for (name, value) in resp.headers.items() if name.lower() not in excluded_headers]
        return Response(resp.content, resp.status_code, response_headers)
    except Exception as e:
        return jsonify({"error": str(e), "url": alist_url}), 503


@app.route('/alist', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
@limiter.exempt
@csrf.exempt
def alist_proxy_root():
    """Proxy requests to Alist root on localhost:5244."""
    alist_url = "http://localhost:5244/"
    return proxy_request(alist_url, request.method)


@app.route('/alist/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
@limiter.exempt
@csrf.exempt
def alist_proxy(path):
    """Proxy requests to Alist on localhost:5244."""
    alist_url = f"http://localhost:5244/{path}"
    return proxy_request(alist_url, request.method)


# Additional routes for Alist API paths (for absolute path requests from frontend)
@app.route('/api/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
@limiter.exempt
@csrf.exempt
def alist_api_proxy(path):
    """Proxy Alist API requests."""
    alist_url = f"http://localhost:5244/api/{path}"
    return proxy_request(alist_url, request.method)


@app.route('/assets/<path:path>', methods=['GET'])
@limiter.exempt
@csrf.exempt
def alist_assets_proxy(path):
    """Proxy Alist assets."""
    alist_url = f"http://localhost:5244/assets/{path}"
    return proxy_request(alist_url, 'GET')


@app.route('/static/<path:path>', methods=['GET'])
@limiter.exempt
@csrf.exempt
def alist_static_proxy(path):
    """Proxy Alist static files."""
    alist_url = f"http://localhost:5244/static/{path}"
    return proxy_request(alist_url, 'GET')


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        # Create admin user on first run (REMOVE AFTER FIRST RUN)
        create_admin_user("kr?nus", "admin", "admin@osint.local")
    # Kaggle/Colab ortamında 5000 portundan başlat
    app.run(host='0.0.0.0', port=5000, debug=True)