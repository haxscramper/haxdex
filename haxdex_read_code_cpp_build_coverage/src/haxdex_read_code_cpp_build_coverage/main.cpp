#include <filesystem>

#include <hstd_cpp_lib/logger/logger.hpp>
#include <hstd_cpp_lib/logger/perfetto_aux.hpp>
#include <hstd_cpp_lib/logger/perfetto_aux_impl_template.hpp>
#include <hstd_cpp_lib/stdlib/Filesystem.hpp>
#include <hstd_cpp_lib/stdlib/serde/Json.hpp>
#include <hstd_cpp_lib/stdlib/serde/JsonSerde.hpp>
#include <hstd_cpp_lib/system/reflection.hpp>

#include <haxdex_read_code_cpp_common/reflection_cli_parse.hpp>
#include <haxdex_read_code_cpp_common/reflection_perf.hpp>

struct BuildProfileEvent {
  int pid{};
  int tid{};
  hstd::Str ph{};
  int ts{};
  int dur{};
  hstd::Str name{};
  hstd::Opt<hstd::Str> cat{};
  json args{};

  DESC_FIELDS(BuildProfileEvent, (pid, tid, ph, ts, dur, name, cat, args));
};

struct BuildProfileFile {
  hstd::Vec<BuildProfileEvent> traceEvents;
  hstd::Str path;
  hstd::u64 beginningOfTime;
  DESC_FIELDS(BuildProfileFile, (traceEvents, path, beginningOfTime));
};

struct BuildProfileCollection {
  hstd::Vec<BuildProfileFile> files;
  DESC_FIELDS(BuildProfileCollection, (files));
};

std::string
generateInstantiateFunctionReport(BuildProfileCollection const &collection) {
  __perf_trace("main", "generateInstantiateFunctionReport");
  std::map<std::string, std::vector<BuildProfileEvent>> groupEvents;

  for (const auto &file : collection.files) {
    for (const auto &event : file.traceEvents) {
      if (event.name == "InstantiateFunction" &&
          event.args.contains("detail")) {
        std::string detail = event.args.at("detail").get<std::string>();
        // if (detail.length() > 120) {
        //     detail = detail.substr(0, 120) + "...";
        // }
        groupEvents[detail].push_back(event);
      }
    }
  }

  std::vector<std::pair<std::string, std::vector<BuildProfileEvent>>>
      sortedGroups;
  for (const auto &[detail, events] : groupEvents) {
    sortedGroups.push_back({detail, events});
  }

  std::sort(sortedGroups.begin(), sortedGroups.end(),
            [](auto const &a, auto const &b) {
              int totalDurA = 0;
              int totalDurB = 0;
              for (const auto &event : a.second) {
                totalDurA += event.dur;
              }
              for (const auto &event : b.second) {
                totalDurB += event.dur;
              }
              return totalDurB < totalDurA;
            });

  auto formatDuration = [](int microseconds) -> std::string {
    if (microseconds < 1000) {
      return fmt::format("{}µs", microseconds);
    }
    int ms = microseconds / 1000;
    int remainingUs = microseconds % 1000;
    if (ms < 1000) {
      if (remainingUs == 0) {
        return fmt::format("{}ms", ms);
      }
      return fmt::format("{}ms {}µs", ms, remainingUs);
    }
    int s = ms / 1000;
    int remainingMs = ms % 1000;
    if (remainingMs == 0 && remainingUs == 0) {
      return fmt::format("{}s", s);
    }
    if (remainingUs == 0) {
      return fmt::format("{}s {}ms", s, remainingMs);
    }
    if (remainingMs == 0) {
      return fmt::format("{}s {}µs", s, remainingUs);
    }
    return fmt::format("{}s {}ms {}µs", s, remainingMs, remainingUs);
  };

  std::string report;
  // int limit = std::min(40, static_cast<int>(sortedGroups.size()));
  for (int i = 0; i < sortedGroups.size(); ++i) {
    auto const &[detail, events] = sortedGroups.at(i);
    int totalDur = 0;
    for (const auto &event : events) {
      totalDur += event.dur;
    }
    int count = static_cast<int>(events.size());
    int avgDur = totalDur / count;

    report += fmt::format("{} = {} * {}: {}\n", formatDuration(totalDur), count,
                          formatDuration(avgDur), detail);
  }

  return report;
}

NO_COVERAGE void build_build_coverage_merge(ReflectionCLI const &cli) {

  BuildProfileCollection collection;

  {
    __perf_trace("main", "Parse JSON files");
    int count = 0;
    for (const auto &entry : std::filesystem::recursive_directory_iterator{
             cli.profdata.build_profile_dir}) {
      if (entry.is_regular_file() && entry.path().extension() == ".json" &&
          entry.path().stem().extension() == ".cpp") {
        // if (30 < ++count) { continue; }
        auto file = hstd::from_json_eval<BuildProfileFile>(
            json::parse(hstd::readFile(entry.path())));
        file.path = entry.path().native();
        collection.files.push_back(file);
        LOG(INFO) << file.path << " OK";
      }
    }
  }

  auto report = generateInstantiateFunctionReport(collection);
  LOG(INFO) << "\n" << report;
  hstd::writeFile(cli.output, report);
}

int main(int argc, char const **argv) {
  ReflectionCLI cli = parse_reflection_cli(argc, argv);

  std::unique_ptr<perfetto::TracingSession> perfetto_session;
  if (cli.perf_path) {
    perfetto_session =
        StartProcessTracing("haxdex_read_code_cpp_build_coverage");
  }

  build_build_coverage_merge(cli);

  if (cli.perf_path) {
    hstd::fs::path out_path{cli.perf_path.value()};
    StopTracing(std::move(perfetto_session), out_path);
  }

  return 0;
}
