# CONFIG_FILE := "~/defaultdirs/temporary_interchange/content_root_indexing.jsonc"
CONFIG_FILE := "~/tmp/full_index_organization/full_index_config.jsonc"

[env("DISPLAY", ":2")]
test:
    uv run python -m pytest -vv -ra \
        --log-level=DEBUG \
        --disable-warnings  \
        > test_results.tmp.log 2>&1
 
#  "tests/e2e/test_pbt_tree.py::test_cli_index_rerun" 
# tests/test_serde.py
#         "tests/e2e/test_pbt_tree.py"  \
#  "tests/gui/test_model_dump.py"
#        "tests/gui/test_execute_actions.py" 

[env("DISPLAY", ":2")]
test_gui:
    uv run python -m pytest -vv -ra tests/gui \
        --log-level=DEBUG \
        --capture=tee-sys \
        --disable-warnings \
        > test_results.tmp.log 2>&1


# "tests/test_search.py::test_full_text_search" 
# "tests/test_search.py::test_full_text_search"

index:
    uv run src/haxdex/cli/cli.py index "{{ CONFIG_FILE }}"

profile_index:
    uv run py-spy record --rate 50 --format chrometrace -o /tmp/haxdex-perf-index.json -- \
      python src/haxdex/cli/cli.py index "{{ CONFIG_FILE }}"

# --indexer file_summary \]
# --resource text_summary \
# --resource flm_server \

# --indexer comfy_input \
# --indexer exif_metadata \
# --indexer safetensor \
# --indexer generation_params \

# --enable-cache exif_metadata \
# --indexer exif_metadata \
# --indexer comfy_input \
# --limit-per-path 200 \
# --indexer wd_tags \
# --indexer ffprobe \
# --indexer pdf_pages \

schema:
    uv run src/haxdex/cli/cli.py schema "{{ CONFIG_FILE }}"

flat_query_view: schema
    uv run src/haxdex/cli/cli.py flat_query_view "{{ CONFIG_FILE }}" 

file_tree: schema
    uv run src/haxdex/cli/cli.py file_tree_view "{{ CONFIG_FILE }}" 

profile_file_tree:
    uv run py-spy record --format chrometrace -o /tmp/haxdex-perf-tree-view.json -- \
      python src/haxdex/cli/cli.py file_tree_view "{{ CONFIG_FILE }}"

visual_trash: schema
    uv run src/haxdex/cli/cli.py visual "{{ CONFIG_FILE }}"

do_act: schema
    uv run src/haxdex/cli/cli.py do_act "{{ CONFIG_FILE }}"

undo_act: schema
    uv run src/haxdex/cli/cli.py undo_act "{{ CONFIG_FILE }}"


gammaray_file_tree: schema
    uv run gammaray $(uv run which python) src/haxdex/cli/cli.py file_tree_view "{{ CONFIG_FILE }}"


run_arango:
    docker run -d -e ARANGO_ROOT_PASSWORD="test" --network host arangodb/enterprise:3.12.9.1 \
        --server.session-timeout 360000 \
        --vector-index
