from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, Any
from datetime import datetime
from .models import EntryType, Visibility, UserRole, EventSeverity


# ============================================================
# Auth
# ============================================================

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1)


class LoginResponse(BaseModel):
    session_id: str
    user: "UserResponse"
    expires_at: datetime
    must_change_password: Optional[bool] = None


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=8, max_length=256)


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    password: str = Field(..., min_length=8, max_length=256)
    email: Optional[str] = Field(None, max_length=256)
    display_name: Optional[str] = Field(None, max_length=256)
    role: str = UserRole.USER.value


class UserUpdate(BaseModel):
    email: Optional[str] = None
    display_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class UserResponse(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    role: str
    is_active: bool
    display_name: Optional[str] = None
    email_verified: Optional[bool] = None
    must_change_password: Optional[bool] = None
    created_at: datetime
    last_login_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class AdminUserRow(UserResponse):
    online: bool = False
    last_ip: Optional[str] = None
    last_seen: Optional[datetime] = None
    entry_count: int = 0
    session_count: int = 0


class AdminSessionRow(BaseModel):
    id: int
    user_id: int
    username: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    active: bool = False
    created_at: datetime
    last_accessed_at: Optional[datetime] = None
    expires_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AdminEntryRow(BaseModel):
    id: int
    user_id: Optional[int] = None
    username: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    entry_type: str = "Journal"
    visibility: str = "private"
    source: Optional[str] = None
    mood: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class OtpSendRequest(BaseModel):
    purpose: str = "verify_email"


class OtpSendResponse(BaseModel):
    sent: bool
    email: str
    expires_in: int
    resend_in: int
    method: str


class OtpVerifyRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=16)
    purpose: str = "verify_email"


class OtpVerifyResponse(BaseModel):
    verified: bool
    email_verified: Optional[bool] = None


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    role: str = UserRole.AGENT.value


class ApiKeyResponse(BaseModel):
    id: int
    name: str
    key: Optional[str] = None
    role: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# Entries / Memory
# ============================================================

class EntryBase(BaseModel):
    title: Optional[str] = Field(None, max_length=512)
    content: str = Field(..., min_length=1)
    entry_type: str = EntryType.JOURNAL.value
    tags: Optional[str] = None
    mood: Optional[str] = Field(None, max_length=64)
    visibility: str = Visibility.PRIVATE.value
    source: str = "journal"
    metadata_json: Optional[str] = None

    @field_validator("entry_type")
    @classmethod
    def validate_entry_type(cls, v):
        valid = {e.value for e in EntryType}
        if v not in valid:
            raise ValueError(f"Invalid entry_type. Must be one of: {valid}")
        return v

    @field_validator("visibility")
    @classmethod
    def validate_visibility(cls, v):
        valid = {e.value for e in Visibility}
        if v not in valid:
            raise ValueError(f"Invalid visibility. Must be one of: {valid}")
        return v


class EntryCreate(EntryBase):
    pass


class EntryUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    entry_type: Optional[str] = None
    tags: Optional[str] = None
    mood: Optional[str] = None
    visibility: Optional[str] = None
    source: Optional[str] = None
    metadata_json: Optional[str] = None

    @field_validator("entry_type")
    @classmethod
    def validate_entry_type(cls, v):
        if v is None:
            return v
        valid = {e.value for e in EntryType}
        if v not in valid:
            raise ValueError(f"Invalid entry_type. Must be one of: {valid}")
        return v

    @field_validator("visibility")
    @classmethod
    def validate_visibility(cls, v):
        if v is None:
            return v
        valid = {e.value for e in Visibility}
        if v not in valid:
            raise ValueError(f"Invalid visibility. Must be one of: {valid}")
        return v


class EntryResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    title: Optional[str] = None
    content: str
    entry_type: str
    tags: Optional[str] = None
    mood: Optional[str] = None
    visibility: str
    source: str
    summary: Optional[str] = None
    metadata_json: Optional[str] = None
    is_pinned: bool = False
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class EntrySummary(BaseModel):
    id: int
    title: Optional[str] = None
    entry_type: str
    source: str
    visibility: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# Entities
# ============================================================

class EntityCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    entity_type: str = "unknown"
    description: Optional[str] = None


class EntityResponse(BaseModel):
    id: int
    name: str
    entity_type: str
    description: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# Collections
# ============================================================

class CollectionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    description: Optional[str] = None
    query: Optional[str] = None
    visibility: str = Visibility.PRIVATE.value


class CollectionResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    query: Optional[str] = None
    visibility: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CollectionDetail(BaseModel):
    collection: CollectionResponse
    entries: list[EntrySummary]


# ============================================================
# Events
# ============================================================

class EventCreate(BaseModel):
    source: str = Field(..., max_length=64)
    event_type: str = Field(..., max_length=128)
    severity: str = EventSeverity.INFO.value
    title: Optional[str] = None
    description: Optional[str] = None
    metadata_json: Optional[str] = None


class EventResponse(BaseModel):
    id: int
    source: str
    event_type: str
    severity: str
    title: Optional[str] = None
    description: Optional[str] = None
    metadata_json: Optional[str] = None
    user_id: Optional[int] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# Links
# ============================================================

class LinkCreate(BaseModel):
    source_type: str
    source_id: int
    target_type: str
    target_id: int
    relationship: Optional[str] = None
    weight: float = 1.0


class LinkResponse(BaseModel):
    id: int
    source_type: str
    source_id: int
    target_type: str
    target_id: int
    relationship: Optional[str] = None
    weight: float = 1.0
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# Search
# ============================================================

class MemorySearchResult(BaseModel):
    id: int
    type: str = "entry"
    title: Optional[str] = None
    content: str
    source: str
    entry_type: Optional[str] = None
    visibility: Optional[str] = None
    tags: Optional[str] = None
    mood: Optional[str] = None
    similarity: Optional[float] = None
    created_at: datetime


class SearchRequest(BaseModel):
    q: str = ""
    source: Optional[str] = None
    entry_type: Optional[str] = None
    visibility: Optional[str] = None
    mood: Optional[str] = None
    tag: Optional[str] = None
    semantic: bool = False
    limit: int = Field(default=20, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


# ============================================================
# Audit
# ============================================================

class AuditLogResponse(BaseModel):
    id: int
    action: str
    actor_type: str
    actor_id: Optional[int] = None
    actor_name: Optional[str] = None
    resource_type: str
    resource_id: Optional[int] = None
    details: Optional[str] = None
    ip_address: Optional[str] = None
    duration_ms: Optional[int] = None
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DashboardResponse(BaseModel):
    total_users: int
    online_users: int
    active_sessions: int
    total_entries: int
    verified_users: int
    users: list[AdminUserRow]
    sessions: list[AdminSessionRow]
    recent_audit: list[AuditLogResponse]


# ============================================================
# Health
# ============================================================

class HealthResponse(BaseModel):
    status: str
    version: str
    database: dict
    embedding: dict
    worker: dict
    queue: dict
    uptime_seconds: float


# ============================================================
# Export / Import
# ============================================================

class ExportRequest(BaseModel):
    format: str = "json"
    entry_ids: Optional[list[int]] = None
    source: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    include_embeddings: bool = False


class ImportResult(BaseModel):
    imported: int
    skipped: int
    errors: list[str]


# ============================================================
# Backup
# ============================================================

class BackupResponse(BaseModel):
    id: int
    filename: str
    size_bytes: Optional[int] = None
    checksum: Optional[str] = None
    backup_type: str
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# Generic
# ============================================================

class PaginatedResponse(BaseModel):
    items: list[Any]
    total: int
    limit: int
    offset: int


class MessageResponse(BaseModel):
    message: str
    status: str = "ok"
