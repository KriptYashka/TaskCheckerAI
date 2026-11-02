"""Экспорт OpenAPI-спецификации TaskCheckerAI.

Примеры:

    python scripts/export_openapi.py
    python scripts/export_openapi.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
DEFAULT_OUTPUT = ROOT / "docs" / "openapi.json"


def build_spec() -> dict:
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))

    from api.app import create_app
    from config import Settings

    app = create_app(Settings(_env_file=None))
    return app.openapi()


def render(spec: dict) -> str:
    return json.dumps(spec, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Экспорт OpenAPI-спецификации.")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Куда сохранить спецификацию (по умолчанию {DEFAULT_OUTPUT.relative_to(ROOT)}).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Только проверить, что файл спецификации актуален.",
    )
    args = parser.parse_args()

    spec = build_spec()
    rendered = render(spec)

    if args.check:
        if not args.out.exists():
            print(f"Файл спецификации не найден: {args.out}", file=sys.stderr)
            return 1
        current = args.out.read_text(encoding="utf-8")
        if current != rendered:
            print(
                f"Спецификация устарела: {args.out}. "
                "Запустите python scripts/export_openapi.py",
                file=sys.stderr,
            )
            return 1
        print(f"Спецификация актуальна: {args.out}")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(rendered, encoding="utf-8")
    print(f"OpenAPI-спецификация сохранена: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
