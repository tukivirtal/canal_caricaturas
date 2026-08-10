# Notas — El Diván / Canal Caricaturas

Contexto para retomar el trabajo en una sesión nueva.
Repo: `tukivirtal/canal_caricaturas`. Canal: <https://www.youtube.com/@eldivanterapia>

**Dónde está cada cosa:** este archivo es el estado del pipeline (qué falta,
qué ya se resolvió, qué trampas hay). `ESTRATEGIA.md` es hacia dónde va el
canal y por qué: costos, rediseño del formato, y qué hacer con los videos
publicados.

## Cómo está armado

```
Sheet (fila Pendiente)
   └─ Automatización C (Make): Claude arma el guion → parsers → ElevenLabs → Cloudinary
        └─ POST /fabricar_caricatura  (Short, vertical 1080x1920)
           POST /fabricar_video_largo (horizontal 1920x1080)
                └─ este repo renderiza y sube a Cloudinary
                     └─ webhook → Automatización D (Make): YouTube + Sheet
```

El servicio corre en un Codespace. Para desplegar un cambio:

```bash
git pull origin main
docker build -t canal_caricaturas .
docker stop $(docker ps -q --filter ancestor=canal_caricaturas) 2>/dev/null
docker run -d -p 5000:5000 --env-file .env canal_caricaturas
curl http://localhost:5000/
```

Para seguir un render: `GET /estado/<job_id>` informa la fase y, durante el
render, `escenas_completadas` / `escenas_totales`. El resultado trae además
`lineas_saltadas` (qué líneas se descartaron y por qué) y `avisos_audio`
(qué voz no se pudo emparejar de volumen). Cuando algo sale raro, esos dos
campos suelen tener la respuesta.

---

## ⏸ EN CURSO: migrar la síntesis de voz de Make al servicio

**Este es el trabajo a medio hacer. Leer esto antes que nada.**

El código ya sabe sintetizar las voces por su cuenta (`sintetizar_voz`,
`VOCES_ELEVENLABS`), y la `ELEVENLABS_API_KEY` ya está cargada en el `.env`
del Codespace y desplegada. **Falta migrar Make**, que todavía hace la
síntesis y por eso sigue gastando 3-4 operaciones por línea.

El cambio es compatible hacia atrás: si la línea trae `audio_url` se
descarga como siempre, y solo se sintetiza cuando no viene. Así que hoy
todo funciona igual que antes, pero sin el ahorro.

Los dos pasos que faltan, **en este orden**:

1. En el Body del HTTP de la Automatización C, **sacar `audio_url`** de
   cada línea. Correr y verificar que `lineas_sintetizadas` del webhook
   pase de `0` al total de líneas.
2. Con eso confirmado, **borrar los módulos ElevenLabs y Cloudinary del
   iterador**. Ahí ocurre el ahorro real: de ~500 operaciones por video
   largo a menos de 10.

Hacerlo al revés (borrar los módulos primero) impide distinguir si un
fallo viene de la síntesis o del Body.

`lineas_sintetizadas` es el testigo: `0` significa que Make sigue haciendo
el trabajo caro.

---

## Pendiente — el resto es todo en Make y Sheets, nada de código

### 1. Columnas de SEO en la pestaña de videos largos — HECHO, con secuela

Ya se agregaron `Titulo Seo`, `Descripción Seo` y `Etiquetas`, pero se
insertaron **en el medio** (G, H, I), así que el layout quedó:

```
A ID · B Estado · C Tema · D Concepto · E Personajes · F Escena 1-4
G Titulo Seo · H Descripción Seo · I Etiquetas
J Color miniatura · K Texto miniatura · L Audios · M Video Final URL · N Fecha
```

Eso no rompe el prompt, porque usa `{{1.`2`}}` (Tema, C) y `{{1.`3`}}`
(Concepto, D), que están antes de la inserción. Pero sí corrió los mapeos
del módulo de Google Sheets (ver Trampas).

Falta todavía la columna **`Titulos Usados`** para el anti-repetición, con
la fórmula apuntando a la columna del Título SEO:

```
=ARRAYFORMULA(IF(A2:A<>""; TEXTJOIN(CHAR(10); TRUE; $G$2:$G); ""))
```

