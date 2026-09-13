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
- [ ] Dominio: delegar `cbadatos.com.ar` a Cloudflare (NIC.ar), CNAME apex
      -> `uni.cluster311.com` (proxied), `www` -> apex, SSL Full (strict),
      certificado Origin CA de la zona instalado en el servidor (root).
- [ ] Mezclar la rama `cbadatos` del deploy, `bootstrap.sh cbadatos` (root
      corre el SQL de los pasos 3 y 4), `deploy.sh cbadatos`, root instala
      `nginx-cbadatos.conf`. Portal vacio en linea con el tema nuevo.

## 1. Home page

- [ ] Tema Midnight Blue (`templates-midnight-blue` / `public-midnight-blue`,
      el default de la proxima version de CKAN). Ya va en el env de la
      instancia; la extension solo extiende esos templates.
- [ ] `home/index.html` propio: texto corto que diga que es una iniciativa
      ciudadana para reunir en un solo lugar los datos de la provincia de
      Cordoba, y una caja de busqueda grande. Debajo, contadores (datasets,
      portales de origen, organizaciones) y accesos por portal de origen.
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

Propuesta (a decidir):

- Tres campos de dataset en el esquema scheming de la extension:
  - `source_portal` (select, obligatorio, facet "Portal de origen"). Las
    opciones viven en la extension: `cbadatos` (produccion propia),
    `datosgestionabierta`, `datosestadistica`, y se agregan las que
    vengan. Cada opcion tiene etiqueta y URL del portal.
  - `source_url` (URL del dataset en el portal de origen; link "Ver en el
    portal de origen"). Vacio en produccion propia.
  - `producer` (texto, facet "Productor"): quien produjo el dato. En
    gestion abierta es el titulo de la organizacion remota (Ministerio de
    Salud, ...). En estadistica es un valor fijo de la fuente (Direccion
    General de Estadistica y Censos) y sus "organizaciones" tematicas
    pasan a grupos (temas). En produccion propia lo carga quien sube.
- Organizaciones CKAN: una por portal de origen (la organizacion de la
  fuente de harvest: "Portal de Gestion Abierta", "Estadistica y Censos")
  mas las organizaciones propias. No se crean organizaciones remotas
  (`remote_orgs` en `only_local`), asi nunca chocan dos "Ministerio de
  Salud" de portales distintos. La organizacion dice de donde viene, el
  campo `producer` dice quien lo hizo, `source_portal` es el facet.
  Alternativa B: crear las organizaciones remotas con prefijo por portal
  y colgarlas de una organizacion padre por portal con ckanext-hierarchy;
  mas fiel para gestion abierta, inutil para estadistica (temas), 32
  organizaciones mas. Recomendacion: A, con B como cambio posible despues.
- Recursos: campo `source_url` tambien en el recurso (URL original del
  archivo), ademas del archivo copiado (ver 3).
- Mapear extras remotos a campos: `Frecuencia de actualización` (las tres
  grafias) -> `update_frequency`; `depto` -> `departamento`; `muncom` ->
  `municipio`. El resto de extras se conserva tal cual. Los extras que
  chocan con nombres de campos del esquema se descartan (si no, el
  dataset se rechaza en la validacion).
- Todo esto lo hace un harvester propio de la extension,
  `cordoba_ckan` (subclase del `ckan` de ckanext-harvest con
  `modify_package_dict`), configurado por fuente con `source_portal`,
  `producer` fijo (opcional) y `user_agent`. Es codigo de la extension, no
  del core ni del fork de harvest.

## 3. Harvest con archivos (ser backup, no un indice de links)

- ckanext-harvest NO baja archivos: su harvester `ckan` borra `url_type`
  y crea recursos con link al original ("we are only creating normal
  resources with links", `ckanharvester.py`, import_stage). Verificado
  2026-09-13. ckanext-archiver bajaria copias a un cache propio, pero no
  reemplaza la URL del recurso ni lo mete en el datastore: no sirve como
  backup navegable.
- [ ] Nuestro harvester `cordoba_ckan` baja cada recurso subido en el
      origen (`url_type = upload`) y lo guarda como upload propio
      (storage del portal); el link original queda en `source_url` del
      recurso. Los recursos que en el origen ya son links (121 "ENLACE",
      instagram, mapascordoba) quedan como links.
- [ ] Con cuidado con el servidor remoto: un archivo por vez, pausa entre
      pedidos (1-2 s), User-Agent identificable (`cbadatos.com.ar
      harvester`), reintentos con espera, tope de tamano por archivo
      (decidir: 200 MB?), y NO volver a bajar lo que no cambio (comparar
      `last_modified`/`size`/`hash` remotos con lo guardado; ETag /
      If-Modified-Since cuando el servidor los da).
- [ ] La descarga NO va dentro del import_stage (bloquearia el consumer):
      un job de CKAN por dataset (`ckan.lib.jobs`), encolado al terminar
      el import, con un solo worker para no paralelizar contra el origen.
- [ ] Frecuencia: primera pasada completa (12k archivos a ~1/s son dias:
      esta bien), luego semanal por fuente; el harvest de metadatos puede
      ser diario porque es barato.
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
- [ ] Portales no CKAN (ArcGIS Hub, Socrata, listas de archivos): un
      harvester por tipo, mas adelante.
- [ ] Produccion propia: organizaciones propias, usuarios editores,
      `source_portal = cbadatos`, formulario con `producer` obligatorio.

## 5. Extensiones a activar (ya revisadas en el demo)

- [ ] scheming (esquema propio en la extension), harvest (+ consumers y
      timer del deploy), xloader, geoview, pages (acerca de / fuentes),
      push_errors (Slack), api_tracking, dbquery. hierarchy solo si se
      elige la alternativa B. dcat para exponer todo como DCAT/JSON-LD.

## 6. Orden sugerido

1. Dominio + portal vacio en linea (seccion 0).
2. Esquema y campos de procedencia + home page (secciones 1 y 2).
3. Harvester `cordoba_ckan` con metadatos (sin archivos), primera fuente:
   gestion abierta (156 datasets, chico). Validar procedencia y facets.
4. Descarga de archivos + backup (seccion 3), primero gestion abierta
   (0,6 GB conocidos), despues estadistica.
5. Segunda fuente: estadistica (temas a grupos, producer fijo).
6. Produccion propia y otros portales.
