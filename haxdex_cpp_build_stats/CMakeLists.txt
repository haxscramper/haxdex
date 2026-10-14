cmake_minimum_required(VERSION 3.20)
project(trace_events_sax_parser LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(RapidJSON REQUIRED)
find_package(lexy REQUIRED)
find_package(fmt REQUIRED)
find_package(cpptrace REQUIRED)
find_package(quill REQUIRED)
find_package(SQLiteCpp REQUIRED)
find_package(duckdb REQUIRED)
find_package(roaring REQUIRED)
find_package(GTest REQUIRED)
find_package(benchmark REQUIRED)
find_package(CLI11 REQUIRED)
find_package(perfetto)

add_library(
    trace_events_lib
    src/haxdex_cpp_build_stats/to_json.hpp
    src/haxdex_cpp_build_stats/to_json.cpp
    src/haxdex_cpp_build_stats/to_json_normalized.hpp
    src/haxdex_cpp_build_stats/string_interner.hpp
    src/haxdex_cpp_build_stats/set_collection.hpp
    src/haxdex_cpp_build_stats/set_collection.cpp
    src/haxdex_cpp_build_stats/name_tree.hpp
    src/haxdex_cpp_build_stats/name_tree.cpp
)

add_executable(trace_event_cli src/haxdex_cpp_build_stats/main.cpp)
add_executable(
    trace_event_test
    tests/tSubsetCollection.cpp
    tests/tStringInterner.cpp
    tests/tNameTree.cpp
    tests/tE2E.cpp
    tests/perf_tu.cpp
)

add_executable(trace_event_bench bench/bSubsetCollection.cpp)

target_include_directories(
    trace_events_lib
    PUBLIC
        "${CMAKE_SOURCE_DIR}/cppdecl/include"
        "${CMAKE_SOURCE_DIR}/src"
)

target_link_libraries(
    trace_events_lib
    PUBLIC
        rapidjson
        foonathan::lexy
        fmt::fmt
        cpptrace::cpptrace
        quill::quill
        duckdb::duckdb
        roaring::roaring
        perfetto::perfetto
)

target_link_libraries(
    trace_event_cli
    PUBLIC
        trace_events_lib
        CLI11::CLI11
)

target_link_libraries(
    trace_event_bench
    PUBLIC
        trace_events_lib
        benchmark::benchmark_main
)

target_link_libraries(
    trace_event_test
    PUBLIC
        trace_events_lib
        gtest::gtest
)
