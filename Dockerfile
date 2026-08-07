FROM python:3.11-slim

# fonts-dejavu-core no es opcional: los subtítulos se queman con libass,
# que resuelve la fuente por fontconfig. Sin una fuente instalada de
# forma explícita, libass usa lo que encuentre en la imagen base y el
# resultado cambia de un rebuild a otro.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=5000
EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--timeout", "600", "--workers", "1", "app:app"]
