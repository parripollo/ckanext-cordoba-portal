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
- [ ] Backup de verdad: cuando un dataset desaparece del origen, NO
      borrarlo (el harvester `ckan` lo borra por defecto): marcarlo
      ("ya no esta en el portal de origen", fecha) y dejarlo visible.
- [ ] Datastore: xloader carga los CSV/XLS copiados (como en el demo);
      los GeoJSON van con geoview. ZIP y RAR (4.500 en estadistica) solo
      se guardan; ver despues si vale la pena abrirlos.
- [x] 2026-09-13: volumen estimado con HEAD (muestra de 37 archivos, todos
      200): estadistica ~0,24 MB promedio, maximo 4 MB -> ~3 GB en total;
      gestion abierta ~0,66 MB promedio -> ~0,6 GB. Unos 4 GB: entra sin
      problema. Falta mirar el disco libre del servidor.

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
- [ ] IDECOR (Infraestructura de Datos Espaciales de Cordoba), portal de
      mapas: https://www.mapascordoba.gob.ar/#/descargas
      Relevado 2026-09-14: SPA Quasar/Vue, pero el catalogo de descargas
      es UN json estatico y publico:
      `https://www.mapascordoba.gob.ar/datos/descargas.json` (467 KB):
      15 super-grupos > 58 grupos > 554 capas. Cada capa: `title`,
      `category` (no_raster 519 / raster 35) y URLs por formato: `shp`,
      `kml`, `json` (485 capas; son WFS GetFeature del GeoServer
      `idecor-ws.mapascordoba.gob.ar/geoserver/idecor/ows` con
      outputFormat SHAPE-ZIP / KML / json, responden al toque, ~1.5 MB el
      shp de prueba), `tiff` (59 rasters, GRANDES: 221 MB el de prueba),
      `qml`/`lyr` (simbologia), `dd` (diccionario de datos PDF),
      `metadatos` (PDF), y 10 capas externas (`externa`, `link` a
      experience.arcgis.com, `organismo`). Las rutas relativas
      (`/metadatos/...`, `/dicdatos/...`, `/simbologia/...`,
      `/download_raster/...`) cuelgan del bucket
      `https://obs-idecor-lib.obs.sa-argentina-1.myhuaweicloud.com`
      (Huawei OBS, da Last-Modified y Content-Length; en el dominio
      principal dan 404). Propuesta: un dataset por capa (554), grupo por
      super-grupo/grupo, recursos = shp + kml + geojson + qml + dd +
      metadatos copiados, tiff copiado solo si entra en `copy_max_mb`
      (si no queda como link), externas como link; org unica `idecor`.
      Sin fecha por capa en el json: usar Last-Modified del bucket para
      no rebajar; el WFS no da fecha (hash del contenido). Licencia: ver
      /acercade. Tambien sirve el WFS GetCapabilities para los nombres de
      capa. Va despues de la Legislatura.

      Volumen medido 2026-09-14 (para decidir que copiar):
      - 59 GeoTIFF: 9,3 GB en total, el mayor 2 GB, 12 pasan los 200 MB.
      - 485 shp (WFS SHAPE-ZIP): muestra de 12 promedia 5 MB (de 0,03 a
        39 MB; `rendimiento_maiz` 21 MB tarda 38 s) -> ~2,4 GB estimados;
        kml y geojson de las mismas capas serian otros ~2 x eso, no vale
        copiarlos (mismo dato: copiar UNO, el geojson, que geoview
        previsualiza; shp y kml como links al WFS).
      - 1.300 PDF/QML (metadatos, diccionarios, simbologia): ~170 MB.
      - Ojo: `descargas.json` tiene ~14 nombres de capa corruptos
        ("bolsa_alfalfa_historicbolsa_maiz_historicoo"): esos links dan
        error en el origen, quedan como link roto (o se corrigen por
        GetCapabilities si el nombre bueno esta).

      **Geoservicios** (https://www.mapascordoba.gob.ar/#/geoservicios),
      relevado 2026-09-14: otro json estatico,
      `https://www.mapascordoba.gob.ar/datos/geoservicios.json` (421 KB),
      arbol label/href/children: 9 grupos ("Generales", "Por Temas",
      "Energias Renovables", "OMI", "Ciudades", "Mapas de Riesgo",
      "Imagenes y Vuelos"...) > 1.118 hojas = URLs GetCapabilities por
      CAPA (`/geoserver/idecor/<capa>/wms|wfs|wcs`): 563 capas, 497 con
      WMS+WFS, 47 rasters con WMS+WCS, 10 solo WMS, 8 solo WCS; mas los
      4 servicios generales (WMS, WFS, WCS, WMTS del GeoServer, 504
      feature types en el WFS general) y 2 de CONAE. 476 de esas capas son
      las mismas de descargas; 87 solo estan como geoservicio (sin entrada
      de descarga) y 14 solo en descargas.

      Estrategia propuesta para IDECOR (un solo harvester `idecor`):
      1. La unidad es la CAPA (union de descargas.json y geoservicios.json
         por nombre de capa): un dataset por capa, ~570. Grupos por
         tema (super-grupo/grupo de descargas; el arbol de geoservicios
         como segundo criterio para las 87 que faltan). Org unica
         `idecor`.
      2. Recursos por capa:
         - COPIADOS (esto es lo que nos hace backup): un vector por capa
           en GeoJSON via WFS GetFeature (o el shp si se prefiere; no los
           tres), los PDF de metadatos y diccionario, la simbologia
           (qml/lyr). ~2,5 GB en total; el WFS se genera al vuelo, ir a
           1 request cada 3 s y con `timeout` largo (300 s).
         - GeoTIFF: copiar solo los que entran en `copy_max_mb` (tope
           200 MB deja afuera 12 de 59, ~7 GB); los grandes quedan como
           link al bucket, que da Last-Modified/Content-Length para
           revisar cambios sin bajar. Decidir con andres si vale un
           tope mayor para estos (9,3 GB en disco).
         - LINKS (no se copian, son servicios, no archivos): WMS, WFS y
           WCS por capa con formato "WMS"/"WFS"/"WCS" y la URL
           GetCapabilities de la capa; ckanext-geoview (ya activo) los
           previsualiza en el mapa. Un servicio no se "descarga": lo que
           un WFS devuelve ya lo copiamos como GeoJSON, y un WCS/WMS
           completo son los GeoTIFF/tiles (los rasters ya estan
           cubiertos por el punto anterior; WMTS/tiles no se copia).
           Los 4 endpoints generales van en un dataset "Geoservicios
           IDECOR" aparte, solo links.
         - Para las 87 capas que solo tienen geoservicio se arma la URL
           WFS GetFeature (mismo GeoServer, mismo patron) y se copia el
           GeoJSON igual.
      3. Cambios: el json no trae fechas. Bucket: Last-Modified. WFS:
         hash del contenido (ya lo hace FileCopyMixin), pero eso implica
         rebajar 2,5 GB por corrida -> frecuencia MENSUAL para IDECOR, o
         mirar antes el `WFS GetCapabilities` / `DescribeFeatureType`
         (no dan fecha) o el numero de features (`resultType=hits`,
         barato) como senal de cambio. Empezar mensual con hits.
- [ ] Rio Cuarto, portal de transparencia de la Secretaria de Economia:
      https://economiariocuarto.gob.ar/transparencia
      Relevado 2026-09-14: Next.js en Vercel, sin API propia, pero cada
      seccion tiene su JSON de pagina en
      `/_next/data/<buildId>/transparencia/<seccion>.json` (el buildId
      sale del `__NEXT_DATA__` de cualquier pagina; cambia con cada
      deploy). Secciones: `informacion-economica-financiera` (186 items
      en listas ejercicios/ejecuciones/presupuesto/recaudacion/informes/
      deudas/realidad, cada item {title, category, status Vigente/No
      Vigente, url}), `escala-salarial` (32), `boletin-oficial` (33, casi
      todo carpetas de Drive), `declaraciones-juradas` (por cargo: 20 PDF
      en `prod.ddjj.riocuarto.gob.ar/ddjj_publicas/<ulid>.pdf` + Drive).
      Los archivos son casi todos Google Drive publicos
      (`drive.google.com/file/d/<id>/view`): se bajan con
      `https://drive.usercontent.google.com/download?id=<id>&export=download`
      (303 desde `drive.google.com/uc?export=download&id=`), devuelve
      Content-Disposition con el nombre real, Last-Modified y
      Content-Length; PDFs de ~1 MB. Las carpetas de Drive (35) no se
      pueden listar sin API key de Google: quedan como link (o se pide
      una key gratuita y se listan; decidir). Propuesta: un dataset por
      categoria (presupuesto, ejecucion, recaudacion, deuda, informes,
      realidad, escala salarial, boletin oficial, DDJJ), cada item un
      recurso copiado; `status` como extra; sin fechas en el JSON (usar
      Last-Modified de Drive para no rebajar). Sin licencia visible.
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
