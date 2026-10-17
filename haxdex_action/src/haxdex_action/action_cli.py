def main():

    def register_db_actions() -> ActionExecutor:
        assert cfg.act
        executor = ActionExecutor(cfg.act.execution)
        actions = load_actions(cfg.action_file, list(executor.handlers.values()))
        executor.init_db()
        executor.register_actions(actions)
        return executor

    match command:
        case "do_act":
            executor = register_db_actions()
            assert cfg.act
            executor.execute_pending(max_operations=cfg.act.execution.max_actions)

        case "undo_act":
            assert cfg.act
            executor = ActionExecutor(cfg.act.execution)
            actions = load_actions(cfg.action_file, list(executor.handlers.values()))
            executor.init_db()
            executor.register_actions(actions)
            executor.revert_done()
