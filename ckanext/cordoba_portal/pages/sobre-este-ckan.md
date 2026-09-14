Este portal es un experimento abierto. Acá contamos con qué está hecho y cómo funciona.

## Qué CKAN usamos

Este portal corre sobre [CKAN](https://ckan.org/), el software libre de
portales de datos abiertos más usado del mundo (lo usan, entre otros,
datos.gob.ar y los dos portales de la provincia de los que tomamos datos).
Pero no es la versión oficial: es una **propuesta de CKAN que funciona
solo con PostgreSQL**, sin Solr ni Redis. La idea es que un portal de datos
pueda instalarse y mantenerse con una sola base de datos, como cualquier
aplicación web sencilla.

La propuesta está documentada en
[ckanito.cluster311.com](https://ckanito.cluster311.com/) y su código es
público: [parripollo/ckanito](https://github.com/parripollo/ckanito). Las
extensiones de CKAN que usamos (esquemas, cosecha, carga al datastore,
vistas de mapas y PDF, entre otras) también fueron probadas contra esa
versión y sus cambios están publicados.

## Cómo es este experimento

- **Cosechamos los portales oficiales de la provincia** una vez por semana
  con el mecanismo estándar de CKAN para eso (ckanext-harvest), extendido
  por nosotros para que cada dato conserve su procedencia.
- **Copiamos los archivos**, no solo los enlaces. Cada archivo se descarga
  una vez, de a uno y con pausas para no molestar al portal de origen, y
  se vuelve a bajar solo si cambió. Así este sitio sirve también como
  respaldo: si un dato desaparece del portal original, acá sigue.
- **Cada conjunto de datos dice de dónde viene**: el portal de origen, el
  enlace al original y la organización que lo produjo. Las organizaciones
  de cada portal se mantienen separadas, para que nunca se mezclen datos de
  fuentes distintas.
- **Los datos tabulares se cargan al datastore**, lo que permite
  consultarlos por API, verlos como tabla y filtrarlos sin descargar.
- **Todo el código de este portal es público**:
  [parripollo/ckanext-cordoba-portal](https://github.com/parripollo/ckanext-cordoba-portal).

## Qué no es

No es un sitio oficial del Gobierno de la Provincia de Córdoba ni de sus
organismos. Los datos son de quienes los producen y se redistribuyen tal
como fueron publicados, con la licencia de cada uno. Es un proyecto
experimental: puede cambiar, y puede tener errores. Si encontrás uno,
avisanos.
