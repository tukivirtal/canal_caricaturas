"""
Canal Caricaturas — servicio de renderizado de video.

Arma un Short vertical (1080x1920) a partir de un guion tipo Ping-Pong:
por cada línea de diálogo, muestra la imagen fija del personaje que
habla durante la duración exacta de su audio, y concatena todas las
líneas en orden.

Patrón async (igual que Bienestar Diario):
  POST /fabricar_caricatura  -> devuelve job_id de inmediato (202)
  GET  /estado/<job_id>      -> consulta estado
  Al terminar, hace POST a webhook_url con el resultado.
"""

import os
import uuid
import threading
import subprocess
import tempfile
import shutil
import textwrap
import requests
from flask import Flask, request, jsonify
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

# URLs fijas de las 3 imágenes de personajes (Cloudinary)
IMAGENES_PERSONAJES = {
    "DOCTOR": "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785369741/Doctor_elj6ei.png",
    "JUAN": "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785369743/Juan_c0fmwo.png",
    "MARIA": "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785369738/Maria_xbz18p.png",
}

CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "ddbjsjmzj")
CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET")
CLOUDINARY_UPLOAD_PRESET = os.environ.get("CLOUDINARY_UPLOAD_PRESET", "caricaturas")

# Ancho/alto del Short (vertical 9:16)
ANCHO = 1080
ALTO = 1920

# Tamaño estándar de miniatura de YouTube (horizontal 16:9), independiente
# de que el video en sí sea vertical.
ANCHO_MINIATURA = 1280
ALTO_MINIATURA = 720
RUTA_FUENTE_MINIATURA = os.path.join(os.path.dirname(__file__), "Anton-Regular.ttf")
COLOR_FRANJA_MINIATURA = (20, 20, 20)       # franja casi negra
COLOR_TEXTO_MINIATURA = (255, 210, 60)      # amarillo/dorado, alto contraste

# Estado de jobs en memoria (simple, como en Bienestar Diario)
jobs = {}
jobs_lock = threading.Lock()


def actualizar_estado(job_id, **kwargs):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(kwargs)


def _campo(linea, *claves):
    """Busca el valor de un campo de una línea probando varios nombres
    posibles, en orden. Tolera que Make mande los nombres nativos que arma
    el Array aggregator (ej. '$1', '$2', 'Secure URL', 'Bundle order
    position') en vez de los nombres esperados ('hablante', 'texto',
    'audio_url', 'orden'), sin tener que renombrar campos en Make."""
    for clave in claves:
        valor = linea.get(clave)
        if valor not in (None, ""):
            return valor
    return ""


# ---------------------------------------------------------------------------
# Helpers de descarga / ffmpeg
# ---------------------------------------------------------------------------

def descargar_archivo(url, destino):
    r = requests.get(url, stream=True, timeout=60)
    r.raise_for_status()
    with open(destino, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)


