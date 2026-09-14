# TODO: cbadatos.com.ar

Lista de trabajo del portal (en espanol, es interna). Lo que ya esta hecho
queda tachado con fecha. Lo del servidor (nginx, certificados, instancia)
vive en el repo de deploy, no aca.

## 0. Estado

- [x] 2026-09-13: extension pelada (`ckan generate extension`), CI verde
      contra el CKAN solo-PostgreSQL.
- [x] 2026-09-13: instancia `cbadatos` definida en el repo de deploy (rama
      `cbadatos`, en revision): clon y venv propios, puerto 8102, vhost
      `cbadatos.com.ar` + redirect de `www`, certificado propio.
- [x] 2026-09-13: dominio delegado a Cloudflare (andres); A `@` -> IP del
      servidor, CNAME `www` -> `@`, ambos proxied.
- [ ] Cloudflare: SSL Full (strict) y certificado Origin CA de la zona
      instalado en el servidor (root, SERVER.md paso 6).
- [x] 2026-09-13: rama `cbadatos` del deploy mezclada en main.
- [x] 2026-09-13: instancia local andando (ckanito-cbadatos.ini, puerto
      5001, bases ckanito_cbadatos*, admin / cbadatos-dev-2026) con la home.
- [ ] Servidor: `deploy.sh demo` (units nuevas), `bootstrap.sh cbadatos`
      (root corre el SQL de los pasos 3 y 4), `deploy.sh cbadatos`, root
      instala los vhosts por instancia y borra el `ckanito.conf` viejo.

## 1. Home page

- [x] 2026-09-13: tema Midnight Blue (env de la instancia y test.ini).
- [x] 2026-09-13: `home/index.html`: iniciativa ciudadana, buscador grande,
      contadores (datasets, organizaciones), datasets recientes. Test.
- [ ] Contador y accesos por portal de origen cuando exista `source_portal`.
- [x] 2026-09-13: seccion "Recent Datasets" quitada de la home (andres):
      ordenaba por `metadata_modified`, que en lo cosechado mezcla la fecha
      remota con la de la copia de archivos. Sin valor.
- [ ] Pie de pagina: sacar la marca CKAN, poner el texto de la iniciativa
      y el link a "Acerca de" / "Fuentes". Logo propio (decidir).
- [ ] Textos en espanol, sin logos ni nombres oficiales del gobierno (no
      somos el gobierno: decirlo en la home y en "Acerca de").
- [ ] Pagina "Acerca de" y "Fuentes" (que portales se cosechan, con que
      frecuencia, y que significa cada campo de procedencia).

## 2. Procedencia: de que portal viene y quien produjo el dato

Problema: vamos a cosechar datosgestionabierta.cba.gov.ar y
datosestadistica.cba.gov.ar, despues otros portales, y ademas cargar datos
propios. No se pueden mezclar las organizaciones ni perder el origen.

Hallazgos (2026-09-13):

- Los dos portales son CKAN 2.11.5 y devuelven 404 a todo lo que no tenga
  User-Agent de navegador (el harvester `ckan` ya soporta `user_agent` en
  la config de la fuente).
- Gestion abierta: 156 datasets, 1.093 recursos (PDF 481, SVG 227, XLSX
  137, CSV 126, "ENLACE" 121), 25 organizaciones = ministerios y agencias
  (productores reales). Un extra `Frecuencia de actualización` escrito de
  tres formas distintas.
- Estadistica: 645 datasets, 12.304 recursos (ZIP 4.088, GEOJSON 3.942,
  PDF 2.645, RAR 489, XLSX 390, CSV 372), 7 "organizaciones" que son
  temas (Territorio 574, Poblacion, Economia, Sociedad, Anuarios,
  Metodologia, Geoportal), no productores: el productor es uno solo
  (Direccion General de Estadistica y Censos). Extras `depto` (565) y
  `muncom` (537): departamento y municipio/comuna del dato.

Propuesta (conversada el 2026-09-13, sigue abierta):

