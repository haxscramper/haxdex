#include <hstd_cpp_lib/logger/perfetto_aux_impl_template.hpp>

#include <haxdex_read_code_cpp_common/reflection_cli_parse.hpp>
#include <haxdex_read_code_cpp_common/reflection_perf.hpp>
#include <haxdex_read_code_cpp_run_coverage/profdata_merger.hpp>

int main(int argc, char const **argv) {
  ReflectionCLI cli = parse_reflection_cli(argc, argv);

  std::unique_ptr<perfetto::TracingSession> perfetto_session;
  if (cli.perf_path) {
    perfetto_session = StartProcessTracing("haxdex_read_code_cpp_run_coverage");
  }

  build_run_coverage_merge(cli);

  if (cli.perf_path) {
    hstd::fs::path out_path{cli.perf_path.value()};
    StopTracing(std::move(perfetto_session), out_path);
  }

  return 0;
}
