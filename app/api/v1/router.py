from fastapi import APIRouter

from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.content import router as content_router
from app.api.v1.endpoints.conversations import router as conversations_router
from app.api.v1.endpoints.users import router as users_router
from app.api.v1.endpoints.summaries import router as summaries_router
from app.api.v1.endpoints.quizzes import router as quizzes_router
from app.api.v1.endpoints.quiz_attempts import router as quiz_attempts_router
from app.api.v1.endpoints.study_plans import router as study_plans_router
from app.api.v1.endpoints.ai_jobs import router as ai_jobs_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(content_router)
api_router.include_router(conversations_router)
api_router.include_router(users_router)
api_router.include_router(summaries_router)
api_router.include_router(quizzes_router)
api_router.include_router(quiz_attempts_router)
api_router.include_router(study_plans_router)
api_router.include_router(ai_jobs_router)
