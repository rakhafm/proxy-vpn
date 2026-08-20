# Image probe harian milik pool manager sendiri - bukan image crawler produksi.
# Instalasi Chrome + chromedriver disalin dari langkah yang sama persis dipakai
# asl-crawler-pricing-engine-v2/Dockerfile, supaya versi Chrome yang menguji exit
# tetap sejenis dengan yang dipakai scrapeselenium() di produksi. Cek ulang kalau
# Dockerfile crawler berubah - itu bukan sesuatu yang otomatis mengikuti di sini.
#
# --platform dikunci amd64: google-chrome-stable tidak dirilis untuk arm64 sama
# sekali (bukan cuma soal Apple Silicon - node k8s/VM target juga amd64). Tanpa
# ini, build di mesin arm64 mengambil base image arm64 dan apt gagal resolve
# dependensi Chrome (libasound2:amd64 dst "not installable").
FROM --platform=linux/amd64 python:3.10-slim
WORKDIR /app

RUN apt-get update \
  && apt-get install -y wget gnupg2 curl unzip jq \
  && rm -rf /var/lib/apt/lists/*

RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/googlechrome-linux-keyring.gpg \
  && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/googlechrome-linux-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
  && apt-get update \
  && apt-get install -y google-chrome-stable --fix-missing

RUN set -e; \
    CHROMEDRIVER_VERSION="$(google-chrome --version | awk '{print $3}')"; \
    wget -O /tmp/chromedriver.zip "https://edgedl.me.gvt1.com/edgedl/chrome/chrome-for-testing/${CHROMEDRIVER_VERSION}/linux64/chromedriver-linux64.zip" \
    && unzip /tmp/chromedriver.zip -d /tmp/ \
    && mkdir -p /usr/src/app/chromedriver \
    && cp /tmp/chromedriver-linux64/chromedriver /usr/src/app/chromedriver/ \
    && rm -rf /tmp/chromedriver*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# olx_probe.py di-mount di runtime (lihat pool/probes.py), bukan dibangun ke
# image - mengubah logika probe tidak menuntut build ulang image ini.
