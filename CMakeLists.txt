cmake_minimum_required(VERSION 3.28)

project(
    haxdex_read_code
    VERSION 0.1.0
    LANGUAGES
        CXX
        C
)

set(HSTD_CPP_BUILD_WITH_PERFETTO ON CACHE BOOL "")

add_subdirectory(haxdex_cpp_code_forensics)
add_subdirectory(haxdex_read_code_cpp_includes)
add_subdirectory(haxdex_read_code_cpp_symbol)
add_subdirectory(haxdex_read_code_cpp_build_coverage)
add_subdirectory(haxdex_read_code_cpp_run_coverage)
add_subdirectory(haxdex_cpp_build_stats)
