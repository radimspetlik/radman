#!/bin/bash

which pip3
which gunicorn
pip3 list
gunicorn --workers 10 --timeout 3600 --bind 0.0.0.0:80 --preload --log-config /opt/project/gunicorn_logging.conf -m 007 wsgi:flask_app