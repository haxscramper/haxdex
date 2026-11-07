cmake_minimum_required(VERSION 4.4)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED True)
set(CXX_STANDARD 23)

find_package(OpenSSL CONFIG REQUIRED)
find_package(ZLIB CONFIG REQUIRED)
find_package(perfetto REQUIRED QUIET)
find_package(hstd_cpp_lib CONFIG REQUIRED QUIET)
find_package(haxdex_read_code_cpp_common CONFIG REQUIRED QUIET)

add_executable(haxdex_cpp_profdata_merger)

target_include_directories(
    haxdex_cpp_profdata_merger
    PUBLIC
        "${CMAKE_CURRENT_LIST_DIR}/src"
)

set_target_properties(
    haxdex_cpp_profdata_merger
    PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY
            "${CMAKE_BINARY_DIR}/bin"
        LIBRARY_OUTPUT_DIRECTORY
            "${CMAKE_BINARY_DIR}/lib"
        ARCHIVE_OUTPUT_DIRECTORY
            "${CMAKE_BINARY_DIR}/lib"
)

haxorg_glob_add_sources2(
  TARGET haxdex_cpp_profdata_merger LS_REGEX "${CMAKE_CURRENT_LIST_DIR}/src/haxdex_cpp_profdata_merger/.*"
  SEARCH_BASE "${CMAKE_CURRENT_LIST_DIR}"
  RECURSE ON
)

target_link_libraries(
    haxdex_cpp_profdata_merger
    PUBLIC
        hstd::hstd_cpp_lib
        range-v3::range-v3
        SQLiteCpp
        sqlite3
        pthread
        absl::base
        absl::flags
        perfetto::perfetto
        haxdex::haxdex_read_code_cpp_common
        ${ZLIB_LIBRARIES}
        ${PCRE_LIBRARIES}
)
