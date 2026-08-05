# Notas pendientes — El Diván / Canal Caricaturas

Contexto para retomar el trabajo en una sesión nueva. Repo: `tukivirtal/canal_caricaturas`,
branch de trabajo habitual: `claude/access-permissions-test-4r3j5v`.

## En proceso ahora mismo

1. **Regenerando las 4 imágenes de escena del video largo (parque)** con diseño
   profesional (colores ricos, degradé de cielo, composición plana y frontal,
   sin personas, franja de pasto pareja de punta a punta).
   - Escena "banco + árbol + mesa de picnic": ✅ lista.
   - Escena "laguna": prompt dado, pendiente de generar.
   - Escena "plaza con juegos": prompt dado, pendiente de generar.
   - Falta una cuarta escena si se quiere mantener el set original de 4.

2. **Simplificar `generar_fondo_escena` / `renderizar_escena` en `app.py`**:
   una vez estén las URLs de Cloudinary de las escenas nuevas, sacar la capa
   de color rotativo (`color_fondo`) y el colorkey sobre el prop — las
   imágenes nuevas ya traen su propio cielo con degradé, no hace falta
   componerlas sobre un color aparte. Usar cada imagen de escena directamente
   como fondo completo.

## Bugs/gaps pendientes de resolver

3. **Miniatura con texto roto en Make**: el campo `texto_miniatura` llega
   como `META|texto_miniatura|...` en vez del valor limpio. Revisar el
   bubble insertado en el Text parser (módulo 113) del Body del HTTP de la
   Automatización C — tiene que ser específicamente `$1`, no el bundle
   completo ni "Fallback Match".

4. **Faltan campos de SEO en video largo**: `titulo_seo`, `descripcion_seo`,
   `hashtags`, `etiquetas_ocultas` llegan `null` en el resultado — nunca se
   agregaron al prompt de Claude (formato texto plano `META|...`) ni al Body
   del HTTP de la Automatización C. Sin `titulo_seo`, el módulo de YouTube
   Upload en la Automatización D no tiene título para el video.

5. **Rediseñar `generar_miniatura()` en el código**: pasar del esquema
   actual (franja de color sólido + texto) a un esquema tipo Zenn — fondo de
   escena completo (reutilizando las imágenes nuevas) + texto grande
   amarillo/contorno negro montado directamente encima, sin caja de fondo.

## Ya resuelto en esta sesión (para referencia, no repetir)

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
