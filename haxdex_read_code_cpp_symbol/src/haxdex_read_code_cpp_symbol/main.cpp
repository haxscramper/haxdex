#include <hstd_cpp_lib/logger/perfetto_aux_impl_template.hpp>

#include <haxdex_read_code_cpp_common/reflection_cli_parse.hpp>
#include <haxdex_read_code_cpp_common/reflection_perf.hpp>
#include <haxdex_read_code_cpp_symbol/reflection_demangler.hpp>

int main(int argc, char const** argv) {
    ReflectionCLI cli = parse_reflection_cli(argc, argv);

#ifdef HAXORG_CPP_ORG_LIB_BUILD_WITH_PERFETTO
    std::unique_ptr<perfetto::TracingSession> perfetto_session;
    if (cli.perf_path) {
        perfetto_session = StartProcessTracing("haxdex_read_code_cpp_symbol");
    }
#endif

    if (cli.input.empty()) {
        throw hstd::invalid_argument::init(
            "Missing 'input' field with the binary path in the CLI configuration.");
    }

    auto db = getSymbolsInBinary(cli.input.at(0));
    db.writeToFile(cli.output);

    if (cli.perf_path) {
        hstd::fs::path out_path{cli.perf_path.value()};
#ifdef HAXORG_CPP_ORG_LIB_BUILD_WITH_PERFETTO
        StopTracing(std::move(perfetto_session), out_path);
#endif
    }

    return 0;
}
