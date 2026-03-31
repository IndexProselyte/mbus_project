FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mbus_poller.py .

CMD ["python", "-u", "mbus_poller.py"]
