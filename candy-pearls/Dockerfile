# BUILD_FROM is supplied by the Supervisor from build.yaml — no default here.
ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-pip

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY app/ /app/app/
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
