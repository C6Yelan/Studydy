from sqlmodel import SQLModel, Field
from datetime import datetime
from typing import Optional

class UserDocument(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    title: str
    file_path: str
    progress: int = Field(default=0)
    last_accessed: datetime = Field(default_factory=datetime.utcnow)

class UserStats(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    study_hours: float = Field(default=0.0)
    stories_completed: int = Field(default=0)