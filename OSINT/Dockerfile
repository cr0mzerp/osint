FROM python:3.10-slim

# Sistem bağımlılıkları
RUN apt-get update && apt-get install -y \
    curl \
    wget \
    unzip \
    ripgrep \
    && rm -rf /var/lib/apt/lists/*

# Alist kurulumu
RUN wget -O alist.tar.gz https://github.com/alist-org/alist/releases/latest/download/alist-linux-amd64.tar.gz && \
    tar -zxvf alist.tar.gz && \
    chmod +x alist && \
    mv alist /usr/local/bin/

# Rclone kurulumu
RUN curl https://rclone.org/install.sh | bash

# Python bağımlılıkları
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Mount point oluştur
RUN mkdir -p /home/user/terabox_data
RUN mkdir -p /root/.config/rclone
RUN mkdir -p /root/.config/alist

# Ana Flask uygulaması dosyalarını kopyala
COPY app.py .
COPY skills_catalog.py .
COPY templates/ /app/templates/

# Environment variables
ENV ALIST_ADMIN_USER=${ALIST_ADMIN_USER:-admin}
ENV ALIST_ADMIN_PASSWORD=${ALIST_ADMIN_PASSWORD:-password}
ENV TERABOX_TOKEN=${TERABOX_TOKEN}
ENV RCLONE_CONFIG=${RCLONE_CONFIG}
ENV SECRET_KEY=${SECRET_KEY:-dev-secret-key-change-in-production}

# Port
EXPOSE 5000 5244

# Flask başlat
CMD ["python", "app.py"]
