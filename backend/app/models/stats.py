from sqlmodel import Field, SQLModel


class UserStats(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(
        foreign_key="user.id",
        index=True,
        sa_column_kwargs={"unique": True},
    )

    study_hours: float = Field(default=0.0)
    stories_completed: int = Field(default=0)

