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
import concurrent.futures
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
    "FABRICIO": "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785725170/Gemini_Generated_Image_seg99fseg99fseg9_tcvyqi.png",
    "JULI": "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785725133/Gemini_Generated_Image_seg99fseg99fseg9_jcdt0h.png",
    "DOCTOR_LARGO": "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785725570/Gemini_Generated_Image_5ywmko5ywmko5ywm_acmjne.png",
    "JUAN_LARGO": "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785726093/Gemini_Generated_Image_o8nvdfo8nvdfo8nv_b8cied.png",
    "MARIA_LARGO": "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785726275/Gemini_Generated_Image_rvrvbirvrvbirvrv_jwslcc.png",
}

# Props del parque, uno por cada una de las 4 escenas de video largo
# (banco/árbol, plaza con juegos, laguna, mesa de picnic), superpuestos
# sobre el color sólido que rota por escena. El índice de la lista
# (0-3) corresponde a "numero" de escena (1-4) menos uno.
IMAGENES_PROP_PARQUE = [
    "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785728004/Gemini_Generated_Image_4fed7s4fed7s4fed_livzxn.png",
    "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785728619/Gemini_Generated_Image_db6vpodb6vpodb6v_eplvoh.png",
    "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785728750/Gemini_Generated_Image_8iwnq78iwnq78iwn_bzvr5c.png",
    "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785728859/Gemini_Generated_Image_y31bwmy31bwmy31b_ewjnl0.png",
]

# En video largo, Doctor/Juan/Maria usan una imagen propia distinta a
# la de los Shorts (misma identidad y misma voz, dibujo separado para
# que los 5 personajes de la sesión grupal se vean con el mismo estilo
# palito). Fabricio y Juli comparten la misma imagen en ambos formatos,
# porque se generaron directamente en ese estilo.
PERSONAJE_IMAGEN_VIDEO_LARGO = {
    "DOCTOR": "DOCTOR_LARGO",
    "JUAN": "JUAN_LARGO",
    "MARIA": "MARIA_LARGO",
}

CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "ddbjsjmzj")
CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET")
CLOUDINARY_UPLOAD_PRESET = os.environ.get("CLOUDINARY_UPLOAD_PRESET", "caricaturas")

# Color de fondo pastel que ya usa el Short (imagen del personaje + fondo fijo)
COLOR_FONDO_DEFAULT = "0xF5E6D3"

# Para los videos largos (sesión grupal), el fondo cambia por escena en vez
# de ser siempre el mismo pastel. Colores para ffmpeg (formato 0xRRGGBB) y
# su equivalente en RGB para las miniaturas (PIL).
COLORES_FONDO = {
    "rojo": "0xE74C3C",
    "azul": "0x3498DB",
    "verde": "0x2ECC71",
    "violeta": "0x9B59B6",
    "naranja": "0xE67E22",
    "amarillo": "0xF1C40F",
}
COLORES_MINIATURA_RGB = {
    "rojo": (192, 57, 43),
    "azul": (41, 128, 185),
    "verde": (39, 174, 96),
    "violeta": (142, 68, 173),
    "naranja": (211, 84, 0),
    "amarillo": (241, 196, 15),
}

# Ancho/alto del Short (vertical 9:16)
ANCHO = 1080
ALTO = 1920

# Ancho/alto del video largo (horizontal 16:9, formato clásico de YouTube)
ANCHO_LARGO = 1920
ALTO_LARGO = 1080

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


def _url_audio(linea):
    """Encuentra la URL del audio de una línea sin depender de cómo Make
    haya nombrado el campo esta vez: primero prueba los nombres conocidos,
    y si no aparece nada, revisa TODOS los valores de la línea y devuelve
    el primero que tenga forma de URL (empieza con http)."""
    conocido = _campo(linea, "audio_url", "Secure URL", "secure_url")
    if conocido:
        return conocido
    for valor in linea.values():
        if isinstance(valor, str) and valor.startswith("http"):
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


