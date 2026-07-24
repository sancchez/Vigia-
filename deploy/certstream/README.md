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

## Estado de validación

Este compose fue escrito y revisado en este change (`sdd-apply` de
`fase3b-vigilancia-continua-certstream`) pero su levantamiento real
(imagen construyendo, servidor emitiendo certificados de verdad, el
listener conectándose y escribiendo un finding) debe confirmarse en un
host con acceso de salida a internet suficiente para clonar el repo de
build y para que `certstream-server-go` alcance los logs CT públicos —
verificalo en tu entorno de despliegue antes de depender de él en
producción.
