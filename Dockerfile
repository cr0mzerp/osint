FROM python:3.10-slim

# Sistem bağımlılıkları
RUN apt-get update && apt-get install -y \
    curl \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Alist kurulumu
RUN wget -O alist.tar.gz https://github.com/alist-org/alist/releases/latest/download/alist-linux-amd64.tar.gz && \
    tar -zxvf alist.tar.gz && \
    chmod +x alist && \
    mv alist /usr/local/bin/

# Rclone kurulumu
RUN curl https://rclone.org/install.sh | bash

# Ripgrep kurulumu
RUN curl -LO https://github.com/BurntSushi/ripgrep/releases/download/14.1.0/ripgrep_14.1.0_amd64.deb.gz && \
    gunzip ripgrep_14.1.0_amd64.deb.gz && \
    dpkg -i ripgrep_14.1.0_amd64.deb && \
    rm ripgrep_14.1.0_amd64.deb

# Python bağımlılıkları
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Mount point oluştur
RUN mkdir -p /home/user/terabox_data
RUN mkdir -p /root/.config/rclone
RUN mkdir -p /root/.config/alist

# Dosyaları kopyala
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

# Environment variables (Hugging Face Secrets)
ENV ALIST_ADMIN_USER=${ALIST_ADMIN_USER:-admin}
ENV ALIST_ADMIN_PASSWORD=${ALIST_ADMIN_PASSWORD:-password}
ENV TERABOX_TOKEN=${TERABOX_TOKEN}
ENV RCLONE_CONFIG=${RCLONE_CONFIG}

# Port
EXPOSE 7860 5244

# Start script
CMD ["/app/start.sh"]