- [x] 2026-09-13: esquemas scheming (dataset, recurso, organizacion) con
      `source_portal` + `source_url`; caja "Procedencia" en la ficha del
      dataset, linea en la del recurso, origen en la pagina de la
      organizacion, facet "Portal de origen" con nombres cortos; desplegado
      en cbadatos.com.ar. Orgs con prefijo por portal (`prefix` en la
      lista de portales); estadistica a una sola org y sus temas a grupos;
      grupos y tags tal cual (decidido con andres).
- Dos campos de dataset en el esquema scheming de la extension, siempre
  visibles en la ficha del dataset y en la del recurso:
  - `source_portal` (select, obligatorio, facet "Portal de origen"). Las
    opciones viven en la extension: `cbadatos` (produccion propia),
    `datosgestionabierta`, `datosestadistica`, y se agregan las que
    vengan. Cada opcion tiene etiqueta y URL del portal.
  - `source_url`: URL del dataset en el portal de origen ("Ver en el
    portal de origen"). Se arma con la URL de la fuente y el id remoto
    (`harvest_object.guid`), asi sigue valiendo aunque el nombre local
    cambie. En el recurso, `source_url` es la URL original del archivo.
- Organizacion = quien produjo el dato, como siempre en CKAN. Sin campo
  `producer` aparte (andres: no quedaba claro que org usar entonces).
  - Gestion abierta: `remote_orgs: create` del harvester estandar crea
    las 25 organizaciones remotas con su id y nombre remotos (Ministerio
    de Salud, ...). Verificado en el codigo: si otro portal trajera una
    org con el mismo nombre, la creacion falla y el dataset queda en la
    org de la fuente (queda en el informe del job; no se mezcla en
    silencio). Si alguna vez pasa, se prefija por portal.
  - Estadistica: sus 7 "organizaciones" son temas; `remote_orgs:
    only_local` deja todo en la org de la fuente ("Direccion General de
    Estadistica y Censos") y los temas pasan a grupos (pocas lineas en
    `modify_package_dict`).
  - Produccion propia: organizaciones propias, `source_portal =
    cbadatos`.
  - A cada organizacion cosechada se le guarda tambien `source_portal`
    (esquema de organizacion), para que su pagina diga de que portal
    viene.
- Donde se hace: `modify_package_dict(package_dict, harvest_object)` del
  harvester `ckan` de ckanext-harvest. Verificado: corre despues de que el
  harvester resolvio organizaciones, grupos y `default_extras`, y justo
  antes de `package_create/update`, con acceso a la fuente
  (`harvest_object.source.url` y su config). Ahi se ponen `source_portal`
  (de la config de la fuente), `source_url`, se mapean los extras y se
  descartan los que chocan con el esquema (scheming los rechaza).
- Mapear extras remotos a campos: `Frecuencia de actualización` (las tres
  grafias) -> `update_frequency`; `depto` -> `departamento`; `muncom` ->
  `municipio`. El resto de extras se conserva tal cual.

- [x] 2026-09-13: harvester `ckan_with_files` (metadatos): procedencia,
      orgs con prefijo y logo copiado (SVG queda como link), `single_org`
      + `remote_orgs_as_groups` para estadistica, extras que chocan se
      descartan. Probado en local contra gestion abierta: 156/156, 19 orgs,
      13 grupos. 19 tests. Bug encontrado en ckanext-harvest (reindex de la
      fuente con dict sin validar, CKAN >= 2.12): fork PR #3, en revision;
      mezclarlo ANTES de desplegar harvest en cbadatos.
- [ ] OJO harvest: los objetos con error NO se reintentan en la corrida
      siguiente (el harvester `ckan` pide solo lo modificado desde el
      ultimo job). Tras arreglar algo, correr una vez con `force_all`.
      Las copias de archivos si se reintentan: el harvester revisa los
      archivos tambien en los datasets "sin cambios".
- [ ] BUG ckanext-harvest (2026-09-13): con `ckan.harvest.log_scope`
      distinto de -1 el `DBLogHandler` hace `Session.commit()` por cada
      linea de log, en medio de la transaccion de `package_update`: se
      cierra el savepoint y el `HarvestSource` no se actualiza
      (`harvest_source_update` dice OK pero la tabla queda vieja;
      `harvest_source_patch` da 500 "This transaction is closed"). El DEMO
      lo tiene activo (`log_scope = 0`). cbadatos no lo usa. PR al fork:
      que el handler use una sesion propia, o sacarlo.
- [ ] `harvest_source_patch` falla siempre por los extras de la fuente
      (ya visto en el demo): usar `harvest_source_update` completo.
- [ ] La org "Córdoba Datos" cuenta la fuente de harvest como dataset (tipo
      `harvest`); ver si ocultarla del conteo.

## 3. Harvest con archivos (ser backup, no un indice de links)

- ckanext-harvest NO baja archivos: su harvester `ckan` borra `url_type`
  y crea recursos con link al original ("we are only creating normal
  resources with links", `ckanharvester.py`, import_stage). Verificado
  2026-09-13. ckanext-archiver bajaria copias a un cache propio, pero no
  reemplaza la URL del recurso ni lo mete en el datastore: no sirve como
  backup navegable.
- [x] 2026-09-13: copia de archivos HECHA en el harvester (import_stage
      -> _copy_files: un archivo por vez, pausa `copy_pause` (3 s), tope
      `copy_max_mb` (200), timeout largo + 1 reintento, SHA-256 en `hash`,
      `source_etag`, `source_downloaded`; conserva la copia en las
      actualizaciones; If-None-Match). 8 tests. EN PROD 2026-09-13: fuentes
      `gestion-abierta` y `estadistica` (WEEKLY) creadas y primera corrida
      lanzada; xloader activo. Velocidad en prod: varios MB/s.
- [ ] Camino elegido (andres, 2026-09-13): simplicidad, usar lo que
      ckanext-harvest da. Un harvester `ckan_with_files` en la extension:
      subclase de `CKANHarvester`, `modify_package_dict` para la
      procedencia (arriba) e `import_stage` que llama al de la base (crea
      o actualiza el dataset con links) y despues baja, uno por uno, los
      recursos alojados en el portal de origen (URL bajo la URL de la
      fuente: 972 de 1.093 y 12.303 de 12.304) y los convierte en upload
      propio con `resource_patch(upload=...)`; el original queda en
      `source_url`. Los que ya eran links quedan como links. Ojo: el
      harvester base borra `url_type` antes de `modify_package_dict`, por
      eso se detecta por URL y no por `url_type`.
      Si el harvester queda generico y probado, se propone despues como
      mejora del fork de ckanext-harvest (opcion `download_resources`).
- [ ] Con cuidado con el servidor remoto: un archivo por vez, pausa de
      unos segundos entre pedidos, reintentos con espera, tope de tamano
      por archivo (decidir: 200 MB?).
- [ ] User-Agent (medido 2026-09-13): los dos portales dan 404 a cualquier
      UA que no tenga tokens de Chrome (probados "cbadatos.com.ar
      harvester", "Mozilla/5.0 (compatible; ...)", python-requests,
      ckan-harvest). Usar `Mozilla/5.0 (X11; Linux x86_64)
      AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36
      cbadatos.com.ar` (pasa, y nos identifica al final). Va en
      `user_agent` de la config de la fuente; el harvester lo usa para la
      API y para bajar archivos.
- [ ] No volver a bajar lo que no cambio, en tres niveles (medido):
      1. el harvester `ckan` solo pide al origen los datasets con
         `metadata_modified` posterior a la ultima corrida;
      2. por recurso, comparar el `last_modified` remoto (presente en el
         100% de los recursos subidos; `size` y `hash` remotos solo en
         16%/1% y 25%/3%: no sirven de base) con `source_last_modified`
         guardado; igual = nada;
      3. si cambio, GET condicional con `If-None-Match` (los dos
         servidores dan ETag y Last-Modified y responden 304); si 200,
         SHA-256 del contenido y reemplazar el archivo solo si difiere del
         `hash` guardado. Campos del recurso: `source_url`,
         `source_last_modified`, `source_etag`, `hash` (sha256 nuestro),
         `source_downloaded`.
- [ ] Donde corre la descarga: dentro del `import_stage` (el fetch
      consumer procesa un objeto a la vez, asi que ya es "un archivo a la
      vez" sin jobs ni colas extra). Cuenta: 12.300 archivos x (descarga +
      3 s de pausa) = unas 12-15 horas la primera vez, despues solo lo que
      cambio. Alternativa si molesta que un job dure horas: encolar la
      descarga como job de CKAN por dataset con un solo worker.
- [ ] Frecuencia: primera pasada completa, luego semanal por fuente.
- [ ] PRIORITARIO (andres, 2026-09-14) Backup de verdad: lo que se borra
      en el origen se conserva aca, marcado. Hoy (verificado): ningun
      harvester borra datasets (ni el `ckan` de ckanext-harvest: solo el
      de DCAT tiene borrado), asi que un dataset borrado queda intacto
      pero SIN aviso; en cambio un RECURSO que desaparece de un dataset
      que sigue existiendo SI se pierde de la vista: package_update
      reemplaza la lista de recursos y CKAN lo marca deleted (el archivo
      queda en el disco). Plan: campo `source_deleted` (fecha) en dataset
      y recurso, facet "Estado en el origen" (vigente / borrado en el
      origen), banner en ficha y recurso; datasets: al final del gather
      comparar la lista completa del origen con los nuestros de esa
      fuente y marcar los que faltan (al harvester `ckan` le cuesta una
      consulta mas), desmarcar si reaparece; recursos: al importar,
      conservar los que tienen copia y ya no vienen, marcados (partir de
      `_existing_copies`). Salvaguarda: marcar solo con listado completo
      sin errores y a la segunda pasada consecutiva. Comando para
      reactivar recursos ya borrados que tengan copia en disco.
- [ ] Datastore: xloader carga los CSV/XLS copiados (como en el demo);
      los GeoJSON van con geoview. ZIP y RAR (4.500 en estadistica) solo
      se guardan; ver despues si vale la pena abrirlos.
- [x] 2026-09-13: volumen estimado con HEAD (muestra de 37 archivos, todos
      200): estadistica ~0,24 MB promedio, maximo 4 MB -> ~3 GB en total;
      gestion abierta ~0,66 MB promedio -> ~0,6 GB. Unos 4 GB: entra sin
      problema.
- [x] 2026-09-14: disco del servidor: 444 GB, 197 GB libres con gestion
      abierta + estadistica completas y municba a medias (storage de
      cbadatos 4,3 GB). Con la ciudad, Villa Maria, Legislatura e IDECOR
      (~4,5 GB) se estima menos de 15 GB. Revisar cada tanto con
      `df -h` y `du -sh ~/ckanito-data/cbadatos/storage`.
- [ ] Respaldo de las copias (andres, 2026-09-14): hoy los archivos viven
      en un solo disco local del servidor; si se rompe se pierde el backup
      que justifica el portal. Ver si conviene un S3 propio (o compatible:
      Backblaze B2, Hetzner Object Storage, MinIO en otra maquina) y como:
      (a) storage de CKAN directo en S3 (ckanext-s3filestore o
      ckanext-cloudstorage, a revisar como extension), o (b) mas simple y
      sin tocar CKAN, un `rclone sync` nocturno de `storage/` + dump de la
      base a un bucket. Con menos de 20 GB, (b) alcanza y es barato.

## 4. Otros origenes

- [ ] Otros portales CKAN: misma receta, una fuente por portal con su
      `source_portal` en la config y una opcion mas en el esquema.
- [x] 2026-09-14: portal de la ciudad (gobiernoabierto.cordoba.gob.ar, no
      es CKAN): harvester `municba` (municba.py), procedencia "Muni CBA".
      Usa la API abierta nueva del sitio (`/api/datos-abiertos/dato`,
      `.../version-dato`, `.../recurso`, `/categoria`): 183 datasets
      publicados, ~3 versiones por dataset, cada recurso de cada version
      es un recurso aca (nombre "version (recurso)"), categorias y
      categoria padre a grupos `municba-<slug>`, periodicidad / categoria
      / fuente como extras, licencia CC BY-SA. Los archivos estan en S3
      con URL firmada que vence en 1 hora: se pide una fresca justo antes
      de bajar (lista de recursos de la version; el endpoint de un recurso
      solo da 500) y `source_api` guarda de donde. Copia rechequeada solo
      si cambio la version o cada `recheck_days` (30). La API vieja
      (`/api/datos-abiertos/`) NO sirve: mezcla no publicados y da 500 en
      la mitad de las paginas. Una sola organizacion `municba`.
- [x] 2026-09-14: portal de Villa Maria (datos.villamaria.gob.ar, Django
      propio, sin API): harvester `villamaria` (villamaria.py), procedencia
      "Villa Maria". Lee el sitemap (53 datasets con lastmod), la pagina
      HTML de cada dataset (titulo, descripcion, categoria, fecha,
      frecuencia, area) y sus recursos de a 6 por POST htmx con cookie
      CSRF. Recurso = "titulo - periodo", formato del portal; descargas
      `/recurso/<uuid>/descargar` estables (nombre por
      Content-Disposition), links externos ("Ver", visor de mapas) quedan
      como links. Categoria a grupo `villamaria-<slug>`. Sin licencia en el
      portal (`notspecified`). Una sola organizacion `villamaria`. Probado
      en local contra el portal real.
- [ ] Portales no CKAN (ArcGIS Hub, Socrata, listas de archivos): un
      harvester por tipo, mas adelante.

### Portales pendientes

Siempre la misma receta: un harvester nuevo si hace falta, en su propio
archivo (`<portal>.py` + `tests/test_<portal>.py` + fixtures reales
recortadas en `tests/data/`), `FileCopyMixin` para COPIAR los archivos (no
links), una opcion mas en `SOURCE_PORTALS`, entry point en pyproject, plugin
en test.ini, fuente en `cli.py` (`init-sources`). De a uno por vez para no
pisarnos entre sesiones.

- [x] 2026-09-14: Legislatura de Cordoba
      (https://legislaturacba.gob.ar/portal-de-datos-abiertos/): harvester
      `legislatura` (legislatura.py), procedencia "Legislatura". WordPress
      5.4: cinco paginas de seccion (composicion-de-la-camara,
      administracion, comisiones-2, sesiones, participacion-ciudadana)
      leidas por `/wp-json/wp/v2/pages?slug=...`; cada h2 es un dataset
      (36) con descripcion, "Ultima actualizacion: d/m/yyyy" y los mismos
      datos en CSV + XLSX + XML (zip) + JSON + metadato.txt (tema,
      frecuencia, fuente -> extras). Id = slug de la pagina propia del
      dataset (el titulo cambia de anio); titulos en MAYUSCULAS pasados a
      oracion; seccion como grupo `legislatura-<seccion>`; links a Drive
      u otros sitios quedan como links (2). Sin licencia (`notspecified`).
      No hace falta User-Agent. Probado en local: 36 datasets, 173
      recursos, 169 copiados (2 JSON pasan los 10 MB del local; en prod
      entran). La biblioteca de medios (`/wp-json/wp/v2/media`, 2.587
      CSV) tiene versiones viejas (RUTA_144..148) que el portal no
      enlaza: no se cosechan.
- [x] 2026-09-14: IDECOR (Infraestructura de Datos Espaciales de Cordoba),
      portal de mapas https://www.mapascordoba.gob.ar/#/descargas y
      /#/geoservicios: harvester `idecor` (idecor.py), procedencia
      "IDECOR", org unica `idecor`, fuente MENSUAL. SPA Quasar; los dos
      catalogos son JSON estaticos: `datos/descargas.json` (15 grupos >
      58 subgrupos > 554 capas: shp/kml/json = WFS GetFeature del
      GeoServer idecor-ws, tiff, qml/lyr, dd y metadatos PDF, 10 externas
      a experience.arcgis.com; rutas relativas en el bucket Huawei OBS
      obs-idecor-lib...myhuaweicloud.com, que da ETag y Last-Modified) y
      `datos/geoservicios.json` (1.118 hojas = GetCapabilities por capa
      `/geoserver/idecor/<capa>/wms|wfs|wcs`, 563 capas; + 4 endpoints
      generales que no se cosechan). Se unen por nombre de capa (los
      rasters por el nombre del tiff): 621 capas, 67 solo geoservicio
      (a esas se les arma la URL WFS GetFeature json). Un dataset por
      capa; titulo con el grupo cuando se repite ("Parcelas - Villa
      Carlos Paz"); grupo `idecor-<categoria de primer nivel>`; extras
      tema / tipo (Vectorial, Raster, Enlace externo) / capa / organismo;
      notas = tema + descripcion del subgrupo. Recursos: COPIADOS GeoJSON
      (WFS), GeoTIFF (si entra en copy_max_mb, 12 de 59 no), simbologia,
      diccionario, metadatos; LINKS shp y kml (WFS a demanda, mismo dato),
      WMS/WFS/WCS por capa, visor externo. Sin fechas en el catalogo:
      `recheck_days` (30) y ETag (el bucket contesta 304; el WFS se rebaja
      y compara por hash). Config `categories` para cosechar de a partes.
      Volumen medido: 59 tiff = 9,3 GB (max 2 GB); 485 shp ~5 MB promedio
      (~2,4 GB de GeoJSON estimados); PDF/QML ~170 MB; ~14 nombres de
      capa corruptos en descargas.json quedan como links rotos.
      Probado en local con `categories: transporte, omi, riesgo` (21 capas,
      139 recursos, 69 copias). El GeoJSON se pide con `srsName=EPSG:4326`
      (el WFS lo da en POSGAR 2007 y los visores no conocen esa
      proyeccion).
      Vistas (2026-09-14, probado en local): geoview muestra el WMS/WFS/KML
      con OpenLayers (`geo_view`) y el GeoJSON copiado con Leaflet
      (`geojson_view`) sobre OSM. Para eso hace falta: (1) fix en el fork
      de geoview (commit local 991bbcc, ol_preview.js: `info.tooltip` no
      existe en CKAN 2.10+ y el mapa nunca dibujaba); (2) config
      `ckanext.spatial.common_map.type = custom` + `custom.url` de OSM +
      `attribution` (las vistas Leaflet no traen mapa base por defecto);
      (3) `ckan views create geo_view geojson_view pdf_view` (o agregarlos a
      `ckan.views.default_views`) - en prod todavia falta. Pendiente en el
      fork: el titulo de la vista dice "Map viewer" sin traducir.
      Pendiente: decidir con andres si vale un tope > 200 MB para los 12
      rasters grandes (~7 GB) o quedan como link; licencia (el portal dice
      "datos libres", sin licencia formal -> notspecified).
- [x] 2026-09-14: Rio Cuarto, portal de transparencia de la Secretaria de
      Economia (https://economiariocuarto.gob.ar/transparencia): harvester
      `riocuarto` (riocuarto.py), procedencia "Rio Cuarto", org
      `riocuarto`, fuente WEEKLY. Next.js en Vercel: el buildId sale del
      `__NEXT_DATA__` de /transparencia y cada seccion tiene su JSON en
      `/_next/data/<buildId>/transparencia/<seccion>.json`. Un dataset por
      tipo de documento (SECTIONS en el modulo: presupuesto, ejecucion,
      cuenta general, recaudacion, deuda, calificacion de riesgo, realidad
      economica, escala salarial, boletin oficial, DDJJ = 10), seccion
      como grupo, extras seccion / documentos_vigentes; cada item un
      recurso, "Vigente"/"No vigente" en la descripcion. Archivos: los de
      Google Drive se copian por `drive.google.com/uc?export=download&id=`
      (Drive da Content-Disposition con el nombre real y Last-Modified,
      sin ETag; el formato sale del nombre del archivo al copiar), los PDF
      de DDJJ directo (`prod.ddjj.riocuarto.gob.ar`, con ETag); las
      carpetas de Drive (boletin oficial, 35) quedan como links (sin API
      key de Google no se listan). Id de recurso por id de Drive (el
      `?usp=` cambia); el mismo archivo listado dos veces son dos
      recursos. Sin fechas: `recheck_days` (30) y hash. FileCopyMixin:
      ahora no guarda como copia una respuesta text/html (la pagina de
      "confirmar descarga" de Drive, un login) y toma el formato del
      nombre del archivo si el recurso no lo tenia. Sin licencia visible.
      Probado en local contra el portal real.

- [x] 2026-09-14: **Rio Tercero** (https://datos.riotercero.gob.ar, CKAN
      2.7.6, 54 datasets / 2.203 recursos, casi todo PDF: boletin oficial,
      cuentas publicas, normativa, tramites...). Sin harvester nuevo:
      fuente `ckan_with_files` en `init-sources` (`source_portal:
      riotercero`, `remote_groups: create`), portal en SOURCE_PORTALS.
      Orgs `riotercero-<secretaria>` (7), grupos remotos tal cual.
      Licencia remota `CC-BY-4.0` -> ahora `ckan_with_files` mapea ids
      remotos al registro local (`cc-by`). init-sources acepta fuentes sin
      organizacion propia (las CKAN crean las suyas); las salta si no
      existe la org `cbadatos` que las posea. Probado en local: 54/54 sin
      copias, copias en curso. Sin User-Agent especial.
- [x] 2026-09-14: Villa Allende
      (https://www.villaallende.gov.ar/transparencia/): harvester
      `villaallende` (villaallende.py), procedencia "Villa Allende", org
      `villaallende`, WEEKLY. WordPress con custom post types y campos ACF
      abiertos en el REST: `wp-json/wp/v2/va_boletin` (39: numero,
      periodo, pdf = id de media, fecha), `va_licitacion` (21: codigo,
      tipo compulsa/licitacion/concesion, estado call/open/closed/
      finished/voided, monto, fecha, documentos [{label, url = id de
      media}]), `compra-publica` (24: titulo, descripcion, fecha,
      url_licitaciones = URL directa). Media por
      `wp-json/wp/v2/media?include=...` (source_url, modified). Un dataset
      por tipo (3), un recurso por documento (97), descripcion con
      expediente/tipo/estado/monto/fecha; 17 compras apuntan a una pagina
      del sitio, no a un archivo: links. Fecha `modified` del media ->
      `source_last_modified` (sin recheck_days). Probado en local: 75
      copias (4 PDF > 10 MB local). Sin licencia (`notspecified`).
      Nota general de los harvesters propios: el "unchanged" compara el
      contenido cosechado, asi que un cambio en el mapeo (codigo) no se
      reaplica a los datasets existentes hasta que cambie el origen; en
      prod no importa (corrida nueva), en local se ve.
- [x] 2026-09-14: Bell Ville (https://bellville.gob.ar/presupuesto/):
      harvester `bellville` (bellville.py), que es el de la Legislatura
      apuntado a otra pagina: `LegislaturaHarvester` ahora es subclasable
      (`portal`, `default_pages`), absolutiza links relativos, distingue
      por el h2 los datasets que comparten la pagina de seccion, y usa
      como nombre del recurso la etiqueta del link cuando es un nombre
      ("Presupuesto - Ejecutado 2024") y no una extension. 4 datasets
      (Presupuesto, Regimen tarifario e impositivo, Regimen de
      contratacion, Ordenanzas = link al digesto del Concejo), 15 PDF.
      MONTHLY. Los llamados a licitacion son posts, no se cosechan.

Revisados el 2026-09-14 y descartados por ahora: turismo.cordoba.gob.ar/datos-abiertos
  (solo enlaza a la categoria Turismo de municba, ya cosechado);
  datos.cordoba.gob.ar (no es datos abiertos: app interna "base unica"
  con login); transparencia.cba.gov.ar y gestionabierta.cba.gov.ar (403
  a curl, Cloudflare; el segundo es la portada de datosgestionabierta, ya
  cosechado); compraspublicas.cba.gov.ar (403); cdcordoba.gob.ar (Concejo
  Deliberante, WordPress sin seccion de datos); justiciacordoba.gob.ar
  (ASP.NET, sin seccion de datos visible en la portada; las estadisticas
  judiciales serian valiosas, mirar mejor a mano); Villa Carlos Paz, San
  Francisco, Alta Gracia, Jesus Maria, Cosquin, Marcos Juarez: sus sitios
  no tienen seccion de datos ni transparencia enlazada desde la portada
  (sus datos geograficos ya vienen por IDECOR Ciudades).

- [ ] Produccion propia: organizaciones propias, usuarios editores,
      `source_portal = cbadatos`, formulario con `producer` obligatorio.

## 5. Extensiones

- [x] 2026-09-13/14 activas en cbadatos: scheming, harvest (+ consumers y
      timer), xloader, push_errors, pdfview (fork nuevo, PR #1), geoview
      (geojson_view + geo_view WMS/WFS/KML/GML/ArcGIS), dcat
      (catalog.jsonld, JSON-LD schema.org en cada dataset), pages (menu
      "Acerca de" desplegable propio + lista al pie de Acerca de; pagina
      "Sobre este CKAN y este experimento" creada por `ckan cordoba-portal
      init-pages`, idempotente, corre en cada deploy), tracking +
      api_tracking (timer horario; dashboard solo admins).
- [x] Sitemap propio en la extension (/sitemap.xml + robots.txt), sin
      ckanext-sitemap.
- [ ] Decidido NO por ahora (andres): spatial, showcase, dbquery.
      Tampoco: hierarchy, archiver/qa, googleanalytics, fluent, superset.
- [ ] Al terminar la primera cosecha: `ckan views create pdf_view
      geojson_view geo_view` (los consumers viejos no crean esas vistas),
      poner `mimetype` a las copias sin el (por extension), reiniciar los
      consumers (`RESTART_HARVEST=1 deploy.sh cbadatos` o systemctl).
- [ ] deploy.sh ya no reinicia los consumers de harvest si hay un job
      corriendo (RESTART_HARVEST=1 fuerza).
- [ ] Imagenes de grupos: el harvester estandar (`remote_groups: create`)
      crea el grupo con el dict remoto y `image_url` es un nombre de
      archivo del portal remoto -> imagen rota (mismo bug que harvest PR #2
      arreglo para orgs). 2026-09-14: arreglado a mano en prod (13 grupos,
      imagen bajada y subida por API). Falta que el harvester lo haga solo
      (copiar la imagen como con los logos de orgs), cuando termine la
      cosecha en curso.
- [ ] Harvester: si la copia de un archivo falla (p. ej. "File upload too
      large"), seguir con el siguiente en vez de cortar el dataset
      (2026-09-14: 3 datasets afectados por el tope de 10 MB; ya subido a
      200 MB en la instancia). Los 404 del origen quedan como link.
- [ ] Cloudflare devuelve 403 a los POST a la API con User-Agent
      `Python-urllib`; usar curl o requests con otro UA.

## 6. Orden sugerido

1. Dominio + portal vacio en linea (seccion 0).
2. Esquema y campos de procedencia + home page (secciones 1 y 2).
3. Harvester `cordoba_ckan` con metadatos (sin archivos), primera fuente:
   gestion abierta (156 datasets, chico). Validar procedencia y facets.
4. Descarga de archivos + backup (seccion 3), primero gestion abierta
   (0,6 GB conocidos), despues estadistica.
5. Segunda fuente: estadistica (temas a grupos, producer fijo).
6. Produccion propia y otros portales.
