cmake_minimum_required(VERSION 3.28)

project(
    haxdex_read_code_cpp_includes
    VERSION 0.1.0
    LANGUAGES
        CXX
        C
)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_SCAN_FOR_MODULES OFF)

if(NOT TARGET haxdex::haxdex_read_code_cpp_common)
    find_package(haxdex_read_code_cpp_common CONFIG REQUIRED QUIET)
endif()

add_library(haxdex_read_code_cpp_includes INTERFACE)
add_library(haxdex::haxdex_read_code_cpp_includes ALIAS haxdex_read_code_cpp_includes)

target_include_directories(
    haxdex_read_code_cpp_includes
    INTERFACE
        "${CMAKE_CURRENT_LIST_DIR}/src"
)

target_link_libraries(
    haxdex_read_code_cpp_includes
    INTERFACE
        haxdex::haxdex_read_code_cpp_common
)
