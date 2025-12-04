from pydantic import BaseModel
from typing import Optional


class UserOut(BaseModel):
    id: int
    telegram_id: str
    username: Optional[str] = None
    full_name: Optional[str] = None
    phone: Optional[str] = None
    role: str
    approved: bool
