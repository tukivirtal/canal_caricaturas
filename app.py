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
import re
import math
import uuid
import threading
import subprocess
import tempfile
import shutil
import concurrent.futures
import requests
from flask import Flask, request, jsonify
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

# URLs fijas de las imágenes de personajes (Cloudinary).
#
# Cada personaje tiene DOS dibujos, y no son intercambiables:
#  - El de Shorts es una escena de consultorio entera (diván, cuadro en la
#    pared, fondo crema). Se usa tal cual como cuadro completo del video.
#  - El de video largo ("_LARGO") es el personaje solo, sobre blanco, para
#    recortarlo por colorkey y pegarlo sobre la escena del parque.
# Usar el de Shorts en el video largo pegaría el consultorio entero sobre
# el parque, y encima sin recortar: el colorkey saca blanco, no crema.
IMAGENES_PERSONAJES = {
    "DOCTOR": "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785369741/Doctor_elj6ei.png",
    "JUAN": "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785369743/Juan_c0fmwo.png",
    "MARIA": "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785369738/Maria_xbz18p.png",
    "FABRICIO": "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1786060268/Fabricio_wybzwe.png",
    "JULI": "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1786060150/Juli_g3zsmb.png",
    "DOCTOR_LARGO": "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785725570/Gemini_Generated_Image_5ywmko5ywmko5ywm_acmjne.png",
    "JUAN_LARGO": "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785726093/Gemini_Generated_Image_o8nvdfo8nvdfo8nv_b8cied.png",
    "MARIA_LARGO": "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785726275/Gemini_Generated_Image_rvrvbirvrvbirvrv_jwslcc.png",
    "FABRICIO_LARGO": "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785725170/Gemini_Generated_Image_seg99fseg99fseg9_tcvyqi.png",
    "JULI_LARGO": "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785725133/Gemini_Generated_Image_seg99fseg99fseg9_jcdt0h.png",
}

# Imágenes de escena completas del parque (fondo + cielo con degradé ya
# incluido, sin recorte de color) que se usan tal cual para las escenas
# de video largo. El índice de la lista corresponde a "numero" de escena
# menos uno, ciclando si hay más escenas que imágenes.
IMAGENES_ESCENAS_PARQUE = [
    "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785973043/dibujo_1_aryiyb.jpg",  # banco + árbol + mesa de picnic
    "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785973044/dibujo2_ds03jq.jpg",  # laguna
    "https://res.cloudinary.com/ddbjsjmzj/image/upload/v1785973043/dibujo3_s1y2w5.jpg",  # plaza con juegos
]

# Qué dibujo usa cada personaje cuando el formato es video largo. Misma
# identidad y misma voz que en el Short: cambia el dibujo, no el
# personaje. Los cinco están acá — si alguno faltara, el video largo
# usaría su escena de consultorio de Shorts sobre el fondo del parque.
PERSONAJE_IMAGEN_VIDEO_LARGO = {
    "DOCTOR": "DOCTOR_LARGO",
    "JUAN": "JUAN_LARGO",
    "MARIA": "MARIA_LARGO",
    "FABRICIO": "FABRICIO_LARGO",
    "JULI": "JULI_LARGO",
}

CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "ddbjsjmzj")
CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET")
CLOUDINARY_UPLOAD_PRESET = os.environ.get("CLOUDINARY_UPLOAD_PRESET", "caricaturas")

# Color de fondo pastel que ya usa el Short (imagen del personaje + fondo fijo)
COLOR_FONDO_DEFAULT = "0xF5E6D3"

# Loudness al que se empareja la voz de todos los personajes (escala EBU
# R128). YouTube normaliza alrededor de -14 LUFS, pero solo hacia abajo:
# atenúa lo que llega fuerte y deja como está lo que llega bajo. -16 deja
# un margen cómodo sin que el canal suene flojo frente a los demás.
LUFS_OBJETIVO = -16.0

# Tope de la corrección de volumen. Sin él, una medición rara sobre un
# clip casi mudo amplificaría el ruido de fondo 40 dB. Cuando la
# corrección necesaria se pasa de este rango, se aplica el tope y se
# informa: quiere decir que esa voz está mal grabada de origen.
GANANCIA_MIN_DB = -12.0
GANANCIA_MAX_DB = 24.0

# Colores de franja para las miniaturas (PIL), según el campo
# 'color_miniatura' de la metadata del video largo.
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

# El video largo es una sucesión de imágenes fijas: no hay movimiento que
# preservar, así que no tiene sentido codificar 25 cuadros por segundo.
# A 12 fps el resultado se ve igual y se codifica la mitad de cuadros.
FPS_LARGO = 12

# Medidas del personaje dentro del cuadro del video largo. Se calculan una
# sola vez acá porque los recortes se precomputan antes de renderizar.
#
# El pie va cerca del borde inferior porque ahí está el pasto en las tres
# escenas del parque: subirlo para despejar la zona del subtítulo deja al
# personaje flotando sobre el piso, que se nota mucho más que el subtítulo
# cruzándole las piernas. El subtítulo se apoya en su contorno negro para
# leerse sobre las piernas, que son líneas finas.
ALTURA_PERSONAJE_LARGO = int(ALTO_LARGO * 0.80)
MARGEN_INFERIOR_PERSONAJE_LARGO = int(ALTO_LARGO * 0.05)

# Tamaño estándar de miniatura de YouTube (horizontal 16:9), independiente
# de que el video en sí sea vertical.
ANCHO_MINIATURA = 1280
ALTO_MINIATURA = 720
# Tipografía de la miniatura: display redondeada y pesada, el registro
# que usa el nicho de dibujos de palitos. Anton (condensada, tipo prensa)
# quedó para los subtítulos. Cambiar de fuente es cambiar esta línea:
# Baloo2-ExtraBold es la alternativa más limpia del mismo registro.
RUTA_FUENTE_MINIATURA = os.path.join(os.path.dirname(__file__), "LuckiestGuy-Regular.ttf")
COLOR_FRANJA_MINIATURA = (20, 20, 20)       # franja casi negra
COLOR_TEXTO_MINIATURA = (255, 226, 60)      # amarillo, alto contraste

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