Su índice para el prompt hay que contarlo sobre el layout de arriba, no
sobre el viejo. En la pestaña de Shorts esto ya está hecho y funcionando.

### 2. El prompt de video largo no genera los campos de SEO

Emite solo `texto_miniatura`, `color_miniatura` y `personajes_participantes`.
Faltan las cuatro líneas `META|titulo_seo|`, `META|descripcion_seo|`,
`META|hashtags|` y `META|etiquetas_ocultas|`. Por eso llegan `null`.

Mientras tanto el código no deja subir un video sin título
(`titulo_para_youtube` arma uno con el tema de la fila), pero eso es un
paracaídas, no SEO.

En el prompt, `{{TITULOS_YA_USADOS}}` va reemplazado por **`{{1.`15`}}`**
(columna P, contando desde `A`=0).

### 3. Automatización C: parsers y Body del HTTP

Que los Text parsers capturen las 4 líneas `META` nuevas y que el Body las
mande.

### 4. Automatización D: arreglos sueltos en el módulo de Sheets

- `Use column headers as IDs` → **Yes**, y después revisar los mapeos: al
  haber insertado columnas, varios quedaron corridos de posición.
- `Color miniatura` está recibiendo `2. url_video`; debería ser
  `2. color_miniatura`.
- Agregar una **segunda ruta con filtro `estado = error`**, para que los
  renders fallidos dejen rastro en vez de descartarse en silencio.
- El **Update a Row tiene que escribir el Título SEO** en su columna. Sin
  eso, la columna `Titulos Usados` queda siempre vacía y el
  anti-repetición no se activa nunca, aunque el prompt esté perfecto.

El filtro `estado` ya está arreglado (comparaba contra `Completado` con
mayúscula y descartaba todo), y el `Row number` ya usa `fila_hoja`, que es
el correcto.

### 5. `texto_miniatura` llega roto desde el Text parser

Llega como `META|texto_miniatura|...` en vez del valor limpio. Revisar el
bubble del Text parser (módulo 113): tiene que ser `$1`, no el bundle
completo ni "Fallback Match". El código ya lo sanea con
`limpiar_texto_miniatura`, así que no arruina el CTR mientras tanto.

### 6. Ajustes de contenido en los prompts

- `texto_miniatura`: el prompt de **video largo** pide "máx 6 palabras" y el
  de **Shorts** pide "2 a 4", y los dos prohíben el signo de pregunta.
  Confirmado en el primer video real: salió *"El domingo que te come vivo"*,
  6 palabras, que en la miniatura se parte en dos renglones y achica la fila
  de personajes. Pedir **2 o 3 palabras** en los dos prompts, y **permitir
  el `?`** (el `!` sí conviene seguir evitándolo) porque las miniaturas de
  referencia del nicho son preguntas casi siempre.
- **Falta `#shorts` en los hashtags del prompt de Shorts.** Ayuda a que
  YouTube clasifique el video en la superficie correcta, y hoy no se
  genera. Debería ir primero de la lista.
- **Bajar el largo del guion de 110-140 líneas a 70-90** (ver la sección de
  costos): corta a la mitad las operaciones de Make y mejora el ritmo.
- Decidir si el Doctor unifica voz entre Shorts y largos. Hoy tiene dos
  IDs distintos porque una sonaba baja — con el volumen ya emparejado eso
  dejó de ser una razón, así que se puede elegir por cómo suena.
- **Poner validación de datos en la columna `Estado`** (lista desplegable
  `Pendiente` / `Completado`). Una vez se escribió `Pendiete` a mano y esa
  fila quedó invisible para el Search Rows, sin error en ningún lado. Es la
  misma familia de fallo que el filtro `Completado`: comparaciones de texto
  exactas que fallan calladas.

---

## Cuánto cuesta cada video en operaciones de Make

El costo escala **por línea de diálogo**, no por video, porque ElevenLabs y
Cloudinary corren una vez por línea:

| | Operaciones aprox. |
|---|---|
| Short (~20 líneas) | ~45 |
| Video largo (142 líneas) | ~290 |

Un video largo cuesta lo mismo que seis Shorts. En 20 días se consumieron
10.000 operaciones de una suscripción paga, y hay dos causas:

