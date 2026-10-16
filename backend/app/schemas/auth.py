import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Password = Annotated[str, Field(min_length=8, max_length=128)]


class RegisterRequest(BaseModel):
    email: EmailStr
    password: Password
    full_name: Annotated[str | None, Field(max_length=200)] = None

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        # "Alice@Example.com" and "alice@example.com" must be the same account.
        return value.strip().lower()


class LoginRequest(BaseModel):
    email: EmailStr
    password: Annotated[str, Field(min_length=1, max_length=128)]

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        return value.strip().lower()


class TokenResponse(BaseModel):
    """Returned on login/refresh for non-browser clients (CLI, Swagger UI).

    Browsers don't need it: the same tokens are set as httpOnly cookies.
    """

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    # from_attributes: build directly from the SQLAlchemy `User` object.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str | None
    created_at: datetime
