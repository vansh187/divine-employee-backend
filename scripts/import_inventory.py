"""Import a plot inventory sheet (CSV) into `projects` / `properties`.

Expected columns (as exported from the site inventory sheets):
    Plot Number, Width (Mtr.), Length (Mtr.), Area in Sq Mtrs, Area in Sq Yard

Safe to re-run: the project is found by name (created if missing) and plots
are upserted on (project, plot number, ignoring case — needs migration 006).
Re-importing updates measurements only; a blank cell keeps the stored value,
and a plot's status (AVAILABLE / LOCKED / DEAL_LOCKED / SOLD) is never changed,
so live locks and deals are untouched. Blank rows and "Total" rows are skipped;
any other row whose plot number has no digit stops the import.

Usage:
    python -m scripts.import_inventory <csv> --project "Suraksha Enclave" [--location "..."] [--dry-run]

Uses DATABASE_URL from the environment / .env (the live database by default).
"""

import argparse
import asyncio
import csv
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import asyncpg

from app.core.config import get_settings

_SQFT_PER_SQM = Decimal("10.7639104")
_TOTAL_LABEL = re.compile(r"\btotal\b", re.IGNORECASE)

_COLUMNS = {
    "plot_no": "Plot Number",
    "width_m": "Width (Mtr.)",
    "length_m": "Length (Mtr.)",
    "area_sqm": "Area in Sq Mtrs",
    "area_sqyd": "Area in Sq Yard",
}


@dataclass(frozen=True)
class PlotRow:
    line: int
    plot_no: str
    width_m: Decimal | None
    length_m: Decimal | None
    area_sqm: Decimal | None
    area_sqyd: Decimal | None

    @property
    def area_sqft(self) -> Decimal | None:
        if self.area_sqm is None:
            return None
        return (self.area_sqm * _SQFT_PER_SQM).quantize(Decimal("0.01"))


