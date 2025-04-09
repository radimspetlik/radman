import base64
from datetime import datetime

from flask import request, url_for, redirect, g, session
from werkzeug.routing import BuildError


def decode_base64(blob_path_base64):
    return base64.b64decode(blob_path_base64.replace('_', '/').encode('utf-8')).decode('utf-8')


def encode_base64(blob_path):
    return base64.b64encode(blob_path.encode('utf-8')).decode('utf-8').replace('/', '_')


def redirect_dest(fallback):
    dest = request.args.get('next')
    try:
        dest_url = url_for(dest)
    except BuildError:
        return redirect(fallback)
    return redirect(dest_url)


def parse_datetime_from_string(date):
    return datetime.strftime(date, "%Y-%m-%d")

