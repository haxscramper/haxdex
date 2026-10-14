cmake_minimum_required(VERSION 4.4)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED True)
set(CXX_STANDARD 23)

find_package(OpenSSL CONFIG REQUIRED)
find_package(ZLIB CONFIG REQUIRED)
find_package(hstd_cpp_lib CONFIG REQUIRED QUIET)

add_executable(code_forensics)

set_target_properties(
    code_forensics
    PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY
            "${CMAKE_BINARY_DIR}/bin"
        LIBRARY_OUTPUT_DIRECTORY
            "${CMAKE_BINARY_DIR}/lib"
        ARCHIVE_OUTPUT_DIRECTORY
            "${CMAKE_BINARY_DIR}/lib"
)

haxorg_glob_add_sources2(
  TARGET code_forensics LS_REGEX "${CMAKE_CURRENT_LIST_DIR}/src/haxdex_cpp_code_forensics/.*"
  SEARCH_BASE "${CMAKE_CURRENT_LIST_DIR}"
  RECURSE ON
)

target_link_libraries(
    code_forensics
    PUBLIC
        hstd::hstd_cpp_lib
        range-v3::range-v3
        SQLiteCpp
        sqlite3
        pthread
        dl
        pcre
        OpenSSL::SSL
        OpenSSL::Crypto
        absl::base
        absl::flags
        perfetto::perfetto
        ${ZLIB_LIBRARIES}
        ${PCRE_LIBRARIES}
)

if(TARGET Boost::process)
    target_link_libraries(code_forensics PUBLIC Boost::process)
endif()

target_link_libraries(code_forensics PUBLIC "${LIBGIT2_LIBRARIES}")
target_include_directories(code_forensics PUBLIC "${LIBGIT2_INCLUDE_DIRS}")
target_link_directories(code_forensics PUBLIC ${LIBGIT2_LIBRARY_DIRS})
target_link_options(code_forensics PUBLIC "-Wl,-rpath,${LIBGIT2_LIBRARY_DIRS}")

target_include_directories(
    code_forensics
    PUBLIC
        "${BASE}/thirdparty/immer"
        "${BASE}/thirdparty/lager"
        "${BASE}/thirdparty/libgit2/include"
)
