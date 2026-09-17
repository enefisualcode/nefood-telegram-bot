"""Persistent SQLite storage for confirmed meals and their food items."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import config
from services.nutrition_calculator import MealNutrition


@dataclass(frozen=True)
class StoredMealItem:
    food_name: str
    grams: int
    nutrition_status: str
    calories: float | None
    protein: float | None
    carbs: float | None
    fat: float | None
    fiber: float | None
    added_sugar: float | None
    sodium: float | None
    nutrition_source: str
    source_reference: str


@dataclass(frozen=True)
class StoredMeal:
    id: int
    telegram_user_id: int
    eaten_at: str
    input_source: str
    items: list[StoredMealItem] = field(default_factory=list)


class MealStore:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS meals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    eaten_at TEXT NOT NULL,
                    input_source TEXT NOT NULL CHECK(input_source IN ('photo', 'text'))
                );

                CREATE TABLE IF NOT EXISTS meal_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meal_id INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
                    food_name TEXT NOT NULL,
                    grams INTEGER NOT NULL,
                    nutrition_status TEXT NOT NULL,
                    calories REAL,
                    protein REAL,
                    carbs REAL,
                    fat REAL,
                    fiber REAL,
                    added_sugar REAL,
                    sodium REAL,
                    nutrition_source TEXT NOT NULL DEFAULT '',
                    source_reference TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_meals_user_time
                    ON meals(telegram_user_id, eaten_at);
                """
            )
            existing_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(meal_items)").fetchall()
            }
            for column in ("fiber", "added_sugar", "sodium"):
                if column not in existing_columns:
                    connection.execute(f"ALTER TABLE meal_items ADD COLUMN {column} REAL")
            connection.commit()

    @staticmethod
    def _provenance(item) -> tuple[str, str]:
        if item.source == "local" and item.record is not None:
            return item.record.source, item.record.source_reference
        if item.source == "usda" and item.usda is not None:
            return item.usda.source, f"FDC {item.usda.fdc_id}: {item.usda.description}"
        return "", ""

    def save(
        self,
        telegram_user_id: int,
        input_source: str,
        meal: MealNutrition,
        eaten_at: datetime | None = None,
    ) -> int:
        if input_source not in ("photo", "text"):
            raise ValueError("input_source must be 'photo' or 'text'")
        if not meal.items:
            raise ValueError("cannot save an empty meal")

        timestamp = eaten_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            raise ValueError("eaten_at must include a timezone")
        eaten_at_text = timestamp.astimezone(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "INSERT INTO meals (telegram_user_id, eaten_at, input_source) VALUES (?, ?, ?)",
                (telegram_user_id, eaten_at_text, input_source),
            )
            meal_id = int(cursor.lastrowid)
            for item in meal.items:
                nutrition = item.nutrition
                source, reference = self._provenance(item)
                connection.execute(
                    """
                    INSERT INTO meal_items (
                        meal_id, food_name, grams, nutrition_status,
                        calories, protein, carbs, fat, fiber, added_sugar, sodium,
                        nutrition_source, source_reference
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        meal_id,
                        item.name,
                        item.estimated_gross_grams,
                        item.status,
                        nutrition.calories if nutrition else None,
                        nutrition.protein if nutrition else None,
                        nutrition.carbs if nutrition else None,
                        nutrition.fat if nutrition else None,
                        nutrition.fiber if nutrition else None,
                        nutrition.added_sugar if nutrition else None,
                        nutrition.sodium if nutrition else None,
                        source,
                        reference,
                    ),
                )
            connection.commit()
        return meal_id

    def get_for_user(self, telegram_user_id: int) -> list[StoredMeal]:
        return self._get_for_user_query(
            telegram_user_id,
            "SELECT * FROM meals WHERE telegram_user_id = ? ORDER BY eaten_at, id",
            (telegram_user_id,),
        )

    def get_for_user_between(
        self,
        telegram_user_id: int,
        start: datetime,
        end: datetime,
    ) -> list[StoredMeal]:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("date range must include a timezone")
        return self._get_for_user_query(
            telegram_user_id,
            """
            SELECT * FROM meals
            WHERE telegram_user_id = ? AND eaten_at >= ? AND eaten_at < ?
            ORDER BY eaten_at, id
            """,
            (
                telegram_user_id,
                start.astimezone(timezone.utc).isoformat(),
                end.astimezone(timezone.utc).isoformat(),
            ),
        )

    def _get_for_user_query(
        self, telegram_user_id: int, query: str, parameters: tuple
    ) -> list[StoredMeal]:
        with closing(self._connect()) as connection:
            meal_rows = connection.execute(query, parameters).fetchall()
            result = []
            for meal_row in meal_rows:
                item_rows = connection.execute(
                    "SELECT * FROM meal_items WHERE meal_id = ? ORDER BY id",
                    (meal_row["id"],),
                ).fetchall()
                result.append(
                    StoredMeal(
                        id=meal_row["id"],
                        telegram_user_id=meal_row["telegram_user_id"],
                        eaten_at=meal_row["eaten_at"],
                        input_source=meal_row["input_source"],
                        items=[
                            StoredMealItem(
                                food_name=row["food_name"],
                                grams=row["grams"],
                                nutrition_status=row["nutrition_status"],
                                calories=row["calories"],
                                protein=row["protein"],
                                carbs=row["carbs"],
                                fat=row["fat"],
                                fiber=row["fiber"],
                                added_sugar=row["added_sugar"],
                                sodium=row["sodium"],
                                nutrition_source=row["nutrition_source"],
                                source_reference=row["source_reference"],
                            )
                            for row in item_rows
                        ],
                    )
                )
        return result


_store: MealStore | None = None


def get_meal_store() -> MealStore:
    global _store
    if _store is None:
        get_path = config.PROFILE_DB_PATH
        _store = MealStore(get_path)
    return _store
