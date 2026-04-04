FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir flask easyocr pillow
COPY app.py .
ENV PYTHONUNBUFFERED=1
EXPOSE 9091
CMD ["python3", "app.py"]
