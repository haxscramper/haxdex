#pragma once

#undef emit
#undef slots

#include <memory>

#include <clang/AST/ASTConsumer.h>
#include <clang/Frontend/CompilerInstance.h>
#include <clang/Frontend/FrontendActions.h>
#include <clang/Tooling/Tooling.h>

#include <haxdex_read_code_cpp_common/reflection_config.hpp>

#include "branch_include.pb.h"

class BranchingIncludeConsumer : public clang::ASTConsumer {
public:
  explicit BranchingIncludeConsumer(clang::CompilerInstance &CI,
                                    ReflectionCLI const &cli);

  void HandleTranslationUnit(clang::ASTContext &Context) override;

private:
  std::unique_ptr<TU> out;
  ReflectionCLI cli;
};

class BranchingIncludeFrontendAction : public clang::ASTFrontendAction {
public:
  explicit BranchingIncludeFrontendAction(ReflectionCLI const &cli)
      : cli(cli) {}

protected:
  std::unique_ptr<clang::ASTConsumer>
  CreateASTConsumer(clang::CompilerInstance &CI,
                    llvm::StringRef filename) override;

private:
  ReflectionCLI cli;
};

class BranchingIncludeFrontendActionFactory
    : public clang::tooling::FrontendActionFactory {
public:
  explicit BranchingIncludeFrontendActionFactory(ReflectionCLI const &cli)
      : cli(cli) {}

  std::unique_ptr<clang::FrontendAction> create() override;

private:
  ReflectionCLI cli;
};

void run_branching_include_collection(ReflectionCLI const &cli);
