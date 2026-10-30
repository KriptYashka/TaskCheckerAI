import asyncio
import json
import sys

from config import get_settings
from services.verification import VerificationService

TASK = """\
Напишите программу на Python, которая читает два целых числа из стандартного ввода
и выводит их сумму. Гарантируется, что числа по модулю не превышают 10^9."""

REFERENCE = "7"

SOLUTION_OK = b"""\
import sys

def main():
    data = sys.stdin.read().split()
    a, b = int(data[0]), int(data[1])
    print(a + b)

if __name__ == "__main__":
    main()
"""

SOLUTION_BAD = b"""\
a = input()
b = input()
print(int(a) * int(b))
"""


async def main() -> None:
    settings = get_settings()
    service = VerificationService(settings)
    print(f"# sandbox backend: {service.runner_name}")

    for name, code in (("solution_ok.py", SOLUTION_OK), ("solution_bad.py", SOLUTION_BAD)):
        result = await service.verify(
            task=TASK,
            uploads=[(name, code)],
            reference=REFERENCE,
            max_score=10,
            stdin_data="3 4",
        )
        print(f"\n### {name}")
        print(json.dumps(json.loads(result.model_dump_json()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
