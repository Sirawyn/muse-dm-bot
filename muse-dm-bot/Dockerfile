FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV MUSE_DB=/data/muse_leads.db
CMD ["sh", "-c", "uvicorn muse_bot.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
