import os

from app.constants import *
from app.daysetup.daysetup import daysetup_bp
from app.dosing_schemes.dosing_schemes import dosing_bp
from app.optim.optim import optim_bp
from app.patients.patients import patients_bp
from app.radiopharmaceutical.radiopharmaceutical import radiopharm_bp
from app.radionuclide.radionuclide import radionuclide_bp
from app.table_manager import get_table_manager
from app.tests.tests import tests_bp

SECRET = os.environ['SECRET_KEY']
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
    app.register_blueprint(radionuclide_bp, url_prefix="/radionuclides")
    app.register_blueprint(radiopharm_bp, url_prefix="/radiopharm")
    app.register_blueprint(dosing_bp, url_prefix="/dosing")
    app.register_blueprint(daysetup_bp, url_prefix="/daysetup")
    app.register_blueprint(patients_bp, url_prefix="/patients")
    app.register_blueprint(optim_bp, url_prefix="/optim")
    app.register_blueprint(tests_bp, url_prefix="/tests")

    table_manager = get_table_manager()
    try:
        table_manager.create_table(USERS_TABLE)
        app.logger.info("Azure table 'users' created or already exists.")
    except Exception as e:
        app.logger.error("Failed to create azure table 'users': %s", e)
    try:
        table_manager.create_table(PHARM_TABLE)
        app.logger.info("Azure table 'pharmaceutical' created or already exists.")
    except Exception as e:
        app.logger.error("Failed to create azure table 'pharmaceutical': %s", e)
    try:
        table_manager.create_table(RADIONUCLIDE_TABLE)
        app.logger.info("Azure table 'radionuclides' created or already exists.")
    except Exception as e:
        app.logger.error("Failed to create azure table 'radionuclides': %s", e)
    try:
        table_manager.create_table(DOSING_SCHEMES_TABLE)
        app.logger.info("Azure table 'dosing_schemes' created or already exists.")
    except Exception as e:
        app.logger.error("Failed to create azure table 'dosing_schemes': %s", e)
    try:
        table_manager.create_table(DAYSETUP_TABLE)
        app.logger.info("Azure table 'daysetup' created or already exists.")
    except Exception as e:
        app.logger.error("Failed to create azure table 'daysetup': %s", e)
    try:
        table_manager.create_table(PATIENTS_TABLE)
        app.logger.info("Azure table 'patients' created or already exists.")
    except Exception as e:
        app.logger.error("Failed to create azure table 'patients': %s", e)
    try:
        table_manager.create_table(TESTS_TABLE)
        app.logger.info("Azure table 'tests' created or already exists.")
    except Exception as e:
        app.logger.error("Failed to create azure table 'tests': %s", e)
    try:
        table_manager.create_table(TEST_PATIENTS_TABLE)
        app.logger.info("Azure table 'test_patients' created or already exists.")
    except Exception as e:
        app.logger.error("Failed to create azure table 'test_patients': %s", e)



def get_app(name):
    global _APP

    if _APP is None:
        from flask import Flask
        _APP = Flask(name)
        _APP.secret_key = SECRET  # for flashing messages, sessions, etc.
        _APP.config['SECRET_KEY'] = SECRET

        init_app(_APP)

    return _APP


def get_login_manager():
    global _LOGIN_MANAGER

    if _LOGIN_MANAGER is None:
        from flask_login import LoginManager
        _LOGIN_MANAGER = LoginManager()
    return _LOGIN_MANAGER
