# Estrategia — El Diván | Terapia con Humor

Estado al momento de escribir esto: **173 suscriptores, 23 videos**, los tres
videos largos con 0-1 visualizaciones. Canal: <https://www.youtube.com/@eldivanterapia>

Referencia principal: **The Archetype** (34.200 suscriptores), videos de
~19 minutos, mismo nicho de psicología pero con personajes dibujados a
color y con ropa, no monigotes de línea.

---

## 1. El costo en Make es el problema número uno

No es un problema de dinero: es un problema de **techo**. Con el diseño
actual no se puede producir volumen aunque todo lo demás funcione.

Cada línea de diálogo atraviesa varios módulos dentro del iterador (Tools
para el `voice_id`, la búsqueda del Voice_ID, ElevenLabs y Cloudinary), o
sea **3-4 operaciones por línea**:

| | Líneas | Operaciones aprox. |
|---|---|---|
| Short | ~20 | 60-80 |
| Video largo | 142 | **400-570** |

Con 10.000 operaciones al mes eso da **~20 videos largos**, contando las
corridas fallidas, que gastan igual. Coincide con haber agotado la
suscripción en 20 días.

### El arreglo de fondo: mover el TTS al código

Hoy Make hace la síntesis de voz y sube cada audio a Cloudinary para que el
servicio los descargue. Pero **el servicio solo necesita los audios en
disco** — el viaje por Cloudinary es puro tránsito, y es la mitad del costo.

Si `app.py` llama a ElevenLabs directamente:

| | Operaciones de Make por video largo |
|---|---|
| Hoy | 400-570 |
| Con el TTS en el código | **~7-10** |

Es un factor de ~50. Las 10.000 operaciones pasarían de ~20 videos a
prácticamente ilimitados, porque el costo dejaría de escalar con la
cantidad de líneas.

Make quedaría haciendo lo que hace bien: leer el Sheet, llamar a Claude,
parsear y disparar el HTTP. El trabajo repetitivo por línea se va al
servicio, donde una iteración no cuesta nada.

**Lo que hay que decidir antes:** la API key de ElevenLabs pasa a vivir en
el `.env` del Codespace en vez de en la conexión de Make. El gasto de
ElevenLabs no cambia (se factura por caracteres, no por llamada).

### El lever barato mientras tanto

El prompt pide 110-140 líneas para 8-10 minutos, o sea turnos de ~4
segundos. Con **70-90 líneas** de turnos más largos se corta el costo casi
a la mitad, y el diálogo probablemente respire mejor: el ping-pong muy
picado cansa en formato largo.

---

## 2. Lo que se ve mal en el canal ahora mismo

### Las tres miniaturas son casi idénticas

Mismo fondo (banco + árbol), misma fila de personajes, mismo tratamiento
de texto. En la grilla del canal parecen el mismo video tres veces.

Eso hace dos daños a la vez:

1. **Mata el CTR.** El espectador no puede distinguir un video de otro, así
   que no tiene motivo para elegir ninguno.
2. **Es exactamente el perfil que persigue la política de contenido
   inauténtico de YouTube**: "clones de plantilla donde solo cambia el
   título".

El fondo de escena de la miniatura hoy sale siempre de la primera escena, y
la primera escena siempre cae en el mismo dibujo. Alcanza con variar eso
para romper el patrón.

### Dos de los tres títulos no son títulos

- "Revisar el chat todo el tiempo"
- "Postergar pedir un turno médico por miedo a lo que te puedan decir"

Eso es el campo **Tema** del Sheet, no un título de YouTube. El tercero
("Por qué el domingo a la tarde te da angustia | Ansiedad anticipatoria")
sí lo es, y se nota la diferencia.

Hay que revisar qué mapea el módulo **YouTube → Upload a Video** en el
campo de título: debería ser `titulo_seo`, no `tema`.

---

## 3. Qué hacer con los videos que ya están

**Ponerlos en privado, no borrarlos.**

