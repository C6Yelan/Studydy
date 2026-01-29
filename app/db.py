import os
from sqlmodel import Session, SQLModel, create_engine
from typing import Generator

DATABASE_URL = "sqlite:///./studydy.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

def create_db_and_tables():
    from app.models.user import User
    from app.models.dashboard import UserDocument, UserStats
    SQLModel.metadata.create_all(engine)

def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session