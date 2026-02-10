from pydantic import BaseModel


class MaterialUploadResponse(BaseModel):
    message: str
    document_id: int
    original_filename: str
    stored_filename: str


class DashboardProfile(BaseModel):
    display_name: str
    role: str


class DashboardActiveDoc(BaseModel):
    document_id: int | None
    title: str | None
    progress: float


class DashboardStats(BaseModel):
    study_hours: float
    stories_completed: int


class DashboardMeResponse(BaseModel):
    greeting: str
    profile: DashboardProfile
    active_doc: DashboardActiveDoc
    stats: DashboardStats