Con 0-1 visualizaciones no aportan nada, pero tampoco arrastran un
historial negativo grande. La razón para ocultarlos es otra: los 15 Shorts
viejos tienen **títulos y descripciones que hablan de otro video** (el
prompt no recibía el Dolor Moderno, así que improvisaba). Eso le enseña al
clasificador de YouTube algo falso sobre el canal, y es acumulativo.

En privado siguen existiendo por si se quieren recuperar los metadatos; en
la papelera, no.

---

## 4. El rediseño del formato largo

Lo que hace The Archetype y este canal todavía no:

| | The Archetype | El Diván hoy |
|---|---|---|
| Duración | ~19 min | 9-11 min |
| Personajes | A color, con ropa, rasgos | Monigotes de línea |
| Escenas | Muchas, con props narrativos | 4, repitiendo 3 fondos |
| Audio | Pistas multi-idioma | Solo español |

**El cambio de mayor impacto y menor costo es la cantidad de escenas.**
Renderizar cuesta ~30 segundos, y cada cuadro nuevo es una composición de
PIL: pasar de 4 escenas a 10-15 cambios visuales **no cuesta prácticamente
nada de cómputo**. El único límite real es tener más dibujos de fondo.

Ese cambio ataca las tres cosas a la vez: sube la retención, rompe el
patrón de plantilla, y hace que las miniaturas se diferencien solas.

El rediseño de los personajes (a color, con ropa) es más caro porque hay
que regenerar los diez dibujos —cinco de Shorts y cinco de largo— y
mantener la coherencia entre ellos. Conviene hacerlo **después** de que las
escenas estén resueltas, no antes.

**La pista multi-idioma es la palanca de alcance más grande y nadie la
está usando en este nicho en español.** YouTube permite subir audio en
varios idiomas al mismo video. Con el TTS ya en el código, generar la
versión en inglés del mismo guion es casi gratis: mismo render, otra pista
de audio.

---

## 5. Make vs n8n

**Quedarse en Make**, y no por comodidad.

El problema no es Make: es *cuánto trabajo por línea* se le está pidiendo.
Migrar a n8n con la misma arquitectura mueve el costo de operaciones al
costo de un servidor, y hay que administrar ese servidor. Con el TTS en el
código, Make baja a ~10 operaciones por video y el argumento para migrar
desaparece.

Hostinger sirve para otra cosa: si en algún momento el Codespace queda
chico, ahí se puede alojar el servicio de render. Pero eso es una decisión
de infraestructura, no de automatización.

---

## 6. El sistema de contenidos

El ciclo TikTok → Instagram → Facebook → YouTube ya se conoce. Lo que falta
es la pieza que lo vuelve un sistema y no cuatro publicaciones sueltas:

**Los Shorts no son para monetizar, son para adquirir.** Con RPM de $0,01 a
$0,07 por mil vistas, un millón de vistas en Shorts deja entre $10 y $70.
El video largo rinde entre $1 y $8 por mil. Los canales que combinan ambos
formatos crecen 41% más rápido que los que hacen uno solo.

Entonces: **el Short tiene que ser un recorte del video largo**, no una
pieza independiente. El mejor momento de la sesión grupal —el remate
psicológico— sale como Short, y el Short lleva al largo. Hoy son dos
pipelines que no se hablan.

Eso además baja el costo: un Short recortado del largo no necesita generar
audio nuevo.

---

## Orden recomendado

1. **Mover el TTS al código.** Destraba el techo de producción; sin esto
   todo lo demás está limitado a ~20 videos por mes.
2. **Arreglar el título en la Automatización D** (`titulo_seo`, no `tema`).
3. **Poner en privado los videos viejos** con metadatos incorrectos.
4. **Más escenas por video**, que es donde está la ventaja competitiva real
   y ya está pago en infraestructura.
5. Recién después: rediseño de personajes, pista en inglés, y Shorts
   recortados del largo.
