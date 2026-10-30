import json

from fastapi.testclient import TestClient

from api.app import create_app
from config import get_settings

TASK = (
    "Напишите программу на Python, которая читает два целых числа из стандартного "
    "ввода и выводит их сумму."
)

SOLUTION = b"a, b = map(int, input().split())\nprint(a + b)\n"


def main() -> None:
    app = create_app(get_settings())
    with TestClient(app) as client:
        health = client.get("/health").json()
        print("HEALTH:", json.dumps(health, ensure_ascii=False))

        resp = client.post(
            "/api/v1/verify",
            data={"task": TASK, "reference": "7", "max_score": "10", "stdin_data": "3 4"},
            files={"files": ("main.py", SOLUTION, "text/x-python")},
        )
        print("STATUS:", resp.status_code)
        print("RESULT:", json.dumps(resp.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
