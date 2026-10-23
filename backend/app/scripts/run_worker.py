from __future__ import annotations

import os
from collections.abc import Mapping


MAX_DOCUMENT_CONCURRENCY = 3


def bounded_worker_concurrency(raw_value: str | None) -> int:
    try:
        requested = int(raw_value or MAX_DOCUMENT_CONCURRENCY)
    except ValueError:
        requested = MAX_DOCUMENT_CONCURRENCY
    return min(MAX_DOCUMENT_CONCURRENCY, max(1, requested))


def worker_command(environ: Mapping[str, str] | None = None) -> list[str]:
    values = environ if environ is not None else os.environ
    concurrency = bounded_worker_concurrency(values.get("CELERY_WORKER_CONCURRENCY"))
    max_tasks_per_child = values.get("CELERY_MAX_TASKS_PER_CHILD", "20")
    return [
        "celery",
        "-A",
        "app.workers.celery_app:celery_app",
        "worker",
        "--loglevel=INFO",
        f"--concurrency={concurrency}",
        f"--max-tasks-per-child={max_tasks_per_child}",
    ]


def main() -> None:
    command = worker_command()
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
