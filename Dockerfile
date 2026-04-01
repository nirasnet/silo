FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ app/
COPY run.py .
ENV PYTHONUNBUFFERED=1
EXPOSE 8200
CMD ["python3", "run.py"]
