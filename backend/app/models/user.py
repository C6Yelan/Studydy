from datetime import datetime, UTC

from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(index=True, sa_column_kwargs={"unique": True})
    password_hash: str
    learning_preference: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
