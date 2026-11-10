# AVC — Harness local de desarrollo con IA

AVC es un proyecto alfa, orientado primero a Windows, que investiga un recorrido
local y supervisado para modificar software mediante lenguaje natural e imágenes:

`pedido → inspección → candidato en copia → validación → captura → comparación → decisión humana`

El objetivo no es prometer una reproducción visual exacta. AVC intenta producir
una primera propuesta segura, mostrar evidencia comprobable y permitir que una
persona la aplique, la rechace o solicite otro intento.

## Capacidades actuales

- conversación local y selección de carpeta de proyecto;
- análisis de solo lectura y adjuntos;
- edición sobre copias candidatas;
- validaciones de código, compilación y pruebas cuando existe un adaptador;
- capturas antes/después en recorridos de escritorio compatibles;
- aplicación, reintento o descarte con decisión humana;
- pruebas de regresión con `unittest`.

## Límites importantes

- la generación de interfaces desde imágenes todavía es aproximada;
- los modelos locales pueden interpretar mal un pedido o afirmar algo no probado;
- el soporte varía según la tecnología;
- la creación autónoma de varios archivos y las refactorizaciones amplias siguen
  siendo experimentales;
- AVC no instala modelos, Aider, SDKs ni recursos propios del proyecto objetivo.
- la suite completa actual ejecuta 740 pruebas: 729 correctas y 11 incidencias
  heredadas del ciclo visual (6 fallos y 5 errores), publicadas expresamente
  como deuda conocida y no ocultadas.

La publicación busca recibir diagnósticos reproducibles, propuestas de
arquitectura y contribuciones pequeñas que puedan comprobarse con pruebas.

Consulta el [README principal](README.md), la [arquitectura](docs/ARCHITECTURE.md)
y la [guía para contribuir](CONTRIBUTING.md).

## Licencia

Distribuido bajo la [licencia Apache 2.0](LICENSE).
