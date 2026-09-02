# El Panorama

Un panorama diario de noticias que se arma solo. Todas las mañanas, alrededor de las
6:15 (hora Uruguay), un robot gratuito de GitHub busca las noticias de las últimas 24
horas de tus temas, arma una página y la publica en una URL fija. Tu computadora no
participa: puede estar apagada.

**La URL de todos los días:** https://gonzalohzw.github.io/panorama-diario/

---

## Cómo editar los temas

Todo vive en **un solo archivo: [`temas.yml`](temas.yml)**. Ahí están las categorías,
las búsquedas de Google News y los feeds de medios. El archivo tiene instrucciones
adentro, pero en resumen:

- **Agregar una búsqueda a una categoría**: sumá una línea `- q: 'lo que quieras buscar'`
  abajo de `busquedas:`. Si la querés en inglés, agregale `idioma: en` en la línea
  siguiente (mirá los ejemplos que ya hay).
- **Agregar o sacar una categoría entera**: copiá el bloque de una existente y cambiale
  `id`, `nombre`, búsquedas y feeds. Para sacarla, borrá el bloque (o ponele `#` adelante
  a todas sus líneas, que es reversible).
- **Activar la categoría de café y gastronomía**: al final del archivo está escrita con
  `#` adelante. Sacale los `#` y listo.
- **Cambiar cuántas notas salen**: son los números de arriba del archivo
  (`max_por_categoria`, `cantidad_destacados`, etc.).

Después de editar, guardá y subí el cambio a GitHub (podés editarlo directo en la web de
GitHub: abrís `temas.yml`, tocás el lápiz, guardás con "Commit changes"). El panorama del
día siguiente ya sale con los cambios.

## Cómo cambiar el horario

El horario está en [`.github/workflows/panorama.yml`](.github/workflows/panorama.yml), en
la línea del `cron`. **Va en hora UTC**; Uruguay es UTC−3 todo el año, así que hay que
sumarle 3 horas a la hora uruguaya que quieras:

| Querés que corra a las (UY) | Poné en el cron |
|---|---|
| 5:15 | `"15 8 * * *"` |
| 6:15 (actual) | `"15 9 * * *"` |
| 7:00 | `"0 10 * * *"` |
| 12:00 | `"0 15 * * *"` |

El formato es `"minutos hora * * *"`. Ojo: GitHub no es puntual al minuto — puede
arrancar hasta 20–40 minutos tarde. Conviene programarlo un rato antes de la hora en
que lo querés leer.

## Si un día el panorama no se generó

La página nunca se rompe: si el robot no corrió, simplemente vas a ver el panorama de
ayer (fijate la fecha del pie de página). Para regenerarlo a mano:

1. Entrá al repositorio en GitHub y tocá la pestaña **Actions** (arriba).
2. En la lista de la izquierda, elegí **Panorama diario**.
3. Tocá el botón **Run workflow** (a la derecha) y confirmá en el botón verde.
4. En 2–3 minutos la página se actualiza. Refrescá la URL del panorama.

Si en Actions ves una corrida con una **cruz roja**, tocala, abrí el paso que falló, y
copiá el texto del error para pedir ayuda (por ejemplo, pegándomelo a mí en una sesión
de Claude). Lo más común es que una fuente cambió su dirección: se arregla editando o
borrando esa línea en `temas.yml`.

## Cómo correr el panorama en tu computadora (opcional)

Solo hace falta para probar cambios sin esperar al día siguiente:

```bash
cd ~/Desktop/Gonzalo/ClaudeCode/panorama-diario
venv/bin/python generar.py
```

El resultado queda en `docs/index.html` (se abre con doble click).
La primera vez, antes, hay que crear el entorno:

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
```

## Si GitHub Desktop habla de "conflicts" al subir

Pasa cuando se trabajó en la computadora el mismo día que el robot ya había
generado su panorama en la nube: los dos escribieron los mismos archivos de
`docs/`. **No hay nada valioso en juego** — esos archivos se regeneran solos
cada mañana, cualquier versión sirve. La salida simple: en el aviso de
conflictos, usá la flechita al lado de "Open in editor" y elegí quedarte con
una de las dos versiones (cualquiera), después "Continue Merge" y "Push
origin". Y para que no vuelva a pasar: antes de trabajar en la computadora,
tocá **Fetch origin / Pull origin** en GitHub Desktop, que trae primero lo
que hizo la nube. Editando `temas.yml` directo en la web de GitHub esto no
pasa nunca.

## Cómo está armado

```
panorama-diario/
├── temas.yml                      ← lo único que se edita a mano
├── generar.py                     ← el script que arma todo
├── plantilla.html                 ← el diseño de la página
├── plantilla_historico.html       ← el diseño de la lista de días anteriores
├── .github/workflows/panorama.yml ← la alarma diaria (hora en UTC)
├── docs/                          ← lo que GitHub Pages publica
│   ├── index.html                 ← el panorama de hoy
│   └── historico/                 ← un archivo por día + su índice
└── README.md                      ← este archivo
```

Qué hace `generar.py`, en criollo: baja todas las fuentes en paralelo (si una falla,
la anota al pie de la página y sigue), tira lo más viejo que 24 horas, junta las notas
que cuentan la misma historia y cuenta cuántos medios la cubrieron — ese conteo define
la relevancia: los **Destacados del día** son las 5 historias con más cobertura. Después
limita cada categoría a 8–10 notas con variedad de medios y escribe el HTML.

## Versión 2 (pendiente, ya prevista)

La idea: que cada nota tenga un resumen propio generado con IA y que el día abra con un
párrafo editorial. El código ya está preparado — en `generar.py` hay una función
`enriquecer()` vacía donde va esa lógica, y la plantilla ya sabe mostrar el campo
`resumen_ia` de cada nota y el bloque `editorial` cuando existan. No hay que tocar nada
más que esa función.
