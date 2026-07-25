"""Archivo de ejemplo -- fixture de eval/agent_quality (Fase 7), NUNCA código
de producción real. Contiene una falla de lógica de negocio deliberada
(compara el rol equivocado) para que el eval de revisión de código IA tenga
algo real que citar como evidencia."""


def puede_eliminar_usuario(usuario_actual, usuario_objetivo):
    # BUG deliberado para este fixture: compara el rol del OBJETIVO en vez
    # del rol de quien hace la solicitud -- cualquier usuario podría
    # eliminar a otro admin mientras el objetivo no sea admin.
    if usuario_objetivo.rol != "admin":
        return True
    return False
