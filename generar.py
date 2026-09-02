#!/usr/bin/env python3
"""
Genera el panorama diario de noticias.

Flujo completo:
    1. leer temas.yml
    2. recolectar: bajar todos los feeds (Google News + medios directos) en paralelo
    3. procesar: filtrar por antigüedad, deduplicar, agrupar historias, rankear
    4. enriquecer: paso reservado para la versión 2 (resúmenes con IA); hoy no hace nada
    5. renderizar: escribir docs/index.html + copia en docs/historico/<fecha>.html

Si una fuente falla, se anota y se sigue con el resto. El script solo termina
con error si no pudo generar nada en absoluto.
"""

import html
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from zoneinfo import ZoneInfo

import feedparser
import requests
import yaml
from jinja2 import Environment, FileSystemLoader

RAIZ = Path(__file__).resolve().parent
DOCS = RAIZ / "docs"
HISTORICO = DOCS / "historico"
ZONA = ZoneInfo("America/Montevideo")

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT = 20  # segundos por fuente

# Palabras que no aportan al comparar titulares (para detectar duplicados)
STOPWORDS = set(
    """a al ante bajo cabe con contra de del desde durante en entre hacia hasta
    la las lo los mediante para por segun según sin so sobre tras un una unas
    unos y o u e ni que se su sus es son fue era esta este estos estas asi así
    ya mas más the a an of in on at for to and or is are was be with from by
    as its it his her their new says say said tras como cuando donde qué cómo
    por qué per cent""".split()
)

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


@dataclass
class Nota:
    titulo: str
    link: str
    medio: str
    fecha: datetime            # con zona horaria de Montevideo
    bajada: str = ""
    imagen: str = ""
    categoria_id: str = ""
    categoria_nombre: str = ""
    es_directo: bool = False   # True si viene de un feed de medio, no de Google News
    cobertura: int = 1         # cuántos medios distintos cubrieron esta historia
    resumen_ia: str = ""       # reservado para la versión 2
    tokens: frozenset = field(default_factory=frozenset)
    entidades: frozenset = field(default_factory=frozenset)  # nombres propios del titular

    @property
    def hora(self) -> str:
        hoy = datetime.now(ZONA).date()
        if self.fecha.date() == hoy:
            return self.fecha.strftime("%H:%M")
        if self.fecha.date() == hoy - timedelta(days=1):
            return "ayer " + self.fecha.strftime("%H:%M")
        return f"{self.fecha.day} de {MESES[self.fecha.month - 1]}"


# ---------------------------------------------------------------------------
# 1. Configuración
# ---------------------------------------------------------------------------

def cargar_config() -> dict:
    with open(RAIZ / "temas.yml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    for clave, defecto in [
        ("titulo", "El Panorama"),
        ("horas_hacia_atras", 24),
        ("max_por_categoria", 9),
        ("max_por_medio", 2),
        ("cantidad_destacados", 5),
        ("min_directas", 3),
        ("excluir_titulares", []),
    ]:
        config.setdefault(clave, defecto)
    return config


def url_google_news(consulta: str, idioma: str) -> str:
    """Arma la URL del RSS de Google News para una búsqueda."""
    q = quote_plus(f"{consulta} when:1d")
    if idioma == "en":
        return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    return f"https://news.google.com/rss/search?q={q}&hl=es-419&gl=UY&ceid=UY:es-419"


# ---------------------------------------------------------------------------
# 2. Recolección
# ---------------------------------------------------------------------------

def limpiar_html(texto: str) -> str:
    texto = re.sub(r"<[^>]+>", " ", texto or "")
    texto = html.unescape(texto)
    return re.sub(r"\s+", " ", texto).strip()


def extraer_imagen(entrada) -> str:
    """Busca una imagen en los distintos lugares donde los feeds la esconden."""
    for miniatura in entrada.get("media_thumbnail", []) or []:
        if miniatura.get("url"):
            return miniatura["url"]
    for medio in entrada.get("media_content", []) or []:
        url = medio.get("url", "")
        if url and (medio.get("medium") == "image" or re.search(r"\.(jpe?g|png|webp)", url, re.I)):
            return url
    for adjunto in entrada.get("enclosures", []) or []:
        if "image" in adjunto.get("type", "") and adjunto.get("href"):
            return adjunto["href"]
    contenido = ""
    if entrada.get("content"):
        contenido = entrada["content"][0].get("value", "")
    contenido += entrada.get("summary", "")
    encontrada = re.search(r'<img[^>]+src="(https?://[^"]+)"', contenido)
    return encontrada.group(1) if encontrada else ""


