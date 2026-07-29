import os, subprocess, tempfile, time

ANCHO = 1080
ALTO = 1920
NUM_SEGMENTOS = 20       # 20 "lineas" de dialogo
DURACION_SEGMENTO = 30   # 30s cada una -> 10 minutos de video total

def crear_imagen_prueba(path, color):
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={ANCHO}x{ALTO}",
        "-frames:v", "1", path
    ], check=True, capture_output=True)

def crear_audio_prueba(path, duracion):
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-t", str(duracion), "-q:a", "9", path
    ], check=True, capture_output=True)

def crear_segmento(imagen_path, audio_path, salida_path):
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", imagen_path,
        "-i", audio_path,
        "-c:v", "libx264", "-tune", "stillimage",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p", "-shortest",
        "-vf", f"scale={ANCHO}:{ALTO}:force_original_aspect_ratio=decrease,"
               f"pad={ANCHO}:{ALTO}:(ow-iw)/2:(oh-ih)/2:color=0xF5E6D3",
        "-preset", "veryfast",
        salida_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)

def concatenar_segmentos(lista_segmentos, salida_path, work_dir):
    lista_txt = os.path.join(work_dir, "lista.txt")
    with open(lista_txt, "w") as f:
        for seg in lista_segmentos:
            f.write(f"file '{seg}'\n")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lista_txt, "-c", "copy", salida_path]
    subprocess.run(cmd, check=True, capture_output=True)

def main():
    work_dir = tempfile.mkdtemp(prefix="test_render_")
    print(f"Directorio de trabajo: {work_dir}")
    colores = ["blue", "green", "red"]
    inicio = time.time()

    imagenes = []
    for i, color in enumerate(colores):
        img_path = os.path.join(work_dir, f"img_{i}.png")
        crear_imagen_prueba(img_path, color)
        imagenes.append(img_path)

    segmentos = []
    for i in range(NUM_SEGMENTOS):
        print(f"Generando segmento {i+1}/{NUM_SEGMENTOS}...")
        audio_path = os.path.join(work_dir, f"audio_{i}.mp3")
        crear_audio_prueba(audio_path, DURACION_SEGMENTO)
        segmento_path = os.path.join(work_dir, f"segmento_{i:03d}.mp4")
        crear_segmento(imagenes[i % 3], audio_path, segmento_path)
        segmentos.append(segmento_path)

    print("Concatenando segmentos...")
    salida = os.path.join(work_dir, "final.mp4")
    concatenar_segmentos(segmentos, salida, work_dir)

    duracion_total = time.time() - inicio
    tamano_mb = os.path.getsize(salida) / (1024 * 1024)
    print(f"\nOK - Video final generado: {salida}")
    print(f"Tamano: {tamano_mb:.1f} MB")
    print(f"Tiempo total: {duracion_total:.1f}s")

if __name__ == "__main__":
    main()
