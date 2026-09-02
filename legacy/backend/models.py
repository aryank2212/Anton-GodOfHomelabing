from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, Float,
    UniqueConstraint, Index, Enum as SAEnum,
)
from sqlalchemy.sql import func
from .database import Base
import enum


class EntryType(str, enum.Enum):
    JOURNAL = "Journal"
    POETRY = "Poetry"
    LETTER = "Letter To Future Self"
    CHRONICLE = "Chronicle"
    ESSAY = "Philosophy Essay"
    LOG = "Engineering Log"
    DREAM = "Dream"
    IDEA = "Idea"
    REFLECTION = "Reflection"
    OBSERVATION = "Observation"
    SYSTEM = "System"


class Visibility(str, enum.Enum):
    PRIVATE = "private"
    SHARED = "shared"
    PUBLIC = "public"
    AGENT_ONLY = "agent-only"
    SYSTEM = "system"


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"
    AGENT = "agent"
    READ_ONLY = "read-only"


class EventSeverity(str, enum.Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AuditAction(str, enum.Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    READ = "read"
    AGENT_ACCESS = "agent_access"
    API_ACCESS = "api_access"
    AUTHENTICATION = "authentication"
    SEARCH = "search"
    LOGIN = "login"
    LOGOUT = "logout"
    EXPORT = "export"
    IMPORT = "import"
    BACKUP = "backup"
    RESTORE = "restore"


# ============================================================
# User & Authentication
# ============================================================

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(128), unique=True, nullable=False)
    email = Column(String(256), unique=True, nullable=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(32), default=UserRole.USER.value, nullable=False)
    is_active = Column(Integer, default=1, nullable=False)
    display_name = Column(String(256), nullable=True)
    email_verified = Column(Integer, default=0, nullable=False)
    must_change_password = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    last_login_at = Column(DateTime(timezone=True), nullable=True)


class OtpCode(Base):
    __tablename__ = "otp_codes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    email = Column(String(256), nullable=False)
    purpose = Column(String(32), nullable=False)
    code_hash = Column(String(64), nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    max_attempts = Column(Integer, default=5, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_otp_codes_user_purpose", "user_id", "purpose"),
        Index("ix_otp_codes_expires_at", "expires_at"),
    )


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(128), unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(512), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_accessed_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        Index("ix_sessions_session_id", "session_id"),
        Index("ix_sessions_user_id", "user_id"),
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    key_hash = Column(String(256), unique=True, nullable=False)
    name = Column(String(128), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(32), default=UserRole.AGENT.value, nullable=False)
    is_active = Column(Integer, default=1, nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ============================================================
# Entry / Memory
# ============================================================

class Entry(Base):
    __tablename__ = "entries"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    title = Column(String(512), nullable=True)
    content = Column(Text, nullable=False)
    entry_type = Column(String(64), default=EntryType.JOURNAL.value, nullable=False)
    tags = Column(String(1024), nullable=True)
    mood = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    summary = Column(Text, nullable=True)
    embedding = Column(Text, nullable=True)
    visibility = Column(String(32), default=Visibility.PRIVATE.value, nullable=False)
    source = Column(String(64), default="journal", nullable=False)
    metadata_json = Column(Text, nullable=True)
    is_pinned = Column(Integer, default=0, nullable=False)

    __table_args__ = (
        Index("ix_entries_created_at", "created_at"),
        Index("ix_entries_visibility", "visibility"),
        Index("ix_entries_source", "source"),
        Index("ix_entries_entry_type", "entry_type"),
        Index("ix_entries_tags", "tags"),
        Index("ix_entries_user_id", "user_id"),
    )


# ============================================================
# Entities
# ============================================================

class Entity(Base):
    __tablename__ = "entities"

    id = Column(Integer, primary_key=True)
    name = Column(String(256), nullable=False)
    entity_type = Column(String(64), nullable=False, default="unknown")
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("name", "entity_type", name="uq_entity_name_type"),
        Index("ix_entities_name", "name"),
        Index("ix_entities_type", "entity_type"),
    )


class EntryEntity(Base):
    __tablename__ = "entry_entities"

    id = Column(Integer, primary_key=True)
    entry_id = Column(
        Integer, ForeignKey("entries.id", ondelete="CASCADE"), nullable=False
    )
    entity_id = Column(
        Integer, ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("entry_id", "entity_id", name="uq_entry_entity"),
        Index("ix_entry_entities_entry_id", "entry_id"),
        Index("ix_entry_entities_entity_id", "entity_id"),
    )


# ============================================================
# Collections
# ============================================================

class Collection(Base):
    __tablename__ = "collections"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    name = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    query = Column(String(512), nullable=True)
    visibility = Column(String(32), default=Visibility.PRIVATE.value, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        Index("ix_collections_user_id", "user_id"),
    )


class CollectionEntry(Base):
    __tablename__ = "collection_entries"

    id = Column(Integer, primary_key=True)
    collection_id = Column(
        Integer, ForeignKey("collections.id", ondelete="CASCADE"), nullable=False
    )
    entry_id = Column(
        Integer, ForeignKey("entries.id", ondelete="CASCADE"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("collection_id", "entry_id", name="uq_collection_entry"),
        Index("ix_collection_entries_collection_id", "collection_id"),
        Index("ix_collection_entries_entry_id", "entry_id"),
    )


# ============================================================
# Settings
# ============================================================

class Setting(Base):
    __tablename__ = "settings"

    key = Column(String(128), primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


# ============================================================
# Events
# ============================================================

class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True)
    source = Column(String(64), nullable=False)
    event_type = Column(String(128), nullable=False)
    severity = Column(String(16), default=EventSeverity.INFO.value, nullable=False)
    title = Column(String(512), nullable=True)
    description = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_events_source_event_type", "source", "event_type"),
        Index("ix_events_created_at", "created_at"),
    )


# ============================================================
# Links
# ============================================================

class Link(Base):
    __tablename__ = "links"

    id = Column(Integer, primary_key=True)
    source_type = Column(String(64), nullable=False)
    source_id = Column(Integer, nullable=False)
    target_type = Column(String(64), nullable=False)
    target_id = Column(Integer, nullable=False)
    relationship = Column(String(128), nullable=True)
    weight = Column(Float, default=1.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_links_source", "source_type", "source_id"),
        Index("ix_links_target", "target_type", "target_id"),
        UniqueConstraint(
            "source_type", "source_id", "target_type", "target_id", "relationship",
            name="uq_link",
        ),
    )


# ============================================================
# Audit Log
# ============================================================

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    action = Column(String(64), nullable=False)
    actor_type = Column(String(32), nullable=False)
    actor_id = Column(Integer, nullable=True)
    actor_name = Column(String(256), nullable=True)
    resource_type = Column(String(64), nullable=False)
    resource_id = Column(Integer, nullable=True)
    details = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(512), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    status = Column(String(16), default="success", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_actor", "actor_type", "actor_id"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
        Index("ix_audit_logs_created_at", "created_at"),
    )


# ============================================================
# Queue / Jobs
# ============================================================

class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    job_type = Column(String(64), nullable=False)
    status = Column(String(16), default="pending", nullable=False)
    priority = Column(Integer, default=0, nullable=False)
    payload = Column(Text, nullable=True)
    result = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)
    max_retries = Column(Integer, default=3, nullable=False)
    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_jobs_status_type", "status", "job_type"),
        Index("ix_jobs_scheduled", "status", "scheduled_at"),
    )


# ============================================================
# Backups
# ============================================================

class Backup(Base):
    __tablename__ = "backups"

    id = Column(Integer, primary_key=True)
    filename = Column(String(512), nullable=False)
    size_bytes = Column(Integer, nullable=True)
    checksum = Column(String(128), nullable=True)
    backup_type = Column(String(32), default="full", nullable=False)
    includes = Column(String(512), nullable=True)
    status = Column(String(16), default="completed", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
