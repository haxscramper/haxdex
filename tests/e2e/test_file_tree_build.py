from pathlib import Path

from tests.utils import init_index_service, init_file_tree_config


def test_e2e_tree_build(stable_test_dir: Path):
    index = init_index_service(stable_test_dir)

    dir = index.root_dir
    dir.joinpath("a.txt").write_text("random_content")
    dir.joinpath("b.txt").write_text("whatever")

    sub = dir / "sub"
    sub.mkdir(exist_ok=True, parents=True)
    sub.joinpath("sub_a.txt").write_text("nested")
    sub.joinpath("sub_b.txt").write_text("whatever 1123")

    index.service.run_index()

    tree_config = init_file_tree_config(index)