def fecha_de(entrada) -> datetime | None:
    cruda = entrada.get("published_parsed") or entrada.get("updated_parsed")
    if not cruda:
        return None
    return datetime(*cruda[:6], tzinfo=timezone.utc).astimezone(ZONA)


def medio_de(entrada, es_google: bool, nombre_feed: str) -> str:
    if es_google:
        fuente = entrada.get("source")
        if fuente and fuente.get("title"):
            return fuente["title"]
        return "Google News"
    if nombre_feed:
        return nombre_feed
    dominio = urlparse(entrada.get("link", "")).netloc
    return dominio.removeprefix("www.") or "sin medio"


def leer_fuente(nombre: str, url: str, es_google: bool, nombre_feed: str) -> list[Nota]:
    """Baja y parsea una fuente. Lanza excepción si la fuente no responde."""
    respuesta = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    respuesta.raise_for_status()
    feed = feedparser.parse(respuesta.content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"el feed no se pudo interpretar ({feed.bozo_exception})")

    notas = []
    for entrada in feed.entries:
        titulo = limpiar_html(entrada.get("title", ""))
        link = entrada.get("link", "")
        fecha = fecha_de(entrada)
        if not titulo or len(titulo) < 15 or not link or not fecha:
            continue
        if es_google:
            # Google News agrega " - Medio" (o " By Medio") al final del titular
            titulo = re.sub(r"\s+-\s+[^-]+$", "", titulo).strip() or titulo
            titulo = re.sub(r"\s+[Bb]y\s+[A-Z][\w.\s-]{1,40}$", "", titulo).strip()
            bajada, imagen = "", ""  # su resumen es solo el titular repetido
        else:
            bajada = limpiar_html(entrada.get("summary", ""))
            if bajada.lower().startswith(titulo.lower()[:40]):
                bajada = ""
            if len(bajada) > 220:
                bajada = bajada[:220].rsplit(" ", 1)[0] + "…"
            imagen = extraer_imagen(entrada)
        notas.append(
            Nota(
                titulo=titulo,
                link=link,
                medio=medio_de(entrada, es_google, nombre_feed),
                fecha=fecha,
                bajada=bajada,
                imagen=imagen,
                es_directo=not es_google,
            )
        )
    return notas


def recolectar(config: dict) -> tuple[list[Nota], list[str]]:
    """Baja todas las fuentes de todas las categorías en paralelo."""
    tareas = []  # (nombre visible, url, es_google, nombre_feed, categoria)
    for categoria in config["categorias"]:
        for busqueda in categoria.get("busquedas", []) or []:
            if isinstance(busqueda, str):
                busqueda = {"q": busqueda}
            idioma = busqueda.get("idioma", "es")
            nombre = f"Google News «{busqueda['q']}» ({categoria['nombre']})"
            tareas.append((nombre, url_google_news(busqueda["q"], idioma), True, "", categoria))
        for feed in categoria.get("feeds", []) or []:
            if isinstance(feed, str):
                feed = {"url": feed}
            nombre_feed = feed.get("nombre", "")
            nombre = f"{nombre_feed or feed['url']} ({categoria['nombre']})"
            tareas.append((nombre, feed["url"], False, nombre_feed, categoria))

    notas, fallidas = [], []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futuros = {
            pool.submit(leer_fuente, nombre, url, es_google, nombre_feed): (nombre, cat)
            for nombre, url, es_google, nombre_feed, cat in tareas
        }
        for futuro in as_completed(futuros):
            nombre, categoria = futuros[futuro]
            try:
                for nota in futuro.result():
                    nota.categoria_id = categoria["id"]
                    nota.categoria_nombre = categoria["nombre"]
                    notas.append(nota)
            except Exception as error:
                print(f"  ⚠ falló: {nombre} — {error}")
                fallidas.append(nombre)
    return notas, fallidas


# ---------------------------------------------------------------------------
# 3. Procesamiento: filtrar, deduplicar, agrupar, rankear
# ---------------------------------------------------------------------------

def normalizar(texto: str) -> str:
    plano = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in plano if not unicodedata.combining(c))


def tokens_de(titulo: str) -> frozenset:
    palabras = re.findall(r"[a-z0-9]{3,}", normalizar(titulo))
    return frozenset(p for p in palabras if p not in STOPWORDS)


def entidades_de(titulo: str) -> frozenset:
    """Los nombres propios del titular (palabras con mayúscula inicial)."""
    propias = re.findall(r"\b[A-ZÁÉÍÓÚÑ][\wáéíóúñ]{2,}", titulo)
    return frozenset(
        p for p in (normalizar(x) for x in propias) if p not in STOPWORDS
    )


