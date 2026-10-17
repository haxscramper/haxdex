class FileTreeViewConfig(BaseModel, extra="forbid"):
    root_dirs: list[DirConfig]
    reference_dir: Optional[DirConfig] = None
    reference_tree_cache_path: OutputFile = Path("/tmp/reference_tree_cache.sqlite")
    visual_tree_cache_path: OutputFile = Path("/tmp/input_tree_cache.sqlite")
    drop_cache_files: bool = False
    user_edit_path: OutputFile = Path("~/tmp/user_actions.sqlite").expanduser()


def run_tree_view(self) -> None:
    if cfg.act:
        executor = ActionExecutor(cfg.act.execution)
        if cfg.action_file.exists():
            actions = load_actions(cfg.action_file, list(executor.handlers.values()))
            executor.init_db()
            executor.register_actions(actions)

    service.run_tree_view()

    qt_app = QApplication(sys.argv)

    assert self.cfg.file_tree_view
    win = FileTreeQueryWindow(
        ctx=self.ctx,
        db=self.db,
        indexer_instances=self.indexer_instances,
        builders=self.get_builders(),
        cfg=self.cfg,
    )

    win.show()
    sys.exit(qt_app.exec())
