# Notas pendientes — El Diván / Canal Caricaturas

Contexto para retomar el trabajo en una sesión nueva. Repo: `tukivirtal/canal_caricaturas`,
branch de trabajo habitual: `claude/access-permissions-test-4r3j5v`.

## Bugs/gaps pendientes de resolver

1. **Miniatura con texto roto en Make**: el campo `texto_miniatura` llega
   como `META|texto_miniatura|...` en vez del valor limpio. Revisar el
   bubble insertado en el Text parser (módulo 113) del Body del HTTP de la
   Automatización C — tiene que ser específicamente `$1`, no el bundle
   completo ni "Fallback Match".

2. **Faltan campos de SEO en video largo**: `titulo_seo`, `descripcion_seo`,
   `hashtags`, `etiquetas_ocultas` llegan `null` en el resultado — nunca se
   agregaron al prompt de Claude (formato texto plano `META|...`) ni al Body
   del HTTP de la Automatización C. Sin `titulo_seo`, el módulo de YouTube
   Upload en la Automatización D no tiene título para el video.

3. **Rediseñar `generar_miniatura()` en el código**: pasar del esquema
   actual (franja de color sólido + texto) a un esquema tipo Zenn — fondo de
   escena completo (reutilizando las imágenes nuevas) + texto grande
   amarillo/contorno negro montado directamente encima, sin caja de fondo.
   (Ya tenemos las 3 imágenes de escena nuevas listas — ver commit de
   simplificación de fondo de video largo.)

## Ya resuelto en esta sesión (para referencia, no repetir)

- Las 3 imágenes de escena nuevas del parque quedaron listas y cargadas en
  `IMAGENES_ESCENAS_PARQUE` (`app.py`): banco+árbol+mesa de picnic, laguna,
  plaza con juegos (URLs de Cloudinary `dibujo_1_aryiyb`, `dibujo2_ds03jq`,
  `dibujo3_s1y2w5`). El set original de props separados por color/escena
  quedó reemplazado por estas 3 (antes eran 4 props sueltos); si se quiere
  una escena distinta más adelante, se agrega un cuarto elemento a la lista.
- Simplificado el fondo de escena del video largo: se eliminó
  `generar_fondo_escena` (color sólido + colorkey sobre el prop) y el campo
  `color_fondo` por línea — ahora cada escena usa directamente su imagen de
  `IMAGENES_ESCENAS_PARQUE` como fondo completo, sin composición previa ni
  proceso de ffmpeg extra por escena.
- Endpoint `/fabricar_video_largo` funcionando end-to-end (probado con 135
  líneas reales, `estado: completado`).
- Formato horizontal 1920x1080 para video largo (antes heredaba el vertical
  de los Shorts).
- Renderizado optimizado: de ~80-135 procesos de ffmpeg (uno por línea) a
  ~4 (uno por escena), usando overlay de personajes por ventana de tiempo
  (`enable='between(t,inicio,fin)'`) en vez de segmentos individuales.
- Fix: fondo con patrón a cuadros (el prop no tenía colorkey aplicado).
- Fix: personajes desincronizados a mitad de video (concatenación de audio
  MP3 por stream-copy arrastraba padding — ahora se reencodea a WAV).
- Reintentos automáticos en `descargar_archivo` (hasta 3 intentos).
- Mensajes de error de ffmpeg/ffprobe ahora incluyen el `stderr` real
  (antes solo decían "exit status N" sin detalle).
- Personajes Fabricio y Juli creados (Shorts y video largo).
- Variantes "_LARGO" de Doctor/Juan/Maria con voces e imágenes propias para
  la sesión grupal de video largo.
- Prompt de Shorts: instrucción para evitar repetir el tema de "likes" /
  redes sociales cuando no corresponde al Dolor Moderno de la fila.
- 3 filas nuevas cargadas en el Sheet de Shorts, con el tema de "likes" en
  pausa.
