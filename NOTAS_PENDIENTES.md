# Notas — El Diván / Canal Caricaturas

Contexto para retomar el trabajo en una sesión nueva.
Repo: `tukivirtal/canal_caricaturas`. Canal: <https://www.youtube.com/@eldivanterapia>

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

## Pendiente — todo en Make y Sheets, nada de código

### 1. Faltan las columnas de SEO en la pestaña de videos largos

Hoy la pestaña llega hasta `K: Fecha`. Hay que agregar **al final** (si se
insertan en el medio se corren los índices y el prompt deja de apuntar a
Tema y Concepto):

```
L: Título SEO   M: Descripción SEO   N: Hashtags   O: Etiquetas Ocultas   P: Titulos Usados
```

Y en **P2**, para la lista anti-repetición:

```
=ARRAYFORMULA(IF(A2:A<>""; TEXTJOIN(CHAR(10); TRUE; $L$2:$L); ""))
```

En la pestaña de Shorts esto ya está hecho y funcionando.

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

### 4. Automatización D: título y escritura de vuelta

- **YouTube → Upload a Video**: el título tiene que salir de `titulo_seo`.
- **Google Sheets → Update a Row**: tiene que escribir el título en la
  columna L. **Sin esto la columna P queda siempre vacía y el
  anti-repetición no se activa nunca**, aunque el prompt esté perfecto.

### 5. `texto_miniatura` llega roto desde el Text parser

Llega como `META|texto_miniatura|...` en vez del valor limpio. Revisar el
bubble del Text parser (módulo 113): tiene que ser `$1`, no el bundle
completo ni "Fallback Match". El código ya lo sanea con
`limpiar_texto_miniatura`, así que no arruina el CTR mientras tanto.

### 6. Ajustes de contenido en los prompts

- `texto_miniatura` pide "2 a 4 palabras" y prohíbe el signo de pregunta.
  Con 4 palabras el título se va a dos renglones y achica la fila de
  personajes; y las miniaturas del nicho usan pregunta casi siempre. Pedir
  **2 o 3 palabras** y **permitir el `?`** (el `!` sí conviene evitarlo).
- Decidir si el Doctor unifica voz entre Shorts y largos. Hoy tiene dos
  IDs distintos porque una sonaba baja — con el volumen ya emparejado eso
  dejó de ser una razón, así que se puede elegir por cómo suena.

---

## Trampas conocidas (no revertir sin leer esto)

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

---

## Ya resuelto (para no rehacerlo)

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
En largo la fuente pasó de 30px a 45px sobre 1080p. Y el personaje bajó a
80% del alto con 12% de margen para que el renglón no le caiga sobre las
piernas.

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
