# Notas pendientes — El Diván / Canal Caricaturas

Contexto para retomar el trabajo en una sesión nueva. Repo: `tukivirtal/canal_caricaturas`,
branch de trabajo habitual: `claude/access-permissions-test-4r3j5v`.

## Bugs/gaps pendientes de resolver

1. **Miniatura con texto roto en Make**: el campo `texto_miniatura` llega
   como `META|texto_miniatura|...` en vez del valor limpio. Revisar el
   bubble insertado en el Text parser (módulo 113) del Body del HTTP de la
   Automatización C — tiene que ser específicamente `$1`, no el bundle
   completo ni "Fallback Match".
   (El código ya lo sanea con `limpiar_texto_miniatura`, así que no
   arruina el CTR mientras tanto, pero el arreglo de fondo sigue siendo
   en Make.)

2. **Faltan campos de SEO en video largo**: `titulo_seo`, `descripcion_seo`,
   `hashtags`, `etiquetas_ocultas` llegan `null` en el resultado — nunca se
   agregaron al prompt de Claude (formato texto plano `META|...`) ni al Body
   del HTTP de la Automatización C. Sin `titulo_seo`, el módulo de YouTube
   Upload en la Automatización D no tiene título para el video.

3. **Ajustar el prompt de Claude para el texto de miniatura**: hoy pide
   "2 a 4 palabras" y **prohíbe signos de pregunta**. Las dos cosas juegan
   en contra del nicho:
   - Con 4 palabras el título se va a dos renglones y achica la fila de
     personajes. Conviene pedir **2 o 3 palabras**, que entran en una sola
     línea y dejan los monigotes grandes.
   - Las miniaturas de referencia del nicho usan pregunta casi siempre
     ("PEAK AGE?", "WHY YOU?"). Habría que **permitir el signo de
     pregunta** en `texto_miniatura` (el de exclamación sí conviene
     seguir evitándolo).

## Ya resuelto en esta sesión (para referencia, no repetir)

- **Volumen emparejado entre personajes** (Shorts y video largo). Las
  voces de ElevenLabs entregan a niveles muy distintos, y YouTube atenúa
  lo que llega fuerte pero NO levanta lo que llega bajo: el personaje
  flojo suena flojo para siempre. Ahora se mide el loudness de cada línea
  y se aplica **una ganancia por personaje** (no por línea: en un "Ajá."
  de medio segundo la medición no es confiable, y por línea se aplasta la
  intención). Objetivo -16 LUFS, tope de seguridad -12/+24 dB.
  - Si el tope recorta la corrección, **se avisa** en el campo
    `avisos_audio` del resultado en vez de dejar la brecha en silencio:
    quiere decir que esa voz está mal grabada de origen.
  - Con esto, elegir voces "más fuertes" deja de ser necesario: se puede
    volver a una sola voz por personaje en Shorts y en largos, elegida
    por cómo suena y no por cuánto entrega.
  - El flujo de Shorts pasó a fases (parsear → descargar en paralelo →
    medir → renderizar), igual que el de largos. De paso las descargas
    dejaron de ser secuenciales.

- **Subtítulos prolijos**: el estilo declaraba `Arial`, que no está en la
  imagen `python:3.11-slim` — libass caía a cualquier fuente disponible y
  el render cambiaba de un rebuild a otro. Ahora el Dockerfile instala
  `fonts-dejavu-core` y el estilo pide `DejaVu Sans` en negrita. En video
  largo la fuente pasó de 2.8% a 4.2% del alto (30px → 45px sobre 1080p)
  y el contorno de 3 a 4.
- **El subtítulo ya no cae sobre las piernas del personaje**: con el pie a
  5% del borde, el personaje llegaba a y=1026 y el renglón quedaba encima.
  Ahora mide 80% del alto con 12% de margen: pie en y=950, texto debajo.
- **Se fue la banda del relleno en los Shorts**: la escena es 3:4 y el
  video 9:16, así que se rellena arriba y abajo. El color era fijo
  (`#F5E6D3`), y cualquier dibujo con otro crema mostraba la costura.
  `color_borde_imagen` toma el color del borde de la propia imagen, así
  que cada personaje rellena con el suyo y no hay nada que medir a mano.

- **Dibujos de Shorts de Fabricio y Juli cargados**, en el estilo de la
  serie (escena de consultorio entera, diván, cuadro, fondo crema). Ojo:
  cada personaje necesita DOS dibujos y no son intercambiables — el de
  Shorts se usa como cuadro completo, y el `_LARGO` es el personaje solo
  sobre blanco para recortarlo por colorkey sobre el parque. Los cinco
  están mapeados en `PERSONAJE_IMAGEN_VIDEO_LARGO`; si a alguno le faltara
  su `_LARGO`, el video largo le pegaría el consultorio sobre el parque.

- **Miniatura rediseñada al esquema del nicho**: texto gancho arriba,
  enorme y a todo el ancho, en amarillo con contorno negro grueso, y los
  personajes que participan parados en fila abajo sobre el fondo de
  escena. Se fue la franja de color con letras chicas adentro, y se dejó
  de recortar la imagen VERTICAL del personaje a 16:9 (eso producía un
  torso sobre negro, ilegible como gancho).
  - Tipografía nueva: **Luckiest Guy** (display redondeada, el registro
    del nicho) en vez de Anton, que es condensada tipo prensa. Anton
    quedó para los subtítulos. Cambiar de fuente es cambiar
    `RUTA_FUENTE_MINIATURA`; `Baloo2-ExtraBold` es la alternativa.
  - El texto se calcula primero y se queda con su banda de arriba: si no,
    un título de dos líneas termina tapando las cabezas.
  - `_recortar_fondo_blanco` saca solo el blanco conectado al borde, así
    la cara del personaje no queda transparente.
- **Render de video largo: de +1 hora a ~1 minuto.** El costo dominante no
  era el hardware: los personajes entraban al filtro con `-loop 1` (stream
  infinito) y el `scale` + `colorkey` de CADA personaje se recalculaba en
  CADA cuadro, aunque solo uno estuviera visible y las imágenes nunca
  cambien. Ahora los recortes y los fondos se preparan una sola vez
  (`preparar_recorte_personaje` / `preparar_fondo_escena`) y cada escena se
  arma pre-componiendo un cuadro por personaje con PIL + demuxer concat,
  sin composición por cuadro. Medido: escena de 2 min con 5 personajes,
  461 s → 12 s; guion completo de 123 líneas (8:12 de video), 48 s.
  También bajó a 12 fps (son imágenes fijas) y `-preset ultrafast`.
- `/estado` ahora informa `escenas_totales` y `escenas_completadas`, para
  ver el progreso real sin adivinar con `docker stats`.
- El paralelismo de render se limita a `os.cpu_count()`: antes lanzaba 3
  ffmpeg simultáneos en un Codespace de 2 núcleos y se peleaban entre sí,
  dejando al servidor sin CPU ni para contestar `/estado`.
- Solo se descargan las imágenes de los personajes que hablan en el guion
  (antes bajaba las 8 siempre).

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
