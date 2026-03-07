from datetime import datetime, timezone

from sqlalchemy import String, Float, DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class GDELTVolume(Base):
    """GDELT news volume and tone per keyword."""
    __tablename__ = "gdelt_volume"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keyword: Mapped[str] = mapped_column(String, index=True)
    timestamp: Mapped[str] = mapped_column(String, index=True)  # YYYYMMDDTHHMMSSZ
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    avg_tone: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SentimentScore(Base):
    """AI-generated sentiment risk score (BYOK LLM)."""
    __tablename__ = "sentiment_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, index=True)  # YYYY-MM-DD
    risk_score: Mapped[float] = mapped_column(Float)  # 1-10
    risk_factors: Mapped[str] = mapped_column(Text, default="")  # JSON array of strings
    source: Mapped[str] = mapped_column(String, default="")  # "openai" or "anthropic"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NewsHeadline(Base):
    """News headline from Finnhub or other providers."""
    __tablename__ = "news_headlines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50), default="finnhub")
    headline: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    category: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