1. **Las corridas fallidas gastan igual.** Toda la depuración (el filtro
   que bloqueaba, YouTube cortando por `tags`, la fórmula de ElevenLabs
   mandada como texto) consumió operaciones sin producir nada.
2. **El prompt pide 110-140 líneas** para 8-10 minutos, o sea turnos de
   ~4 segundos. Bajarlo a **70-90 líneas** con turnos más largos corta el
   costo casi a la mitad, y probablemente mejore el ritmo: el ping-pong
   muy picado cansa en formato largo.

Ese segundo punto es el lever real de escala: es cambiar un número en el
prompt y duplicar la cantidad de videos por suscripción.

## Trampas conocidas (no revertir sin leer esto)

- **Hay DOS `estado` distintos y no son el mismo.** El del *payload* del
  webhook lo escribe `app.py` y va siempre en **minúscula**
  (`completado`, `error`); es el que compara el filtro de la
  Automatización D. El de la *columna B del Sheet* lo escribe el módulo
  de Google Sheets con **`Completado`** en mayúscula y lo lee el Search
  Rows de la Automatización C para elegir la próxima fila. Confundirlos
  cuesta caro: el filtro de D estuvo comparando contra `Completado` y
  descartó en silencio cada webhook, así que la automatización de largos
  entera nunca corrió. Y si se pasan los `Pendiente` del Sheet a
  minúscula, la Automatización C deja de encontrar filas.
- **La Automatización D descarta los errores en silencio.** Su filtro
  solo deja pasar `estado = completado`, pero el código también dispara
  el webhook cuando falla, con `estado = error` y el detalle. Hoy esos
  avisos se pierden: conviene una segunda ruta con filtro `estado =
  error` que los registre.
- **En el módulo de Google Sheets, `Use column headers as IDs` está en
  `No`**, o sea que Make identifica las columnas **por posición**. Al
  insertar columnas en el medio, los valores mapeados se quedan pegados a
  la posición vieja y terminan en la columna equivocada — así fue como
  `Color miniatura` quedó recibiendo `url_video`. Conviene ponerlo en
  `Yes` y revisar los mapeos uno por uno.
- **YouTube exige `tags` para subir.** Si llega vacío, el módulo de
  Upload corta la ejecución y no se sube nada ni se registra nada. El
  código ya completa título, etiquetas y descripción cuando el guion no
  los trae, pero es un piso, no SEO.
- **Los dos flujos parsean distinto, a propósito.** Shorts usa un módulo
  **JSON → Parse JSON** (el prompt devuelve JSON), así que los campos
  viajan mapeados y es el flujo más robusto. Video largo usa cuatro
  **Text parser** sobre el formato de texto plano `META|clave|valor`.
  Unificar largo a JSON sería mejor, pero implica rehacer la cadena
  entera de la Automatización C — es deuda técnica asumida, no un
  pendiente para hacer al pasar.

- **Cada personaje necesita DOS dibujos y no son intercambiables.** El de
  Shorts es la escena de consultorio entera (diván, cuadro, fondo crema) y
  se usa como cuadro completo. El `_LARGO` es el personaje solo sobre
  blanco, para recortarlo por colorkey sobre la escena del parque. Los
  cinco están mapeados en `PERSONAJE_IMAGEN_VIDEO_LARGO`; si a alguno le
  faltara su `_LARGO`, el video largo le pegaría el consultorio encima del
  parque, y sin recortar (el colorkey saca blanco, no crema).
- **El formato de línea del video largo tiene 4 campos y el segundo ya no
  se usa**: `<escena>|<color>|<hablante>|<texto>`. El parser lee por
  posición (`$1`..`$4`), así que sacar el campo de color rompe todo. Va
  siempre `azul`.
- **Nada de ejemplos de contenido en los prompts.** Los ejemplos se
  copian: el prompt de Shorts sugería "Nadie te dice que..." como
  alternativa para dar variedad y el modelo lo usó 6 veces de 15. Las
  reglas van como prohibiciones, y la variedad se logra inyectando los
  títulos ya usados.
- **En el video largo nada se mueve.** Cualquier filtro de ffmpeg que
  dependa del cuadro (scale, colorkey, overlay) se recalcula miles de
  veces sobre imágenes idénticas. Todo lo que sea por imagen se precomputa
  una vez; el render solo codifica.
