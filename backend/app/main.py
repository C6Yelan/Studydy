from fastapi import FastAPI

from app.routers import auth, health, root

app = FastAPI(title="Studydy Backend")


app.include_router(root.router)
app.include_router(health.router)
app.include_router(auth.router)
