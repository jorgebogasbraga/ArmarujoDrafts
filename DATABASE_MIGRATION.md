# Database Migration Guide — ArmaDraft

## Current State: JSON File Persistence

Right now, every `DraftState` is serialised to a JSON file under `data/state_<division>.json`.
This is simple and works well for a bot of this scale, but has one limitation:
concurrent writes to the same file from multiple async tasks could theoretically cause
a race condition (mitigated by the `os.replace()` atomic write in `PersistenceService`).

---

## When to migrate to SQLite

Migrate when any of the following apply:

- You want to run multiple bot instances (sharding)
- You want to query history across seasons ("all-time kills by coach")
- You want the admin panel / standings to query data without the bot running
- The JSON files get large enough to cause noticeable startup latency (> ~50 picks)

---

## Step 1 — Add SQLAlchemy

```
pip install sqlalchemy aiosqlite
```

`aiosqlite` gives SQLAlchemy an async engine for SQLite.
When you later want PostgreSQL: `pip install asyncpg` and change the engine URL.

---

## Step 2 — Create models/db_models.py

```python
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, Float, Boolean, ForeignKey, JSON
import datetime

class Base(DeclarativeBase):
    pass

class DBDivision(Base):
    __tablename__ = "divisions"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    season: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    channel_id: Mapped[int] = mapped_column(Integer)
    team_size: Mapped[int] = mapped_column(Integer)
    total_points: Mapped[int] = mapped_column(Integer)
    snake_direction: Mapped[int] = mapped_column(Integer, default=1)
    current_round: Mapped[int] = mapped_column(Integer, default=1)
    current_coach_index: Mapped[int] = mapped_column(Integer, default=0)
    global_pick_counter: Mapped[int] = mapped_column(Integer, default=0)

class DBCoach(Base):
    __tablename__ = "coaches"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    discord_id: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(128))
    team_name: Mapped[str] = mapped_column(String(128))
    division_id: Mapped[int] = mapped_column(ForeignKey("divisions.id"))
    remaining_points: Mapped[int] = mapped_column(Integer)
    skip_count: Mapped[int] = mapped_column(Integer, default=0)
    makeup_picks_owed: Mapped[int] = mapped_column(Integer, default=0)
    pick_deadline: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_replaced: Mapped[bool] = mapped_column(Boolean, default=False)

class DBPick(Base):
    __tablename__ = "picks"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    division_id: Mapped[int] = mapped_column(ForeignKey("divisions.id"))
    coach_id: Mapped[int] = mapped_column(ForeignKey("coaches.id"))
    pick_number: Mapped[int] = mapped_column(Integer)
    round_number: Mapped[int] = mapped_column(Integer)
    pokemon_name: Mapped[str] = mapped_column(String(128))
    points_cost: Mapped[int] = mapped_column(Integer)
    is_makeup: Mapped[bool] = mapped_column(Boolean, default=False)
    is_bank: Mapped[bool] = mapped_column(Boolean, default=False)
    timestamp: Mapped[float] = mapped_column(Float)

class DBPickBank(Base):
    __tablename__ = "pick_banks"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    coach_id: Mapped[int] = mapped_column(ForeignKey("coaches.id"))
    division_id: Mapped[int] = mapped_column(ForeignKey("divisions.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    entries: Mapped[dict] = mapped_column(JSON)  # [{round: N, priority_list: [...]}]

class DBAlias(Base):
    __tablename__ = "aliases"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    alias: Mapped[str] = mapped_column(String(128), unique=True)
    canonical_name: Mapped[str] = mapped_column(String(128))
```

---

## Step 3 — Create database/repository.py

This layer replaces `PersistenceService`. Every read/write goes through here.

```python
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from models.db_models import Base, DBDivision, DBCoach, DBPick

DATABASE_URL = "sqlite+aiosqlite:///data/arma_draft.db"
# For PostgreSQL: "postgresql+asyncpg://user:pass@host/dbname"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def save_pick(session: AsyncSession, pick: DBPick):
    session.add(pick)
    await session.commit()

async def get_division(session: AsyncSession, name: str) -> DBDivision | None:
    from sqlalchemy import select
    result = await session.execute(
        select(DBDivision).where(DBDivision.name == name)
    )
    return result.scalar_one_or_none()
```

---

## Step 4 — Replace PersistenceService

In `services/persistence_service.py`, swap out the JSON logic:

```python
# Before (JSON):
def save(self, state: DraftState) -> None:
    with open(path, "w") as f:
        json.dump(state.to_dict(), f)

# After (SQLAlchemy, async):
async def save(self, state: DraftState) -> None:
    async with AsyncSessionLocal() as session:
        division = await get_division(session, state.division_name)
        if not division:
            division = DBDivision(name=state.division_name, ...)
            session.add(division)
        else:
            division.status = state.status.value
            division.current_coach_index = state.current_coach_index
            # ... update all fields
        await session.commit()
```

Because the rest of the codebase calls `persistence.save(state)` in one place,
**only this file needs to change**. The cogs and DraftService stay identical.

---

## Step 5 — Migrate AliasManager

Replace `data/aliases.json` reads/writes with:

```python
async def learn(self, alias: str, canonical: str):
    async with AsyncSessionLocal() as session:
        session.add(DBAlias(alias=alias, canonical_name=canonical))
        await session.commit()
```

---

## Step 6 — PostgreSQL for production

Change one line in `repository.py`:

```python
# SQLite (development / small deployments)
DATABASE_URL = "sqlite+aiosqlite:///data/arma_draft.db"

# PostgreSQL (production)
DATABASE_URL = "postgresql+asyncpg://user:password@localhost:5432/arma_draft"
```

No other code changes needed thanks to SQLAlchemy's abstraction layer.

---

## Migration order summary

1. `pip install sqlalchemy aiosqlite`
2. Create `models/db_models.py` (new file, doesn't touch anything)
3. Create `database/repository.py` (new file)
4. Rewrite `PersistenceService.save()` and `load()` to use the repository
5. Rewrite `AliasManager._load()` and `_save()` to use the repository
6. Call `await init_db()` in `main.py` on startup before `bot.run()`
7. Delete the `data/state_*.json` files (they're no longer needed)

Total files changed: **2** (`persistence_service.py`, `alias_manager.py`)
Total new files: **2** (`db_models.py`, `repository.py`)
Everything else stays exactly as-is.
