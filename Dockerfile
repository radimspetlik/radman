FROM python:3.8-slim

RUN mkdir /opt/project/
WORKDIR /opt/project/

RUN apt-get update && apt-get install -y gnupg dirmngr debian-keyring debian-archive-keyring

RUN apt-key adv --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys 7638D0442B90D010 \
    && apt-key adv --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys 8B48AD6246925553

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    apt-utils \
    build-essential

RUN pip3 install --upgrade pip setuptools
RUN pip3 install pipenv

#COPY Pipfile Pipfile.lock ./
COPY Pipfile ./
RUN pipenv install --system --deploy

COPY app app
COPY start.sh ./
COPY wsgi.py ./
COPY gunicorn_logging.conf ./

RUN chmod +x ./start.sh
CMD ["./start.sh"]
