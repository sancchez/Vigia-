# Setup de OWASP Juice Shop como target de laboratorio

Juice Shop es la aplicacion vulnerable de referencia contra la que corre el
harness de evaluacion (`eval/run_eval.py` + `eval/ground_truth.yaml`). Es un
proyecto OWASP publico, legal de escanear indefinidamente, y por eso es el
set de evaluacion fijo que pide la seccion 8.2 del plan maestro.

Repo oficial: https://github.com/juice-shop/juice-shop

Hay dos formas de levantarlo. Usa la que tengas disponible.

---

## Opcion A — Docker Compose (RECOMENDADA, para cuando Docker este instalado)

**Estado en esta maquina: Docker NO esta instalado todavia** (confirmado por
otro agente del equipo). Esta seccion queda lista para el dia que se instale
Docker Desktop (Windows) o Docker Engine.

### Requisitos
- Docker + Docker Compose v2 (`docker compose version` debe responder).

### Pasos
1. Desde la raiz del proyecto, entra a la carpeta `eval/`:
   ```
   cd D:\freestyle\ciberseguridad\eval
   ```
2. Levanta el servicio en segundo plano:
   ```
   docker compose up -d
   ```
3. Verifica que el contenedor esta corriendo:
   ```
   docker compose ps
   docker compose logs -f juice-shop
   ```
   Espera el mensaje en logs similar a `info: Server listening on port 3000`.
4. Abre `http://localhost:3000` en el navegador — debe verse la tienda de
   Juice Shop (banner "OWASP Juice Shop" y catalogo de productos).
5. Verificacion programatica (sin navegador):
   ```
   curl http://localhost:3000/rest/admin/application-version
   ```
   Debe responder JSON con un campo `version`.
6. Para apagar:
   ```
   docker compose down
   ```

El archivo de configuracion vive en `eval/docker-compose.yml` — usa la imagen
oficial `bkimminich/juice-shop` y mapea el puerto 3000 del contenedor al 3000
del host.

---

## Opcion B — Sin Docker, directo con Node.js (usar mientras Docker no este disponible)

### Requisitos
- **Node.js**: version LTS recomendada por el proyecto Juice Shop (Node 18.x o
  20.x funcionan con las versiones recientes de Juice Shop; revisa el campo
  `engines` en el `package.json` del repo clonado para la version exacta
  soportada por el commit que descargues). `npm` viene incluido con Node.
- **git** para clonar el repositorio.
- Espacio en disco: ~500 MB tras `npm install` (node_modules + dependencias).

Nota: no instales Node ni git tu mismo si no estan presentes — solo sigue
estos pasos cuando esas herramientas ya esten disponibles en la maquina.

### Pasos exactos

1. Clonar el repositorio oficial (fuera de este proyecto de eval, para no
   mezclar el codigo de Juice Shop con el harness — por ejemplo en una
   carpeta hermana):
   ```
   git clone https://github.com/juice-shop/juice-shop.git
   cd juice-shop
   ```

2. Instalar dependencias:
   ```
   npm install
   ```
   Esto puede tardar varios minutos la primera vez (descarga node_modules
   completo). Si falla por version de Node incompatible, revisar
   `package.json` -> campo `"engines"` para la version exacta requerida por
   ese commit y usar un manejador de versiones de Node (ej. `nvm`) para
   cambiarla — sin instalar nada nuevo por tu cuenta si no se te ha pedido.

3. Arrancar la aplicacion:
   ```
   npm start
   ```
   Por defecto escucha en el puerto **3000** (`http://localhost:3000`).

4. Verificar que esta corriendo:
   - En el navegador: abrir `http://localhost:3000` — debe verse la tienda.
   - Por linea de comandos:
     ```
     curl http://localhost:3000/rest/admin/application-version
     ```
     Debe devolver JSON con la version de la app.
   - En logs de la terminal donde corriste `npm start`, deberia aparecer
     algo como `info: Server listening on port 3000`.

5. Para detenerlo: `Ctrl+C` en la terminal donde corre `npm start`.

### Cambiar el puerto (opcional)

Si el 3000 esta ocupado, Juice Shop respeta la variable de entorno `PORT`:

```
# PowerShell
$env:PORT = "3001"
npm start
```

---

## Que endpoint/URL usar en las herramientas de escaneo

Una vez levantado (por cualquiera de las dos opciones), el target base para
el Agente de Escaneo (Nuclei, ZAP, etc. — ver `orchestrator`/`agents`, fuera
del alcance de esta carpeta) es:

```
http://localhost:3000
```

Este es exactamente el `scope` autorizado de facto para pruebas de laboratorio
(no requiere el documento de autorizacion firmada de la Fase 0, porque es una
app publica de OWASP diseñada para practicar — pero SI se sigue exigiendo esa
autorizacion firmada para cualquier objetivo que no sea de laboratorio, tal
como exige la seccion 0 del plan maestro).

## Relacion con el harness de evaluacion

Este target (localhost:3000 con Juice Shop corriendo) es contra lo que,
cuando el pipeline real (`orchestrator/agents`) exista, se ejecutara el
escaneo cuyo output se compara con `eval/ground_truth.yaml` usando
`eval/run_eval.py`. Ver `eval/README.md` para el loop completo.
