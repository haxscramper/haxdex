#include <cppdecl/declarations/parse.h>
#include <fstream>
#include <haxdex_cpp_build_stats/to_json.hpp>
#include <haxdex_cpp_build_stats/to_json_normalized.hpp>
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

// Add the project headers declaring cppdecl's parser and JSON converters.

rapidjson::Value parseQualifiedName(
    std::string_view                    original,
    rapidjson::Document::AllocatorType& allocator) {
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

    cppdecl::ParseQualifiedNameFlags flags{};
    std::string_view                 input{original};
    auto                             result = cppdecl::ParseQualifiedName(input, flags);

    if (std::get_if<cppdecl::ParseError>(&result) != nullptr) { return output; }

    const auto& parsed   = std::get<0>(result);
    output["parsed"]     = cppdecl::ToJson(parsed, allocator);
    output["normalized"] = cppdecl::ToJsonNormalized(parsed, allocator);
    output["ok"].SetBool(true);

    return output;
}

int main(int argc, char** argv) {
    if (argc != 3) {
        throw std::runtime_error{fmt::format(
            "Usage: {} <input.json> <output.json>; expected two "
            "positional arguments, received {}",
            argv[0],
            argc - 1)};
    }

    const std::string inputPath{argv[1]};
    const std::string outputPath{argv[2]};

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
        auto                   parsed = parseQualifiedName(original, allocator);
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