def descargar_archivo(url, destino, intentos=3):
    """Descarga un archivo, reintentando si la conexión se corta a mitad
    de camino (más probable ahora que se descargan varios audios en
    paralelo). Si todos los intentos fallan, deja que la última
    excepción se propague."""
    for intento in range(1, intentos + 1):
        try:
            r = requests.get(url, stream=True, timeout=60)
            r.raise_for_status()
            with open(destino, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            return
        except (requests.RequestException, OSError):
            if intento == intentos:
                raise


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


def color_borde_imagen(imagen_path, color_por_defecto=COLOR_FONDO_DEFAULT):
    """
    Devuelve el color del borde de la imagen, en formato ffmpeg (0xRRGGBB).

    La escena del Short es 3:4 y el video es 9:16, así que la imagen se
    achica para entrar y el resto se rellena. Con un color fijo, cualquier
    dibujo cuyo crema no fuera exactamente ese quedaba con una banda
    visible arriba y abajo — y cada personaje puede traer el suyo apenas
    distinto. Tomando el color del propio borde, el relleno siempre
    coincide y la costura desaparece sola.
    """
    try:
        with Image.open(imagen_path) as imagen:
            muestra = imagen.convert("RGB").crop(
                (0, 0, min(8, imagen.width), min(8, imagen.height))
            )
        rojo, verde, azul = muestra.resize((1, 1)).getpixel((0, 0))
        return f"0x{rojo:02X}{verde:02X}{azul:02X}"
    except Exception:
        # Un dibujo ilegible no debería tumbar el render: se rellena con
        # el crema de siempre y sigue.
        return color_por_defecto


def crear_segmento(imagen_path, audio_path, salida_path, color_fondo=None,
                   ancho=ANCHO, alto=ALTO):
    """
    Crea un clip de video: la imagen fija durante la duración exacta
    del audio (usando -shortest, que corta cuando termina el audio).

    El audio llega ya normalizado desde preparar_audio_linea, así que
    acá no se toca el volumen.

    Sin 'color_fondo' explícito, el relleno sale del borde de la propia
    imagen (ver color_borde_imagen).
    """
    if color_fondo is None:
        color_fondo = color_borde_imagen(imagen_path)
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
    ]
    cmd += ["-preset", "veryfast", salida_path]
    _run_ffmpeg(cmd)


def preparar_fondo_escena(imagen_escena_path, salida_path, ancho=ANCHO_LARGO, alto=ALTO_LARGO):
    """
    Deja la imagen de escena ya escalada al tamaño exacto del video, una
    sola vez.

    Sin esto el escalado queda dentro del filtro del render y ffmpeg lo
    rehace en CADA cuadro, porque la imagen entra como stream infinito
    (-loop 1) y no como una imagen suelta.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", imagen_escena_path,
        "-vf",
        f"scale={ancho}:{alto}:force_original_aspect_ratio=decrease,"
        f"pad={ancho}:{alto}:(ow-iw)/2:(oh-ih)/2",
        "-frames:v", "1",
        salida_path,
    ]
    _run_ffmpeg(cmd)


def preparar_recorte_personaje(imagen_personaje_path, salida_path, altura=ALTURA_PERSONAJE_LARGO):
    """
    Deja al personaje ya escalado y recortado por colorkey, como PNG con
    canal alfa real, una sola vez por personaje.

    Antes el scale + colorkey vivían dentro del filtro de cada escena, y
    como los personajes entran con -loop 1 (stream infinito), ffmpeg los
    recalculaba en CADA cuadro para TODOS los personajes de la escena,
    aunque solo uno estuviera visible. Ese era el costo dominante del
    render: decenas de miles de recortes de una imagen que nunca cambia.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", imagen_personaje_path,
        "-vf", f"scale=-1:{altura},colorkey=0xFFFFFF:0.15:0.05",
        "-frames:v", "1",
        salida_path,
    ]
    _run_ffmpeg(cmd)


