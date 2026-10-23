from app.scripts.run_worker import bounded_worker_concurrency, worker_command


def test_document_worker_defaults_to_three_concurrent_tasks():
    assert bounded_worker_concurrency(None) == 3
    assert "--concurrency=3" in worker_command({})


def test_document_worker_concurrency_is_bounded_between_one_and_three():
    assert bounded_worker_concurrency("0") == 1
    assert bounded_worker_concurrency("2") == 2
    assert bounded_worker_concurrency("3") == 3
    assert bounded_worker_concurrency("8") == 3
    assert bounded_worker_concurrency("invalid") == 3
