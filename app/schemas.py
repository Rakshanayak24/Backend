from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import ConsultationStatus, Role


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    full_name: str = Field(min_length=1, max_length=160)
    phone: str | None = Field(default=None, max_length=30)


class LoginIn(BaseModel):
    email: EmailStr
    password: str
    mfa_code: str | None = Field(default=None, pattern=r"^\d{6}$")


class UserOut(BaseModel):
    id: str
    email: EmailStr
    full_name: str
    role: Role
    model_config = ConfigDict(from_attributes=True)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class DoctorCreate(BaseModel):
    specialty: str = Field(min_length=2, max_length=100)
    bio: str = Field(default="", max_length=4000)
    languages: list[str] = Field(default_factory=list, max_length=10)
    consultation_fee_minor: int = Field(default=0, ge=0, le=100000000)


class SlotCreate(BaseModel):
    starts_at: datetime
    ends_at: datetime

    @field_validator("ends_at")
    @classmethod
    def valid_order(cls, value, info):
        start = info.data.get("starts_at")
        if start and value <= start:
            raise ValueError("ends_at must be after starts_at")
        return value


class SlotOut(BaseModel):
    id: str
    doctor_id: str
    starts_at: datetime
    ends_at: datetime
    model_config = ConfigDict(from_attributes=True)


class BookingIn(BaseModel):
    doctor_id: str
    slot_id: str
    reason: str = Field(default="", max_length=1000)


class ConsultationOut(BaseModel):
    id: str
    doctor_id: str
    patient_id: str
    slot_id: str
    status: ConsultationStatus
    reason: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class StatusUpdate(BaseModel):
    status: ConsultationStatus
    notes: str | None = Field(default=None, max_length=10000)


class PrescriptionIn(BaseModel):
    items: list[dict] = Field(min_length=1, max_length=30)
    instructions: str = Field(default="", max_length=4000)
