"""API key model for external REST API access."""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from models.base import Base


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    key_hash = Column(String(64), nullable=False, unique=True, index=True)
    prefix = Column(String(8), nullable=False)  # first 8 chars for identification
    role = Column(String(16), nullable=False, default="readonly")  # readonly | editor | admin
    enabled = Column(Boolean, default=True)
    last_used = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(String(64), nullable=True)
    note = Column(Text, nullable=True)
