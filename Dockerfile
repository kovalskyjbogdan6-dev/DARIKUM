# Dockerfile для Railway — базируемся на Python 3.11
FROM python:3.11-slim

WORKDIR /app

# Установим системные зависимости для сборки sqlite/возможных wheel
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --upgrade pip setuptools wheel
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway предоставляет $PORT, но в Docker мы слушаем 8000 по умолчанию
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