ARTICULOS = {"el", "la", "los", "las", "the", "le", "il"}


def raiz_medio(nombre: str) -> str:
    """
    'Investing.com Canada' e 'Investing.com India' son el mismo medio: para
    contar cobertura y variedad se usa la primera palabra significativa.
    """
    partes = [p for p in normalizar(nombre).split() if p not in ARTICULOS]
    return partes[0] if partes else normalizar(nombre)


def son_la_misma_historia(a: Nota, b: Nota) -> bool:
    if not a.tokens or not b.tokens:
        return False
    comunes = len(a.tokens & b.tokens)
    jaccard = comunes / len(a.tokens | b.tokens)
    contencion = comunes / min(len(a.tokens), len(b.tokens))
    return jaccard >= 0.5 or contencion >= 0.8


def agrupar(notas: list[Nota]) -> list[list[Nota]]:
    """Junta las notas que cuentan la misma historia (greedy, por similitud)."""
    grupos: list[list[Nota]] = []
    for nota in sorted(notas, key=lambda n: n.fecha, reverse=True):
        for grupo in grupos:
            if son_la_misma_historia(nota, grupo[0]):
                grupo.append(nota)
                break
        else:
            grupos.append([nota])
    return grupos


def representante(grupo: list[Nota]) -> Nota:
    """De un grupo de notas iguales, elige la mejor versión para mostrar."""
    def puntaje(n: Nota):
        return (n.es_directo, bool(n.imagen), bool(n.bajada), n.fecha)

    elegida = max(grupo, key=puntaje)
    elegida.cobertura = len({raiz_medio(n.medio) for n in grupo})
    return elegida


def procesar(config: dict, notas: list[Nota]) -> tuple[list[dict], list[Nota]]:
    limite = datetime.now(ZONA) - timedelta(hours=config["horas_hacia_atras"])
    frescas = [n for n in notas if n.fecha >= limite]

    # Afuera las notas de servicio (resultados en vivo, horóscopos, etc.)
    excluidas = [normalizar(e) for e in config["excluir_titulares"] or []]
    frescas = [
        n for n in frescas
        if not any(e in normalizar(n.titulo) for e in excluidas)
    ]

    # Duplicado exacto (mismo link) entre categorías: queda en la primera
    vistas, unicas = set(), []
    for nota in frescas:
        if nota.link in vistas:
            continue
        vistas.add(nota.link)
        nota.tokens = tokens_de(nota.titulo)
        nota.entidades = entidades_de(nota.titulo)
        unicas.append(nota)

    # Por categoría: agrupar historias y armar el top con variedad de medios
    categorias_render = []
    todos_los_grupos: list[tuple[list[Nota], Nota]] = []
    for categoria in config["categorias"]:
        de_aca = [n for n in unicas if n.categoria_id == categoria["id"]]
        grupos = agrupar(de_aca)
        candidatas = [(g, representante(g)) for g in grupos]
        todos_los_grupos.extend(candidatas)
        # Cobertura manda; a igual cobertura, los medios elegidos a mano
        # (que traen imagen y bajada) le ganan a Google News
        candidatas.sort(
            key=lambda par: (
                par[1].cobertura + (0.5 if par[1].es_directo else 0),
                par[1].fecha,
            ),
            reverse=True,
        )

        # Selección: primero por ranking general, reservando lugares para
        # notas de medios directos (imagen y bajada); variedad por medio raíz
        cupo = config["max_por_categoria"]
        libres = max(0, cupo - config["min_directas"])
        elegidas, por_medio = [], {}

        def intentar(nota):
            raiz = raiz_medio(nota.medio)
            if nota in elegidas or por_medio.get(raiz, 0) >= config["max_por_medio"]:
                return
            # Mismo protagonista contando casi lo mismo: no repetir en la sección
            if any(
                len(nota.entidades & otra.entidades) >= 2
                and len(nota.tokens & otra.tokens) >= 4
                for otra in elegidas
            ):
                return
            elegidas.append(nota)
            por_medio[raiz] = por_medio.get(raiz, 0) + 1

        for _, nota in candidatas:
            if len(elegidas) >= libres:
                break
            intentar(nota)
        for _, nota in candidatas:  # los lugares reservados, con directas
            if len(elegidas) >= cupo:
                break
            if nota.es_directo:
                intentar(nota)
        for _, nota in candidatas:  # si faltó, se completa con lo que haya
            if len(elegidas) >= cupo:
                break
            intentar(nota)
        elegidas.sort(
            key=lambda n: (n.cobertura + (0.5 if n.es_directo else 0), n.fecha),
            reverse=True,
        )
        categorias_render.append(
            {"id": categoria["id"], "nombre": categoria["nombre"], "notas": elegidas}
        )

    # Destacados: las historias con más cobertura cruzando todas las categorías,
    # con variedad (máximo 2 por categoría)
    ordenados = sorted(
        (nota for _, nota in todos_los_grupos),
        key=lambda n: (n.cobertura, n.es_directo, n.fecha),
        reverse=True,
    )
    destacados, por_categoria = [], {}
    for nota in ordenados:
        if por_categoria.get(nota.categoria_id, 0) >= 2:
            continue
        # Sin protagonistas repetidos en el top 5 (dos notas de la misma
        # persona/equipo comparten dos o más nombres propios)
        if any(len(nota.entidades & d.entidades) >= 2 for d in destacados):
            continue
        destacados.append(nota)
        por_categoria[nota.categoria_id] = por_categoria.get(nota.categoria_id, 0) + 1
        if len(destacados) >= config["cantidad_destacados"]:
            break

    return categorias_render, destacados


