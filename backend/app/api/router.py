from fastapi import APIRouter

from app.modules.auth.router import router as auth_router
from app.modules.conversations.router import router as conversations_router
from app.modules.documents.public_router import router as public_documents_router
from app.modules.documents.router import router as admin_documents_router
from app.modules.knowledge_bases.router import router as admin_knowledge_bases_router
from app.modules.operations.home_router import router as home_router
from app.modules.operations.router import admin_router as operations_admin_router
from app.modules.operations.router import notification_router
from app.modules.system.router import router as system_router
from app.modules.tasks.router import router as admin_tasks_router
from app.modules.users.admin_router import router as admin_users_router
from app.modules.users.router import router as me_router


api_router = APIRouter()
api_router.include_router(system_router, prefix="/system", tags=["system"])
api_router.include_router(auth_router, prefix="/auth", tags=["authentication"])
api_router.include_router(me_router, prefix="/me", tags=["personal profile"])
api_router.include_router(home_router, tags=["home and search"])
api_router.include_router(conversations_router, tags=["conversations"])
api_router.include_router(public_documents_router, tags=["citations and documents"])
api_router.include_router(notification_router, tags=["notifications"])
api_router.include_router(admin_knowledge_bases_router, prefix="/admin", tags=["admin knowledge bases"])
api_router.include_router(admin_documents_router, prefix="/admin", tags=["admin documents"])
api_router.include_router(admin_tasks_router, prefix="/admin", tags=["admin tasks"])
api_router.include_router(admin_users_router, prefix="/admin", tags=["admin users"])
api_router.include_router(operations_admin_router, prefix="/admin", tags=["admin operations"])
