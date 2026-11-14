#include <CLI/CLI.hpp>
#include <cppdecl/declarations/parse.h>
#include <cppdecl/declarations/parse_simple.h>
#include <fstream>
#include <haxdex_cpp_build_stats/event_writer.hpp>
#include <haxdex_cpp_build_stats/to_json.hpp>
#include <haxdex_cpp_build_stats/to_json_normalized.hpp>
#include <hstd_cpp_lib/logger/logger.hpp>
#include <iterator>
#include <stdexcept>
#include <string>
#include <string_view>
#include <variant>

#include <fmt/format.h>
#include <rapidjson/document.h>
#include <rapidjson/error/en.h>
#include <rapidjson/ostreamwrapper.h>
#include <rapidjson/prettywriter.h>

#include <haxdex_cpp_build_stats/perfetto_aux.hpp>
#include <hstd_cpp_lib/logger/perfetto_main.hpp>


// Add the project headers declaring cppdecl's parser and JSON converters.

rapidjson::Value parseQualifiedName(
    std::string_view                    original,
    rapidjson::Document::AllocatorType& allocator,
    bool                                with_full_qualified) {
    rapidjson::Value output{rapidjson::kObjectType};
    output.AddMember("ok", false, allocator);
    output.AddMember("normalized", rapidjson::Value{}, allocator);
    output.AddMember("parsed", rapidjson::Value{}, allocator);
    output.AddMember(
        "original",
        rapidjson::Value{
            original.data(),
            static_cast<rapidjson::SizeType>(original.size()),
            allocator},
        allocator);

    cppdecl::ParseDeclFlags flags{cppdecl::ParseDeclFlags::accept_everything};
    std::string_view        input{original};
    auto                    result = cppdecl::ParseDecl(input, flags);

    if (input.size() != 0) {
        HSLOG_WARNING_DEPTH_SCOPE_ANON(
            "Could not finish parsing everything: {} left", input);

        try {
            auto name   = cppdecl::ParseQualifiedName_Simple(input);
            std::ignore = cppdecl::ToJson(name, allocator);
            std::ignore = cppdecl::ToJsonNormalized(name, allocator);
            HSLOG_INFO("Parsed as qualified name OK");
        } catch (std::exception const& e) {
            HSLOG_ERROR("Value cannot be parsed as type: {}", e.what());
        }

        try {
            auto type   = cppdecl::ParseType_Simple(input);
            std::ignore = cppdecl::ToJson(type, allocator);
            std::ignore = cppdecl::ToJsonNormalized(type, allocator);
            HSLOG_INFO("Parsed as type OK");
        } catch (std::exception const& e) {
            HSLOG_ERROR("Value cannot be parsed as type: {}", e.what());
        }

        try {
            auto decl   = cppdecl::ParseDecl_Simple(input);
            std::ignore = cppdecl::ToJson(decl, allocator);
            std::ignore = cppdecl::ToJsonNormalized(decl, allocator);
            HSLOG_INFO("Parsed as decl OK");
        } catch (std::exception const& e) {
            HSLOG_ERROR("Value cannot be parsed as decl: {}", e.what());
        }
    }

    if (std::get_if<cppdecl::ParseError>(&result) != nullptr) { return output; }

    const auto& parsed = std::get<0>(result);
    if (with_full_qualified) { output["parsed"] = cppdecl::ToJson(parsed, allocator); }
    output["normalized"] = cppdecl::ToJsonNormalized(parsed, allocator);
    output["ok"].SetBool(true);

    return output;
}


int main(int argc, char** argv) {
    CLI::App app{"Process a JSON file"};

    std::string inputPath;
    std::string outputPath;
    bool        with_full_qualified = false;

    app.add_option("input", inputPath, "Input JSON path")->required();
    app.add_option("output", outputPath, "Output JSON path")->required();
    app.add_flag(
        "--with-full-qualfied", with_full_qualified, "Use fully qualified names");

    hstd::log::push_sink(hstd::log::init_stdout_sink(false));

    CLI11_PARSE(app, argc, argv);

    std::ifstream inputFile{};
    inputFile.exceptions(std::ios::failbit | std::ios::badbit);
    inputFile.open(inputPath, std::ios::binary);

    const std::string inputText{
        std::istreambuf_iterator<char>{inputFile}, std::istreambuf_iterator<char>{}};
    inputFile.close();

    rapidjson::Document inputDocument{};
    inputDocument.Parse(inputText.data(), inputText.size());

    if (inputDocument.HasParseError()) {
        throw std::runtime_error{fmt::format(
            "Cannot parse JSON file '{}': {} at byte offset {}",
            inputPath,
            rapidjson::GetParseError_En(inputDocument.GetParseError()),
            inputDocument.GetErrorOffset())};
    }

    if (!inputDocument.IsArray()) {
        throw std::runtime_error{fmt::format(
            "Invalid input in '{}': expected a JSON array of strings", inputPath)};
    }

    rapidjson::Document outputDocument{rapidjson::kArrayType};
    auto&               allocator = outputDocument.GetAllocator();

    int index{0};
    for (const auto& value : inputDocument.GetArray()) {
        if (!value.IsString()) {
            throw std::runtime_error{fmt::format(
                "Invalid input in '{}': array element {} must be a string",
                inputPath,
                index)};
        }

        const std::string_view original{value.GetString(), value.GetStringLength()};
        auto parsed = parseQualifiedName(original, allocator, with_full_qualified);
        outputDocument.PushBack(parsed, allocator);
        ++index;
    }

    std::ofstream outputFile{};
    outputFile.exceptions(std::ios::failbit | std::ios::badbit);
    outputFile.open(outputPath, std::ios::binary | std::ios::trunc);

    rapidjson::OStreamWrapper                          outputStream{outputFile};
    rapidjson::PrettyWriter<rapidjson::OStreamWrapper> writer{outputStream};
    if (!outputDocument.Accept(writer)) {
        throw std::runtime_error{fmt::format(
            "Cannot serialize results to '{}': JSON writer rejected "
            "the output document",
            outputPath)};
    }

    outputFile.put('\n');
    outputFile.close();
    return 0;
}
