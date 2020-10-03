FROM python:3.8.6-alpine3.12

ARG UID=1000
ARG VERSION

RUN set -eux ; \
  adduser -D -u ${UID} -h /dropbox dropbox ; \
  apk add --no-cache --virtual .build-deps \
    gcc \
    musl-dev \
    python3-dev \
    libffi-dev \
    openssl-dev ; \
  pip install maestral==${VERSION} ; \
  pip cache purge ; \
  apk del --no-network .build-deps

USER dropbox
VOLUME ["/dropbox"]
WORKDIR /dropbox

CMD ["maestral", "-f", "start"] 