def obtener_duracion(ruta_audio):
    """Duración en segundos de un archivo de audio, vía ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        ruta_audio,
    ]
    resultado = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return float(resultado.stdout.strip())


def crear_segmento(imagen_path, audio_path, salida_path):
    """
    Crea un clip de video: la imagen fija durante la duración exacta
    del audio (usando -shortest, que corta cuando termina el audio).
    """
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", imagen_path,
        "-i", audio_path,
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        "-vf",
        f"scale={ANCHO}:{ALTO}:force_original_aspect_ratio=decrease,"
        f"pad={ANCHO}:{ALTO}:(ow-iw)/2:(oh-ih)/2:color=0xF5E6D3",
        "-preset", "veryfast",
        salida_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def concatenar_segmentos(lista_segmentos, salida_path, work_dir):
    """
    Concatena los segmentos en orden usando el demuxer concat de ffmpeg.
    """
    lista_txt = os.path.join(work_dir, "lista.txt")
    with open(lista_txt, "w") as f:
        for seg in lista_segmentos:
            f.write(f"file '{seg}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", lista_txt,
        "-c", "copy",
        salida_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _formato_srt(segundos):
    """Convierte segundos (float) al formato de timestamp que usa .srt:
    HH:MM:SS,mmm"""
    horas = int(segundos // 3600)
    minutos = int((segundos % 3600) // 60)
    segs = int(segundos % 60)
    milisegundos = int(round((segundos - int(segundos)) * 1000))
    return f"{horas:02d}:{minutos:02d}:{segs:02d},{milisegundos:03d}"


def escribir_srt(bloques, ruta_srt):
    with open(ruta_srt, "w", encoding="utf-8") as f:
        for idx, (inicio, fin, texto) in enumerate(bloques, start=1):
            f.write(f"{idx}\n")
            f.write(f"{_formato_srt(inicio)} --> {_formato_srt(fin)}\n")
            f.write(f"{texto}\n\n")


# Blanco con borde negro, centrado abajo — pensado para el ancho angosto
# del Short vertical (1080x1920).
ESTILO_SUBTITULOS = (
    "FontName=Arial,FontSize=26,PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H00000000,BorderStyle=3,Outline=2,Alignment=2,MarginV=80"
)


def quemar_subtitulos(ruta_video_entrada, ruta_srt, ruta_video_salida):
    """Quema los subtítulos sobre el video ya renderizado (filtro subtitles,
    vía libass)."""
    filtro = f"subtitles={ruta_srt}:force_style='{ESTILO_SUBTITULOS}'"
    cmd = [
        "ffmpeg", "-y",
        "-i", ruta_video_entrada,
        "-vf", filtro,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-c:a", "copy",
        ruta_video_salida,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def recortar_a_medida(imagen, ancho, alto):
    """Escala y recorta la imagen para llenar exactamente ancho x alto
    (equivalente a PIL.ImageOps.fit, escrito a mano para no sumar otra
    dependencia)."""
    ratio_destino = ancho / alto
    ratio_origen = imagen.width / imagen.height
    if ratio_origen > ratio_destino:
        nuevo_alto = alto
        nuevo_ancho = int(alto * ratio_origen)
    else:
        nuevo_ancho = ancho
        nuevo_alto = int(ancho / ratio_origen)
    imagen = imagen.resize((nuevo_ancho, nuevo_alto))
    izquierda = (nuevo_ancho - ancho) // 2
    arriba = (nuevo_alto - alto) // 2
    return imagen.crop((izquierda, arriba, izquierda + ancho, arriba + alto))


def generar_miniatura(ruta_imagen_personaje, texto_miniatura, ruta_salida):
    """
    Arma la miniatura (1280x720) a partir de la imagen del personaje que
    abre el video, con una franja superior y el texto gancho en mayúsculas.
    """
    imagen = Image.open(ruta_imagen_personaje).convert("RGB")
    imagen = recortar_a_medida(imagen, ANCHO_MINIATURA, ALTO_MINIATURA)

    franja_rect = (0, 0, ANCHO_MINIATURA, int(ALTO_MINIATURA * 0.32))
    overlay = Image.new("RGBA", imagen.size, (0, 0, 0, 0))
    dibujo_overlay = ImageDraw.Draw(overlay)
    dibujo_overlay.rectangle(franja_rect, fill=COLOR_FRANJA_MINIATURA + (235,))
    imagen = Image.alpha_composite(imagen.convert("RGBA"), overlay).convert("RGB")
    dibujo = ImageDraw.Draw(imagen)

    titulo = texto_miniatura.upper()
    tamano_fuente = int(ANCHO_MINIATURA * 0.09)
    fuente = ImageFont.truetype(RUTA_FUENTE_MINIATURA, tamano_fuente)
    ancho_franja = franja_rect[2] - franja_rect[0]
    lineas = textwrap.wrap(titulo, width=14)

    while True:
        anchos = [dibujo.textbbox((0, 0), linea, font=fuente)[2] for linea in lineas]
        if max(anchos, default=0) <= ancho_franja - 80 or tamano_fuente <= 40:
            break
        tamano_fuente -= 5
        fuente = ImageFont.truetype(RUTA_FUENTE_MINIATURA, tamano_fuente)

    alto_linea = tamano_fuente * 1.15
    alto_total_texto = alto_linea * len(lineas)
    centro_y = (franja_rect[1] + franja_rect[3]) / 2 - alto_total_texto / 2
    centro_x = (franja_rect[0] + franja_rect[2]) / 2

    for i, linea in enumerate(lineas):
        ancho_linea = dibujo.textbbox((0, 0), linea, font=fuente)[2]
        pos_x = centro_x - ancho_linea / 2
        pos_y = centro_y + (i * alto_linea)
        dibujo.text((pos_x, pos_y), linea, font=fuente, fill=COLOR_TEXTO_MINIATURA)

    imagen.save(ruta_salida, quality=95)


def recortar_a_medida(imagen, ancho, alto):
    """Escala y recorta la imagen para llenar exactamente ancho x alto
    (equivalente a PIL.ImageOps.fit, escrito a mano para no sumar otra
    dependencia)."""
    ratio_destino = ancho / alto
    ratio_origen = imagen.width / imagen.height
    if ratio_origen > ratio_destino:
        nuevo_alto = alto
        nuevo_ancho = int(alto * ratio_origen)
    else:
        nuevo_ancho = ancho
        nuevo_alto = int(ancho / ratio_origen)
    imagen = imagen.resize((nuevo_ancho, nuevo_alto))
    izquierda = (nuevo_ancho - ancho) // 2
    arriba = (nuevo_alto - alto) // 2
    return imagen.crop((izquierda, arriba, izquierda + ancho, arriba + alto))


def subir_a_cloudinary(archivo_path, public_id, tipo_recurso="video"):
    """
    Sube un archivo (video o imagen) a Cloudinary usando el API de upload
    firmado (requests directo, sin SDK, para mantener el servicio liviano).
    """
    import time
    import hashlib

    timestamp = int(time.time())
    params_a_firmar = {
        "public_id": public_id,
        "timestamp": timestamp,
        "upload_preset": CLOUDINARY_UPLOAD_PRESET,
    }
    # Cloudinary firma en orden alfabético de las claves
    cadena_firma = "&".join(f"{k}={v}" for k, v in sorted(params_a_firmar.items()))
    cadena_firma += CLOUDINARY_API_SECRET
    firma = hashlib.sha1(cadena_firma.encode("utf-8")).hexdigest()

    url = f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}/{tipo_recurso}/upload"
    with open(archivo_path, "rb") as f:
        files = {"file": f}
        data = {
            "public_id": public_id,
            "timestamp": timestamp,
            "upload_preset": CLOUDINARY_UPLOAD_PRESET,
            "api_key": CLOUDINARY_API_KEY,
            "signature": firma,
        }
        resp = requests.post(url, files=files, data=data, timeout=300)
    resp.raise_for_status()
    return resp.json()["secure_url"]


# ---------------------------------------------------------------------------
# Procesamiento principal (corre en background thread)
# ---------------------------------------------------------------------------

def procesar_caricatura(job_id, lineas, fila, metadata, webhook_url):
    work_dir = tempfile.mkdtemp(prefix=f"caricatura_{job_id}_")
    try:
        actualizar_estado(job_id, estado="descargando_audios")

        # Ordenar líneas por su posición (orden / Bundle order position)
        lineas_ordenadas = sorted(
            lineas, key=lambda x: int(_campo(x, "orden", "Bundle order position") or 0)
        )
        hablante_apertura = str(_campo(lineas_ordenadas[0], "hablante", "$1")).strip().upper()

        # Descargar las 3 imágenes de personajes una sola vez
        rutas_imagenes = {}
        for personaje, url in IMAGENES_PERSONAJES.items():
            ruta = os.path.join(work_dir, f"img_{personaje}.png")
            descargar_archivo(url, ruta)
            rutas_imagenes[personaje] = ruta

        actualizar_estado(job_id, estado="generando_segmentos")

        segmentos = []
        bloques_subtitulos = []
        tiempo_acumulado = 0.0
        for idx, linea in enumerate(lineas_ordenadas):
            hablante = str(_campo(linea, "hablante", "$1")).strip().upper()
            audio_url = _campo(linea, "audio_url", "Secure URL")
            texto = str(_campo(linea, "texto", "$2")).strip()

            if hablante not in rutas_imagenes:
                raise ValueError(f"Hablante desconocido en línea {idx}: '{hablante}'")

            audio_path = os.path.join(work_dir, f"audio_{idx}.mp3")
            descargar_archivo(audio_url, audio_path)
            duracion = obtener_duracion(audio_path)

            if texto:
                bloques_subtitulos.append((tiempo_acumulado, tiempo_acumulado + duracion, texto))
            tiempo_acumulado += duracion

            segmento_path = os.path.join(work_dir, f"segmento_{idx:03d}.mp4")
            crear_segmento(rutas_imagenes[hablante], audio_path, segmento_path)
            segmentos.append(segmento_path)

        actualizar_estado(job_id, estado="concatenando")

        video_sin_subs_path = os.path.join(work_dir, "sin_subtitulos.mp4")
        concatenar_segmentos(segmentos, video_sin_subs_path, work_dir)

        if bloques_subtitulos:
            actualizar_estado(job_id, estado="quemando_subtitulos")
            ruta_srt = os.path.join(work_dir, "subtitulos.srt")
            escribir_srt(bloques_subtitulos, ruta_srt)
            video_final_path = os.path.join(work_dir, "final.mp4")
            quemar_subtitulos(video_sin_subs_path, ruta_srt, video_final_path)
        else:
            video_final_path = video_sin_subs_path

        actualizar_estado(job_id, estado="generando_miniatura")

        texto_miniatura = (metadata.get("texto_miniatura") or "").strip()
        imagen_miniatura_url = None
        if texto_miniatura:
            ruta_miniatura = os.path.join(work_dir, "miniatura.jpg")
            generar_miniatura(rutas_imagenes[hablante_apertura], texto_miniatura, ruta_miniatura)
            public_id_miniatura = f"caricaturas/miniaturas/short_{fila}_{job_id[:8]}"
            imagen_miniatura_url = subir_a_cloudinary(ruta_miniatura, public_id_miniatura, tipo_recurso="image")

        actualizar_estado(job_id, estado="subiendo")

        public_id = f"caricaturas/videos/short_{fila}_{job_id[:8]}"
        url_video = subir_a_cloudinary(video_final_path, public_id)

        resultado = {
            "job_id": job_id,
            "estado": "completado",
            "url_video": url_video,
            "imagen_miniatura_url": imagen_miniatura_url,
            "fila": fila,
            **metadata,
        }
        actualizar_estado(**resultado)

        if webhook_url:
            requests.post(webhook_url, json=resultado, timeout=30)

    except Exception as e:
        error_info = {"job_id": job_id, "estado": "error", "error": str(e), "fila": fila}
        actualizar_estado(**error_info)
        if webhook_url:
            try:
                requests.post(webhook_url, json=error_info, timeout=30)
            except Exception:
                pass
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.route("/fabricar_caricatura", methods=["POST"])
def fabricar_caricatura():
    """
    Body JSON esperado (desde Make, después del Array Aggregator):
    {
      "lineas": [
        {"hablante": "JUAN", "texto": "...", "audio_url": "...", "orden": 1},
        {"hablante": "DOCTOR", "texto": "...", "audio_url": "...", "orden": 2},
        ...
      ],
      "fila": 3,
      "titulo_seo": "...",
      "descripcion_seo": "...",
      "hashtags": "...",
      "etiquetas_ocultas": "...",
      "texto_miniatura": "NADIE TE MIRA",
      "webhook_url": "https://hook.make.com/..."
    }
    """
    data = request.get_json(force=True)

    lineas = data.get("lineas")
    fila = data.get("fila")
    webhook_url = data.get("webhook_url")

    if not lineas or not isinstance(lineas, list):
        return jsonify({"error": "Falta el campo 'lineas' (array)"}), 400
    if not fila:
        return jsonify({"error": "Falta el campo 'fila'"}), 400

    metadata = {
        "titulo_seo": data.get("titulo_seo"),
        "descripcion_seo": data.get("descripcion_seo"),
        "hashtags": data.get("hashtags"),
        "etiquetas_ocultas": data.get("etiquetas_ocultas"),
        "texto_miniatura": data.get("texto_miniatura"),
    }

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {"job_id": job_id, "estado": "en_cola", "fila": fila}

    hilo = threading.Thread(
        target=procesar_caricatura,
        args=(job_id, lineas, fila, metadata, webhook_url),
        daemon=True,
    )
    hilo.start()

    return jsonify({"job_id": job_id, "estado": "en_cola"}), 202


@app.route("/estado/<job_id>", methods=["GET"])
def estado(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "job_id no encontrado"}), 404
    return jsonify(job), 200


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "servicio": "canal_caricaturas_renderizador"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
