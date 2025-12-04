from pydantic import BaseModel


class UserOut(BaseModel):
    id: int
    telegram_id: str
    username: str | None = None
    role: str
    approved: bool


