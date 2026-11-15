CONFIG_FILE := env_var('HAXDEX_CONFIG_FILE')

[env("DISPLAY", ":2")]
test:
    uv run python -m pytest -vv -ra \
        --log-level=DEBUG \
        --disable-warnings tests/gui/test_column_proxy.py \
        > test_results.tmp.log 2>&1
 
#  "tests/e2e/test_pbt_tree.py::test_cli_index_rerun" 
# tests/test_serde.
#          "tests/e2e/test_pbt_tree.py::test_generated_indexer_directory"  
#  "tests/gui/test_model_dump.py"
#        "tests/gui/test_execute_actions.py" 

[env("DISPLAY", ":2")]
test_gui:
    uv run python -m pytest -vv -ra tests/gui \
        --log-level=DEBUG \
        --capture=tee-sys \
        --disable-warnings \
        > test_results.tmp.log 2>&1

index:
    uv run python -X faulthandler haxdex_cli/src/haxdex_cli/cli.py index "{{ CONFIG_FILE }}"

profile_index:
    uv run py-spy record --rate 50 --format chrometrace -o /tmp/haxdex-perf-index.json -- \
      python haxdex_cli/src/haxdex_cli/cli.py index "{{ CONFIG_FILE }}"

schema:
    uv run haxdex_cli/src/haxdex_cli/cli.py schema "{{ CONFIG_FILE }}"

flat_query_view: schema
    uv run src/haxdex/cli/cli.py flat_query_view "{{ CONFIG_FILE }}" 

file_tree: schema
    uv run src/haxdex/cli/cli.py file_tree_view "{{ CONFIG_FILE }}" 

generation_explorer:
    uv run src/haxdex/cli/cli.py generation_explorer "{{ CONFIG_FILE }}"

generation_search:
    uv run src/haxdex/cli/cli.py generation_search "{{ CONFIG_FILE }}"

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



HAXDEX_ROOT := source_directory()
CONAN_PROFILE := env("CONAN_PROFILE")
# justfile triggers evaluation lazily, so this garbage here is necessary to ensure the
# hstd py lib is force-synced even if the hash/version mismatches in some place. 
CONAN_PYTHON_CONFIG := shell("uv lock --refresh-package hstd-py-lib --upgrade-package hstd-py-lib && uv run python -m hstd_py_lib.project_utils.get_python_config") \
    + " -c " + quote("user.haxorg:uv_project=" + HAXDEX_ROOT)


SUPPRESSION_FILE := HAXDEX_ROOT / "clang_suppressions.supp"

conan_validate target editable:
  uv run --project "{{HAXDEX_ROOT}}" \
    python -m "hstd_py_lib.project_utils.test_conan_install" \
    "{{HAXDEX_ROOT}}/{{target}}" \
    {{editable}} \
    --profile "{{CONAN_PROFILE}}" \
    --python-config {{quote(CONAN_PYTHON_CONFIG)}} \
    --warning-suppressions "{{SUPPRESSION_FILE}}" \
    --log-dir "{{HAXDEX_ROOT}}/build/logs/{{target}}"

py_validate target *args:
    rm -rf {{HAXDEX_ROOT}}/build/logs/{{target}}
    mkdir -p {{HAXDEX_ROOT}}/build/logs/{{target}}
    uv run --project "{{HAXDEX_ROOT}}" \
        python -m "hstd_py_lib.project_utils.test_uv_install" \
        {{HAXDEX_ROOT}}/{{target}} \
        --conan-profile={{CONAN_PROFILE}} \
        --output-dir={{HAXDEX_ROOT}}/build/logs/{{target}} \
        {{args}}

validate_all *args:
    just py_validate haxdex_view_tree {{args}}
    just py_validate haxdex_action {{args}}
    just py_validate haxdex_cli {{args}}
    just py_validate haxdex_core {{args}}
    just py_validate haxdex_read_code_tsitter {{args}}
    just py_validate haxdex_read_logs {{args}}
    just py_validate haxdex_read_media {{args}}
    just py_validate haxdex_view_generation {{args}}
    just py_validate haxdex_view_results {{args}}
    just py_validate hstd_py_qt {{args}}

conan_workspace_install_monorepo:
  conan workspace super-install \
    --output-folder=build/conan_super \
    -c tools.build:skip_test=True \
    --build=missing \
    {{CONAN_PYTHON_CONFIG}} \
    --profile:all={{CONAN_PROFILE}}

run_to_output target *ARGS:
    -just {{target}} {{ARGS}} > build/target_result.log 2>&1
    echo "execution OK"
    