def medir_loudness(audio_path):
    """
    Loudness integrado del archivo en LUFS (escala EBU R128), o None si
    no se pudo medir. Solo decodifica, no escribe nada: cuesta ~35 ms.
    """
    resultado = subprocess.run(
        ["ffmpeg", "-i", audio_path, "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    medidas = re.findall(r"I:\s+(-?[\d.]+) LUFS", resultado.stderr)
    if not medidas:
        return None
    valor = float(medidas[-1])
    # Por debajo de -70 LUFS es silencio o basura: mejor no normalizar
    # que amplificar ruido de fondo 50 dB.
    return valor if valor > -70 else None


def _combinar_loudness(mediciones):
    """
    Combina varias mediciones (lufs, duracion) en una sola.

    El promedio se hace en energía y ponderado por duración, que es como
    se suma el loudness de verdad — promediar los LUFS directamente daría
    un número que no corresponde a nada.
    """
    energia_total = duracion_total = 0.0
    for lufs, duracion in mediciones:
        if lufs is None or duracion <= 0:
            continue
        energia_total += (10 ** (lufs / 10)) * duracion
        duracion_total += duracion
    if duracion_total == 0 or energia_total <= 0:
        return None
    return 10 * math.log10(energia_total / duracion_total)


def ganancias_por_personaje(items):
    """
    Calcula UNA ganancia en dB por personaje, para que todos entreguen al
    mismo volumen.

    Las voces de ElevenLabs vienen a niveles muy distintos entre sí
    (medido en este repo: hasta 18 dB de diferencia), y YouTube atenúa lo
    que llega fuerte pero NO levanta lo que llega bajo. O sea que un
    personaje que entrega flojo suena flojo para siempre, y el
    espectador sube el volumen o se va.

    Se calcula por personaje y no por línea a propósito: en un "Ajá." de
    medio segundo la medición de loudness es poco confiable, y normalizar
    línea por línea aplasta las diferencias de intención dentro de un
    mismo personaje. Así se corrige la voz, no la actuación.

    Devuelve (ganancias, avisos). Los avisos son los casos en que la
    corrección quedó recortada por el tope de seguridad: ahí el
    emparejado queda incompleto y hay que revisar esa voz en ElevenLabs.
    Se informan en el resultado del job en vez de quedar en silencio —
    una brecha de volumen que nadie ve es justo la que termina publicada.
    """
    mediciones = {}
    for item in items:
        mediciones.setdefault(item["imagen_clave"], []).append(
            (item.get("lufs"), item.get("duracion", 0.0))
        )

    ganancias, avisos = {}, []
    for personaje, medidas in sorted(mediciones.items()):
        combinado = _combinar_loudness(medidas)
        if combinado is None:
            ganancias[personaje] = 0.0
            avisos.append(
                f"{personaje}: no se pudo medir el audio, se deja sin cambios"
            )
            continue
        # El tope evita que una medición rara amplifique ruido de fondo o
        # deje a un personaje inaudible.
        necesaria = LUFS_OBJETIVO - combinado
        acotada = max(GANANCIA_MIN_DB, min(GANANCIA_MAX_DB, necesaria))
        ganancias[personaje] = acotada
        if abs(necesaria - acotada) > 0.5:
            avisos.append(
                f"{personaje}: entrega {combinado:.1f} LUFS y necesitaba "
                f"{necesaria:+.1f} dB, pero el tope permite {acotada:+.1f} dB — "
                f"va a seguir sonando distinto al resto, conviene revisar esa voz"
            )
    return ganancias, avisos


def preparar_audio_linea(entrada_path, salida_wav, ganancia_db=0.0):
    """
    Decodifica la línea a WAV aplicándole su ganancia, y devuelve la
    duración exacta del resultado.

    Se hace SIEMPRE, aunque la ganancia sea cero, porque el WAV es el que
    fija la línea de tiempo. En un MP3 la duración que declara el
    contenedor no coincide con la que sale al decodificarlo: medido acá,
    unos 44 ms de más por archivo. Cronometrar los subtítulos y los
    cambios de personaje con la duración declarada mientras suena la
    decodificada desfasa el video de a poco — en un guion de 142 líneas,
    más de 6 segundos, o sea un turno entero de diálogo.

    Midiendo sobre el WAV la duración es exacta (muestras / frecuencia) y
    es la misma que después se concatena, así que no hay nada que derive.
    """
    _run_ffmpeg([
        "ffmpeg", "-y",
        "-i", entrada_path,
        "-af", f"volume={ganancia_db:.2f}dB",
        "-ar", "44100",
        salida_wav,
    ])
    return obtener_duracion(salida_wav)


def concatenar_segmentos(lista_segmentos, salida_path, work_dir, nombre_lista="lista.txt", recodificar_audio=False):
    """
    Concatena los segmentos en orden usando el demuxer concat de ffmpeg.

    Por defecto usa stream copy (rápido, sin pérdida de calidad) — bien
    para concatenar videos que ya comparten códec. Para concatenar
    muchos MP3 individuales (audio de las líneas dentro de una escena),
    activar recodificar_audio=True: el copy directo de MP3s arrastra el
    padding/delay propio de cada archivo, y con decenas de líneas ese
    desfase se va acumulando — termina notándose como personajes fuera
    de sincronía a mitad del video. Reencodear fuerza una concatenación
    a nivel de muestra de audio, sin ese arrastre.
    """
    lista_txt = os.path.join(work_dir, nombre_lista)
    with open(lista_txt, "w") as f:
        for seg in lista_segmentos:
            f.write(f"file '{seg}'\n")

    codec = ["-c:a", "pcm_s16le"] if recodificar_audio else ["-c", "copy"]
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", lista_txt,
        *codec,
        salida_path,
    ]
    _run_ffmpeg(cmd)


def renderizar_escena(lineas_escena, rutas_imagenes, ruta_fondo, ruta_audio_escena, salida_path, ancho=ANCHO_LARGO, alto=ALTO_LARGO):
    """
    Arma una escena entera sin componer nada por cuadro.

    Como el fondo y los personajes son imágenes fijas, la escena es en
    realidad una sucesión de muy pocos cuadros distintos: uno por cada
    personaje que habla en ella. Se componen esos cuadros una sola vez
    con PIL y se encadenan con el demuxer concat, dándole a cada línea
    su duración; a ffmpeg le queda solo el trabajo de codificar.

    El enfoque anterior superponía los personajes con el filtro overlay
    sobre un fondo en loop, lo que obliga a ffmpeg a recomponer el cuadro
    entero —para TODOS los personajes de la escena— en cada uno de los
    miles de cuadros del video, aunque la imagen nunca cambie. Medido
    sobre una escena de 2 minutos con 5 personajes: 461 s con overlay
    contra 12 s con este enfoque, mismo tamaño de archivo.
    """
    prefijo = os.path.splitext(salida_path)[0]

    # Un cuadro por personaje, no uno por línea: alguien que habla diez
    # veces en la escena reusa siempre el mismo cuadro compuesto.
    fondo = Image.open(ruta_fondo).convert("RGBA")
    cuadros = {}
    for clave in {item["imagen_clave"] for item in lineas_escena}:
        personaje = Image.open(rutas_imagenes[clave]).convert("RGBA")
        marco = fondo.copy()
        marco.paste(
            personaje,
            ((ancho - personaje.width) // 2,
             alto - personaje.height - MARGEN_INFERIOR_PERSONAJE_LARGO),
            personaje,
        )
        ruta_cuadro = f"{prefijo}_cuadro_{clave}.png"
        marco.convert("RGB").save(ruta_cuadro)
        cuadros[clave] = ruta_cuadro

    # Lista del demuxer concat: cada línea aporta su cuadro y su
    # duración. La última entrada se repite sin duración porque concat
    # ignora la duración del último archivo de la lista.
    ruta_lista = f"{prefijo}_cuadros.txt"
    with open(ruta_lista, "w") as f:
        for item in lineas_escena:
            f.write(f"file '{cuadros[item['imagen_clave']]}'\n")
            f.write(f"duration {item['duracion']:.3f}\n")
        f.write(f"file '{cuadros[lineas_escena[-1]['imagen_clave']]}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", ruta_lista,
        "-i", ruta_audio_escena,
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-r", str(FPS_LARGO),
        "-shortest",
        "-preset", "ultrafast",
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


# Los subtítulos se queman con libass, que resuelve el nombre de la
# fuente por fontconfig. Declarar "Arial" era pedir algo que no está
# instalado en la imagen: libass caía a cualquier fuente disponible y el
# render dependía de con qué se armara el contenedor. DejaVu Sans viene
# del paquete fonts-dejavu-core del Dockerfile y tiene los acentos y la
# eñe que el español necesita.
FUENTE_SUBTITULOS = "DejaVu Sans"

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
Style: Default,{FUENTE_SUBTITULOS},{TAMANO_FUENTE_SUBTITULOS},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,1,2,{MARGEN_LATERAL_SUBTITULOS},{MARGEN_LATERAL_SUBTITULOS},{MARGEN_INFERIOR_SUBTITULOS},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# En el video largo el subtítulo no es un pie de página decorativo: es
# el texto que sigue quien mira sin audio, durante 8 minutos. Al 2.8% de
# la altura quedaba en 30px sobre 1080p, chico de más para el formato
# horizontal. Al 4.2% son 45px, que es lo que usa el resto del formato.
TAMANO_FUENTE_SUBTITULOS_LARGO = int(ALTO_LARGO * 0.042)
MARGEN_INFERIOR_SUBTITULOS_LARGO = int(ALTO_LARGO * 0.08)
MARGEN_LATERAL_SUBTITULOS_LARGO = int(ANCHO_LARGO * 0.08)

ENCABEZADO_ASS_LARGO = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {ANCHO_LARGO}
PlayResY: {ALTO_LARGO}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{FUENTE_SUBTITULOS},{TAMANO_FUENTE_SUBTITULOS_LARGO},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,4,1,2,{MARGEN_LATERAL_SUBTITULOS_LARGO},{MARGEN_LATERAL_SUBTITULOS_LARGO},{MARGEN_INFERIOR_SUBTITULOS_LARGO},1

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


def _acortar(texto, limite=100):
    """Corta a 'limite' caracteres sin partir una palabra al medio."""
    if len(texto) <= limite:
        return texto
    corte = texto[:limite].rsplit(" ", 1)[0]
    # Si respetar la palabra deja un título mutilado —pasa cuando viene una
    # sola palabra larguísima— conviene el corte duro.
    if len(corte) < limite * 0.6:
        return texto[:limite]
    return corte


def titulo_para_youtube(metadata):
    """
    Devuelve el título con el que se va a subir el video.

    Si el guion no trajo 'titulo_seo', el módulo de YouTube Upload sube el
    video SIN título, y un video sin título son cero impresiones. Antes de
    permitir eso se arma uno con el tema de la fila: no es un buen título
    de SEO, pero es infinitamente mejor que ninguno. El arreglo de fondo
    es que el prompt lo genere.
    """
    titulo = (metadata.get("titulo_seo") or "").strip()
    if titulo:
        return _acortar(titulo)
    tema = (metadata.get("tema") or "").strip()
    if tema:
        return _acortar(f"El Diván | {tema}")
    return "El Diván | Terapia con Humor"


def etiquetas_para_youtube(metadata):
    """
    Devuelve las etiquetas con las que se sube el video.

    YouTube pide 'tags' como campo OBLIGATORIO: si llega vacío, el módulo
    de Upload corta la ejecución y el video no se sube — y como el resto
    de la automatización va detrás, tampoco se registra nada en el Sheet.
    Mientras el prompt no genere 'etiquetas_ocultas', se arman con el tema
    y el concepto de la fila. No es SEO, es un piso para que nada se
    bloquee.
    """
    etiquetas = (metadata.get("etiquetas_ocultas") or "").strip()
    if etiquetas:
        return _acortar(etiquetas, 480)

    base = ["psicologia animada", "terapia", "humor cinico", "salud mental",
            "psicologia cotidiana", "neurosis moderna"]
    for clave in ("concepto_psicologico", "tema"):
        valor = (metadata.get(clave) or "").strip()
        if valor:
            base.append(valor.lower())
    # YouTube corta las etiquetas a 500 caracteres en total.
    return _acortar(", ".join(base), 480)


def descripcion_para_youtube(metadata):
    """
    Devuelve la descripción del video.

    Mismo criterio que el título y las etiquetas: si el guion no la trajo,
    se arma una con el tema y el concepto de la fila. Una descripción
    pobre posiciona mal; una vacía desperdicia el espacio donde YouTube
    busca de qué trata el video.
    """
    descripcion = (metadata.get("descripcion_seo") or "").strip()
    if descripcion:
        return descripcion

    tema = (metadata.get("tema") or "").strip()
    concepto = (metadata.get("concepto_psicologico") or "").strip()
    partes = []
    if tema:
        partes.append(f"Hoy en El Diván: {tema}.")
    if concepto:
        partes.append(f"El concepto detrás de eso se llama {concepto}.")
    partes.append(
        "Psicología real explicada sin solemnidad, con un terapeuta cínico y "
        "pacientes que sobrepiensan todo. Suscríbete si te sentiste identificado."
    )
    return "\n\n".join(partes)


def limpiar_texto_miniatura(valor):
    """
    Devuelve el texto de la miniatura ya limpio.

    El Text parser de Make a veces manda el bundle entero en vez del valor
    extraído, y llega algo tipo 'META|texto_miniatura|NADIE TE MIRA'. Con
    ese texto la miniatura sale con basura encima, así que lo recortamos
    acá también — el arreglo de fondo va en Make, pero no queremos que un
    bubble mal configurado nos queme el CTR de un video entero.
    """
    texto = str(valor or "").strip()
    if texto.upper().startswith("META|"):
        texto = texto.rsplit("|", 1)[-1].strip()
    return texto


def _recortar_fondo_blanco(imagen, umbral=235):
    """Vuelve transparente el fondo blanco de la imagen de un personaje.

    Los personajes del video largo ya llegan recortados por ffmpeg, pero
    los de los Shorts vienen con el blanco original; sin esto quedaría un
    rectángulo blanco pegado sobre el fondo de la miniatura.

    Solo se saca el blanco CONECTADO AL BORDE: el relleno blanco de la
    cara o de los ojos queda intacto. Un colorkey plano, que borra todo
    píxel blanco esté donde esté, deja al personaje con la cabeza
    transparente.

    Se resuelve con operaciones de canal (en C) y no pixel por pixel:
    sobre imágenes de 1-2 megapíxeles la diferencia es de segundos."""
    rojo, verde, azul = imagen.convert("RGB").split()
    binarizar = lambda canal: canal.point(lambda v: 255 if v >= umbral else 0)
    blanco = ImageChops.multiply(
        ImageChops.multiply(binarizar(rojo), binarizar(verde)), binarizar(azul)
    )

    # Se marca con 128 el blanco alcanzable desde los bordes; lo que
    # quede en 255 es blanco interior del dibujo y se conserva.
    ancho, alto = blanco.size
    borde = [(x, y) for x in range(ancho) for y in (0, alto - 1)]
    borde += [(x, y) for y in range(alto) for x in (0, ancho - 1)]
    for punto in borde:
        if blanco.getpixel(punto) == 255:
            ImageDraw.floodfill(blanco, punto, 128)

    imagen = imagen.convert("RGBA")
    imagen.putalpha(blanco.point(lambda v: 0 if v == 128 else 255))
    return imagen


def _ajustar_texto_miniatura(texto, ancho_max, tamano_inicial, max_lineas=2):
    """Elige el tamaño de fuente más grande con el que el texto entra en
    ancho_max sin pasarse de max_lineas renglones."""
    medidor = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    palabras = texto.split()
    for tamano in range(tamano_inicial, 44, -4):
        fuente = ImageFont.truetype(RUTA_FUENTE_MINIATURA, tamano)
        lineas, actual = [], ""
        for palabra in palabras:
            candidata = f"{actual} {palabra}".strip()
            if not actual or medidor.textbbox((0, 0), candidata, font=fuente)[2] <= ancho_max:
                actual = candidata
            else:
                lineas.append(actual)
                actual = palabra
        lineas.append(actual)
        if len(lineas) <= max_lineas and all(
            medidor.textbbox((0, 0), l, font=fuente)[2] <= ancho_max for l in lineas
        ):
            return fuente, lineas, tamano
    fuente = ImageFont.truetype(RUTA_FUENTE_MINIATURA, 44)
    return fuente, palabras[:max_lineas], 44


def _cargar_personaje(ruta):
    """Abre la imagen de un personaje lista para pegar: con alfa real y
    recortada a lo que ocupa el dibujo (sin aire alrededor)."""
    personaje = Image.open(ruta)
    if personaje.mode != "RGBA" or not personaje.getchannel("A").getbbox():
        personaje = _recortar_fondo_blanco(personaje)
    caja = personaje.getchannel("A").getbbox()
    return personaje.crop(caja) if caja else personaje


def generar_miniatura(rutas_personajes, texto_miniatura, ruta_salida,
                      ruta_fondo=None, color_franja=COLOR_FRANJA_MINIATURA):
    """
    Arma la miniatura (1280x720) con el esquema que usa el nicho: el
    texto gancho arriba, enorme y a todo el ancho, en amarillo con
    contorno negro grueso; y los personajes parados en fila abajo, sobre
    el fondo de escena.

    Nada de franja de color con letras adentro: lo que hace legible al
    texto es el contorno, y ocupar el cuadro entero rinde mucho más. El
    texto se mantiene arriba, lejos del borde inferior derecho, que es
    donde YouTube estampa la duración del video.

    'rutas_personajes' puede ser una ruta sola o una lista: en el video
    largo conviene mandar a todos los que participan, que es justamente
    lo que da la fila de monigotes de la referencia. Sin 'ruta_fondo'
    (caso Shorts) se usa un fondo sólido en vez de la escena.
    """
    titulo = limpiar_texto_miniatura(texto_miniatura).upper()
    if isinstance(rutas_personajes, (str, bytes, os.PathLike)):
        rutas_personajes = [rutas_personajes]
    rutas_personajes = list(rutas_personajes)[:5]

    if ruta_fondo:
        fondo = recortar_a_medida(
            Image.open(ruta_fondo).convert("RGB"), ANCHO_MINIATURA, ALTO_MINIATURA
        ).convert("RGBA")
    else:
        fondo = Image.new("RGBA", (ANCHO_MINIATURA, ALTO_MINIATURA), color_franja + (255,))

    # Velo claro que se desvanece hacia abajo: levanta el contraste del
    # texto sin el borde duro de una caja. Los fondos de escena son
    # saturados (cielos de atardecer) y sin esto el amarillo pelea.
    velo = Image.new("RGBA", fondo.size, (0, 0, 0, 0))
    dibujo_velo = ImageDraw.Draw(velo)
    alto_velo = int(ALTO_MINIATURA * 0.55)
    for y in range(alto_velo):
        dibujo_velo.line(
            [(0, y), (ANCHO_MINIATURA, y)],
            fill=(255, 255, 255, int(120 * (1 - y / alto_velo))),
        )
    imagen = Image.alpha_composite(fondo, velo)

    # El texto se calcula PRIMERO y se queda con su banda de arriba; los
    # personajes reciben lo que sobra. Al revés, un título de dos líneas
    # termina tapando las cabezas.
    margen = int(ANCHO_MINIATURA * 0.05)
    fuente, lineas, tamano = _ajustar_texto_miniatura(
        titulo, ANCHO_MINIATURA - margen * 2, int(ANCHO_MINIATURA * 0.16)
    )
    alto_linea = tamano * 1.05
    pos_y_texto = int(ALTO_MINIATURA * 0.04)
    piso_texto = pos_y_texto + alto_linea * len(lineas)

    # Personajes en fila abajo, todos a la misma altura, repartidos.
    personajes = [_cargar_personaje(r) for r in rutas_personajes]
    if personajes:
        base_y = int(ALTO_MINIATURA * 0.97)
        alto_fila = max(int(ALTO_MINIATURA * 0.30),
                        int(base_y - piso_texto - ALTO_MINIATURA * 0.03))
        escalados = []
        for p in personajes:
            ancho = max(1, int(p.width * alto_fila / p.height))
            escalados.append(p.resize((ancho, alto_fila)))
        # Si entre todos no entran a lo ancho, se achican en bloque.
        ancho_util = int(ANCHO_MINIATURA * 0.94)
        ancho_total = sum(p.width for p in escalados)
        if ancho_total > ancho_util:
            factor = ancho_util / ancho_total
            escalados = [
                p.resize((max(1, int(p.width * factor)), max(1, int(p.height * factor))))
                for p in escalados
            ]
            ancho_total = sum(p.width for p in escalados)

        hueco = (ANCHO_MINIATURA - ancho_total) / (len(escalados) + 1)
        posiciones, x = [], hueco
        for p in escalados:
            posiciones.append((int(x), base_y - p.height))
            x += p.width + hueco

        # Halo oscuro difuso detrás de la fila: los despega del fondo sin
        # necesidad de recuadros.
        silueta = Image.new("RGBA", imagen.size, (0, 0, 0, 0))
        for p, pos in zip(escalados, posiciones):
            silueta.paste(p, pos, p)
        halo = Image.new("RGBA", imagen.size, (0, 0, 0, 0))
        halo.paste((0, 0, 0, 150), (0, 0, *imagen.size),
                   silueta.split()[3].filter(ImageFilter.GaussianBlur(12)))
        imagen = Image.alpha_composite(imagen, halo)
        for p, pos in zip(escalados, posiciones):
            imagen.paste(p, pos, p)

    # Texto arriba, a todo el ancho, por encima de la fila.
    dibujo = ImageDraw.Draw(imagen)
    grosor_contorno = max(7, int(tamano * 0.13))
    for i, linea in enumerate(lineas):
        caja = dibujo.textbbox((0, 0), linea, font=fuente, stroke_width=grosor_contorno)
        pos_x = (ANCHO_MINIATURA - (caja[2] - caja[0])) / 2 - caja[0]
        dibujo.text(
            (pos_x, pos_y_texto + i * alto_linea - caja[1]), linea, font=fuente,
            fill=COLOR_TEXTO_MINIATURA, stroke_width=grosor_contorno, stroke_fill=(0, 0, 0),
        )

    imagen.convert("RGB").save(ruta_salida, quality=95)


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

        # Descargar las 3 imágenes de personajes una sola vez
        rutas_imagenes = {}
        for personaje, url in IMAGENES_PERSONAJES.items():
            ruta = os.path.join(work_dir, f"img_{personaje}.png")
            descargar_archivo(url, ruta)
            rutas_imagenes[personaje] = ruta

        actualizar_estado(job_id, estado="generando_segmentos")

        # Fase 1: resolver los campos de cada línea, sin tocar la red
        # todavía. Una línea puntual mal formada (hablante desconocido o
        # sin audio_url) no debe tirar abajo el render entero — se salta
        # esa línea y se sigue con el resto.
        lineas_validas = []
        lineas_saltadas = []
        for idx, linea in enumerate(lineas_ordenadas):
            hablante = str(_campo(linea, "hablante", "$1")).strip().upper()
            audio_url = _url_audio(linea)
            texto = str(_campo(linea, "texto", "$2")).strip()

            if hablante not in rutas_imagenes or not audio_url:
                lineas_saltadas.append(
                    f"línea {idx} (hablante='{hablante}', audio_url='{audio_url}', "
                    f"claves_recibidas={list(linea.keys())})"
                )
                continue

            lineas_validas.append({
                "idx": idx, "imagen_clave": hablante,
                "audio_url": audio_url, "texto": texto,
            })

        if not lineas_validas:
            raise ValueError(
                f"Ninguna línea del guion se pudo procesar (se recibieron "
                f"{len(lineas_ordenadas)} líneas en total). Líneas descartadas: "
                + "; ".join(lineas_saltadas)
            )

        # Fase 2: descargar los audios en paralelo (son independientes
        # entre sí) y medir duración y loudness de cada uno.
        def _descargar_audio_linea(item):
            audio_path = os.path.join(work_dir, f"audio_{item['idx']}.mp3")
            descargar_archivo(item["audio_url"], audio_path)
            item["audio_path"] = audio_path
            item["duracion"] = obtener_duracion(audio_path)
            item["lufs"] = medir_loudness(audio_path)
            return item

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            lineas_validas = list(executor.map(_descargar_audio_linea, lineas_validas))

        ganancias, avisos_audio = ganancias_por_personaje(lineas_validas)

        # Fase 2b: cada línea pasa a WAV con su ganancia. Además de
        # emparejar el volumen, es lo que le da a la línea una duración
        # exacta: el clip dura lo que dura su audio decodificado, y si
        # los subtítulos se cronometran con la duración que declara el
        # MP3, se van desfasando de a milisegundos por línea.
        def _normalizar_audio_linea(item):
            salida = os.path.join(work_dir, f"audio_norm_{item['idx']}.wav")
            item["duracion"] = preparar_audio_linea(
                item["audio_path"], salida, ganancias.get(item["imagen_clave"], 0.0)
            )
            item["audio_path"] = salida
            return item

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            lineas_validas = list(executor.map(_normalizar_audio_linea, lineas_validas))

        # Fase 3: un clip por línea, en el orden original (los tiempos de
        # subtítulo se acumulan y dependen de ese orden).
        segmentos = []
        bloques_subtitulos = []
        tiempo_acumulado = 0.0
        for item in lineas_validas:
            if item["texto"]:
                bloques_subtitulos.append(
                    (tiempo_acumulado, tiempo_acumulado + item["duracion"], item["texto"])
                )
            tiempo_acumulado += item["duracion"]

            segmento_path = os.path.join(work_dir, f"segmento_{item['idx']:03d}.mp4")
            crear_segmento(
                rutas_imagenes[item["imagen_clave"]], item["audio_path"], segmento_path,
            )
            segmentos.append(segmento_path)

        hablante_apertura = lineas_validas[0]["imagen_clave"]

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

        texto_miniatura = limpiar_texto_miniatura(metadata.get("texto_miniatura"))
        # El valor limpio vuelve a metadata para que el webhook informe lo
        # mismo que quedó dibujado en la miniatura. Si no, Make recibe el
        # "META|texto_miniatura|..." crudo y lo escribe así en el Sheet.
        metadata["texto_miniatura"] = texto_miniatura
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
            "avisos_audio": avisos_audio,
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
    largo: el fondo de cada línea es la imagen de escena del parque que le
    corresponde según el número de escena, en vez de ser siempre el mismo
    pastel. Las líneas llegan en el orden en que Make las agregó (escena
    por escena, línea por línea dentro de cada escena) — no se reordenan.
    """
    work_dir = tempfile.mkdtemp(prefix=f"video_largo_{job_id}_")
    try:
        actualizar_estado(job_id, estado="preparando_imagenes")

        # Fase 1: resolver los campos de cada línea y descartar las que
        # falten datos, sin tocar ffmpeg todavía.
        lineas_validas = []
        lineas_saltadas = []
        for idx, linea in enumerate(lineas):
            hablante = str(_campo(linea, "hablante", "$3")).strip().upper()
            imagen_clave = PERSONAJE_IMAGEN_VIDEO_LARGO.get(hablante, hablante)
            audio_url = _url_audio(linea)
            texto = str(_campo(linea, "texto", "$4")).strip()
            try:
                numero_escena = int(_campo(linea, "numero", "$1") or 1)
            except ValueError:
                numero_escena = 1

            if imagen_clave not in IMAGENES_PERSONAJES or not audio_url:
                lineas_saltadas.append(
                    f"línea {idx} (hablante='{hablante}', audio_url='{audio_url}', "
                    f"claves_recibidas={list(linea.keys())})"
                )
                continue

            lineas_validas.append({
                "idx": idx, "imagen_clave": imagen_clave, "audio_url": audio_url,
                "texto": texto, "numero_escena": numero_escena,
            })

        if not lineas_validas:
            raise ValueError(
                f"Ninguna línea del guion se pudo procesar (se recibieron "
                f"{len(lineas)} líneas en total). Líneas descartadas: "
                + "; ".join(lineas_saltadas)
            )

        # Fase 1b: bajar SOLO las imágenes que este guion usa realmente y
        # dejarlas listas para componer (personajes ya recortados, fondos
        # ya escalados). Todo el trabajo de imagen se hace acá, una vez
        # por imagen — no una vez por cuadro dentro del render.
        rutas_imagenes = {}
        for personaje in sorted({item["imagen_clave"] for item in lineas_validas}):
            ruta_origen = os.path.join(work_dir, f"img_{personaje}.png")
            descargar_archivo(IMAGENES_PERSONAJES[personaje], ruta_origen)
            ruta_recorte = os.path.join(work_dir, f"recorte_{personaje}.png")
            preparar_recorte_personaje(ruta_origen, ruta_recorte)
            rutas_imagenes[personaje] = ruta_recorte

        fondos_escena = {}
        for indice in sorted({
            (item["numero_escena"] - 1) % len(IMAGENES_ESCENAS_PARQUE)
            for item in lineas_validas
        }):
            ruta_origen = os.path.join(work_dir, f"escena_parque_{indice}.jpg")
            descargar_archivo(IMAGENES_ESCENAS_PARQUE[indice], ruta_origen)
            ruta_fondo = os.path.join(work_dir, f"fondo_escena_{indice}.png")
            preparar_fondo_escena(ruta_origen, ruta_fondo)
            fondos_escena[indice] = ruta_fondo

        actualizar_estado(job_id, estado="descargando_audios")

        # Fase 2: descargar todos los audios en paralelo (independientes
        # entre sí), medir su duración y su loudness.
        def _descargar_audio_linea(item):
            audio_path = os.path.join(work_dir, f"audio_{item['idx']}.mp3")
            descargar_archivo(item["audio_url"], audio_path)
            item["audio_path"] = audio_path
            item["duracion"] = obtener_duracion(audio_path)
            item["lufs"] = medir_loudness(audio_path)
            return item

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            lineas_validas = list(executor.map(_descargar_audio_linea, lineas_validas))

        # Fase 2b: emparejar el volumen de los personajes entre sí. Cada
        # voz de ElevenLabs entrega a un nivel distinto, y el que entrega
        # bajo va a sonar bajo en YouTube para siempre (ver
        # ganancias_por_personaje).
        ganancias, avisos_audio = ganancias_por_personaje(lineas_validas)

        # Se pasa por acá SIEMPRE, aun sin ganancia que aplicar: además de
        # emparejar el volumen, es lo que fija la duración exacta de cada
        # línea (ver preparar_audio_linea).
        def _normalizar_audio_linea(item):
            salida = os.path.join(work_dir, f"audio_norm_{item['idx']}.wav")
            item["duracion"] = preparar_audio_linea(
                item["audio_path"], salida, ganancias.get(item["imagen_clave"], 0.0)
            )
            item["audio_path"] = salida
            return item

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            lineas_validas = list(executor.map(_normalizar_audio_linea, lineas_validas))

        actualizar_estado(job_id, estado="generando_segmentos")

        # Fase 3: agrupar las líneas por escena. El video se arma escena
        # por escena y las escenas se concatenan en orden ascendente, así
        # que ESTE es el orden real en que se va a ver y escuchar todo.
        escenas = {}
        for item in lineas_validas:
            escenas.setdefault(item["numero_escena"], []).append(item)

        # Los subtítulos se cronometran sobre esa misma secuencia, no
        # sobre el orden en que llegaron las líneas. Si Make manda las
        # líneas sin agrupar por escena, las dos secuencias no coinciden
        # y el subtítulo termina mostrando la línea de un personaje
        # mientras en pantalla está el otro. Recorriendo lo mismo que se
        # renderiza, la sincronía no depende de cómo llegue el guion.
        lineas_en_orden_de_render = [
            item for numero_escena in sorted(escenas.keys())
            for item in escenas[numero_escena]
        ]

        bloques_subtitulos = []
        tiempo_acumulado = 0.0
        for item in lineas_en_orden_de_render:
            if item["texto"]:
                bloques_subtitulos.append(
                    (tiempo_acumulado, tiempo_acumulado + item["duracion"], item["texto"])
                )
            tiempo_acumulado += item["duracion"]

        # Fase 4: concatenar el audio de cada escena en una sola pista.
        #
        # De paso se anota qué fondo le tocó a cada escena. Los números de
        # escena los elige el guion, y si no vienen como 1,2,3,4 el reparto
        # de fondos cambia sin que se note: un dibujo puede no aparecer
        # nunca y desde afuera parece que el código lo ignora.
        escenas_render = []
        reparto_escenas = []
        for numero_escena in sorted(escenas.keys()):
            lineas_escena = escenas[numero_escena]
            indice_fondo = (numero_escena - 1) % len(IMAGENES_ESCENAS_PARQUE)
            ruta_fondo = fondos_escena[indice_fondo]
            reparto_escenas.append({
                "escena": numero_escena,
                "lineas": len(lineas_escena),
                "fondo": IMAGENES_ESCENAS_PARQUE[indice_fondo].rsplit("/", 1)[-1],
            })

            ruta_audio_escena = os.path.join(work_dir, f"audio_escena_{numero_escena}.wav")
            concatenar_segmentos(
                [item["audio_path"] for item in lineas_escena], ruta_audio_escena, work_dir,
                nombre_lista=f"lista_audio_{numero_escena}.txt",
                recodificar_audio=True,
            )

            escenas_render.append({
                "lineas": lineas_escena,
                "ruta_fondo": ruta_fondo,
                "ruta_audio": ruta_audio_escena,
                "salida": os.path.join(work_dir, f"escena_{numero_escena:03d}.mp4"),
            })

        # Fase 5: renderizar cada escena COMPLETA en un solo comando de
        # ffmpeg (overlay por personaje con ventanas de tiempo), en
        # paralelo entre escenas — en vez de un archivo de video por
        # cada línea de diálogo y decenas de procesos de ffmpeg.
        #
        # El paralelismo se limita a la cantidad de núcleos disponibles:
        # cada ffmpeg ya usa varios hilos por su cuenta, así que lanzar
        # más renders simultáneos que núcleos los hace pelear entre sí y
        # encima deja al servidor sin CPU para contestar /estado.
        contador_lock = threading.Lock()
        escenas_completadas = 0
        actualizar_estado(
            job_id, escenas_totales=len(escenas_render), escenas_completadas=0
        )

        def _renderizar_escena_item(esc):
            nonlocal escenas_completadas
            renderizar_escena(
                esc["lineas"], rutas_imagenes, esc["ruta_fondo"], esc["ruta_audio"], esc["salida"],
                ancho=ANCHO_LARGO, alto=ALTO_LARGO,
            )
            with contador_lock:
                escenas_completadas += 1
                actualizar_estado(job_id, escenas_completadas=escenas_completadas)
            return esc["salida"]

        workers_render = max(1, min(3, os.cpu_count() or 1))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers_render) as executor:
            segmentos = list(executor.map(_renderizar_escena_item, escenas_render))

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

        texto_miniatura = limpiar_texto_miniatura(metadata.get("texto_miniatura"))
        # El valor limpio vuelve a metadata para que el webhook informe lo
        # mismo que quedó dibujado en la miniatura. Si no, Make recibe el
        # "META|texto_miniatura|..." crudo y lo escribe así en el Sheet.
        metadata["texto_miniatura"] = texto_miniatura
        imagen_miniatura_url = None
        if texto_miniatura:
            color_miniatura_nombre = str(metadata.get("color_miniatura") or "").strip().lower()
            color_franja = COLORES_MINIATURA_RGB.get(color_miniatura_nombre, COLOR_FRANJA_MINIATURA)
            ruta_miniatura = os.path.join(work_dir, "miniatura.jpg")
            # Todos los que hablan, en el orden en que aparecen: es la
            # fila de monigotes que da su carácter a la miniatura.
            personajes_miniatura = list(dict.fromkeys(
                item["imagen_clave"] for item in lineas_validas
            ))
            generar_miniatura(
                [rutas_imagenes[p] for p in personajes_miniatura],
                texto_miniatura,
                ruta_miniatura,
                ruta_fondo=fondos_escena[0] if 0 in fondos_escena else next(iter(fondos_escena.values())),
                color_franja=color_franja,
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
            "avisos_audio": avisos_audio,
            "reparto_escenas": reparto_escenas,
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
        {"hablante": "Doctor", "texto": "...", "audio_url": "...", "numero": 1},
        {"hablante": "Juan", "texto": "...", "audio_url": "...", "numero": 1},
        {"hablante": "Maria", "texto": "...", "audio_url": "...", "numero": 2},
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
    # YouTube pide título y etiquetas sí o sí; sin ellos el Upload corta la
    # ejecución y no se sube nada. Se completan acá para que un campo que el
    # prompt todavía no genera no bloquee el pipeline entero.
    metadata["titulo_seo"] = titulo_para_youtube(metadata)
    metadata["etiquetas_ocultas"] = etiquetas_para_youtube(metadata)
    metadata["descripcion_seo"] = descripcion_para_youtube(metadata)

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