# ---------------------------------------------------------------------------
# 4. Enriquecer — reservado para la versión 2
# ---------------------------------------------------------------------------

def enriquecer(categorias: list[dict], destacados: list[Nota]) -> str:
    """
    Versión 2 (todavía no implementada): acá se completará `resumen_ia` de cada
    nota con un resumen propio generado con IA, y se devolverá un párrafo
    editorial de apertura del día. La plantilla ya sabe mostrarlos si existen.
    """
    editorial = ""
    return editorial


# ---------------------------------------------------------------------------
# 5. Render
# ---------------------------------------------------------------------------

def fecha_larga(momento: datetime) -> str:
    return (
        f"{DIAS[momento.weekday()]} {momento.day} de "
        f"{MESES[momento.month - 1]} de {momento.year}"
    )


def renderizar(config, categorias, destacados, editorial, fallidas) -> None:
    entorno = Environment(loader=FileSystemLoader(RAIZ), autoescape=True)
    plantilla = entorno.get_template("plantilla.html")
    ahora = datetime.now(ZONA)

    base = dict(
        titulo=config["titulo"],
        fecha_larga=fecha_larga(ahora),
        fecha_corta=ahora.strftime("%d/%m/%Y"),
        categorias=categorias,
        destacados=destacados,
        editorial=editorial,
        fallidas=fallidas,
        generado=f"{fecha_larga(ahora)}, {ahora.strftime('%H:%M')} (hora Uruguay)",
    )

    DOCS.mkdir(exist_ok=True)
    HISTORICO.mkdir(exist_ok=True)

    (DOCS / "index.html").write_text(
        plantilla.render(**base, es_historico=False), encoding="utf-8"
    )
    (HISTORICO / f"{ahora.date().isoformat()}.html").write_text(
        plantilla.render(**base, es_historico=True), encoding="utf-8"
    )

    # Índice del histórico: una lista simple de todos los días guardados
    dias = sorted(
        (a.stem for a in HISTORICO.glob("????-??-??.html")), reverse=True
    )
    filas = []
    for dia in dias:
        momento = datetime.fromisoformat(dia)
        filas.append(
            f'<li><a href="{dia}.html">{fecha_larga(momento).capitalize()}</a></li>'
        )
    indice = entorno.get_template("plantilla_historico.html")
    (HISTORICO / "index.html").write_text(
        indice.render(titulo=config["titulo"], filas=filas), encoding="utf-8"
    )


# ---------------------------------------------------------------------------

def main() -> int:
    config = cargar_config()
    print(f"Generando «{config['titulo']}» — {datetime.now(ZONA):%Y-%m-%d %H:%M} (UY)")

    print("Recolectando fuentes…")
    notas, fallidas = recolectar(config)
    print(f"  {len(notas)} notas crudas, {len(fallidas)} fuentes caídas")

    categorias, destacados = procesar(config, notas)
    total = sum(len(c["notas"]) for c in categorias)
    if total == 0:
        print("ERROR: no se pudo armar ninguna categoría. No se toca la página anterior.")
        return 1

    editorial = enriquecer(categorias, destacados)
    renderizar(config, categorias, destacados, editorial, fallidas)

    print("Destacados del día:")
    for i, nota in enumerate(destacados, 1):
        print(f"  {i}. [{nota.categoria_nombre}] {nota.titulo} ({nota.cobertura} medios)")
    for categoria in categorias:
        con_imagen = sum(1 for n in categoria["notas"] if n.imagen)
        print(f"  {categoria['nombre']}: {len(categoria['notas'])} notas, {con_imagen} con imagen")
    print(f"Listo: {total} notas en {DOCS / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
