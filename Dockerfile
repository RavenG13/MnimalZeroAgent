FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8010

ENV SERVER_HOST=0.0.0.0
ENV SERVER_PORT=8010

CMD ["python", "server.py"]
