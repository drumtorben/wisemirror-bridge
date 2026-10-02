FROM python:3.12-slim
RUN pip install --no-cache-dir "paho-mqtt>=2.0"
WORKDIR /app
COPY wisemirror_bridge.py .
CMD ["python", "-u", "wisemirror_bridge.py"]