- **El personaje pisa el pasto y el subtítulo le cruza las piernas: es
  deliberado.** Los dos se disputan la franja de abajo, no se puede tener
  las dos cosas. Se probó subir al personaje (12% de margen) para despejar
  el renglón y quedaba flotando sobre el piso, que se nota mucho más. Las
  piernas son líneas finas y el contorno negro del texto alcanza para
  leerse encima. Si alguien vuelve a subirlo, va a reintroducir el flote.
- **Los subtítulos se cronometran sobre el orden de render, no sobre el
  orden de llegada.** El video agrupa por escena; si la línea de tiempo de
  subtítulos recorre otra secuencia, el texto de un personaje aparece
  mientras en pantalla está el otro.

---

## Banco de temas sin usar

Verificados contra las 18 filas de Shorts y las 2 de largos: ningún tema ni
concepto se repite. Los conceptos son reales y documentados, que es lo que
el prompt exige para el remate.

**Para video largo** (necesitan aguantar que 3 personas los cuenten desde
ángulos distintos; si el tema es muy puntual, los tres dicen lo mismo):

| Tema | Concepto |
|---|---|
| Tener 40 pestañas abiertas y no poder cerrar ninguna porque "las voy a leer" | Atención residual |
| Ver una serie con el celular en la mano y no enterarte de ninguna de las dos | Costo de cambio de tarea |
| Postergar pedir un turno médico por miedo a lo que te digan | Evitación experiencial |
| Comprar para la versión de vos que empieza el lunes: el kit, el curso, los libros | Descuento hiperbólico |
| No poder descansar sin sentir que deberías estar produciendo | Aversión a la inactividad |

**Para Shorts** (micro-conductas concretas que se entienden en el primer
segundo, no temas amplios):

| Persona | Dolor Moderno | Concepto |
|---|---|---|
| Juli | Quedarte despierto hasta las 2 cayéndote de sueño, solo para tener un rato tuyo | Procrastinación del sueño por venganza |
| Fabricio | Buscar el celular por toda la casa teniéndolo en la mano | Ceguera por desatención |
| Juli | Pausar la película cada cinco minutos para buscar de dónde conocés al actor | Intolerancia a la incertidumbre |
| Fabricio | Escuchar un audio de cuatro minutos a doble velocidad y aun así impacientarte | Intolerancia a la frustración |
| Juli | Sentir que todos los demás tienen su vida más resuelta que vos | Ignorancia pluralista |

Ojo con *ignorancia pluralista*: es distinta del síndrome del impostor (que
ya se usó). Aquella es sobre la propia competencia; esta es sobre creerse
el único perdido mientras se asume que el resto la tiene clara. Da buen
remate: la persona con la que te comparás piensa lo mismo de vos.

## Ya resuelto (para no rehacerlo)

**Los subtítulos se desfasaban de a poco.** El síntoma era "arranca bien y
a los dos minutos ya no acompaña a quien habla". `obtener_duracion` leía la
duración **declarada por el contenedor** del MP3, pero lo que suena es el
audio **decodificado**, y difieren ~44 ms por archivo. Sobre 142 líneas eso
daba +5,46 s acumulados; a los 2 minutos ya era ~1,5 s, casi un turno de
diálogo. Ahora cada línea se decodifica a WAV (con su ganancia) y la
duración se mide ahí, donde es exacta y es el mismo archivo que se
concatena. Verificado en video real: el diálogo acompaña a los personajes.

Dos hipótesis que se descartaron midiendo, para que nadie las repita: (1)
que el emparejado de volumen introdujera desfase — 0,000 s sobre 20 líneas;
(2) que el demuxer `concat` corriera el video una línea. Esta segunda llegó
a "medirse" como cierta, pero era un **error del banco de pruebas**: los
colores que identificaban cada cuadro estaban separados por 8 y la
tolerancia era 12, así que siempre matcheaba el índice anterior. Con
colores separados: 0/30 líneas mal, desvío máximo 1 cuadro. El armado de
video estaba bien.

**Respaldos de SEO en el código.** `titulo_para_youtube`,
`etiquetas_para_youtube` y `descripcion_para_youtube` completan esos campos
con el tema y el concepto de la fila cuando el guion no los trae,
respetando los límites de YouTube (100 caracteres el título, 500 las
etiquetas). Es lo que evita que un campo faltante tumbe el pipeline entero.

