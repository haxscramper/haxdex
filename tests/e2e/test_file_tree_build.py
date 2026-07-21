from pathlib import Path

from tests.utils import init_index_service


def test_e2e_tree_build(stable_test_dir: Path):
    service = init_index_service(stable_test_dir)
    service.service.run_index()
