FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY src ./src
COPY tests ./tests
COPY container_entrypoint.py ./container_entrypoint.py
RUN python -B -m unittest discover -s tests -v
CMD ["python", "-B", "container_entrypoint.py"]
