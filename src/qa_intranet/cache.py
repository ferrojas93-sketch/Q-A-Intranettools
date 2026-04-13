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


class Snapshot(Base):
    """A single Power BI QES response captured at a point in time.

    The natural grain is one snapshot per HAR entry (one DAX query = one
    visual worth of data). ``request_body`` is the DAX we'd have to send to
    replay the query, ``columns_json`` is the decoded column list, and
    ``rows`` is the list of ``SnapshotRow`` we extracted.
    """

    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_file: Mapped[str] = mapped_column(String, nullable=False, index=True)
    url: Mapped[str] = mapped_column(String, nullable=False)
    report_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, index=True
    )
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    columns_json: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    request_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, unique=True, index=True
    )

    rows: Mapped[list["SnapshotRow"]] = relationship(
        "SnapshotRow",
        back_populates="snapshot",
        cascade="all, delete-orphan",
        order_by="SnapshotRow.row_index",
    )


class SnapshotRow(Base):
    __tablename__ = "snapshot_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("snapshots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    row_json: Mapped[str] = mapped_column(Text, nullable=False)

    snapshot: Mapped[Snapshot] = relationship("Snapshot", back_populates="rows")


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def _init_fts(engine) -> None:
    """Create the FTS5 virtual tables."""
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
        conn.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS snapshot_rows_fts USING fts5(
                    snapshot_id UNINDEXED,
                    row_index UNINDEXED,
                    columns_text,
                    row_text,
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


# --- Snapshot helpers --------------------------------------------------------

def _report_id_from_url(url: str) -> Optional[str]:
    """Extract the Power BI report UUID embedded in some URLs."""
    import re
    m = re.search(
        r"reports/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        url, flags=re.IGNORECASE,
    )
    return m.group(1).lower() if m else None


def reset_snapshots(session: Session) -> None:
    """Delete every snapshot and its rows (for --reset)."""
    session.execute(text("DELETE FROM snapshot_rows_fts"))
    for row in session.scalars(select(SnapshotRow)).all():
        session.delete(row)
    for snap in session.scalars(select(Snapshot)).all():
        session.delete(snap)
    session.flush()


def upsert_snapshot(
    session: Session,
    *,
    source_file: str,
    url: str,
    columns: list[str],
    rows: list[list],
    title: Optional[str] = None,
    request_body: Optional[str] = None,
    content_hash: Optional[str] = None,
) -> Optional[Snapshot]:
    """Insert a snapshot or return None if one with the same content_hash exists."""
    if content_hash:
        existing = session.scalar(
            select(Snapshot).where(Snapshot.content_hash == content_hash)
        )
        if existing:
            return None

    snap = Snapshot(
        source_file=source_file,
        url=url,
        report_id=_report_id_from_url(url),
        title=title,
        columns_json=json.dumps(columns, ensure_ascii=False),
        row_count=len(rows),
        request_body=request_body,
        content_hash=content_hash,
    )
    session.add(snap)
    session.flush()

    for idx, row in enumerate(rows):
        sr = SnapshotRow(
            snapshot_id=snap.id,
            row_index=idx,
            row_json=json.dumps(row, ensure_ascii=False, default=str),
        )
        session.add(sr)
    session.flush()

    # Populate FTS index: one document per row with column header context.
    cols_text = " | ".join(columns)
    for idx, row in enumerate(rows):
        row_text = " | ".join(
            f"{c}={v}" for c, v in zip(columns, row) if v is not None
        )
        session.execute(
            text(
                "INSERT INTO snapshot_rows_fts"
                "(snapshot_id, row_index, columns_text, row_text) "
                "VALUES (:sid, :ri, :ct, :rt)"
            ),
            {"sid": snap.id, "ri": idx, "ct": cols_text, "rt": row_text},
        )
    return snap


def list_snapshots(
    session: Session, keyword: Optional[str] = None, limit: int = 200
) -> list[dict]:
    stmt = select(Snapshot).order_by(Snapshot.id)
    rows = session.scalars(stmt).all()
    out = []
    kw = (keyword or "").strip().lower()
    for s in rows:
        cols = json.loads(s.columns_json)
        if kw:
            haystack = " ".join([s.title or "", s.url, " ".join(cols)]).lower()
            if kw not in haystack:
                continue
        out.append({
            "id": s.id,
            "source_file": s.source_file,
            "url": s.url,
            "report_id": s.report_id,
            "title": s.title,
            "columns": cols,
            "row_count": s.row_count,
            "captured_at": s.captured_at.isoformat() if s.captured_at else None,
        })
        if len(out) >= limit:
            break
    return out


def get_snapshot_rows(
    session: Session, snapshot_id: int, *, offset: int = 0, limit: int = 50
) -> Optional[dict]:
    snap = session.get(Snapshot, snapshot_id)
    if snap is None:
        return None
    cols = json.loads(snap.columns_json)
    rows_stmt = (
        select(SnapshotRow)
        .where(SnapshotRow.snapshot_id == snapshot_id)
        .order_by(SnapshotRow.row_index)
        .offset(offset)
        .limit(limit)
    )
    rows = [json.loads(r.row_json) for r in session.scalars(rows_stmt).all()]
    return {
        "id": snap.id,
        "source_file": snap.source_file,
        "url": snap.url,
        "report_id": snap.report_id,
        "title": snap.title,
        "columns": cols,
        "total_rows": snap.row_count,
        "offset": offset,
        "returned_rows": len(rows),
        "rows": [dict(zip(cols, r)) for r in rows],
    }


def search_snapshot_rows(
    session: Session, query: str, *, limit: int = 20
) -> list[dict]:
    q = query.replace('"', " ").strip()
    if not q:
        return []
    result = session.execute(
        text(
            """
            SELECT f.snapshot_id, f.row_index,
                   snippet(snapshot_rows_fts, 3, '[', ']', '…', 12) AS snippet,
                   s.url, s.title
            FROM snapshot_rows_fts f
            JOIN snapshots s ON s.id = f.snapshot_id
            WHERE snapshot_rows_fts MATCH :q
            ORDER BY rank
            LIMIT :lim
            """
        ),
        {"q": q, "lim": limit},
    )
    return [dict(row._mapping) for row in result]
