from aiogram import Router

from app.handlers.callbacks import router as callbacks_router
from app.handlers.commands import router as commands_router
from app.handlers.materials import router as materials_router


def build_router() -> Router:
    root = Router(name="root")
    root.include_router(commands_router)
    root.include_router(callbacks_router)
    root.include_router(materials_router)
    return root
