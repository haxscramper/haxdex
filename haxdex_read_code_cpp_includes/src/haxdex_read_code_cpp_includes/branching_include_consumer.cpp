#include "branching_include_consumer.hpp"

#include "branching_include_collector.hpp"

#include <fstream>
#include <stdexcept>
#include <string>
#include <utility>

#include <clang/Basic/SourceManager.h>
#include <clang/Lex/PPCallbacks.h>
#include <clang/Lex/Preprocessor.h>
#include <clang/Tooling/CommonOptionsParser.h>
#include <clang/Tooling/JSONCompilationDatabase.h>

#include <google/protobuf/util/json_util.h>
#include <llvm/Support/TimeProfiler.h>

#include <haxdex_read_code_cpp_common/demangle_json.hpp>
#include <hstd_cpp_lib/logger/logger.hpp>
#include <hstd_cpp_lib/stdlib/Filesystem.hpp>

namespace {

class IncludeCollectorCallback : public clang::PPCallbacks {
public:
  IncludeCollectorCallback(TU *out, clang::SourceManager *sourceManager)
      : out(out), sourceManager(sourceManager) {}

  void
  InclusionDirective(clang::SourceLocation HashLoc,
                     clang::Token const &IncludeTok, llvm::StringRef FileName,
                     bool IsAngled, clang::CharSourceRange FilenameRange,
                     clang::OptionalFileEntryRef File,
                     llvm::StringRef SearchPath, llvm::StringRef RelativePath,
                     clang::Module const *SuggestedModule, bool ModuleImported,
                     clang::SrcMgr::CharacteristicKind FileType) override {
    if (File && sourceManager->isInMainFile(HashLoc)) {
      auto *include = out->add_includes();
      include->set_absolutepath(File->getName().str());
      include->set_relativepath(FileName.str());
    }
  }

private:
  TU *out;
  clang::SourceManager *sourceManager;
};

std::string
getMainFileAbsolutePath(clang::CompilerInstance const &compilerInstance) {
  auto const &sourceManager = compilerInstance.getSourceManager();
  auto mainFileID = sourceManager.getMainFileID();
  auto fileEntryRef = sourceManager.getFileEntryRefForID(mainFileID);

  if (fileEntryRef) {
    return std::string{fileEntryRef->getName()};
  }

  return "";
}

void saveIncludeData(TU const &out, std::string const &filename) {
  google::protobuf::util::JsonPrintOptions options;
  options.add_whitespace = true;
  options.always_print_fields_with_no_presence = true;
  options.preserve_proto_field_names = true;

  std::string json;
  auto status =
      google::protobuf::util::MessageToJsonString(out, &json, options);

  if (!status.ok()) {
    throw std::runtime_error("Failed to serialize include data to JSON: " +
                             std::string(status.message()));
  }

  {
    std::ofstream file;
    file.exceptions(std::ios::failbit | std::ios::badbit);
    file.open(filename, std::ios::out | std::ios::trunc | std::ios::binary);

    if (!out.SerializeToOstream(&file)) {
      throw std::runtime_error("Failed to serialize include data to " +
                               filename);
    }

    file.close();
  }

  {
    std::ofstream file;
    file.exceptions(std::ios::failbit | std::ios::badbit);
    file.open(filename + ".json", std::ios::out | std::ios::trunc);
    file << json;
    file.close();
  }
}

} // namespace

BranchingIncludeConsumer::BranchingIncludeConsumer(clang::CompilerInstance &CI,
                                                   ReflectionCLI const &cli)
    : out(std::make_unique<TU>()), cli(cli) {
  out->set_absolutepath(getMainFileAbsolutePath(CI));

  CI.getPreprocessor().addPPCallbacks(
      std::make_unique<IncludeCollectorCallback>(out.get(),
                                                 &CI.getSourceManager()));

  CI.getPreprocessor().addPPCallbacks(
      std::make_unique<BranchingIncludeCollectorCallback>(
          out.get(), &CI.getSourceManager(), &CI.getPreprocessor()));
}

void BranchingIncludeConsumer::HandleTranslationUnit(clang::ASTContext &) {
  llvm::TimeTraceScope timeScope{"branching-include-write-tu"};

  if (cli.output.empty()) {
    throw std::invalid_argument("CLI output is empty");
  }

  saveIncludeData(*out, cli.output);
}

std::unique_ptr<clang::ASTConsumer>
BranchingIncludeFrontendAction::CreateASTConsumer(clang::CompilerInstance &CI,
                                                  llvm::StringRef) {
  return std::make_unique<BranchingIncludeConsumer>(CI, cli);
}

std::unique_ptr<clang::FrontendAction>
BranchingIncludeFrontendActionFactory::create() {
  return std::make_unique<BranchingIncludeFrontendAction>(cli);
}

void run_branching_include_collection(ReflectionCLI const &cli) {
  std::string errorMessage;
  auto database = clang::tooling::JSONCompilationDatabase::loadFromFile(
      cli.reflection.compilation_database, errorMessage,
      clang::tooling::JSONCommandLineSyntax::AutoDetect);

  if (!database) {
    throw hstd::runtime_error::init(
        fmt::format("Failed to process provided JSON DB, failure was: {}\n"
                    "CompilationDB = {}",
                    errorMessage, cli.reflection.compilation_database));
  }

  clang::tooling::ArgumentsAdjustingCompilations adjustedCompilations(
      std::move(database));

  adjustedCompilations.appendArgumentsAdjuster(
      [cli](clang::tooling::CommandLineArguments const &args,
            llvm::StringRef filename) -> clang::tooling::CommandLineArguments {
        return dropReflectionPlugin(args, filename, cli);
      });

  clang::tooling::ClangTool tool(adjustedCompilations, cli.input);

  if (cli.reflection.toolchain_include) {
    if (!hstd::fs::is_directory(
            std::string(cli.reflection.toolchain_include.value()))) {
      throw hstd::FilesystemError::init(fmt::format(
          "Toolchain include is not a directory or does not exist '{}'",
          cli.reflection.toolchain_include.value()));
    }
  }

  HSLOG_INFO("Starting branching include frontend tool run");

  BranchingIncludeFrontendActionFactory factory{cli};
  int result = tool.run(&factory);

  if (result != 0) {
    throw std::runtime_error("Branching include tool execution failed");
  }
}
