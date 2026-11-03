# Feed CertStream self-hosted (Fase 3c)

`api/certstream_listener.py` vigila en tiempo real los certificados SSL
emitidos en el mundo (Certificate Transparency logs) para detectar dominios
clon apenas se registran. Necesita un feed CertStream vivo — el público
histórico (`wss://certstream.calidog.io`) fue descontinuado (ver el
docstring del propio listener). Este directorio levanta un feed propio con
[`certstream-server-go`](https://github.com/d-Rickyy-b/certstream-server-go).

## Levantar el feed

```bash
cd deploy/certstream
docker compose up -d --build
```

La primera vez tarda un poco más porque construye la imagen desde el repo
oficial (no fijamos un tag de registry — ver el comentario en
`docker-compose.yml`). Una vez arriba, expone un websocket en
`ws://localhost:8080/full-stream` con el mismo shape que el CertStream
original (`message_type: "certificate_update"`,
`data.leaf_cert.all_domains`) — el listener de Vigia ya sabe parsear ese
shape sin cambios de código.

Puede tardar unos minutos en emitir el primer certificado tras arrancar:
está escuchando logs CT reales, no es instantáneo.

## Apuntar Vigia al feed local

En el `.env` del servicio de la API (ver `.env.example`, sección
"Vigilancia continua (CertStream)"):

```
VIGIA_CERTSTREAM_URL=ws://localhost:8080/full-stream
VIGIA_CERTSTREAM_ENABLED=true
```

Si el backend de Vigia corre en otro contenedor/red Docker, cambiá
`localhost` por el nombre del servicio (`vigia-certstream-server-go`) o el
host accesible desde ahí.

## Apagar

```bash
docker compose down
```

## Degradación (sin sorpresas)

Si este compose no está levantado (o `VIGIA_CERTSTREAM_URL` sigue en su
default muerto), el listener de Vigia lo detecta, lo loguea, y se apaga
solo — el resto de la API sigue funcionando con normalidad (ver
`api/certstream_listener.py::_run_listener`, mismo comportamiento de
degradación que ya existía antes de este change).

## Estado de validación — VERIFICADO EN VIVO (2026-08-01)

Ya no es teoría. Este compose se levantó de verdad y se comprobó cada
eslabón de la cadena, no solo que el contenedor arranque:

| Eslabón | Cómo se comprobó | Resultado |
|---|---|---|
| El servidor alcanza los logs CT reales | `docker logs vigia-certstream-server-go` | `ct-watcher.go: Starting worker for CT log: https://mon.sycamore.ct.letsencrypt.org/2026h2` (y ~20 logs más), luego `Processed 7000 entries \| Queue length: 0` a los ~15 s |
| El websocket emite el shape que Vigia parsea | cliente `websocket-client` contra `ws://localhost:8080/full-stream` | 3/3 mensajes con `message_type = "certificate_update"` y `data.leaf_cert.all_domains` poblado (p. ej. `['*.677889.cc', '677889.cc']`) |
| El cliente Python `certstream` consume ese endpoint | `certstream.listen_for_events(cb, url="ws://localhost:8080/full-stream")` | 3 mensajes `certificate_update` recibidos por el mismo callback que usa `api/certstream_listener.py::_run_listener` |
| El healthcheck del compose es ejecutable | `docker exec ... wget -q --spider http://localhost:8080/example.json` | exit 0 (`wget` está en la imagen) |

Se cambió el `build:` desde git por `image: 0rickyy0/certstream-server-go:latest`
justamente porque esa imagen es la que se verificó (y evita 5 minutos de
build y una toolchain Go en el host del cliente).

Lo único que sigue dependiendo de tu entorno: **salida a internet** hacia los
logs CT públicos. Si tu red la bloquea, el contenedor arranca igual pero
`Processed N entries` nunca sube — mirá los logs antes de dar la vigilancia
por buena.

## Si NO vas a levantar este feed

No pasa nada, pero entonces **la vigilancia continua de marca la hace el
barrido programado**, no CertStream: `api/scheduler.py::run_brand_cycle_once()`
corre dnstwist sobre cada dominio verificado y cada marca vigilada cada
`VIGIA_BRAND_CYCLE_HOURS` horas (default 24) y escribe los mismos hallazgos
`dominio_variante`. La diferencia real es la latencia de detección: minutos
(CertStream, en cuanto se emite el certificado) vs. horas (barrido). Decí eso
al cliente, no "monitoreo en tiempo real", si no levantaste este contenedor.
