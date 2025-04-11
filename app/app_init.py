import base64
import os

from cryptography.fernet import Fernet

from app.radiopharmaceutical.radiopharmaceutical import radiopharm_bp
from app.table_manager import get_table_manager

SECRET = os.environ['SECRET_KEY']
_FERNET = None
_LOGIN_MANAGER = None
_APP = None

def init_app(app):
    """
    Initialize the user blueprint and login manager.
    In your main application, you can call:
        from user_blueprint import init_app
        init_app(app)
    """
    from app.auth.auth import user_bp
    login_manager = get_login_manager()
    login_manager.login_view = 'user.login'
    login_manager.init_app(app)

    app.register_blueprint(user_bp)
    app.register_blueprint(radiopharm_bp, url_prefix="/radiopharm")

    table_manager = get_table_manager()
    try:
        table_manager.create_table("users")
        app.logger.info("Azure table 'users' created or already exists.")
    except Exception as e:
        app.logger.error("Failed to create azure table 'users': %s", e)
    try:
        table_manager.create_table("pharmaceutical")
        app.logger.info("Azure table 'pharmaceutical' created or already exists.")
    except Exception as e:
        app.logger.error("Failed to create azure table 'pharmaceutical': %s", e)

def get_app(name):
    global _APP

    if _APP is None:
        from flask import Flask
        _APP = Flask(name)
        _APP.secret_key = SECRET  # for flashing messages, sessions, etc.
        _APP.config['SECRET_KEY'] = SECRET

        init_app(_APP)

    return _APP

def get_fernet():
    global _FERNET

    if _FERNET is None:
        key = base64.urlsafe_b64encode(SECRET[:32].encode())
        _FERNET = Fernet(key)
    return _FERNET

def get_login_manager():
    global _LOGIN_MANAGER

    if _LOGIN_MANAGER is None:
        from flask_login import LoginManager
        _LOGIN_MANAGER = LoginManager()
    return _LOGIN_MANAGER