**Render de video largo: de +1 hora a ~30 s.** Los personajes entraban al
filtro con `-loop 1`, o sea como stream infinito, así que el `scale` +
`colorkey` de cada uno se recalculaba en cada cuadro aunque solo uno
estuviera visible. Ahora los recortes y fondos se preparan una vez
(`preparar_recorte_personaje` / `preparar_fondo_escena`) y cada escena se
arma pre-componiendo un cuadro por personaje con PIL + demuxer concat.
Medido: escena de 2 min con 5 personajes, 461 s → 12 s; guion de 123 líneas
(8:12 de video), 29 s. Además 12 fps (son imágenes fijas), `-preset
ultrafast`, paralelismo limitado a `os.cpu_count()` y descarga solo de los
personajes que hablan.

**Miniatura al esquema del nicho.** Antes recortaba la imagen vertical del
personaje a 16:9 (un torso sobre negro) y le pegaba una franja de color con
texto chico sin contorno. Ahora: fondo de escena a sangre, texto gancho
arriba a todo el ancho en amarillo con contorno negro grueso, y los
personajes en fila abajo. Tipografía **Luckiest Guy** (`RUTA_FUENTE_MINIATURA`;
`Baloo2-ExtraBold` es la alternativa). El texto se calcula primero y se
queda con su banda: si no, un título de dos líneas tapa las cabezas.
`_recortar_fondo_blanco` saca solo el blanco conectado al borde, para no
dejar la cara del personaje transparente.

**Subtítulos.** El estilo pedía `Arial`, que no está en `python:3.11-slim`;
libass caía a cualquier fuente y el render cambiaba entre rebuilds. Ahora el
Dockerfile instala `fonts-dejavu-core` y se pide `DejaVu Sans` en negrita.
En largo la fuente pasó de 30px a 45px sobre 1080p, con contorno 4.

**Subtítulos sincronizados con lo que se ve.** El video se arma agrupando
las líneas por escena y concatenando las escenas en orden ascendente, pero
la línea de tiempo de subtítulos se calculaba sobre el orden en que las
líneas habían llegado de Make. Si el guion no venía perfectamente agrupado,
las dos secuencias divergían y el subtítulo mostraba la línea de un
personaje mientras en pantalla estaba el otro (visto en el primer video
real). Ahora se cronometra sobre la misma secuencia que se renderiza, así
que no depende de cómo llegue el guion.

Descartado en el camino: se sospechó que el emparejado de volumen
introducía desfase, porque las duraciones se miden sobre el MP3 y el audio
concatenado es el WAV normalizado. Medido sobre 20 líneas: 0,000 s. No era
eso.

**Volumen emparejado entre personajes.** Las voces de ElevenLabs entregan a
niveles muy distintos (medido: 18 dB) y YouTube atenúa lo fuerte pero no
levanta lo flojo. Se mide el loudness de cada línea y se aplica una ganancia
**por personaje** hacia -16 LUFS — por personaje y no por línea porque en un
"Ajá." de medio segundo la medición no es confiable y por línea se aplasta
la intención. Tope de -12/+24 dB, y cuando el tope recorta lo informa en
`avisos_audio`.

**Relleno del Short sin costura.** La escena es 3:4 y el video 9:16, así que
se rellena arriba y abajo. El color era fijo, y cualquier dibujo con otro
crema mostraba la banda. `color_borde_imagen` lo toma del borde de la propia
imagen.

**Escenas del parque.** 3 imágenes completas en `IMAGENES_ESCENAS_PARQUE`
(banco+árbol+mesa, laguna, plaza con juegos). Reemplazaron a los 4 props
sueltos que se componían sobre un color rotativo. Las 4 escenas del guion
ciclan sobre esas 3; si se quiere una cuarta distinta, se agrega a la lista.

**Otros:** formato horizontal 1920x1080, fix de audio desincronizado
(concatenación MP3 por stream-copy arrastraba padding, ahora se reencodea a
WAV), reintentos en `descargar_archivo`, `stderr` real en los errores de
ffmpeg, y personajes Fabricio y Juli completos en ambos formatos.
