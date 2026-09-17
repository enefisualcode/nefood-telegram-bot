"""Small persistent store for Telegram user profiles.

SQLite is part of Python and keeps the MVP data in one local file. Every
operation is scoped by Telegram user ID so one user cannot read another
user's profile through the bot.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import config


@dataclass(frozen=True)
class UserProfile:
    telegram_user_id: int
    age: int
    gender: str
    height_cm: float
    weight_kg: float
    activity_level: str
    goal: str
    updated_at: str = ""


class ProfileStore:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_profiles (
                    telegram_user_id INTEGER PRIMARY KEY,
                    age INTEGER NOT NULL,
                    gender TEXT NOT NULL,
                    height_cm REAL NOT NULL,
                    weight_kg REAL NOT NULL,
                    activity_level TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def save(self, profile: UserProfile) -> None:
        updated_at = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO user_profiles (
                    telegram_user_id, age, gender, height_cm, weight_kg,
                    activity_level, goal, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    age = excluded.age,
                    gender = excluded.gender,
                    height_cm = excluded.height_cm,
                    weight_kg = excluded.weight_kg,
                    activity_level = excluded.activity_level,
                    goal = excluded.goal,
                    updated_at = excluded.updated_at
                """,
                (
                    profile.telegram_user_id,
                    profile.age,
                    profile.gender,
                    profile.height_cm,
                    profile.weight_kg,
                    profile.activity_level,
                    profile.goal,
                    updated_at,
                ),
            )
            connection.commit()

    def get(self, telegram_user_id: int) -> UserProfile | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM user_profiles WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()
        if row is None:
            return None
        return UserProfile(
            telegram_user_id=row["telegram_user_id"],
            age=row["age"],
            gender=row["gender"],
            height_cm=row["height_cm"],
            weight_kg=row["weight_kg"],
            activity_level=row["activity_level"],
            goal=row["goal"],
            updated_at=row["updated_at"],
        )


_store: ProfileStore | None = None


def get_profile_store() -> ProfileStore:
    global _store
    if _store is None:
        _store = ProfileStore(config.PROFILE_DB_PATH)
    return _store
