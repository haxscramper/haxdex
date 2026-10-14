def include_visit_to_rich_tree(
    root: pb.IncludeVisit,
    absolute_prefix: str = "",
    max_depth: int = 10,
) -> Tree:

    def build_label(node: pb.IncludeVisit) -> str:
        return (
            f"{node.relative_path} ({node.absolute_path.removeprefix(absolute_prefix)}) "
            f"[dim](line {node.include_location_line}, "
            f"used {node.used_line_count}/{node.file_line_count})[/dim]"
        )

    def add_children(tree: Tree, node: pb.IncludeVisit, depth: int) -> None:
        for child in node.nested:
            branch = tree.add(build_label(child))
            if depth < max_depth:
                add_children(branch, child, depth + 1)

    tree = Tree(build_label(root))
    add_children(tree, root, 0)
    return tree


@beartype
def get_include_tree(
    text: str | dict[str, str],
    stable_test_dir: Path,
    main_file_suffix: str,
    code_dir_override: Path | None = None,
    **kwargs: Any,
) -> pb.IncludeVisit:
    result = run_reflection_tool_provider(
        text,
        code_dir=code_dir_override or stable_test_dir,
        output_dir=stable_test_dir,
        **kwargs,
    )

    matches = [
        tu.main_file_include_tree
        for tu in result.tus
        if tu.main_file_include_tree.absolute_path.endswith(main_file_suffix)
    ]

    assert len(matches) == 1, (
        f"Expected one include tree ending with {main_file_suffix!r}; "
        f"found {len(matches)}. Available paths: "
        f"{[tu.main_file_include_tree.absolute_path for tu in result.tus]}"
    )
    return matches[0]
