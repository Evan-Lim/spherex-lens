"""
spherexlens.routes
Extra API routers.  Every module in this package that defines a module-level
``router`` (a fastapi.APIRouter) is included by app.py at import time, so a
subsystem can add endpoints without editing app.py.
"""
import importlib
import pkgutil


def all_routers():
    routers = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{__name__}.{info.name}")
        router = getattr(mod, "router", None)
        if router is not None:
            routers.append(router)
    return routers
