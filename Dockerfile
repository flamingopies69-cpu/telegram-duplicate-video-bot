FROM python:3.12-slim
WORKDIR /app
COPY bot.py /app/bot.py
RUN mkdir -p /data
CMD ["python", "-u", "/app/bot.py"]
