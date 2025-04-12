import os
import hashlib
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, current_app)
from flask_login import (login_user, logout_user, login_required, current_user,
                         UserMixin)
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

# Import your table manager and helper function get_table_manager from your module.
# For example, assuming your table manager code is in a module named table_manager:

from app.app_init import get_login_manager
from app.encrypt import get_fernet
from app.constants import USERS_TABLE
from app.table_manager import get_table_manager

# Create the blueprint (set template_folder to your templates location)
user_bp = Blueprint('user', __name__, template_folder='templates')

fernet = get_fernet()
login_manager = get_login_manager()

# Set up encryption using Fernet.
# Note: os.environ['SECRET_KEY'] should be a 32 url-safe base64-encoded bytes string.

SECRET = os.environ['SECRET_KEY']
# Set up a serializer (for generating/verifying tokens in password reset)
serializer = URLSafeTimedSerializer(SECRET)


# Define a User class that works with flask-login. Here, we assume username is unique.
class User(UserMixin):
    def __init__(self, username, encrypted_email, password_hash, active=True):
        self.id = username  # flask-login uses this as the unique id.
        self.username = username
        self.encrypted_email = encrypted_email
        self.password_hash = password_hash
        self.active = active

    @property
    def email(self):
        try:
            # Decrypt the encrypted email for display/use.
            return fernet.decrypt(self.encrypted_email.encode()).decode()
        except Exception as e:
            current_app.logger.error("Email decryption failed: %s", e)
            return None

    def get_id(self):
        return self.username

    @property
    def is_active(self):
        # Flask-Login uses this property to permit/deny login.
        return self.active


# Helper functions for hashing and encrypting
def hash_password(password):
    """Return the SHA-256 hash of the given password."""
    return hashlib.sha256(password.encode()).hexdigest()


def encrypt_email(email):
    """Return an encrypted version of the email."""
    return fernet.encrypt(email.encode()).decode()


# Function to load a user from NoSQL storage using TableManager.
def load_user(username):
    table_manager = get_table_manager()
    # Here, we assume all user records are stored in the USERS_TABLE table under partition "USER"
    entity = table_manager.get_entity(USERS_TABLE, "USER", username)
    if entity:
        # Build the User object using entity data.
        user = User(
            username=entity['RowKey'],
            encrypted_email=entity.get('email', ''),
            password_hash=entity.get('password', ''),
            active=(entity.get('active', 'True') == 'True')
        )
        return user
    return None


# Flask-login user loader callback.
@login_manager.user_loader
def user_loader(username):
    return load_user(username)


# =============================
# Routes for User Management
# =============================

@user_bp.route('/register', methods=['GET', 'POST'])
def register():
    """
    Render a registration form and create a new user.
    Expects form fields: username, email, password.
    """
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        if not (username and email and password):
            flash("All fields are required.")
            return redirect(url_for('user.register'))
        if load_user(username):
            flash("User already exists.")
            return redirect(url_for('user.register'))
        password_hash = hash_password(password)
        encrypted_email = encrypt_email(email)
        # Create user entity for the NoSQL table.
        entity = {
            'PartitionKey': 'USER',
            'RowKey': username,
            'email': encrypted_email,
            'password': password_hash,
            'active': 'True'
        }
        table_manager = get_table_manager()
        # Upload the new user entity as a batch (list of one entity)
        table_manager.upload_batch_to_table(USERS_TABLE, [entity])
        flash("User registered successfully. Please log in.")
        return redirect(url_for('user.login'))
    return render_template('register.html')


@user_bp.route('/login', methods=['GET', 'POST'])
def login():
    """
    Render the login form.
    Expects form fields: username and password.
    Provides links to registration and password reset.
    """
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = load_user(username)
        if user and user.password_hash == hash_password(password):
            if not user.active:
                flash("Account is disabled.")
                return redirect(url_for('user.login'))
            login_user(user)
            flash("Logged in successfully.")
            # Redirect to a profile or dashboard page.
            return redirect(url_for('user.profile'))
        else:
            flash("Invalid username or password.")
            return redirect(url_for('user.login'))
    return render_template('login.html')


