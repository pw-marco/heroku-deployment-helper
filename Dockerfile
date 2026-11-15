# Standard Debian-based slim image
FROM python:3.12-slim

WORKDIR /app
COPY bot/ /app/

ENV DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=0
ENV LC_ALL=en_US.UTF-8
ENV LANG=en_US.UTF-8

# 1. Sabse pehle missing system tools install karo (unzip, tar, libicu-dev, locales added)
RUN apt-get update && apt-get install -y \
    gcc \
    libffi-dev \
    ffmpeg \
    wget \
    tar \
    unzip \
    libicu-dev \
    locales \
    locales-all \
    aria2 \
    nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 2. PERMANENT N_m3u8DL-RE v0.6.0-beta INSTALLATION
RUN wget -q https://github.com/nilaoda/N_m3u8DL-RE/releases/download/v0.6.0-beta/N_m3u8DL-RE_v0.6.0-beta_linux-x64_20260629.tar.gz -O re.tar.gz \
    && tar -xzf re.tar.gz \
    && find . -name 'N_m3u8DL-RE' -type f -exec mv {} /usr/local/bin/N_m3u8DL-RE \; \
    && chmod +x /usr/local/bin/N_m3u8DL-RE \
    && rm -rf re.tar.gz

# 3. PERMANENT mp4decrypt INSTALLATION (with fallback URLs)
RUN wget -q --timeout=30 https://www.bok.net/Bento4/binaries/Bento4-SDK-1-6-0-640.x86_64-unknown-linux.zip -O bento.zip \
    || wget -q --timeout=30 https://www.bok.net/Bento4/binaries/Bento4-SDK-1-6-0-640.x86_64-linux-gnu.zip -O bento.zip \
    || echo "Bento4 download failed, mp4decrypt will rely on runtime auto-download" \
    ; if [ -f bento.zip ] && [ $(stat -c%s "bento.zip") -gt 1000 ]; then \
        unzip -q bento.zip -d bento_ext && \
        find bento_ext -name 'mp4decrypt' -exec mv {} /usr/local/bin/ \; && \
        chmod +x /usr/local/bin/mp4decrypt && \
        rm -rf bento_ext bento.zip; \
    else \
        rm -f bento.zip; \
    fi

# 4. Python Packages (requirements.txt is in /app, copied from bot/)
RUN pip3 install --no-cache-dir --upgrade pip \
    && pip3 install --no-cache-dir -r requirements.txt \
    && pip3 install -U yt-dlp

# Run API and Telegram Bot
CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000} & python3 main.py"]
