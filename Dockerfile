FROM python:3.10-slim

RUN mkdir /opt/project/
WORKDIR /opt/project/

RUN apt-get update && apt-get install -y gnupg dirmngr debian-keyring debian-archive-keyring

RUN apt-key adv --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys 7638D0442B90D010 \
    && apt-key adv --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys 8B48AD6246925553

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    apt-utils \
    build-essential

# Define the URL for the COIN-OR OptimizationSuite archive.
ENV COIN_OR_URL="https://www.coin-or.org/download/binary/OptimizationSuite/COIN-OR-1.7.4-linux-x86_64-gcc4.7.2-static.tar.gz"

# Download and extract the archive.
RUN wget ${COIN_OR_URL} -O coin_or.tar.gz \
    && tar -xzf coin_or.tar.gz \
    && rm coin_or.tar.gz

# Assume that the extraction produces a directory named "COIN-OR-1.7.4-linux-x86_64-gcc4.7.2-static"
# (adjust the directory name if it is different).
ENV COIN_OR_DIR=/opt/COIN-OR-1.7.4-linux-x86_64-gcc4.7.2-static

# Update the PATH environment variable to include the bin folder.
ENV PATH="${COIN_OR_DIR}/bin:${PATH}"

RUN pip3 install --upgrade pip setuptools wheel
RUN pip3 install pipenv

COPY Pipfile Pipfile.lock ./
RUN pipenv install --system --deploy

COPY app app
COPY start.sh ./
COPY wsgi.py ./
COPY gunicorn_logging.conf ./

RUN chmod +x ./start.sh
CMD ["./start.sh"]
