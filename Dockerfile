FROM python:3.11-slim

WORKDIR /app

# Устоновка системных пакетов, если нужны для сборки sqlite и т.п.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN python -m pip install --upgrade pip setuptools wheel
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Отладочная строка — если хотите увидеть содержимое при сборке, временно раскомментируйте:
# RUN ls -la /app && pwd

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