@user_bp.route('/logout')
@login_required
def logout():
    """Log out the current user."""
    logout_user()
    flash("Logged out successfully.")
    return redirect(url_for('user.login'))


@user_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """
    Allow an authenticated user to update their password or registered email.
    Expects form fields: email and/or password.
    """
    if request.method == 'POST':
        new_email = request.form.get('email')
        new_password = request.form.get('password')
        table_manager = get_table_manager()
        # Fetch the current user entity.
        entity = table_manager.get_entity(USERS_TABLE, "USER", current_user.username)
        if not entity:
            flash("User not found.")
            return redirect(url_for('user.profile'))
        if new_email:
            entity['email'] = encrypt_email(new_email)
        if new_password:
            entity['password'] = hash_password(new_password)
        # Update the entity using an upsert batch.
        table_manager.upload_batch_to_table(USERS_TABLE, [entity])
        flash("Profile updated successfully.")
        return redirect(url_for('user.profile'))
    return render_template('profile.html', user=current_user)


@user_bp.route('/users', methods=['GET', 'POST'])
@login_required
def manage_users():
    """
    Display a list of users with options to delete or disable/enable them.
    (In a production system, you might restrict this to administrators only.)
    """
    table_manager = get_table_manager()
    # Retrieve all user entities from the USERS_TABLE table.
    users = list(table_manager.query_entities(USERS_TABLE, query=None))
    # Decrypt emails and convert active flag for display.
    for entity in users:
        try:
            entity['email'] = fernet.decrypt(entity['email'].encode()).decode()
        except Exception:
            entity['email'] = "Decryption error"
        entity['active'] = (entity.get('active', 'True') == 'True')
    if request.method == 'POST':
        # Determine the action: either delete or toggle active status.
        action = request.form.get('action')
        target_username = request.form.get('username')
        entity = table_manager.get_entity(USERS_TABLE, "USER", target_username)
        if entity:
            if action == 'delete':
                table_manager.delete_entities(USERS_TABLE, [entity])
                flash(f"User {target_username} deleted.")
            elif action == 'toggle':
                # Toggle the user's active flag.
                current_status = (entity.get('active', 'True') == 'True')
                entity['active'] = 'False' if current_status else 'True'
                table_manager.upload_batch_to_table(USERS_TABLE, [entity])
                flash(f"User {target_username} status updated.")
        return redirect(url_for('user.manage_users'))
    return render_template('users.html', users=users)


@user_bp.route('/reset_password', methods=['GET', 'POST'])
def reset_password_request():
    """
    Render a form that accepts a username for password reset.
    On submission, generate a password reset token and send an email (here, simulated via flash).
    """
    if request.method == 'POST':
        username = request.form.get('username')
        user = load_user(username)
        if user:
            # Generate a token valid for (for example) 1 hour.
            token = serializer.dumps(username, salt='password-reset-salt')
            reset_url = url_for('user.reset_password_token', token=token, _external=True)
            # Here you would send an email with the reset link.
            # For demonstration, we just flash the link.
            flash(f"Password reset link (simulate email): {reset_url}")
        else:
            flash("Username not found.")
        return redirect(url_for('user.login'))
    return render_template('reset_password_request.html')


@user_bp.route('/reset/<token>', methods=['GET', 'POST'])
def reset_password_token(token):
    """
    Handle the password reset after a user clicks the link in their email.
    The link contains a token that identifies the user.
    """
    try:
        username = serializer.loads(token, salt='password-reset-salt', max_age=3600)
    except (SignatureExpired, BadSignature):
        flash("The reset link is invalid or has expired.")
        return redirect(url_for('user.reset_password_request'))

    if request.method == 'POST':
        new_password = request.form.get('password')
        table_manager = get_table_manager()
        entity = table_manager.get_entity(USERS_TABLE, "USER", username)
        if entity:
            entity['password'] = hash_password(new_password)
            table_manager.upload_batch_to_table(USERS_TABLE, [entity])
            flash("Password has been reset. Please log in.")
            return redirect(url_for('user.login'))
    return render_template('reset_password.html', token=token)