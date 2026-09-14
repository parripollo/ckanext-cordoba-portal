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
