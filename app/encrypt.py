import base64
import os

from cryptography.fernet import Fernet


SECRET = os.environ['SECRET_KEY']
_FERNET = None


def get_fernet():
    global _FERNET

    if _FERNET is None:
        key = base64.urlsafe_b64encode(SECRET[:32].encode())
        _FERNET = Fernet(key)
    return _FERNET


