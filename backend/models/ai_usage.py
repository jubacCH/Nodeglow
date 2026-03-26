"""AI usage tracking model — logs token consumption per API call."""
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text

from models.base import Base


class AiUsageLog(Base):
    __tablename__ = "ai_usage_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    feature = Column(String(64), nullable=False)     # daily_summary | postmortem | copilot
    model = Column(String(64), nullable=False)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    cost_usd = Column(Float, nullable=True)          # estimated cost
    metadata_json = Column(Text, nullable=True)      # optional extra context

    __table_args__ = (
        Index("ix_ai_usage_ts", timestamp.desc()),
        Index("ix_ai_usage_feature", "feature"),
    )
