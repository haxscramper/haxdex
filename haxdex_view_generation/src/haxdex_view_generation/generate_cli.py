# case "generation_explorer":
#     assert cfg.generation_explorer
#     qt_app = QApplication(sys.argv)
#     win = GenerationExplorerWindow(
#         settings=cfg.generation_explorer,
#         index_database=service.db,
#         safetensor_indexer=service.get_indexer(
#             SafetensorIndexer.asset_name
#         ),
#         wd_tag_indexer=service.get_indexer(WdTagIndexer.asset_name),
#         generation_params_indexer=service.get_indexer(
#             GenerationParamsIndexer.asset_name
#         ),
#     )

#     win.show()
#     sys.exit(qt_app.exec())

# case "generation_search":
#     assert cfg.generation_explorer
#     qt_app = QApplication(sys.argv)
#     win = ImageSearchWindow(
#         settings=cfg.generation_explorer,
#         index_database=service.db,
#         safetensor_indexer=service.get_indexer(
#             SafetensorIndexer.asset_name
#         ),
#         wd_tag_indexer=service.get_indexer(WdTagIndexer.asset_name),
#         generation_params_indexer=service.get_indexer(
#             GenerationParamsIndexer.asset_name
#         ),
#     )
