FROM python:3.11.6-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./DockFlare-Agent .

CMD ["python", "main.py"]
