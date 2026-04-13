"""SQLite cache for discovered reports and their data.

Schema:
- reports: hierarchical tree of menu nodes (informes / subinformes)
- report_data: tabular content for leaf reports (JSON + Markdown text)
- reports_fts: FTS5 index over title + content_text for search
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
    select,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from qa_intranet.config import CACHE_DB_PATH


class Base(DeclarativeBase):
    pass


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    parent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("reports.id"), nullable=True
    )
    is_leaf: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_fetched: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    data: Mapped[Optional["ReportData"]] = relationship(
        "ReportData", back_populates="report", uselist=False, cascade="all, delete-orphan"
    )


class ReportData(Base):
    __tablename__ = "report_data"

    report_id: Mapped[int] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), primary_key=True
    )
    content_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    schema_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    report: Mapped[Report] = relationship("Report", back_populates="data")


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def _init_fts(engine) -> None:
    """Create the FTS5 virtual table and keep it in sync via triggers."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS reports_fts USING fts5(
                    report_id UNINDEXED,
                    title,
                    content_text,
                    tokenize = 'unicode61 remove_diacritics 2'
                );
                """
            )
        )


def get_engine(db_path=None):
    global _engine, _SessionLocal
    if _engine is not None:
        return _engine
    path = db_path or CACHE_DB_PATH
    _engine = create_engine(f"sqlite:///{path}", future=True)

    @event.listens_for(_engine, "connect")
    def _fk_on(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(_engine)
    _init_fts(_engine)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# --- High-level helpers ------------------------------------------------------

def upsert_report(
    session: Session,
    *,
    slug: str,
    title: str,
    url: str,
    parent_id: Optional[int] = None,
    is_leaf: bool = True,
) -> Report:
    report = session.scalar(select(Report).where(Report.slug == slug))
    if report is None:
        report = Report(
            slug=slug, title=title, url=url, parent_id=parent_id, is_leaf=is_leaf
        )
        session.add(report)
        session.flush()
    else:
        report.title = title
        report.url = url
        report.parent_id = parent_id
        report.is_leaf = is_leaf
    return report


def store_report_data(
    session: Session,
    report: Report,
    *,
    rows: list[dict],
    columns: list[str],
    content_text: str,
    content_hash: str,
) -> None:
    report.last_fetched = datetime.utcnow()
    report.content_hash = content_hash
    payload_json = json.dumps(rows, ensure_ascii=False, default=str)
    schema_json = json.dumps(columns, ensure_ascii=False)

    if report.data is None:
        report.data = ReportData(
            content_json=payload_json,
            content_text=content_text,
            schema_json=schema_json,
        )
    else:
        report.data.content_json = payload_json
        report.data.content_text = content_text
        report.data.schema_json = schema_json

    session.flush()

    # Keep FTS index in sync (simple delete+insert).
    session.execute(
        text("DELETE FROM reports_fts WHERE report_id = :rid"),
        {"rid": report.id},
    )
    session.execute(
        text(
            "INSERT INTO reports_fts(report_id, title, content_text) "
            "VALUES (:rid, :title, :text)"
        ),
        {"rid": report.id, "title": report.title, "text": content_text},
    )


def list_tree(session: Session) -> list[dict]:
    """Return all reports as a flat list of dicts ordered by parent then title."""
    rows = session.scalars(
        select(Report).order_by(Report.parent_id.is_(None).desc(), Report.title)
    ).all()
    return [
        {
            "id": r.id,
            "slug": r.slug,
            "title": r.title,
            "url": r.url,
            "parent_id": r.parent_id,
            "is_leaf": r.is_leaf,
            "last_fetched": r.last_fetched.isoformat() if r.last_fetched else None,
        }
        for r in rows
    ]


def search(session: Session, query: str, limit: int = 10) -> list[dict]:
    """Full-text search over title + content_text."""
    # FTS5 MATCH with basic sanitization — strip double quotes.
    q = query.replace('"', " ").strip()
    if not q:
        return []
    result = session.execute(
        text(
            """
            SELECT reports_fts.report_id, reports.title, reports.slug,
                   snippet(reports_fts, 2, '[', ']', '…', 12) AS snippet
            FROM reports_fts
            JOIN reports ON reports.id = reports_fts.report_id
            WHERE reports_fts MATCH :q
            ORDER BY rank
            LIMIT :lim
            """
        ),
        {"q": q, "lim": limit},
    )
    return [dict(row._mapping) for row in result]


def get_report_detail(session: Session, report_id: int) -> Optional[dict]:
    report = session.get(Report, report_id)
    if report is None:
        return None
    data = report.data
    return {
        "id": report.id,
        "slug": report.slug,
        "title": report.title,
        "url": report.url,
        "parent_id": report.parent_id,
        "is_leaf": report.is_leaf,
        "last_fetched": report.last_fetched.isoformat() if report.last_fetched else None,
        "columns": json.loads(data.schema_json) if data and data.schema_json else [],
        "rows": json.loads(data.content_json) if data and data.content_json else [],
        "content_text": data.content_text if data else None,
    }


def get_report_by_slug(session: Session, slug: str) -> Optional[Report]:
    return session.scalar(select(Report).where(Report.slug == slug))