class InventorySheetReader:
    """Parses and validates the sheet; collects every problem instead of stopping at the first."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self.errors: list[str] = []
        self.skipped: list[int] = []

    def read(self) -> list[PlotRow]:
        with self._path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            missing = [header for header in _COLUMNS.values() if header not in (reader.fieldnames or [])]
            if missing:
                self.errors.append(f"Missing columns: {', '.join(missing)}")
                return []
            rows: list[PlotRow] = []
            for line, record in enumerate(reader, start=2):
                plot_no = (record.get(_COLUMNS["plot_no"]) or "").strip()
                if not plot_no or _TOTAL_LABEL.search(plot_no):
                    self.skipped.append(line)  # blank separator or totals row
                    continue
                if not any(character.isdigit() for character in plot_no):
                    # Every real plot number has a digit (C1, B46A, B1-A); anything
                    # else is probably a heading or note — never import it as a plot.
                    self.errors.append(f"Line {line}: {plot_no!r} doesn't look like a plot number")
                    continue
                rows.append(
                    PlotRow(
                        line=line,
                        plot_no=plot_no,
                        width_m=self._number(record, "width_m", line),
                        length_m=self._number(record, "length_m", line),
                        area_sqm=self._number(record, "area_sqm", line),
                        area_sqyd=self._number(record, "area_sqyd", line),
                    )
                )
        self._check_duplicates(rows)
        return rows

    def _number(self, record: dict[str, str], key: str, line: int) -> Decimal | None:
        raw = (record.get(_COLUMNS[key]) or "").strip().replace(",", "")
        if not raw:
            return None
        try:
            value = Decimal(raw)
        except InvalidOperation:
            self.errors.append(f"Line {line}: '{_COLUMNS[key]}' is not a number: {raw!r}")
            return None
        if value <= 0:
            self.errors.append(f"Line {line}: '{_COLUMNS[key]}' must be positive: {raw!r}")
            return None
        return value

    def _check_duplicates(self, rows: list[PlotRow]) -> None:
        seen: dict[str, int] = {}
        for row in rows:
            key = row.plot_no.upper()
            if key in seen:
                self.errors.append(f"Line {row.line}: plot {row.plot_no} duplicates line {seen[key]}")
            seen[key] = row.line


class InventoryImporter:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def run(self, rows: list[PlotRow], project_name: str, location: str | None, dry_run: bool) -> None:
        connection = await asyncpg.connect(self._database_url)
        try:
            transaction = connection.transaction()
            await transaction.start()
            try:
                project_id, project_created = await self._get_or_create_project(connection, project_name, location)
                inserted, updated = await self._upsert_plots(connection, project_id, rows)
                if dry_run:
                    await transaction.rollback()
                else:
                    await transaction.commit()
            except Exception:
                await transaction.rollback()
                raise
        finally:
            await connection.close()

        verb = "Would" if dry_run else "Did"
        print(f"{verb} {'create' if project_created else 'use existing'} project '{project_name}' ({project_id})")
        print(f"{verb} insert {inserted} plots and update {updated} existing plots")
        if dry_run:
            print("Dry run — nothing was written.")

    async def _get_or_create_project(self, connection, name: str, location: str | None) -> tuple[str, bool]:
        existing = await connection.fetchval("SELECT id FROM projects WHERE lower(name) = lower($1)", name)
        if existing is not None:
            return str(existing), False
        created = await connection.fetchval(
            "INSERT INTO projects (name, location, status) VALUES ($1, $2, 'ACTIVE') RETURNING id", name, location
        )
        return str(created), True

    async def _upsert_plots(self, connection, project_id: str, rows: list[PlotRow]) -> tuple[int, int]:
        inserted = updated = 0
        for row in rows:
            was_inserted = await connection.fetchval(
                """
                INSERT INTO properties (project_id, plot_no, unit_type, area_sqft, width_m, length_m, area_sqm, area_sqyd)
                VALUES ($1, $2, 'Plot', $3, $4, $5, $6, $7)
                -- Matches uq_properties_project_plot_no_lower, so "b46a" updates
                -- the existing "B46A" (keeping its stored spelling).
                ON CONFLICT (project_id, lower(plot_no)) DO UPDATE SET
                    area_sqft = COALESCE(EXCLUDED.area_sqft, properties.area_sqft),
                    width_m = COALESCE(EXCLUDED.width_m, properties.width_m),
                    length_m = COALESCE(EXCLUDED.length_m, properties.length_m),
                    area_sqm = COALESCE(EXCLUDED.area_sqm, properties.area_sqm),
                    area_sqyd = COALESCE(EXCLUDED.area_sqyd, properties.area_sqyd)
                RETURNING (xmax = 0)
                """,
                project_id,
                row.plot_no,
                row.area_sqft,
                row.width_m,
                row.length_m,
                row.area_sqm,
                row.area_sqyd,
            )
            if was_inserted:
                inserted += 1
            else:
                updated += 1
        return inserted, updated


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a plot inventory CSV into projects/properties.")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--project", required=True, help="Project name (created if it doesn't exist)")
    parser.add_argument("--location", default=None, help="Project location, used only when creating it")
    parser.add_argument("--dry-run", action="store_true", help="Validate and report without writing")
    args = parser.parse_args()

    if not args.csv_path.is_file():
        print(f"File not found: {args.csv_path}", file=sys.stderr)
        return 1

    sheet = InventorySheetReader(args.csv_path)
    rows = sheet.read()
    if sheet.errors:
        print("The sheet has problems; nothing was imported:", file=sys.stderr)
        for error in sheet.errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    if not rows:
        print("No plot rows found; nothing to import.", file=sys.stderr)
        return 1

    incomplete = [row.plot_no for row in rows if None in (row.width_m, row.length_m, row.area_sqm, row.area_sqyd)]
    print(f"Read {len(rows)} plots from {args.csv_path.name}; skipped {len(sheet.skipped)} non-plot rows (lines {sheet.skipped})")
    if incomplete:
        print(
            f"Plots with blank measurement cells (a stored value is kept; a new plot gets blanks): "
            f"{', '.join(incomplete)}"
        )

    try:
        asyncio.run(InventoryImporter(get_settings().database_url).run(rows, args.project, args.location, args.dry_run))
    except (asyncpg.PostgresError, OSError) as exc:
        print(f"Import failed and was rolled back: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
