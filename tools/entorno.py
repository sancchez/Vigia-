"""Una sola fuente de verdad sobre "¿esto es un entorno local de laboratorio?".

Existe porque DOS decisiones de seguridad distintas dependen de la misma
pregunta, y antes de este módulo cada una la respondía sola (mal):

1. `tools/asset_verification.py` eximía de verificación de propiedad a
   cualquier IP privada/loopback/link-local, SIEMPRE -- incluido en
   producción. Eso convertía el gate de propiedad en un SSRF: bastaba
   registrar `169.254.169.254` (el metadata service de AWS/GCP/Azure) como
   "activo propio" para que el motor de escaneo lo alcanzara desde dentro de
   la red del servidor.
2. `auth/jwt_auth.py` caía a un secreto hardcodeado cuando `JWT_SECRET` no
   estaba en el entorno -- un despliegue real que olvidara la variable
   quedaba firmando tokens con un secreto público, sin ningún aviso.

Ambas ahora **fallan cerrado**: el comportamiento permisivo requiere
declarar el entorno explícitamente, y el default (variable ausente) es
producción.

Cómo se declara un entorno local:

- `VIGIA_ENTORNO=local` (recomendado -- una sola variable para todo), o
- `VIGIA_ALLOW_PRIVATE_TARGETS=true` para habilitar SOLO la exención de
  targets privados sin tocar nada de auth (útil en un runner de CI que sí
  quiere JWT_SECRET real pero escanea contenedores locales).

Se lee del entorno en cada llamada, no en tiempo de import: los tests
(`monkeypatch.setenv`) y un reload de configuración deben poder cambiarlo
sin recargar módulos.
"""

from __future__ import annotations

import os

_VALORES_VERDADEROS = frozenset({"1", "true", "yes", "y", "si", "sí", "on"})
_ENTORNOS_LOCALES = frozenset({"local", "dev", "desarrollo", "test", "testing"})


def _flag(nombre: str) -> bool:
    return (os.environ.get(nombre) or "").strip().lower() in _VALORES_VERDADEROS


def entorno_es_local() -> bool:
    """True solo si `VIGIA_ENTORNO` declara explícitamente un entorno no productivo."""
    return (os.environ.get("VIGIA_ENTORNO") or "").strip().lower() in _ENTORNOS_LOCALES


def permitir_targets_privados() -> bool:
    """True si este entorno puede escanear localhost / rangos de IP privada sin verificación.

    Nunca aplica a link-local (169.254.0.0/16, fe80::/10): ver
    `tools/asset_verification.py::es_target_prohibido`, que lo bloquea en
    todo entorno.
    """
    return entorno_es_local() or _flag("VIGIA_ALLOW_PRIVATE_TARGETS")
