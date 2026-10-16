"""Aggregate all v1 routers. Adding a feature = add a module in endpoints/ + one line here."""

from fastapi import APIRouter

from app.api.v1.endpoints import auth, chat, documents, health

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(documents.router)
api_router.include_router(chat.router)
