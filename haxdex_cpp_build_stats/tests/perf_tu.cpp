#ifndef HSTD_CPP_BUILD_WITH_PERFETTO
#    error HSTD_CPP_BUILD_WITH_PERFETTO must be defined
#endif

#if !HSTD_CPP_BUILD_WITH_PERFETTO
#    error HSTD_CPP_BUILD_WITH_PERFETTO must be 1
#endif

#include <haxdex_cpp_build_stats/perfetto_aux.hpp>
#include <hstd_cpp_lib/logger/perfetto_main.hpp>
