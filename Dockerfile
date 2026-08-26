FROM python:3.11-slim

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Koyeb injects PORT at runtime; 8000 is just the default/documented value.
ENV PORT=8000
EXPOSE 8000

# Basic container-level health check (optional, Koyeb also does its own).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8000)}/health', timeout=3)" || exit 1

CMD ["python", "server.py"]