def _run_ffmpeg(cmd):
    """subprocess.run para comandos de ffmpeg/ffprobe, pero si fallan
    incluye el stderr real en el error — sin esto, el mensaje solo dice
    'returned non-zero exit status N' sin ninguna pista de la causa."""
    resultado = subprocess.run(cmd, capture_output=True, text=True)
    if resultado.returncode != 0:
        raise RuntimeError(
            f"{cmd[0]} falló (código {resultado.returncode}): {resultado.stderr[-800:]}"
        )
    return resultado


def obtener_duracion(ruta_audio):
    """Duración en segundos de un archivo de audio, vía ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        ruta_audio,
    ]
    resultado = _run_ffmpeg(cmd)
    return float(resultado.stdout.strip())


def crear_segmento(imagen_path, audio_path, salida_path, color_fondo=COLOR_FONDO_DEFAULT, ancho=ANCHO, alto=ALTO):
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
        f"scale={ancho}:{alto}:force_original_aspect_ratio=decrease,"
        f"pad={ancho}:{alto}:(ow-iw)/2:(oh-ih)/2:color={color_fondo}",
        "-preset", "veryfast",
        salida_path,
    ]
    _run_ffmpeg(cmd)


def generar_fondo_escena(color_fondo, imagen_prop_path, salida_path, ancho=ANCHO_LARGO, alto=ALTO_LARGO):
    """
    Renderiza UNA sola vez la combinación color sólido (cielo) + prop del
    parque (banco/árbol, anclado abajo) como una imagen estática. Se
    reutiliza para todas las líneas de una misma escena, en vez de
    recomponer las mismas dos capas de fondo en cada línea.
    """
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c={color_fondo}:s={ancho}x{alto}",
        "-i", imagen_prop_path,
        "-filter_complex",
        f"[1:v]scale={ancho}:-1[prop];[0:v][prop]overlay=0:H-h[final]",
        "-map", "[final]",
        "-frames:v", "1",
        salida_path,
    ]
    _run_ffmpeg(cmd)


def crear_segmento_escena(imagen_personaje_path, ruta_fondo, audio_path, salida_path, ancho=ANCHO_LARGO, alto=ALTO_LARGO):
    """
    Igual que crear_segmento, pero para la sesión grupal de video largo:
    superpone al personaje (recortando su fondo blanco vía colorkey)
    sobre un fondo de escena ya renderizado (ver generar_fondo_escena),
    en vez de armar el fondo desde cero en cada línea.
    """
    altura_personaje = int(alto * 0.85)
    margen_inferior_personaje = int(alto * 0.05)
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", ruta_fondo,
        "-loop", "1", "-i", imagen_personaje_path,
        "-i", audio_path,
        "-filter_complex",
        f"[1:v]scale=-1:{altura_personaje}[pj];"
        f"[pj]colorkey=0xFFFFFF:0.15:0.05[pjck];"
        f"[0:v][pjck]overlay=(W-w)/2:H-h-{margen_inferior_personaje}[final]",
        "-map", "[final]",
        "-map", "2:a",
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        "-preset", "veryfast",
        salida_path,
    ]
    _run_ffmpeg(cmd)


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
    _run_ffmpeg(cmd)


def _formato_ass(segundos):
    """Convierte segundos (float) al formato de timestamp que usa .ass:
    H:MM:SS.cc (centésimas, no milisegundos)."""
    horas = int(segundos // 3600)
    minutos = int((segundos % 3600) // 60)
    segs = int(segundos % 60)
    centesimas = int(round((segundos - int(segundos)) * 100))
    return f"{horas}:{minutos:02d}:{segs:02d}.{centesimas:02d}"


# Tamaño de fuente pensado como pie de página: relativamente chico (2.5%
# de la altura del video) para que una línea de diálogo completa entre
# cómoda en 1-2 renglones, sin tapar la escena. Al declarar PlayResX/
# PlayResY explícitamente iguales al video real, el tamaño sale exacto
# — a diferencia de un .srt simple, donde ffmpeg asume una resolución
# genérica y termina escalando la fuente de forma impredecible.
TAMANO_FUENTE_SUBTITULOS = int(ALTO * 0.025)
MARGEN_INFERIOR_SUBTITULOS = int(ALTO * 0.10)
MARGEN_LATERAL_SUBTITULOS = int(ANCHO * 0.08)

ENCABEZADO_ASS = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {ANCHO}
PlayResY: {ALTO}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{TAMANO_FUENTE_SUBTITULOS},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,3,0,2,{MARGEN_LATERAL_SUBTITULOS},{MARGEN_LATERAL_SUBTITULOS},{MARGEN_INFERIOR_SUBTITULOS},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# Mismo criterio para el video largo (horizontal), con su propia
# resolución declarada explícitamente.
TAMANO_FUENTE_SUBTITULOS_LARGO = int(ALTO_LARGO * 0.028)
MARGEN_INFERIOR_SUBTITULOS_LARGO = int(ALTO_LARGO * 0.08)
MARGEN_LATERAL_SUBTITULOS_LARGO = int(ANCHO_LARGO * 0.08)

ENCABEZADO_ASS_LARGO = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {ANCHO_LARGO}
PlayResY: {ALTO_LARGO}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{TAMANO_FUENTE_SUBTITULOS_LARGO},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,3,0,2,{MARGEN_LATERAL_SUBTITULOS_LARGO},{MARGEN_LATERAL_SUBTITULOS_LARGO},{MARGEN_INFERIOR_SUBTITULOS_LARGO},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def escribir_ass(bloques, ruta_ass, encabezado=ENCABEZADO_ASS):
    """Escribe un archivo .ass (subtítulos con estilo propio) con la
    resolución y el tamaño de fuente declarados explícitamente, para que
    ffmpeg no tenga que adivinar cómo escalarlo."""
    with open(ruta_ass, "w", encoding="utf-8") as f:
        f.write(encabezado)
        for inicio, fin, texto in bloques:
            texto_ass = texto.replace("\n", "\\N")
            f.write(
                f"Dialogue: 0,{_formato_ass(inicio)},{_formato_ass(fin)},"
                f"Default,,0,0,0,,{texto_ass}\n"
            )


def quemar_subtitulos(ruta_video_entrada, ruta_ass, ruta_video_salida):
    """Quema los subtítulos sobre el video ya renderizado (filtro
    subtitles, vía libass), usando el .ass con estilo y resolución ya
    definidos en el propio archivo."""
    filtro = f"subtitles={ruta_ass}"
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
    _run_ffmpeg(cmd)


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


def generar_miniatura(ruta_imagen_personaje, texto_miniatura, ruta_salida, color_franja=COLOR_FRANJA_MINIATURA):
    """
    Arma la miniatura (1280x720) a partir de la imagen del personaje que
    abre el video, con una franja superior y el texto gancho en mayúsculas.
    """
    imagen = Image.open(ruta_imagen_personaje).convert("RGB")
    imagen = recortar_a_medida(imagen, ANCHO_MINIATURA, ALTO_MINIATURA)

    franja_rect = (0, 0, ANCHO_MINIATURA, int(ALTO_MINIATURA * 0.32))
    overlay = Image.new("RGBA", imagen.size, (0, 0, 0, 0))
    dibujo_overlay = ImageDraw.Draw(overlay)
    dibujo_overlay.rectangle(franja_rect, fill=color_franja + (235,))
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
    if not resp.ok:
        raise RuntimeError(
            f"Cloudinary rechazo la subida ({resp.status_code}): {resp.text[:500]}"
        )
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
        hablante_apertura = None

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
        lineas_saltadas = []
        for idx, linea in enumerate(lineas_ordenadas):
            hablante = str(_campo(linea, "hablante", "$1")).strip().upper()
            audio_url = _url_audio(linea)
            texto = str(_campo(linea, "texto", "$2")).strip()

            # Una línea puntual mal formada (hablante desconocido o sin
            # audio_url) no debe tirar abajo el render entero — se salta
            # esa línea y se sigue con el resto.
            if hablante not in rutas_imagenes or not audio_url:
                lineas_saltadas.append(
                    f"línea {idx} (hablante='{hablante}', audio_url='{audio_url}', "
                    f"claves_recibidas={list(linea.keys())})"
                )
                continue

            audio_path = os.path.join(work_dir, f"audio_{idx}.mp3")
            descargar_archivo(audio_url, audio_path)
            duracion = obtener_duracion(audio_path)

            if texto:
                bloques_subtitulos.append((tiempo_acumulado, tiempo_acumulado + duracion, texto))
            tiempo_acumulado += duracion

            segmento_path = os.path.join(work_dir, f"segmento_{idx:03d}.mp4")
            crear_segmento(rutas_imagenes[hablante], audio_path, segmento_path)
            segmentos.append(segmento_path)
            if hablante_apertura is None:
                hablante_apertura = hablante

        if not segmentos:
            raise ValueError(
                f"Ninguna línea del guion se pudo procesar (se recibieron "
                f"{len(lineas_ordenadas)} líneas en total). Líneas descartadas: "
                + "; ".join(lineas_saltadas)
            )

        actualizar_estado(job_id, estado="concatenando")

        video_sin_subs_path = os.path.join(work_dir, "sin_subtitulos.mp4")
        concatenar_segmentos(segmentos, video_sin_subs_path, work_dir)

        if bloques_subtitulos:
            actualizar_estado(job_id, estado="quemando_subtitulos")
            ruta_ass = os.path.join(work_dir, "subtitulos.ass")
            escribir_ass(bloques_subtitulos, ruta_ass)
            video_final_path = os.path.join(work_dir, "final.mp4")
            quemar_subtitulos(video_sin_subs_path, ruta_ass, video_final_path)
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
            # "fila" es el ID lógico (columna "ID (Fila)" del Sheet), pero la
            # fila 1 real de la hoja son los encabezados — así que la fila
            # real donde vive ese registro es ID + 1. Se manda aparte para
            # que el módulo de Google Sheets en Make apunte a este campo.
            "fila_hoja": int(fila) + 1,
            "lineas_saltadas": lineas_saltadas,
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


def procesar_video_largo(job_id, lineas, fila, metadata, webhook_url):
    """
    Igual que procesar_caricatura, pero para la sesión grupal de video
    largo: el fondo de cada línea es un color sólido según la escena a la
    que pertenece (campo 'color_fondo'), en vez de ser siempre el mismo
    pastel. Las líneas llegan en el orden en que Make las agregó (escena
    por escena, línea por línea dentro de cada escena) — no se reordenan.
    """
    work_dir = tempfile.mkdtemp(prefix=f"video_largo_{job_id}_")
    try:
        actualizar_estado(job_id, estado="descargando_audios")

        rutas_imagenes = {}
        for personaje, url in IMAGENES_PERSONAJES.items():
            ruta = os.path.join(work_dir, f"img_{personaje}.png")
            descargar_archivo(url, ruta)
            rutas_imagenes[personaje] = ruta

        rutas_props_parque = []
        for i, url in enumerate(IMAGENES_PROP_PARQUE):
            ruta = os.path.join(work_dir, f"prop_parque_{i}.png")
            descargar_archivo(url, ruta)
            rutas_props_parque.append(ruta)

        # Fase 1: resolver los campos de cada línea y descartar las que
        # falten datos, sin tocar ffmpeg todavía.
        lineas_validas = []
        lineas_saltadas = []
        for idx, linea in enumerate(lineas):
            hablante = str(_campo(linea, "hablante", "$3")).strip().upper()
            imagen_clave = PERSONAJE_IMAGEN_VIDEO_LARGO.get(hablante, hablante)
            audio_url = _url_audio(linea)
            texto = str(_campo(linea, "texto", "$4")).strip()
            color_nombre = str(_campo(linea, "color_fondo", "$2")).strip().lower()
            color_fondo = COLORES_FONDO.get(color_nombre, COLOR_FONDO_DEFAULT)
            try:
                numero_escena = int(_campo(linea, "numero", "$1") or 1)
            except ValueError:
                numero_escena = 1

            if imagen_clave not in rutas_imagenes or not audio_url:
                lineas_saltadas.append(
                    f"línea {idx} (hablante='{hablante}', audio_url='{audio_url}', "
                    f"claves_recibidas={list(linea.keys())})"
                )
                continue

            lineas_validas.append({
                "idx": idx, "imagen_clave": imagen_clave, "audio_url": audio_url,
                "texto": texto, "color_fondo": color_fondo, "numero_escena": numero_escena,
            })

        if not lineas_validas:
            raise ValueError(
                f"Ninguna línea del guion se pudo procesar (se recibieron "
                f"{len(lineas)} líneas en total). Líneas descartadas: "
                + "; ".join(lineas_saltadas)
            )

        # Fase 2: descargar todos los audios en paralelo (independientes
        # entre sí) y medir su duración.
        def _descargar_audio_linea(item):
            audio_path = os.path.join(work_dir, f"audio_{item['idx']}.mp3")
            descargar_archivo(item["audio_url"], audio_path)
            item["audio_path"] = audio_path
            item["duracion"] = obtener_duracion(audio_path)
            return item

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            lineas_validas = list(executor.map(_descargar_audio_linea, lineas_validas))

        # Fase 3: timing acumulado de subtítulos, secuencial y en el orden
        # original (necesita el orden real para que los tiempos den bien).
        bloques_subtitulos = []
        tiempo_acumulado = 0.0
        for item in lineas_validas:
            if item["texto"]:
                bloques_subtitulos.append((tiempo_acumulado, tiempo_acumulado + item["duracion"], item["texto"]))
            tiempo_acumulado += item["duracion"]
        hablante_apertura = lineas_validas[0]["imagen_clave"]

        actualizar_estado(job_id, estado="generando_segmentos")

        # Fase 4: precomputar el fondo (color + prop del parque) UNA sola
        # vez por combinación, en vez de recrearlo en cada línea.
        fondos_cache = {}
        for item in lineas_validas:
            ruta_prop = rutas_props_parque[(item["numero_escena"] - 1) % len(rutas_props_parque)]
            clave = (item["color_fondo"], ruta_prop)
            if clave not in fondos_cache:
                ruta_fondo = os.path.join(work_dir, f"fondo_{len(fondos_cache)}.png")
                generar_fondo_escena(item["color_fondo"], ruta_prop, ruta_fondo, ANCHO_LARGO, ALTO_LARGO)
                fondos_cache[clave] = ruta_fondo
            item["ruta_fondo"] = fondos_cache[clave]

        # Fase 5: generar los segmentos de video en paralelo.
        def _generar_segmento_linea(item):
            segmento_path = os.path.join(work_dir, f"segmento_{item['idx']:03d}.mp4")
            crear_segmento_escena(
                rutas_imagenes[item["imagen_clave"]], item["ruta_fondo"], item["audio_path"], segmento_path,
                ancho=ANCHO_LARGO, alto=ALTO_LARGO,
            )
            return segmento_path

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(_generar_segmento_linea, lineas_validas))

        segmentos = [os.path.join(work_dir, f"segmento_{item['idx']:03d}.mp4") for item in lineas_validas]

        actualizar_estado(job_id, estado="concatenando")

        video_sin_subs_path = os.path.join(work_dir, "sin_subtitulos.mp4")
        concatenar_segmentos(segmentos, video_sin_subs_path, work_dir)

        if bloques_subtitulos:
            actualizar_estado(job_id, estado="quemando_subtitulos")
            ruta_ass = os.path.join(work_dir, "subtitulos.ass")
            escribir_ass(bloques_subtitulos, ruta_ass, encabezado=ENCABEZADO_ASS_LARGO)
            video_final_path = os.path.join(work_dir, "final.mp4")
            quemar_subtitulos(video_sin_subs_path, ruta_ass, video_final_path)
        else:
            video_final_path = video_sin_subs_path

        actualizar_estado(job_id, estado="generando_miniatura")

        texto_miniatura = (metadata.get("texto_miniatura") or "").strip()
        imagen_miniatura_url = None
        if texto_miniatura:
            color_miniatura_nombre = str(metadata.get("color_miniatura") or "").strip().lower()
            color_franja = COLORES_MINIATURA_RGB.get(color_miniatura_nombre, COLOR_FRANJA_MINIATURA)
            ruta_miniatura = os.path.join(work_dir, "miniatura.jpg")
            generar_miniatura(
                rutas_imagenes[hablante_apertura], texto_miniatura, ruta_miniatura, color_franja=color_franja
            )
            public_id_miniatura = f"caricaturas/miniaturas/largo_{fila}_{job_id[:8]}"
            imagen_miniatura_url = subir_a_cloudinary(ruta_miniatura, public_id_miniatura, tipo_recurso="image")

        actualizar_estado(job_id, estado="subiendo")

        public_id = f"caricaturas/videos_largos/largo_{fila}_{job_id[:8]}"
        url_video = subir_a_cloudinary(video_final_path, public_id)

        resultado = {
            "job_id": job_id,
            "estado": "completado",
            "url_video": url_video,
            "imagen_miniatura_url": imagen_miniatura_url,
            "fila": fila,
            "fila_hoja": int(fila) + 1,
            "lineas_saltadas": lineas_saltadas,
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
        jobs[job_id] = {
            "job_id": job_id,
            "estado": "en_cola",
            "fila": fila,
            "lineas_recibidas": len(lineas),
        }

    hilo = threading.Thread(
        target=procesar_caricatura,
        args=(job_id, lineas, fila, metadata, webhook_url),
        daemon=True,
    )
    hilo.start()

    return jsonify({"job_id": job_id, "estado": "en_cola"}), 202


@app.route("/fabricar_video_largo", methods=["POST"])
def fabricar_video_largo():
    """
    Body JSON esperado (desde Make, después del Array Aggregator de la
    sesión grupal):
    {
      "lineas": [
        {"hablante": "Doctor", "texto": "...", "audio_url": "...", "numero": 1, "color_fondo": "rojo"},
        {"hablante": "Juan", "texto": "...", "audio_url": "...", "numero": 1, "color_fondo": "rojo"},
        {"hablante": "Maria", "texto": "...", "audio_url": "...", "numero": 2, "color_fondo": "azul"},
        ...
      ],
      "fila": 3,
      "tema": "...",
      "concepto_psicologico": "...",
      "personajes_participantes": "Doctor, Juan, Maria",
      "titulo_seo": "...",
      "descripcion_seo": "...",
      "hashtags": "...",
      "etiquetas_ocultas": "...",
      "texto_miniatura": "NADIE TE MIRA",
      "color_miniatura": "rojo",
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
        "tema": data.get("tema"),
        "concepto_psicologico": data.get("concepto_psicologico"),
        "personajes_participantes": data.get("personajes_participantes"),
        "titulo_seo": data.get("titulo_seo"),
        "descripcion_seo": data.get("descripcion_seo"),
        "hashtags": data.get("hashtags"),
        "etiquetas_ocultas": data.get("etiquetas_ocultas"),
        "texto_miniatura": data.get("texto_miniatura"),
        "color_miniatura": data.get("color_miniatura"),
    }

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "job_id": job_id,
            "estado": "en_cola",
            "fila": fila,
            "lineas_recibidas": len(lineas),
        }

    hilo = threading.Thread(
        target=procesar_video_largo,
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
