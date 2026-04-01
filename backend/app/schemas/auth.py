from pydantic import BaseModel, EmailStr
from typing import Optional
from app.models.employee import Role


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str          # NEW: long-lived refresh token
    token_type: str = "bearer"
    employee_id: int
    full_name: str
    role: Role
    terminal_id: Optional[str]


class EmployeeCreate(BaseModel):
    full_name: str
    email: str
    phone: Optional[str] = None
    pin: Optional[str] = None
    password: str
    role: Role = Role.CASHIER
    terminal_id: Optional[str] = None


class PinSet(BaseModel):
    pin: str


class PinVerify(BaseModel):
    pin: str


class EmployeeOut(BaseModel):
    id: int
    full_name: str
    email: str
    phone: Optional[str]
    role: Role
    is_active: bool
    terminal_id: Optional[str]

    class Config:
        from_attributes = True
