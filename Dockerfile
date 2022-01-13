FROM python:3.10.1-alpine3.15

ARG UID=1000
ARG VERSION

RUN set -eux ; \
  adduser -D -u ${UID} -h /dropbox dropbox ; \
  apk add --no-cache --virtual .build-deps \
    gcc \
    musl-dev \
    python3-dev \
    libffi-dev \
    openssl-dev; \
  pip install -U pip ; \
  pip install maestral==${VERSION} ; \
  rm -rf /root/.cache ; \
  apk del --no-network .build-deps

USER dropbox
VOLUME ["/dropbox"]
WORKDIR /dropbox

CMD ["maestral", "start", "-f"] 
